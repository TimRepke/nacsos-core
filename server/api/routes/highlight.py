from typing import TYPE_CHECKING

from nacsos_data.util.auth import UserPermissions
from sqlalchemy import select
from fastapi import APIRouter, Depends

from nacsos_data.db.schemas.annotations import AssignmentScope
from nacsos_data.db.schemas.highlight import Highlighter
from nacsos_data.models.highlight import HighlighterModel

from server.api.errors import \
    NoDataForKeyError, \
    DataNotFoundWarning
from server.util.security import UserPermissionChecker, InsufficientPermissions
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


@router.get('/scope/{assignment_scope_id}', response_model=HighlighterModel | None)
async def get_scope_highlighter(assignment_scope_id: str,
                                permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> HighlighterModel:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(Highlighter) \
            .join(AssignmentScope,
                  AssignmentScope.highlighter_id == Highlighter.highlighter_id) \
            .where(Highlighter.project_id == permissions.permissions.project_id,
                   AssignmentScope.assignment_scope_id == assignment_scope_id)
        result = (await session.scalars(stmt)).one_or_none()
        if result is not None:
            return HighlighterModel.parse_obj(result.__dict__)
        raise DataNotFoundWarning(f'No highlighter in project {permissions.permissions.project_id} '
                                  f'for scope with id {assignment_scope_id}!')


@router.get('/project', response_model=list[HighlighterModel])
async def get_project_highlighters(permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[HighlighterModel]:
    print('HERE!')
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(Highlighter).where(Highlighter.project_id == permissions.permissions.project_id)
        results = (await session.scalars(stmt)).all()
        return [HighlighterModel.parse_obj(r.__dict__) for r in results]


@router.put('/project', response_model=str)
async def upsert_highlighter(highlighter: HighlighterModel,
                             permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> str:
    if str(permissions.permissions.project_id) != str(highlighter.project_id):
        raise InsufficientPermissions('Project IDs don\'t match!')

    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(Highlighter).where(Highlighter.project_id == permissions.permissions.project_id,
                                         Highlighter.highlighter_id == highlighter.highlighter_id)
        result: Highlighter | None = (await session.scalars(stmt)).one_or_none()

        if result is not None:
            result.name = highlighter.name
            result.style = highlighter.style
            result.keywords = highlighter.keywords
        else:
            new_highlighter = Highlighter(**highlighter.dict())
            session.add(new_highlighter)

        await session.commit()

        return str(highlighter.highlighter_id)


@router.get('/{highlighter_id}', response_model=HighlighterModel | None)
async def get_highlighter(highlighter_id: str,
                          permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> HighlighterModel:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(Highlighter).where(Highlighter.project_id == permissions.permissions.project_id,
                                         Highlighter.highlighter_id == highlighter_id)
        result = (await session.scalars(stmt)).one_or_none()
        if result is not None:
            return HighlighterModel.parse_obj(result.__dict__)
        raise NoDataForKeyError(f'No highlighter in project {permissions.permissions.project_id} '
                                f'with id {highlighter_id}!')
