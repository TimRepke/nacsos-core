import csv
import os
import tempfile
import uuid
from typing import TYPE_CHECKING

from nacsos_data.db.crud.projects import read_project_by_id

from fastapi import APIRouter, Depends
from nacsos_data.models.nql import NQLFilter
from nacsos_data.util.annotations.export import (
    prepare_export_table,
    get_project_labels,
    get_project_scopes,
    get_project_bot_scopes,
    get_project_users,
    LabelOptions,
)
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from server.util.security import UserPermissionChecker

from nacsos_data.util.auth import UserPermissions

from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


def cleanup(file: str) -> None:
    os.remove(file)


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


@router.post('/annotations/csv', response_class=CFR)
async def get_annotations_csv(
    query: ExportRequest,
    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read')),
) -> FileResponse:
    result = await prepare_export_table(
        bot_annotation_metadata_ids=query.bot_annotation_metadata_ids,
        assignment_scope_ids=query.assignment_scope_ids,
        user_ids=query.user_ids,
        project_id=permissions.permissions.project_id,
        labels=query.labels,
        nql_filter=query.nql_filter,
        ignore_repeat=query.ignore_repeat,
        ignore_hierarchy=query.ignore_hierarchy,
        db_engine=db_engine,
    )

    with tempfile.NamedTemporaryFile(suffix='.csv', mode='w', newline='', delete=False) as fp:
        writer = csv.DictWriter(fp, fieldnames=list(result[0].keys()))
        writer.writeheader()
        [writer.writerow(lab) for lab in result]

        return FileResponse(fp.name, background=BackgroundTask(cleanup, fp.name), media_type='application/csv')


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
