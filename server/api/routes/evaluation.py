import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Body
from nacsos_data.db.crud import upsert_orm
from nacsos_data.db.schemas import (
    AnnotationTracker,
    AssignmentScope,
    AnnotationScheme,
    BotAnnotationMetaData,
    AnnotationQuality
)
from nacsos_data.models.annotation_quality import AnnotationQualityModel
from nacsos_data.models.annotation_tracker import AnnotationTrackerModel, DehydratedAnnotationTracker
from nacsos_data.models.bot_annotations import BotAnnotationMetaDataBaseModel
from nacsos_data.util.annotations.evaluation.buscar import compute_recall, retrospective_h0, recall_frontier
from nacsos_data.util.annotations.evaluation.irr import compute_irr_scores
from nacsos_data.util.annotations.label_transform import annotations_to_sequence, get_annotations
from nacsos_data.util.auth import UserPermissions
from pydantic import BaseModel
from sqlalchemy import select, String, literal, delete

from server.data import db_engine
from server.api.errors import DataNotFoundWarning
from server.util.logging import get_logger
from server.util.security import UserPermissionChecker

from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger('nacsos.api.route.eval')
logger.debug('Setup nacsos.api.route.eval router')

router = APIRouter()


class LabelScope(BaseModel):
    scope_id: str
    name: str
    scope_type: Literal['H', 'R']


@router.get('/tracking/scopes', response_model=list[LabelScope])
async def get_project_scopes(permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[LabelScope]:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (select(AssignmentScope.assignment_scope_id.cast(String).label('scope_id'),
                       AssignmentScope.name,
                       literal('H', type_=String).label('scope_type'))
                .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == AssignmentScope.annotation_scheme_id)
                .where(AnnotationScheme.project_id == permissions.permissions.project_id)
                .order_by(AssignmentScope.time_created))
        rslt = (await session.execute(stmt)).mappings().all()

        assignment_scopes = [LabelScope.model_validate(r) for r in rslt]

        stmt = (select(BotAnnotationMetaData.bot_annotation_metadata_id.cast(String).label('scope_id'),
                       BotAnnotationMetaData.name,
                       literal('R', type_=String).label('scope_type'))
                .where(BotAnnotationMetaData.project_id == permissions.permissions.project_id)
                .order_by(BotAnnotationMetaData.time_created))
        rslt = (await session.execute(stmt)).mappings().all()
        resolution_scopes = [LabelScope.model_validate(r) for r in rslt]

        return assignment_scopes + resolution_scopes


