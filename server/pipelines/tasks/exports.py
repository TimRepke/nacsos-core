import logging
import sys
from pathlib import Path
from typing import Any
from fastapi import HTTPException


import dramatiq

from nacsos_data.db import get_engine_async
from nacsos_data.util.export.dict import prepare_export_table
from nacsos_data.util.export.file import get_author_names, write_csv, write_ris, write_excel, write_jsonl

from server.util.config import settings
from server.pipelines.actor import NacsosActor
from server.api.routes.export import ExportRequest


logger = logging.getLogger('pipelines')

if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logger.addHandler(handler)

logger.setLevel(logging.INFO)
logger.propagate = False


def prefix_sources(sources: list[Path]) -> list[Path]:
    return [settings.PIPES.user_data_dir / path for path in sources]


@dramatiq.actor(actor_class=NacsosActor, max_retries=0)
async def export_task(project_id: str, export_format: str, query_json: dict[str, Any], max_results: int, export_path: str) -> Path:
    logger.info('Received export task')
    query = ExportRequest(**query_json)
    async with NacsosActor.exec_context() as (db_settings, logger_, target_dir, work_dir, task_id, message_id):
        logger.info('Preparing export task!')
        db_engine = get_engine_async(settings=db_settings)
        result: list[dict[str, Any]] = await prepare_export_table(
            bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
            assignment_scope_ids=query.assignment_scope_ids,
            user_ids=query.user_ids,
            project_id=project_id,
            labels=query.labels,
            nql_filter=query.nql_filter,
            ignore_repeat=query.ignore_repeat,
            ignore_hierarchy=query.ignore_hierarchy,
            db_engine=db_engine,
            max_results=max_results,
        )

        # dropping columns and author name transformation is required for all export formats, so it's done here
        cols_to_drop = query.columns_to_drop
        result = [
            {k: v for k, v in (row | {'authors': get_author_names(row.get('authors')), 'authors_raw': row.get('authors')}).items() if k not in cols_to_drop}
            for row in result
        ]

        match export_format:
            case 'csv':
                fp = write_csv(result)
                # return CSVResponse(fp, background=BackgroundTask(cleanup, fp))
            case 'excel':
                fp = write_excel(result)
                # return ExcelResponse(fp, background=BackgroundTask(cleanup, fp))
            case 'ris':
                fp = write_ris(result, query.labels)
                # return RISResponse(fp, background=BackgroundTask(cleanup, fp))
            case 'jsonl':
                fp = write_jsonl(result)
                # return JSONLResponse(fp, background=BackgroundTask(cleanup, fp))
            case _:
                raise HTTPException(
                    status_code=400, detail=f"Requested export format '{export_format}' is not one of the recognized formats: ['csv', 'excel', 'ris', 'jsonl']"
                )

        # user_id, project_id, config = cast(tuple[str, str, ExportConfig], ensure_values(import_details, 'user_id', 'project_id', 'config'))
        # logger.info(f'Task config: {config.kind}')

        logger.info(f'Temporary file saved to {fp}')

        # new_fp = '.data/exports/[project-id]/[username]/datetime.[ext]'
        # new_fp = f'{target_dir}/data/exports/{project_id}/{user_id}/datetime.[ext]'
        # 'settings.PIPES.priority_dir / exports / [project-id] / [username] / {export_id}.{ext}'

        # src: temp file, dest: file to save
        src = Path(fp)
        dest = Path(export_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        with open(src, 'rb') as fsrc, open(dest, 'wb') as fdst:
            fdst.write(fsrc.read())

        # remove the temp file after copy
        src.unlink()

        logger.info(f'File path: {dest}')
        return dest

        # TODO:
        # why are there PENDING tasks in DB table that dont match default.XQ for imports???? --> probably because i deleted the image and some stuff at some point
        # how are they supposed to get picked? --> by special handling of default.XQ queue
        # why can't see default.msgs in redis????? --> because it gets deleted after getting consumed quite fast
        # why did default.XQ.msgs got renamed to default.msgs at some point and new failed messages started to accumulate
        #   in default.XQ.msgs again?
        # why do i get a logger recursion error when i change the request from jsonl to csv?

        # return fp
