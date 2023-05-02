import json
from typing import TYPE_CHECKING
import tempfile
import csv

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from nacsos_data.db.schemas import AcademicItem, Project, ItemType, TwitterItem
from nacsos_data.util.annotations.export import prepare_export_table, LabelSelector

from server.util.security import \
    UserPermissionChecker, \
    InsufficientPermissions

from nacsos_data.util.annotations import export as anno_export
from nacsos_data.util.auth import UserPermissions

from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


@router.get('/annotations/csv', response_class=PlainTextResponse)
async def get_annotations_csv(labels: list[LabelSelector],
                                 bot_annotation_metadata_ids: list[str] | None = Query(default=None),
                                 assignment_scope_ids: list[str] | None = Query(default=None),
                                 user_ids: list[str] | None = Query(default=None),
                                 item_fields: list[str] | None = Query(default=None),
                                 permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> str:
    async with db_engine.session() as session:  # type: AsyncSession
        project = await session.get(Project, permissions.permissions.project_id)
        if project.type == ItemType.academic:
            item_type = AcademicItem
        elif project.type == ItemType.twitter:
            item_type = TwitterItem

    labels = await prepare_export_table(bot_annotation_metadata_ids=bot_annotation_metadata_ids,
                                        assignment_scope_ids=assignment_scope_ids,
                                        user_ids=user_ids,
                                        labels=labels,
                                        item_fields=[AcademicItem.text, AcademicItem.title],
                                        item_type=item_type,
                                        db_engine=db_engine)

    ret = ''
    for l in labels[:10]:
        ret += json.dumps(l) + '\n'
    return ret

    # with tempfile.TemporaryFile() as fp:
    #     pass
