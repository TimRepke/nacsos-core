import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import select, func as F, distinct, text
from sqlalchemy.orm import load_only
from fastapi import APIRouter, Depends, HTTPException, status as http_status, Query

from nacsos_data.db.schemas import (
    BotAnnotationMetaData,
    AssignmentScope,
    User,
    Annotation,
    BotAnnotation, Assignment
)
from nacsos_data.models.annotations import (
    AnnotationSchemeModel,
    AssignmentScopeModel,
    AssignmentModel,
    AssignmentStatus,
    AssignmentScopeConfig,
    AnnotationSchemeModelFlat
)
from nacsos_data.models.bot_annotations import (
    BotKind,
    BotAnnotationMetaDataBaseModel,
    BotAnnotationResolution,
    ResolutionMatrix,
    BotMetaResolveBase,
    ResolutionProposal
)
from nacsos_data.models.users import UserModel
from nacsos_data.models.items import AnyItemModel
from nacsos_data.db.crud.items import read_any_item_by_item_id
from nacsos_data.db.crud.projects import read_project_by_id
from nacsos_data.db.crud.annotations import (
    read_assignment,
    read_assignments_for_scope,
    read_assignments_for_scope_for_user,
    read_assignment_scopes_for_project,
    read_assignment_scopes_for_project_for_user,
    read_annotations_for_assignment,
    read_next_assignment_for_scope_for_user,
    read_next_open_assignment_for_scope_for_user,
    read_annotation_schemes_for_project,
    upsert_annotations,
    read_assignment_scope,
    upsert_annotation_scheme,
    delete_annotation_scheme,
    upsert_assignment_scope,
    delete_assignment_scope,
    read_item_ids_with_assignment_count_for_project,
    read_assignment_counts_for_scope,
    ItemWithCount,
    AssignmentCounts,
    UserProjectAssignmentScope,
    store_assignments,
    store_resolved_bot_annotations,
    update_resolved_bot_annotations,
    read_assignment_overview_for_scope,
    AssignmentScopeEntry,
    read_resolved_bot_annotations,
    read_resolved_bot_annotation_meta,
    read_resolved_bot_annotations_for_meta
)
from nacsos_data.util.annotations.resolve import (
    get_resolved_item_annotations,
    read_annotation_scheme
)
from nacsos_data.util.annotations.validation import (
    merge_scheme_and_annotations,
    annotated_scheme_to_annotations,
    flatten_annotation_scheme
)
from nacsos_data.util.annotations.assignments.random import random_assignments
from nacsos_data.util.annotations.assignments.random_exclusion import random_assignments_with_exclusion
from nacsos_data.util.annotations.assignments.random_nql import random_assignments_with_nql

from server.api.errors import (
    SaveFailedError,
    AssignmentScopeNotFoundError,
    NoNextAssignmentWarning,
    ProjectNotFoundError,
    AnnotationSchemeNotFoundError,
    MissingInformationError,
    RemainingDependencyWarning
)
from server.util.security import UserPermissionChecker
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

router = APIRouter()


class AnnotatedItem(BaseModel):
    scheme: AnnotationSchemeModel
    assignment: AssignmentModel


class AnnotationItem(AnnotatedItem):
    scope: AssignmentScopeModel
    item: AnyItemModel


@router.get('/schemes/definition/{annotation_scheme_id}',
            response_model=AnnotationSchemeModelFlat | AnnotationSchemeModel)
