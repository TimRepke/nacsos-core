from pathlib import Path
from typing import cast

from nacsos_data.db.schemas import Import
from nacsos_data.models.imports import ImportConfig
from nacsos_data.util import ensure_values
from nacsos_data.util.academic.importing import (
    import_wos_files,
    import_openalex_files,
    import_academic_db,
    import_scopus_csv_file,
    import_openalex
)
from nacsos_data.util.errors import NotFoundError

from server.util.config import settings, conf_file
from . import TaskContext, celery_task, unpack_context


@celery_task
async def submit_import_task(ctx: TaskContext, import_id: str | None = None) -> None:  # type: ignore[arg-type] #FIXME
    session, target_dir, logger, work_dir, task, celery = unpack_context(ctx, __name__)
    logger.info('Preparing import task!')

    if import_id is None:
        raise ValueError('import_id is required here.')

    import_details = await session.get(Import, import_id)
    if import_details is None:
        raise NotFoundError(f'No import info for id={import_id}')

    user_id, project_id, config = cast(tuple[str, str, ImportConfig],
                                       ensure_values(import_details, 'user_id', 'project_id', 'config'))
    logger.info(f'Task config: {config.kind}')

    if config.kind == 'wos':
        logger.info('Proceeding with Web of Science import...')
        await import_wos_files(sources=config.sources,
                               project_id=project_id,
                               import_id=import_id,
                               db_config=Path(conf_file),
                               logger=logger.getChild('wos'))
    elif config.kind == 'scopus':
        logger.info('Proceeding with Scopus import...')
        await import_scopus_csv_file(sources=config.sources,
                                     project_id=project_id,
                                     import_id=import_id,
                                     db_config=Path(conf_file),
                                     logger=logger.getChild('scopus'))
    elif config.kind == 'academic':
        logger.info('Proceeding with AcademicItem file import...')
        await import_academic_db(sources=config.sources,
                                 project_id=project_id,
                                 import_id=import_id,
                                 db_config=Path(conf_file),
                                 logger=logger.getChild('academic'))
    elif config.kind == 'oa-file':
        logger.info('Proceeding with OpenAlex file import...')
        await import_openalex_files(sources=config.sources,
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
