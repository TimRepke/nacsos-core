from pathlib import Path
from typing import cast

import dramatiq
from nacsos_data.db.schemas import Import
from nacsos_data.models.imports import ImportConfig, ImportModel
from nacsos_data.util import ensure_values
from nacsos_data.util.academic.importing import (
    import_wos_files,
    import_openalex_files,
    import_academic_db,
    import_scopus_csv_file,
    import_openalex
)
from nacsos_data.util.errors import NotFoundError
from sqlalchemy import select

from server.util.config import settings, conf_file
from server.pipelines.actor import NacsosActor


def prefix_sources(sources: list[Path]):
    return [settings.PIPES.user_data_dir / path for path in sources]


@dramatiq.actor(actor_class=NacsosActor, max_retries=0)
async def import_task(import_id: str | None = None) -> None:
    async with NacsosActor.exec_context() as (session, logger, target_dir, work_dir, task_id, message_id):
        logger.info('Preparing import task!')

        if import_id is None:
            raise ValueError('import_id is required here.')

        stmt = select(Import).where(Import.import_id == import_id)
        result = (await session.execute(stmt)).scalars().one_or_none()
        if result is None:
            raise NotFoundError(f'No import info for id={import_id}')

        import_details = ImportModel.model_validate(result.__dict__)
        result.pipeline_task_id = task_id
        await session.commit()

        user_id, project_id, config = cast(tuple[str, str, ImportConfig],
                                           ensure_values(import_details, 'user_id', 'project_id', 'config'))
        logger.info(f'Task config: {config.kind}')

        if config.kind == 'wos':
            logger.info('Proceeding with Web of Science import...')
            await import_wos_files(sources=prefix_sources(config.sources),
                                   project_id=project_id,
                                   import_id=import_id,
                                   db_config=Path(conf_file),
                                   logger=logger.getChild('wos'))
        elif config.kind == 'scopus':
            logger.info('Proceeding with Scopus import...')
            await import_scopus_csv_file(sources=prefix_sources(config.sources),
                                         project_id=project_id,
                                         import_id=import_id,
                                         db_config=Path(conf_file),
                                         logger=logger.getChild('scopus'))
        elif config.kind == 'academic':
            logger.info('Proceeding with AcademicItem file import...')
            await import_academic_db(sources=prefix_sources(config.sources),
                                     project_id=project_id,
                                     import_id=import_id,
                                     db_config=Path(conf_file),
                                     logger=logger.getChild('academic'))
        elif config.kind == 'oa-file':
            logger.info('Proceeding with OpenAlex file import...')
            await import_openalex_files(sources=prefix_sources(config.sources),
                                        project_id=project_id,
                                        import_id=import_id,
                                        db_config=Path(conf_file),
                                        logger=logger.getChild('oa-file'))
        elif config.kind == 'oa-solr':
            logger.info('Proceeding with OpenAlex solr import...')
            await import_openalex(query=config.query,
                                  openalex_url=str(settings.OA_SOLR),
                                  def_type=config.def_type,
                                  field=config.field,
                                  op=config.op,
                                  project_id=project_id,
                                  import_id=import_id,
                                  db_config=Path(conf_file),
                                  logger=logger.getChild('oa-solr'))

        logger.info('Done, yo!')