async def get_scheme_definition(annotation_scheme_id: str,
                                flat: bool = Query(default=False),
                                permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> AnnotationSchemeModelFlat | AnnotationSchemeModel:
    """
    This endpoint returns the detailed definition of an annotation scheme.

    :param annotation_scheme_id: database id of the annotation scheme.
    :param flat: True to get the flattened scheme
    :param permissions:
    :return: a single annotation scheme
    """
    scheme: AnnotationSchemeModel | None = await read_annotation_scheme(annotation_scheme_id=annotation_scheme_id,
                                                                        db_engine=db_engine)
    if scheme is not None:
        if flat:
            return flatten_annotation_scheme(scheme)
        return scheme
    raise AnnotationSchemeNotFoundError(f'No `AnnotationScheme` found in DB for id {annotation_scheme_id}')


@router.put('/schemes/definition/', response_model=str)
async def put_annotation_scheme(annotation_scheme: AnnotationSchemeModel,
                                permissions=Depends(UserPermissionChecker('annotations_edit'))) -> str:
    key = await upsert_annotation_scheme(annotation_scheme=annotation_scheme, db_engine=db_engine)
    return str(key)


@router.delete('/schemes/definition/{scheme_id}')
async def remove_annotation_scheme(annotation_scheme_id: str,
                                   permissions=Depends(UserPermissionChecker('annotations_edit'))) -> None:
    await delete_annotation_scheme(annotation_scheme_id=annotation_scheme_id, db_engine=db_engine, use_commit=True)


@router.get('/schemes/list/{project_id}', response_model=list[AnnotationSchemeModel])
async def get_scheme_definitions_for_project(project_id: str,
                                             permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AnnotationSchemeModel]:
    """
    This endpoint returns the detailed definitions of all annotation schemes associated with a project.

    :param project_id: database id of the project
    :param permissions:
    :return: list of annotation schemes
    """
    return await read_annotation_schemes_for_project(project_id=project_id, db_engine=db_engine)


async def _construct_annotation_item(assignment: AssignmentModel, project_id: str) -> AnnotationItem:
    if assignment.assignment_id is None:
        raise MissingInformationError('No `assignment_id` set for `assignment`.')

    scope = await read_assignment_scope(assignment_scope_id=assignment.assignment_scope_id, db_engine=db_engine)
    if scope is None:
        raise AnnotationSchemeNotFoundError(f'No annotation scope found in DB for id '
                                            f'{assignment.assignment_scope_id}')

    scheme = await read_annotation_scheme(annotation_scheme_id=assignment.annotation_scheme_id, db_engine=db_engine)
    if scheme is None:
        raise AnnotationSchemeNotFoundError(f'No annotation scheme found in DB for id '
                                            f'{assignment.annotation_scheme_id}')

    annotations = await read_annotations_for_assignment(assignment_id=assignment.assignment_id, db_engine=db_engine)
    merged_scheme = merge_scheme_and_annotations(annotation_scheme=scheme, annotations=annotations)

    project = await read_project_by_id(project_id=project_id, engine=db_engine)
    if project is None:
        raise ProjectNotFoundError(f'No project found in DB for id {project_id}')

    item = await read_any_item_by_item_id(item_id=assignment.item_id, item_type=project.type, engine=db_engine)
    if item is None:
        raise MissingInformationError(f'No item found in DB for id {assignment.item_id}')

    return AnnotationItem(scheme=merged_scheme, assignment=assignment, scope=scope, item=item)


@router.get('/annotate/next/{assignment_scope_id}/{current_assignment_id}', response_model=AnnotationItem)
async def get_next_assignment_for_scope_for_user(assignment_scope_id: str,
                                                 current_assignment_id: str,
                                                 permissions=Depends(UserPermissionChecker('annotations_read'))):
    # FIXME response for "last in list"
    assignment = await read_next_assignment_for_scope_for_user(current_assignment_id=current_assignment_id,
                                                               assignment_scope_id=assignment_scope_id,
                                                               user_id=permissions.user.user_id,
                                                               db_engine=db_engine)
    if assignment is None:
        raise NoNextAssignmentWarning(f'Could not determine a next assignment for scope {assignment_scope_id}')
    return await _construct_annotation_item(assignment=assignment, project_id=permissions.permissions.project_id)


class NoAssignments(Warning):
    pass


@router.get('/annotate/next/{assignment_scope_id}', response_model=AnnotationItem)
async def get_next_open_assignment_for_scope_for_user(assignment_scope_id: str,
                                                      permissions=Depends(UserPermissionChecker('annotations_read'))):
    assignment = await read_next_open_assignment_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                                    user_id=permissions.user.user_id,
                                                                    db_engine=db_engine)
    # Either there are no assignments, or everything is done.
    if assignment is None:
        assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                                user_id=permissions.user.user_id,
                                                                db_engine=db_engine, limit=1)
        if len(assignments) > 0:
            assignment = assignments[0]
        else:
            raise NoAssignments('This user has no assignments in this scope.')

    return await _construct_annotation_item(assignment=assignment, project_id=permissions.permissions.project_id)


@router.get('/annotate/assignment/{assignment_id}', response_model=AnnotationItem)
async def get_assignment(assignment_id: str,
                         permissions=Depends(UserPermissionChecker('annotations_read'))):
    assignment = await read_assignment(assignment_id=assignment_id, db_engine=db_engine)

    if (assignment is None) or (assignment.user_id != permissions.user.user_id):
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED,
                            detail='You do not have permission to handle this assignment, as it is not yours!')

    return await _construct_annotation_item(assignment=assignment, project_id=permissions.permissions.project_id)


