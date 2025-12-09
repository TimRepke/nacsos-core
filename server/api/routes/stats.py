import datetime
import uuid
from typing import TYPE_CHECKING

from nacsos_data.models.nql import NQLFilter
from nacsos_data.util.nql import NQLQuery
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query, Body
import sqlalchemy as sa

from nacsos_data.db.schemas import (
    Item,
    Import,
    AnnotationScheme,
    AssignmentScope,
    Annotation,
    User,
    Project,
    ItemType,
    AcademicItem,
    TwitterItem,
    LexisNexisItemSource,
    LexisNexisItem,
)
from nacsos_data.util.auth import UserPermissions

from server.api.errors import ProjectNotFoundError
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

logger = get_logger('nacsos.api.route.stats')
router = APIRouter()

logger.info('Setting up projects route')


class BasicProjectStats(BaseModel):
    num_items: int
    num_imports: int
    num_schemes: int
    num_scopes: int
    num_labels: int
    num_labeled_items: int


@router.get('/basics', response_model=BasicProjectStats)
async def get_basic_stats(permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> BasicProjectStats:
    project_id = permissions.permissions.project_id

    async with db_engine.session() as session:  # type: AsyncSession
        num_items: int = await session.scalar(sa.select(sa.func.count(Item.item_id)).where(Item.project_id == project_id)) or 0
        num_imports: int = await session.scalar(sa.select(sa.func.count(Import.import_id)).where(Import.project_id == project_id)) or 0
        num_schemes: int = (
            await session.scalar(sa.select(sa.func.count(AnnotationScheme.annotation_scheme_id)).where(AnnotationScheme.project_id == project_id)) or 0
        )
        num_scopes: int = (
            await session.scalar(
                sa.select(sa.func.count(AssignmentScope.assignment_scope_id))
                .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == AssignmentScope.annotation_scheme_id)
                .where(AnnotationScheme.project_id == project_id)
            )
            or 0
        )
        num_labels: int = (
            await session.scalar(
                sa.select(sa.func.count(Annotation.annotation_id))
                .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id)
                .where(AnnotationScheme.project_id == project_id)
            )
            or 0
        )
        num_labeled_items: int = (
            await session.scalar(
                sa.select(sa.func.count(sa.func.distinct(Annotation.item_id)))
                .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id)
                .where(AnnotationScheme.project_id == project_id)
            )
            or 0
        )

        return BasicProjectStats(
            num_items=num_items,
            num_imports=num_imports,
            num_schemes=num_schemes,
            num_scopes=num_scopes,
            num_labels=num_labels,
            num_labeled_items=num_labeled_items,
        )


class RankEntry(BaseModel):
    user_id: uuid.UUID | str
    username: str
    full_name: str
    email: str
    affiliation: str
    num_labels: int
    num_labeled_items: int


@router.get('/rank', response_model=list[RankEntry])
async def get_annotator_ranking(permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> list[RankEntry]:
    project_id = permissions.permissions.project_id

    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (
            sa.select(
                User.user_id,
                User.username,
                User.full_name,
                User.email,
                User.affiliation,
                sa.func.count(sa.func.distinct(Annotation.annotation_id)).label('num_labels'),
                sa.func.count(sa.func.distinct(Annotation.item_id)).label('num_labeled_items'),
            )
            .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id)
            .join(User, User.user_id == Annotation.user_id)
            .where(AnnotationScheme.project_id == project_id)
            .group_by(User.user_id, User.username, User.full_name, User.email, User.affiliation)
            .order_by(sa.desc('num_labeled_items'))
        )
        result = (await session.execute(stmt)).mappings().all()

        return [RankEntry.model_validate(r) for r in result]


class HistogramEntry(BaseModel):
    bucket: datetime.datetime
    num_items: int


@router.get('/histogram/years', response_model=list[HistogramEntry])
async def get_publication_year_histogram(
    from_year: int = Query(default=1990), to_year: int = Query(default=2025), permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))
) -> list[HistogramEntry]:
    project_id = permissions.permissions.project_id
    from_date = datetime.datetime(year=from_year, month=1, day=1, hour=0, minute=0, second=0)
    to_date = datetime.datetime(year=to_year, month=12, day=31, hour=23, minute=59, second=59)

    async with db_engine.session() as session:  # type: AsyncSession
        project = await session.get(Project, project_id)

        if project is None:
            raise ProjectNotFoundError('This error should never happen.')

        if project.type == ItemType.academic:
            alias = 'itm'
            from_stmt = f'{AcademicItem.__tablename__} itm'
            column = f'make_timestamp(itm.{AcademicItem.publication_year.name},2,2,2,2,2)'
        elif project.type == ItemType.twitter:
            alias = 'itm'
            from_stmt = f'{TwitterItem.__tablename__} itm'
            column = TwitterItem.created_at.name
        elif project.type == ItemType.lexis:
            alias = 'jn'
            from_stmt = f'{LexisNexisItemSource.__tablename__} itm LEFT JOIN {LexisNexisItem.__tablename__} jn ON itm.item_id = jn.item_id'
            column = f'itm.{LexisNexisItemSource.published_at.name}'
        else:
            raise NotImplementedError('Only available for academic, lexisnexis, and twitter projects!')

        stmt = sa.text(f"""
            WITH buckets as (SELECT generate_series(:from_date ::timestamp, :to_date ::timestamp, '1 year') as bucket),
                 items as (SELECT {column} as time_ref, itm.item_id
                          FROM {from_stmt}
                          WHERE {alias}.project_id = :project_id)
                SELECT b.bucket as bucket, count(DISTINCT item_id) as num_items
                FROM buckets b
                         LEFT OUTER JOIN items ON (items.time_ref >= b.bucket AND items.time_ref < (b.bucket + '1 year'::interval))
                GROUP BY b.bucket
                ORDER BY b.bucket;
        """)

        result = (await session.execute(stmt, {'from_date': from_date, 'to_date': to_date, 'project_id': project_id})).mappings().all()
        return [HistogramEntry.model_validate(r) for r in result]


class LabelCount(BaseModel):
    num_items: int
    key: str
    value_bool: bool | None = None
    value_int: int | None = None
    value_float: float | None = None
    value_str: str | None = None
    multi: int | None = None


@router.post('/labels', response_model=list[LabelCount])
async def label_stats(
    query: NQLFilter | None = Body(default=None), permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))
) -> list[LabelCount]:
    async with db_engine.session() as session:  # type: AsyncSession
        nql = await NQLQuery.get_query(session=session, query=query, project_id=str(permissions.permissions.project_id))
        stmt_items = nql.stmt.subquery()

        stmt = (
            sa.select(
                Annotation.key,
                Annotation.value_bool,
                Annotation.value_int,
                Annotation.value_float,
                Annotation.value_str,
                # sa.text('unnest(COALESCE(multi_int, ARRAY[NULL]::integer[]))'),
                sa.func.unnest(sa.func.coalesce(Annotation.multi_int, [None])).label('multi'),
                sa.func.count(sa.distinct(Annotation.item_id)).label('num_items'),
            )
            .join(stmt_items, Annotation.item_id == stmt_items.c.item_id)
            .where(stmt_items.c.item_id.is_not(None), Annotation.item_id.is_not(None))
            .group_by(Annotation.key, Annotation.value_bool, Annotation.value_int, Annotation.value_float, Annotation.value_str, sa.text('multi'))
        )

        rslt = (await session.execute(stmt)).mappings().all()
        return [LabelCount(**r) for r in rslt]
