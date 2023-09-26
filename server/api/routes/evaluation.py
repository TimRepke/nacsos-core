import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from nacsos_data.db.crud import upsert_orm
from nacsos_data.db.schemas import AnnotationTracker
from nacsos_data.models.annotation_tracker import AnnotationTrackerModel
from nacsos_data.util.annotations.evaluation import get_new_label_batches
from nacsos_data.util.annotations.evaluation.buscar import (
    calculate_h0s_for_batches,
    compute_recall,
    calculate_h0s)
from nacsos_data.util.annotations.evaluation.label_transform import annotations_to_sequence, get_annotations
from nacsos_data.util.auth import UserPermissions
from sqlalchemy import select

from server.data import db_engine
from server.api.errors import DataNotFoundWarning
from server.util.logging import get_logger
from server.util.security import UserPermissionChecker

from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger('nacsos.api.route.eval')
logger.debug('Setup nacsos.api.route.eval router')

router = APIRouter()


async def read_tracker(session: AsyncSession, tracker_id: str | uuid.UUID,
                       project_id: str | uuid.UUID | None = None) -> AnnotationTracker:
    stmt = (select(AnnotationTracker)
            .where(AnnotationTracker.annotation_tracking_id == tracker_id))
    rslt = (await session.scalars(stmt)).one_or_none()
    if rslt is None:
        raise DataNotFoundWarning(f'No Tracker in project {project_id} for id {tracker_id}!')
    return rslt


@router.get('/tracking/tracker/{tracker_id}', response_model=AnnotationTrackerModel)
async def get_tracker(tracker_id: str,
                      permissions: UserPermissions = Depends(UserPermissionChecker('annotations_read'))) \
        -> AnnotationTrackerModel:
    async with db_engine.session() as session:  # type: AsyncSession
        return AnnotationTrackerModel.model_validate(read_tracker(tracker_id=tracker_id, session=session,
                                                                  project_id=permissions.permissions.project_id))


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
                            for annotations in batched_annotations]

        diff: list[list[int]] | None = None
        if reset:
            tracker.buscar = None
            tracker.recall = None
        elif tracker.labels is not None:
            diff = get_new_label_batches(tracker.labels, batched_sequence)

        # Update labels
        tracker.labels = batched_sequence
        await session.commit()

        # We are not handing over the existing tracker ORM, because the session is not persistent
        background_tasks.add_task(bg_populate_tracker, tracker_id, batch_size, diff)

        return AnnotationTrackerModel.model_validate(tracker)


async def bg_populate_tracker(tracker_id: str, batch_size: int | None = None, labels: list[list[int]] | None = None):
    async with db_engine.session() as session:  # type: AsyncSession
        tracker = await read_tracker(tracker_id=tracker_id, session=session)

        if labels is None:
            labels = tracker.labels

        flat_labels = [lab for batch in labels for lab in batch]

        recall = compute_recall(labels_=flat_labels)
        if tracker.recall is None:
            tracker.recall = recall
        else:
            tracker.recall += recall

        await session.commit()

        # Initialise buscar scores
        if tracker.buscar is None:
            tracker.buscar = []

        if batch_size is None:
            # Use scopes as batches
            it = calculate_h0s_for_batches(labels_=tracker.labels,
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
            await session.commit()