@router.get('/annotate/scopes/{project_id}', response_model=list[UserProjectAssignmentScope])
async def get_assignment_scopes_for_user(project_id: str,
                                         permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[UserProjectAssignmentScope]:
    scopes = await read_assignment_scopes_for_project_for_user(project_id=project_id,
                                                               user_id=permissions.user.user_id,
                                                               db_engine=db_engine)
    return scopes


@router.get('/annotate/scopes/', response_model=list[AssignmentScopeModel])
async def get_assignment_scopes_for_project(permissions=Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[AssignmentScopeModel]:
    scopes = await read_assignment_scopes_for_project(project_id=permissions.permissions.project_id,
                                                      db_engine=db_engine)

    return scopes


@router.get('/annotate/scope/{assignment_scope_id}', response_model=AssignmentScopeModel)
async def get_assignment_scope(assignment_scope_id: str,
                               permissions=Depends(
                                   UserPermissionChecker(['annotations_read', 'annotations_edit'], fulfill_all=False))
                               ) -> AssignmentScopeModel:
    scope = await read_assignment_scope(assignment_scope_id=assignment_scope_id, db_engine=db_engine)
    if scope is not None:
        return scope
    raise AssignmentScopeNotFoundError(f'No assignment scope found in the DB for {assignment_scope_id}')


@router.put('/annotate/scope/', response_model=str)
async def put_assignment_scope(assignment_scope: AssignmentScopeModel,
                               permissions=Depends(UserPermissionChecker('annotations_edit'))) -> str:
    key = await upsert_assignment_scope(assignment_scope=assignment_scope, db_engine=db_engine)
    return str(key)


@router.delete('/annotate/scope/{assignment_scope_id}')
async def remove_assignment_scope(assignment_scope_id: str,
                                  permissions=Depends(UserPermissionChecker('annotations_edit'))) -> None:
    try:
        await delete_assignment_scope(assignment_scope_id=assignment_scope_id, db_engine=db_engine, use_commit=True)
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                            detail=str(e))


@router.get('/annotate/scope/counts/{assignment_scope_id}', response_model=AssignmentCounts)
async def get_num_assignments_for_scope(assignment_scope_id: str,
                                        permissions=Depends(
                                            UserPermissionChecker(['annotations_read', 'annotations_edit'],
                                                                  fulfill_all=False))
                                        ) -> AssignmentCounts:
    scope = await read_assignment_counts_for_scope(assignment_scope_id=assignment_scope_id, db_engine=db_engine)
    return scope


@router.get('/annotate/assignments/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_assignments(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            db_engine=db_engine)
    return assignments


@router.get('/annotate/assignment/progress/{assignment_scope_id}', response_model=list[AssignmentScopeEntry])
async def get_assignment_indicators_for_scope(assignment_scope_id: str,
                                              permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentScopeEntry]:
    return await read_assignment_overview_for_scope(assignment_scope_id=assignment_scope_id,
                                                    db_engine=db_engine)


@router.get('/annotate/assignments/scope/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_assignments_for_scope(assignment_scope_id: str,
                                    permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope(assignment_scope_id=assignment_scope_id,
                                                   db_engine=db_engine)
    return assignments


@router.get('/annotate/annotations/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_annotations(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            db_engine=db_engine)
    return assignments


@router.post('/annotate/save', response_model=AssignmentStatus)
async def save_annotation(annotated_item: AnnotatedItem,
                          permissions=Depends(UserPermissionChecker('annotations_read'))) -> AssignmentStatus:
    # double-check, that the supposed assignment actually exists
    if annotated_item.assignment.assignment_id is None:
        raise MissingInformationError('Missing `assignment_id` in `annotation_item`!')

    assignment_db = await read_assignment(assignment_id=annotated_item.assignment.assignment_id, db_engine=db_engine)

    if assignment_db is None:
        raise MissingInformationError('No assignment found!')

    if permissions.user.user_id == assignment_db.user_id \
            and str(assignment_db.assignment_scope_id) == annotated_item.assignment.assignment_scope_id \
            and str(assignment_db.item_id) == annotated_item.assignment.item_id \
            and str(assignment_db.annotation_scheme_id) == annotated_item.assignment.annotation_scheme_id:
        annotations = annotated_scheme_to_annotations(annotated_item.scheme)
        status = await upsert_annotations(annotations=annotations,
                                          assignment_id=annotated_item.assignment.assignment_id,
                                          db_engine=db_engine)
        if status is not None:
            return status
        raise SaveFailedError('Failed to save annotation!')
    else:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail='The combination of project, assignment, user, task, and item is invalid.',
        )


@router.get('/config/items/', response_model=list[ItemWithCount])
async def get_items_with_count(permissions=Depends(UserPermissionChecker('dataset_read'))) \
        -> list[ItemWithCount]:
    items = await read_item_ids_with_assignment_count_for_project(project_id=permissions.permissions.project_id,
                                                                  db_engine=db_engine)
    return items


class MakeAssignmentsRequestModel(BaseModel):
    annotation_scheme_id: str
    scope_id: str
    config: AssignmentScopeConfig
    save: bool = False


@router.post('/config/assignments/', response_model=list[AssignmentModel])
async def make_assignments(payload: MakeAssignmentsRequestModel,
                           permissions=Depends(UserPermissionChecker('annotations_edit'))):
    if payload.config.config_type == 'random':
        try:
            assignments = await random_assignments(assignment_scope_id=payload.scope_id,
                                                   annotation_scheme_id=payload.annotation_scheme_id,
                                                   project_id=permissions.permissions.project_id,
                                                   config=payload.config,
                                                   engine=db_engine)
        except ValueError as e:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                                detail=str(e))
    elif payload.config.config_type == 'random_exclusion':
        try:
            assignments = await random_assignments_with_exclusion(
                assignment_scope_id=payload.scope_id,
                annotation_scheme_id=payload.annotation_scheme_id,
                project_id=permissions.permissions.project_id,
                config=payload.config,  # type: ignore[arg-type] # FIXME
                engine=db_engine)
        except ValueError as e:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                                detail=str(e))
    elif payload.config.config_type == 'random_nql':
        try:
            assignments = await random_assignments_with_nql(
                assignment_scope_id=payload.scope_id,
                annotation_scheme_id=payload.annotation_scheme_id,
                project_id=permissions.permissions.project_id,
                config=payload.config,  # type: ignore[arg-type] # FIXME
                engine=db_engine
            )
        except ValueError as e:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                                detail=str(e))
    else:
        raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                            detail=f'Method "{payload.config.config_type}" is unknown.')

    if payload.save:
        await store_assignments(assignments=assignments, db_engine=db_engine, use_commit=True)

    return assignments