@router.get('/resolutions', response_model=list[BotAnnotationMetaDataBaseModel])
async def get_resolutions_for_scope(assignment_scope_id: str,
                                    permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[BotAnnotationMetaDataBaseModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (select(BotAnnotationMetaData)
                .where(BotAnnotationMetaData.assignment_scope_id == assignment_scope_id))
        rslt = (await session.execute(stmt)).scalars().all()
        return [BotAnnotationMetaDataBaseModel.model_validate(r.__dict__) for r in rslt]


async def read_tracker(session: AsyncSession, tracker_id: str | uuid.UUID,
                       project_id: str | uuid.UUID | None = None) -> AnnotationTracker:
    stmt = (select(AnnotationTracker)
            .where(AnnotationTracker.annotation_tracking_id == tracker_id))
    rslt = (await session.scalars(stmt)).one_or_none()
    if rslt is None:
        raise DataNotFoundWarning(f'No Tracker in project {project_id} for id {tracker_id}!')
    return rslt


@router.get('/tracking/trackers', response_model=list[DehydratedAnnotationTracker])
async def get_project_trackers(permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[DehydratedAnnotationTracker]:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (select(AnnotationTracker.name, AnnotationTracker.annotation_tracking_id)
                .where(AnnotationTracker.project_id == permissions.permissions.project_id))
        rslt = (await session.execute(stmt)).mappings().all()
        return [DehydratedAnnotationTracker.model_validate(r) for r in rslt]


@router.get('/tracking/tracker/{tracker_id}', response_model=AnnotationTrackerModel)
async def get_tracker(tracker_id: str,
                      permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> AnnotationTrackerModel:
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session,
                                     project_id=permissions.permissions.project_id)
        return AnnotationTrackerModel.model_validate(tracker.__dict__)


@router.put('/tracking/tracker', response_model=str)
async def save_tracker(tracker: AnnotationTrackerModel,
                       permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) -> str:
    pkey = await upsert_orm(upsert_model=tracker, Schema=AnnotationTracker,
                            primary_key='annotation_tracking_id', db_engine=db_engine, use_commit=True,
                            skip_update=['labels', 'recall', 'buscar'])
    return str(pkey)


@router.post('/tracking/refresh', response_model=AnnotationTrackerModel)
async def update_tracker(tracker_id: str,
                         background_tasks: BackgroundTasks,
                         reset: bool = Body(default=True, deprecated='Not used anymore, just here for compatibility!'),
                         permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> AnnotationTrackerModel:
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session,
                                     project_id=permissions.permissions.project_id)

        batched_annotations = [await get_annotations(session=session, source_ids=[sid])
                               for sid in tracker.source_ids]

        batched_sequence = [annotations_to_sequence(tracker.inclusion_rule,
                                                    annotations=annotations,
                                                    majority=tracker.majority)
                            for annotations in batched_annotations
                            if len(annotations) > 0]

        # reset scores
        tracker.recall = None
        tracker.buscar = None
        tracker.buscar_frontier = None

        # Update labels
        tracker.labels = batched_sequence

        model = AnnotationTrackerModel.model_validate(tracker.__dict__)
        await session.commit()

        # We are not handing over the existing tracker ORM, because the session is not persistent
        background_tasks.add_task(bg_populate_tracker, tracker_id, batched_sequence)

        return model


async def bg_populate_tracker(tracker_id: str, labels: list[list[int]] | None = None):
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session)

        if labels is None or len(labels) == 0:
            labels = tracker.labels

        if labels is not None:
            flat_labels = [lab for batch in labels for lab in batch]

            recall = compute_recall(labels_=flat_labels)
            tracker.recall = recall

            scores = retrospective_h0(
                labels_=flat_labels,
                n_docs=tracker.n_items_total,
                recall_target=tracker.recall_target,
                bias=tracker.bias,
                batch_size=tracker.batch_size,
                confidence_level=tracker.confidence_level,
            )
            # retrospective_h0() -> tuple[list[int], list[float | None]]
            # tracker.buscar: list[tuple[int, float | None]]

            recall = recall_frontier(labels_=flat_labels, n_docs=tracker.n_items_total, bias=tracker.bias)

            tracker.buscar = list(zip(*scores))
            tracker.buscar_frontier = list(zip(*recall))
            await session.commit()


@router.get('/quality/load/{assignment_scope_id}', response_model=list[AnnotationQualityModel])
async def get_irr(assignment_scope_id: str,
                  permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AnnotationQualityModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        results = (
            await session.execute(select(AnnotationQuality)
                                  .where(AnnotationQuality.assignment_scope_id == assignment_scope_id))
        ).scalars().all()

        return [AnnotationQualityModel(**r.__dict__) for r in results]


@router.get('/quality/compute', response_model=list[AnnotationQualityModel])
async def recompute_irr(assignment_scope_id: str,
                        bot_annotation_metadata_id: str | None = None,
                        permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AnnotationQualityModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        # Delete existing metrics
        await session.execute(delete(AnnotationQuality)
                              .where(AnnotationQuality.assignment_scope_id == assignment_scope_id))
        # Compute new metrics
        metrics = await compute_irr_scores(session=session,
                                           assignment_scope_id=assignment_scope_id,
                                           resolution_id=bot_annotation_metadata_id,
                                           project_id=permissions.permissions.project_id)

        metrics_orm = [AnnotationQuality(**metric.model_dump()) for metric in metrics]
        session.add_all(metrics_orm)
        await session.commit()

    return await get_irr(assignment_scope_id, permissions)
