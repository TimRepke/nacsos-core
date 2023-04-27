import datetime
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func as F, desc, text

from nacsos_data.db.schemas import Item, Import, AnnotationScheme, AssignmentScope, Annotation, User, Project, ItemType, \
    AcademicItem, TwitterItem
from nacsos_data.util.auth import UserPermissions

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
async def get_basic_stats(
        permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> BasicProjectStats:
    project_id = permissions.permissions.project_id

    async with db_engine.session() as session:  # type: AsyncSession
        num_items: int = await session.scalar(select(F.count(Item.item_id))
                                              .where(Item.project_id == project_id))
        num_imports: int = await session.scalar(select(F.count(Import.import_id))
                                                .where(Import.project_id == project_id))
        num_schemes: int = await session.scalar(select(F.count(AnnotationScheme.annotation_scheme_id))
                                                .where(AnnotationScheme.project_id == project_id))
        num_scopes: int = await session.scalar(select(F.count(AssignmentScope.assignment_scope_id))
                                               .join(AnnotationScheme,
                                                     AnnotationScheme.annotation_scheme_id == AssignmentScope.annotation_scheme_id)
                                               .where(AnnotationScheme.project_id == project_id))
        num_labels: int = await session.scalar(select(F.count(Annotation.annotation_id))
                                               .join(AnnotationScheme,
                                                     AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id)
                                               .where(AnnotationScheme.project_id == project_id))
        num_labeled_items: int = await session.scalar(select(F.count(F.distinct(Annotation.item_id)))
                                                      .join(AnnotationScheme,
                                                            AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id)
                                                      .where(AnnotationScheme.project_id == project_id))

        return BasicProjectStats(
            num_items=num_items,
            num_imports=num_imports,
            num_schemes=num_schemes,
            num_scopes=num_scopes,
            num_labels=num_labels,
            num_labeled_items=num_labeled_items
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
async def get_annotator_ranking(permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) \
        -> list[RankEntry]:
    project_id = permissions.permissions.project_id

    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(User.user_id,
                      User.username,
                      User.full_name,
                      User.email,
                      User.affiliation,
                      F.count(F.distinct(Annotation.annotation_id)).label('num_labels'),
                      F.count(F.distinct(Annotation.item_id)).label('num_labeled_items')) \
            .join(AnnotationScheme,
                  AnnotationScheme.annotation_scheme_id == Annotation.annotation_scheme_id) \
            .join(User, User.user_id == Annotation.user_id) \
            .where(AnnotationScheme.project_id == project_id) \
            .group_by(User) \
            .order_by(desc('num_labeled_items'))
        result = (await session.execute(stmt)).mappings().all()

        return [RankEntry.parse_obj(r) for r in result]


class HistogramEntry(BaseModel):
    bucket: datetime.datetime
    num_items: int


@router.get('/histogram/years', response_model=list[HistogramEntry])
async def get_publication_year_histogram(
        from_year: int = Query(default=1990),
        to_year: int = Query(default=2023),
        permissions: UserPermissions = Depends(UserPermissionChecker('dataset_read'))) -> list[HistogramEntry]:
    project_id = permissions.permissions.project_id
    from_date = datetime.datetime(year=from_year, month=1, day=1, hour=0, minute=0, second=0)
    to_date = datetime.datetime(year=to_year, month=12, day=31, hour=23, minute=59, second=59)

    async with db_engine.session() as session:  # type: AsyncSession
        project = await session.get(Project, project_id)

        if project.type == ItemType.academic:
            table = AcademicItem.__tablename__
            column = f'make_timestamp({AcademicItem.publication_year.name},2,2,2,2,2)'
        elif project.type == ItemType.twitter:
            table = TwitterItem.__tablename__
            column = TwitterItem.created_at.name
        else:
            raise NotImplementedError('Only available for academic and twitter projects!')

        stmt = text(f'''
            WITH buckets as (SELECT generate_series(:from_date ::timestamp, :to_date ::timestamp, '1 year') as bucket),
                 items as (SELECT {column} as time_ref, item_id
                          FROM {table}
                          WHERE {table}.project_id = :project_id)
                SELECT b.bucket as bucket, count(DISTINCT item_id) as num_items
                FROM buckets b
                         LEFT OUTER JOIN items ON (items.time_ref >= b.bucket AND items.time_ref < (b.bucket + '1 year'::interval))
                GROUP BY b.bucket
                ORDER BY b.bucket;
        ''')

        result = (await session.execute(stmt, {
            'from_date': from_date,
            'to_date': to_date,
            'project_id': project_id
        })).mappings().all()
        return [HistogramEntry.parse_obj(r) for r in result]