@router.post('/config/scopes/clear/{scheme_id}')
async def clear_empty_assignments(scope_id: str,
                                  permissions=Depends(UserPermissionChecker('annotations_edit'))) -> None:
    """
    Drop all assignments in a scope that are still incomplete...

    :param scope_id:
    :param permissions:
    :return:
    """
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = text('''
            DELETE
            FROM assignment
            WHERE assignment_id IN (
                WITH counts AS (
                    SELECT ass.assignment_id, count(ann.assignment_id) as cnt
                    FROM assignment ass
                        LEFT OUTER JOIN annotation ann ON ass.assignment_id = ann.assignment_id
                    WHERE ass.assignment_scope_id = :scope_id
                    GROUP BY ass.assignment_id
                )
                SELECT assignment_id
                FROM counts
                WHERE cnt = 0
        );''')
        await session.execute(stmt, {'scope_id': scope_id})
        await session.commit()


class AssignmentEditInfo(BaseModel):
    scope_id: str
    scheme_id: str
    item_id: str
    user_id: str
    order: int


@router.put('/config/assignments/edit/', response_model=AssignmentModel)
async def edit_assignment(info: AssignmentEditInfo,
                          permissions=Depends(UserPermissionChecker('annotations_edit'))) -> AssignmentModel:
    async with db_engine.session() as session:  # type: AsyncSession

        # Check, if we already have an assignment for this...
        assignment = (await session.execute(
            select(Assignment)
            .where(
                Assignment.item_id == info.item_id,
                Assignment.user_id == info.user_id,
                Assignment.assignment_scope_id == info.scope_id
            ))).scalars().one_or_none()
        n_annotations: int = (await session.execute(  # type: ignore[assignment]
            select(F.count(Annotation.annotation_id).label('n_annotations'))
            .join(Assignment)
            .where(Assignment.item_id == info.item_id,
                   Assignment.user_id == info.user_id,
                   Assignment.assignment_scope_id == info.scope_id))).scalar()

        # yes we do, drop this assignment!
        if assignment is not None:
            model = AssignmentModel.model_validate(assignment.__dict__)
            if n_annotations == 0:
                await session.delete(assignment)
                return model

            raise RemainingDependencyWarning('Assignment has annotations, won\'t delete!')

        # seems to be a new one, create it!
        assignment = Assignment(
            assignment_id=uuid.uuid4(),
            item_id=info.item_id,
            user_id=info.user_id,
            assignment_scope_id=info.scope_id,
            annotation_scheme_id=info.scheme_id,
            order=info.order,
            status=AssignmentStatus.OPEN
        )
        session.add(assignment)
        model = AssignmentModel.model_validate(assignment.__dict__)
        await session.commit()
        return model


