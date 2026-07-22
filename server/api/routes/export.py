import os
import uuid
from typing import TYPE_CHECKING, Any
# from memory_profiler import profile

from nacsos_data.db.crud.projects import read_project_by_id

from fastapi import APIRouter, Depends, HTTPException
from nacsos_data.models.nql import NQLFilter
from nacsos_data.util.export.dict import (
    prepare_export_table,
    get_project_labels,
    get_project_scopes,
    get_project_bot_scopes,
    get_project_users,
)
from nacsos_data.util.export.util import LabelOptions
from nacsos_data.util.export.file import get_author_names, write_csv, write_excel, write_jsonl, write_ris
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from server.util.security import UserPermissionChecker

from nacsos_data.util.auth import UserPermissions

from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()

DEFAULT_COLUMNS_TO_DROP = ['type', 'time_edited', 'project_id', 'title_slug', 'keywords', 'meta']


def cleanup(file: str) -> None:
    os.remove(file)


# todo what does this do, how to use it?
class CFR(FileResponse):  # custom file response to set the media type
    media_type = 'application/csv'


class ExportRequest(BaseModel):
    labels: list[LabelOptions]
    nql_filter: NQLFilter | None = None
    bot_annotation_metadata_ids: list[str] | None = None
    assignment_scope_ids: list[str] | None = None
    user_ids: list[str] | None = None
    ignore_hierarchy: bool = True
    ignore_repeat: bool = True
    columns_to_drop: list[str] = DEFAULT_COLUMNS_TO_DROP


# @profile
@router.post('/annotations/{export_format}', response_class=CFR)
async def export_annotations(
    export_format: str,
    query: ExportRequest,
    max_results: int = 15000,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> FileResponse:
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
    result = [{k: (v if k != 'authors' else get_author_names(v)) for k, v in row.items() if k not in cols_to_drop} for row in result]

    match export_format:
        case 'csv':
            fp = write_csv(result)
            return FileResponse(fp, background=BackgroundTask(cleanup, fp), media_type='application/csv')
        case 'excel':
            fp = write_excel(result)
            return FileResponse(fp, background=BackgroundTask(cleanup, fp), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        case 'ris':
            fp = write_ris(result, query.labels)
            return FileResponse(fp, background=BackgroundTask(cleanup, fp), media_type='application/x-research-info-systems')
        case 'jsonl':
            fp = write_jsonl(result)
            return FileResponse(fp, background=BackgroundTask(cleanup, fp), media_type='text/plain')
        case _:
            raise HTTPException(
                status_code=400, detail=f"Requested export format '{export_format}' is not one of the recognized formats: ['csv', 'excel', 'ris', 'jsonl']"
            )


class ProjectBaseInfoEntry(BaseModel):
    id: str | uuid.UUID
    name: str


class ProjectBaseInfoScopeEntry(ProjectBaseInfoEntry):
    scheme_id: str | uuid.UUID
    scheme_name: str


class ProjectBaseInfo(BaseModel):
    users: list[ProjectBaseInfoEntry]
    scopes: list[ProjectBaseInfoScopeEntry]
    bot_scopes: list[ProjectBaseInfoEntry]
    labels: dict[str, LabelOptions]


@router.get('/project/baseinfo', response_model=ProjectBaseInfo)
async def get_export_baseinfo(
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> ProjectBaseInfo:
    project_users = await get_project_users(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_scopes = await get_project_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_bot_scopes = await get_project_bot_scopes(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project_labels = await get_project_labels(project_id=permissions.permissions.project_id, db_engine=db_engine)
    project = await read_project_by_id(project_id=permissions.permissions.project_id, engine=db_engine)

    if project is None:
        raise RuntimeError('Invalid state!')

    return ProjectBaseInfo(
        users=[ProjectBaseInfoEntry.model_validate(pu) for pu in project_users],
        scopes=[ProjectBaseInfoScopeEntry.model_validate(ps) for ps in project_scopes],
        bot_scopes=[ProjectBaseInfoEntry.model_validate(pbs) for pbs in project_bot_scopes],
        labels=project_labels,
    )
