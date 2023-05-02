import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from nacsos_data.db.schemas import AcademicItem, Project, ItemType, TwitterItem
from nacsos_data.util.annotations.export import prepare_export_table, LabelSelector

from server.util.security import \
    UserPermissionChecker

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

        if project is None:
            raise RuntimeError('This should not happen!')

        if project.type == ItemType.academic:
            item_type = AcademicItem  # type: ignore[assignment]
        elif project.type == ItemType.twitter:
            item_type = TwitterItem  # type: ignore[assignment]

    result = await prepare_export_table(bot_annotation_metadata_ids=bot_annotation_metadata_ids,
                                        assignment_scope_ids=assignment_scope_ids,
                                        user_ids=user_ids,
                                        labels=labels,
                                        item_fields=[AcademicItem.text, AcademicItem.title],  # type: ignore[list-item]
                                        item_type=item_type,  # type: ignore[type-var]
                                        db_engine=db_engine)

    ret = ''
    for lab in result[:10]:
        ret += json.dumps(lab) + '\n'
    return ret

    # with tempfile.TemporaryFile() as fp:
    #     pass