@router.get('/config/scopes/{scheme_id}', response_model=list[AssignmentScopeModel])
async def get_assignment_scopes_for_scheme(scheme_id: str,
                                           permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentScopeModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        return [AssignmentScopeModel.model_validate(scope.__dict__)
                for scope in (await session.execute(select(AssignmentScope)
                                                    .where(AssignmentScope.annotation_scheme_id == scheme_id))
                              ).scalars().all()]


@router.get('/config/annotators/{scheme_id}', response_model=list[UserModel])
async def get_annotators_for_scheme(scheme_id: str,
                                    permissions=Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserModel]:
    async with db_engine.session() as session:  # type: AsyncSession
        return [UserModel.model_validate(user.__dict__)
                for user in (
                    await session.execute(select(User)
                                          .join(Annotation)
                                          .distinct()
                                          .where(Annotation.annotation_scheme_id == scheme_id))).scalars().all()]


@router.post('/config/resolve', response_model=ResolutionProposal)
async def get_resolved_annotations(settings: BotMetaResolveBase,
                                   assignment_scope_id: str | None = None,
                                   bot_annotation_metadat_id: str | None = None,
                                   include_empty: bool = False,
                                   include_new: bool = False,
                                   update_existing: bool = False,
                                   permissions=Depends(UserPermissionChecker('annotations_edit'))) \
        -> ResolutionProposal:
    """
    Get all annotations that match the filters (e.g. all annotations made by users in scope with :scope_id).

    :param include_new:
    :param update_existing:
    :param assignment_scope_id:
    :param bot_annotation_metadat_id:
    :param include_empty:
    :param settings
    :param permissions:
    :return:
    """
    if include_empty is None:
        include_empty = True  # type: ignore[unreachable]
    if include_new is None:
        include_new = False  # type: ignore[unreachable]
    if update_existing is None:
        update_existing = False  # type: ignore[unreachable]

    if bot_annotation_metadat_id is not None:
        return await read_resolved_bot_annotations(db_engine=db_engine,
                                                   existing_resolution=bot_annotation_metadat_id,
                                                   include_new=include_new,
                                                   include_empty=include_empty,
                                                   update_existing=update_existing)
    if assignment_scope_id is None:
        raise ValueError('Missing assignment scope')
    return await get_resolved_item_annotations(strategy=settings.algorithm,
                                               assignment_scope_id=assignment_scope_id,
                                               ignore_repeat=settings.ignore_repeat,
                                               ignore_hierarchy=settings.ignore_hierarchy,
                                               include_new=include_new,
                                               include_empty=include_empty,
                                               update_existing=update_existing,
                                               db_engine=db_engine)


class SavedResolution(BaseModel):
    meta: BotAnnotationResolution
    proposal: ResolutionProposal


@router.get('/config/resolved/{bot_annotation_metadata_id}', response_model=SavedResolution)
async def get_saved_resolved_annotations(bot_annotation_metadata_id: str,
                                         permissions=Depends(UserPermissionChecker('annotations_edit'))) \
        -> SavedResolution:
    async with db_engine.session() as session:  # type: AsyncSession
        bot_meta = await read_resolved_bot_annotation_meta(bot_annotation_metadata_id=bot_annotation_metadata_id,
                                                           session=session)
        proposal = await read_resolved_bot_annotations_for_meta(session=session,
                                                                bot_meta=bot_meta,
                                                                include_new=False,
                                                                include_empty=False,
                                                                update_existing=False)
        return SavedResolution(meta=bot_meta, proposal=proposal)


@router.put('/config/resolve/', response_model=str)
async def save_resolved_annotations(settings: BotMetaResolveBase,
                                    matrix: ResolutionMatrix,
                                    name: str,
                                    assignment_scope_id: str,
                                    annotation_scheme_id: str,
                                    permissions=Depends(UserPermissionChecker('annotations_edit'))):
    meta_id = await store_resolved_bot_annotations(db_engine=db_engine, use_commit=True,
                                                   project_id=permissions.permissions.project_id,
                                                   assignment_scope_id=assignment_scope_id,
                                                   annotation_scheme_id=annotation_scheme_id,
                                                   name=name,
                                                   algorithm=settings.algorithm,
                                                   ignore_hierarchy=settings.ignore_hierarchy,
                                                   ignore_repeat=settings.ignore_repeat,
                                                   matrix=matrix)
    return meta_id


@router.put('/config/resolve/update')
async def update_resolved_annotations(bot_annotation_metadata_id: str,
                                      name: str,
                                      matrix: ResolutionMatrix,
                                      permissions=Depends(UserPermissionChecker('annotations_edit'))) -> None:
    # TODO: allow update of filters and settings?
    await update_resolved_bot_annotations(bot_annotation_metadata_id=bot_annotation_metadata_id,
                                          name=name, matrix=matrix, db_engine=db_engine, use_commit=True)


@router.get('/config/resolved-list/', response_model=list[BotAnnotationMetaDataBaseModel])
async def list_saved_resolved_annotations(annotation_scheme_id: str | None = None,
                                          permissions=Depends(UserPermissionChecker('annotations_read'))):
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (
            select(BotAnnotationMetaData)
            .where(BotAnnotationMetaData.project_id == permissions.permissions.project_id,
                   BotAnnotationMetaData.kind == BotKind.RESOLVE)
            .order_by(BotAnnotationMetaData.time_created)
            .options(load_only(BotAnnotationMetaData.bot_annotation_metadata_id,
                               BotAnnotationMetaData.annotation_scheme_id,
                               BotAnnotationMetaData.assignment_scope_id,
                               BotAnnotationMetaData.project_id,
                               BotAnnotationMetaData.name,
                               BotAnnotationMetaData.kind,
                               BotAnnotationMetaData.time_updated,
                               BotAnnotationMetaData.time_created))
        )
        if annotation_scheme_id is not None:
            stmt = stmt.where(BotAnnotationMetaData.annotation_scheme_id == annotation_scheme_id)
        exports = (await session.execute(stmt)).scalars().all()
        return [BotAnnotationMetaDataBaseModel.model_validate(e.__dict__) for e in exports]


@router.delete('/config/resolved/{bot_annotation_meta_id}')
async def delete_saved_resolved_annotations(bot_annotation_metadata_id: str,
                                            permissions=Depends(UserPermissionChecker('annotations_edit'))):
    async with db_engine.session() as session:  # type: AsyncSession
        meta: BotAnnotationMetaData | None = (await session.execute(
            select(BotAnnotationMetaData)
            .where(BotAnnotationMetaData.bot_annotation_metadata_id == bot_annotation_metadata_id))) \
            .scalars().one_or_none()
        if meta is not None:
            await session.delete(meta)
            await session.commit()
        # TODO: do we need to commit?
        # TODO: ensure bot_annotations are deleted via cascade


class BotMetaInfo(BotAnnotationMetaDataBaseModel):
    num_annotations: int
    num_annotated_items: int


@router.get('/bot/annotations')
async def get_bot_annotations(include_resolve: bool = False,
                              permissions=Depends(UserPermissionChecker('annotations_read'))) -> list[BotMetaInfo]:
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = (select(BotAnnotationMetaData,
                       F.count(BotAnnotation.bot_annotation_id).label('num_annotations'),
                       F.count(distinct(BotAnnotation.item_id)).label('num_annotated_items'))
                .join(BotAnnotation,
                      BotAnnotation.bot_annotation_metadata_id == BotAnnotationMetaData.bot_annotation_metadata_id)
                # TODO: filter for != RESOLVE
                .where(BotAnnotationMetaData.project_id == permissions.permissions.project_id)
                .group_by(BotAnnotationMetaData.bot_annotation_metadata_id))
        rslt = (await session.execute(stmt)).mappings().all()
        if rslt:
            return [BotMetaInfo.model_validate({
                **r['BotAnnotationMetaData'].__dict__,
                'num_annotations': r['num_annotations'],
                'num_annotated_items': r['num_annotated_items']
            }) for r in rslt]
        return []
