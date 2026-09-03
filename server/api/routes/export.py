import os
from typing import TYPE_CHECKING, Any

from nacsos_data.db.crud.annotations import read_annotation_scheme
from nacsos_data.db.crud.projects import read_project_by_id

from fastapi import APIRouter, Depends, HTTPException
from nacsos_data.models.nql import NQLFilter
from nacsos_data.scripts.exporter import ExportTypeEnum
from nacsos_data.util.export.dict import (
    prepare_export_table,
    get_project_scopes,
    get_project_bot_scopes,
    get_project_users,
    BaseInfoWithScheme,
    BaseInfo,
    get_labels_with_names,
)
from nacsos_data.util.export.util import (
    LabelOptions,
    RISLabelFormat,
    scheme_to_label_options,
)
from nacsos_data.util.export.file import (
    get_author_names,
    write_csv,
    write_excel,
    write_jsonl,
    write_ris,
    DEFAULT_COLUMNS_TO_DROP,
)
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from server.api.errors import AnnotationSchemeNotFoundError
from server.util.security import UserPermissionChecker

from nacsos_data.util.auth import UserPermissions

from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


def cleanup(file: str) -> None:
    os.remove(file)


class ExportRequest(BaseModel):
    labels: list[LabelOptions]
    nql_filter: NQLFilter | None = None
    bot_annotation_metadata_ids: list[str] | None = None
    assignment_scope_ids: list[str] | None = None
    user_ids: list[str] | None = None
    ignore_hierarchy: bool = True
    ignore_repeat: bool = True
    columns_to_drop: list[str] = DEFAULT_COLUMNS_TO_DROP
    ris_label_format: RISLabelFormat = RISLabelFormat.RAW_TAGS


class CSVResponse(FileResponse):  # custom file response to set the media type
    media_type = 'application/csv'


class ExcelResponse(FileResponse):
    media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class RISResponse(FileResponse):
    media_type = 'application/x-research-info-systems'


class JSONLResponse(FileResponse):
    media_type = 'text/plain'


@router.post(
    '/annotations/{export_format}',
    response_model=None,
    responses={
        200: {
            'content': {
                'application/csv': {},
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': {},
                'application/x-research-info-systems': {},
                'text/plain': {},
            }
        }
    },
)
async def export_annotations(
    export_format: ExportTypeEnum,
    query: ExportRequest,
    max_results: int = 15000,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> CSVResponse | ExcelResponse | RISResponse | JSONLResponse:
    result: list[dict[str, Any]] = await prepare_export_table(
        bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
        assignment_scope_ids=query.assignment_scope_ids,
        user_ids=query.user_ids,
        project_id=permissions.permissions.project_id,
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
            return CSVResponse(fp, background=BackgroundTask(cleanup, fp))
        case 'excel':
            fp = write_excel(result)
            return ExcelResponse(fp, background=BackgroundTask(cleanup, fp))
        case 'ris':
            label_mappings = await get_labels_with_names(scopes=query.assignment_scope_ids, db_engine=db_engine)
            fp = write_ris(result, query.labels, label_mappings, query.ris_label_format)
            return RISResponse(fp, background=BackgroundTask(cleanup, fp))
        case 'jsonl':
            fp = write_jsonl(result)
            return JSONLResponse(fp, background=BackgroundTask(cleanup, fp))
        case _:
            raise HTTPException(
                status_code=400, detail=f"Requested export format '{export_format}' is not one of the recognized formats: ['csv', 'excel', 'ris', 'jsonl']"
            )


class ProjectBaseInfo(BaseModel):
    users: list[BaseInfo]
    scopes: list[BaseInfoWithScheme]
    bot_scopes: list[BaseInfoWithScheme]


@router.get('/project/baseinfo', response_model=ProjectBaseInfo)
async def get_export_baseinfo(
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> ProjectBaseInfo:
    project = await read_project_by_id(project_id=permissions.permissions.project_id, engine=db_engine)
    if project is None:
        raise RuntimeError('Invalid state!')

    return ProjectBaseInfo(
        users=await get_project_users(project_id=permissions.permissions.project_id, db_engine=db_engine),
        scopes=await get_project_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine),
        bot_scopes=await get_project_bot_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine),
    )


@router.get('/project/label_options/{scheme_id}')
async def get_export_label_options(
    scheme_id: str,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> dict[str, LabelOptions]:
    scheme = await read_annotation_scheme(annotation_scheme_id=scheme_id, engine=db_engine)
    if scheme is None:
        raise AnnotationSchemeNotFoundError(f'No annotation scheme found with id {scheme_id}')
    return scheme_to_label_options(scheme)
