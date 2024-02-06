import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from nacsos_data.db.crud import upsert_orm
from nacsos_data.db.schemas import AnnotationTracker, AssignmentScope, AnnotationScheme, BotAnnotationMetaData, \
    AnnotationQuality
from nacsos_data.models.annotation_quality import AnnotationQualityModel
from nacsos_data.models.annotation_tracker import AnnotationTrackerModel, DehydratedAnnotationTracker
from nacsos_data.util.annotations.evaluation import get_new_label_batches
from nacsos_data.util.annotations.evaluation.buscar import (
    calculate_h0s_for_batches,
    compute_recall,
    calculate_h0s)
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
                            primary_key='annotation_tracking_id', db_engine=db_engine,
                            skip_update=['labels', 'recall', 'buscar'])
    return str(pkey)


@router.post('/tracking/refresh', response_model=AnnotationTrackerModel)
async def update_tracker(tracker_id: str,
                         background_tasks: BackgroundTasks,
                         batch_size: int | None = None,
                         reset: bool = False,
                         permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> AnnotationTrackerModel:
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session,
                                     project_id=permissions.permissions.project_id)

        batched_annotations = [await get_annotations(session=session, source_ids=[sid])
                               for sid in tracker.source_ids]

        batched_sequence = [annotations_to_sequence(tracker.inclusion_rule, annotations=annotations,
                                                    majority=tracker.majority)
                            for annotations in batched_annotations
                            if len(annotations) > 0]

        diff: list[list[int]] | None = None
        if reset:
            tracker.buscar = None
            tracker.recall = None
        elif tracker.labels is not None:
            diff = get_new_label_batches(tracker.labels, batched_sequence)

        # Update labels
        tracker.labels = batched_sequence
        await session.flush()

        # We are not handing over the existing tracker ORM, because the session is not persistent
        background_tasks.add_task(bg_populate_tracker, tracker_id, batch_size, diff)

        return AnnotationTrackerModel.model_validate(tracker.__dict__)


async def bg_populate_tracker(tracker_id: str, batch_size: int | None = None, labels: list[list[int]] | None = None):
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session)

        if labels is None or len(labels) == 0:
            labels = tracker.labels

        if labels is not None:
            flat_labels = [lab for batch in labels for lab in batch]

            recall = compute_recall(labels_=flat_labels)
            if tracker.recall is None:
                tracker.recall = recall
            else:
                tracker.recall += recall

            await session.flush()

            # Initialise buscar scores
            if tracker.buscar is None:
                tracker.buscar = []

            if batch_size is None:
                # Use scopes as batches
                it = calculate_h0s_for_batches(labels=labels,
                                               recall_target=tracker.recall_target,
                                               n_docs=tracker.n_items_total)
            else:
                # Ignore the batches derived from scopes and use fixed step sizes
                it = calculate_h0s(labels_=flat_labels,
                                   batch_size=batch_size,
                                   recall_target=tracker.recall_target,
                                   n_docs=tracker.n_items_total)

            for x, y in it:
                tracker.buscar = tracker.buscar + [(x, y)]
                # save after each step, so the user can refresh the page and get data as it becomes available
                await session.flush()


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


@router.get('/quality/compute/{assignment_scope_id}', response_model=list[AnnotationQualityModel])
async def recompute_irr(assignment_scope_id: str,
                        permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AnnotationQualityModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        # Delete existing metrics
        await session.execute(delete(AnnotationQuality)
                              .where(AnnotationQuality.assignment_scope_id == assignment_scope_id))
        # Compute new metrics
        metrics = await compute_irr_scores(session=session,
                                           assignment_scope_id=assignment_scope_id,
                                           project_id=permissions.permissions.project_id)

        metrics_orm = [AnnotationQuality(**metric.model_dump()) for metric in metrics]
        session.add_all(metrics_orm)
        await session.commit()

    return await get_irr(assignment_scope_id, permissions)
