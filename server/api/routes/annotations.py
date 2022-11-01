from fastapi import APIRouter, Depends, HTTPException, status as http_status, Query
from nacsos_data.db.schemas import BotAnnotationMetaData, BotAnnotation
from nacsos_data.models.annotations import \
    AnnotationSchemeModel, \
    AssignmentScopeModel, \
    AssignmentModel, \
    AssignmentStatus, \
    AssignmentScopeConfig, \
    AnnotationSchemeModelFlat
from nacsos_data.models.bot_annotations import \
    ResolutionMethod, \
    AnnotationFilters, \
    ResolvedAnnotations, \
    BotAnnotationModel, \
    AnnotationMatrix, \
    BotMetaResolve
from nacsos_data.models.items import AnyItemModel
from nacsos_data.db.crud.items import read_any_item_by_item_id
from nacsos_data.db.crud.projects import read_project_by_id
from nacsos_data.db.crud.annotations import \
    read_assignment, \
    read_assignments_for_scope, \
    read_assignments_for_scope_for_user, \
    read_assignment_scopes_for_project, \
    read_assignment_scopes_for_project_for_user, \
    read_annotations_for_assignment, \
    read_next_assignment_for_scope_for_user, \
    read_next_open_assignment_for_scope_for_user, \
    read_annotation_scheme, \
    read_annotation_schemes_for_project, \
    upsert_annotations, \
    read_assignment_scope, \
    upsert_annotation_scheme, \
    delete_annotation_scheme, \
    upsert_assignment_scope, \
    delete_assignment_scope, \
    read_item_ids_with_assignment_count_for_project, \
    read_assignment_counts_for_scope, \
    ItemWithCount, \
    AssignmentCounts, \
    UserProjectAssignmentScope, \
    store_assignments
from nacsos_data.util.annotations.resolve import \
    AnnotationFilterObject, \
    get_resolved_item_annotations
from nacsos_data.util.annotations.validation import \
    merge_scheme_and_annotations, \
    annotated_scheme_to_annotations, \
    flatten_annotation_scheme
from nacsos_data.util.annotations.assignments.random import random_assignments

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.errors import SaveFailedError, AssignmentScopeNotFoundError, NoNextAssignmentWarning, \
    ProjectNotFoundError, AnnotationSchemeNotFoundError, MissingInformationError, NoDataForKeyError
from server.util.security import UserPermissionChecker
from server.data import db_engine

router = APIRouter()


class AnnotatedItem(BaseModel):
    scheme: AnnotationSchemeModel
    assignment: AssignmentModel


class AnnotationItem(AnnotatedItem):
    scope: AssignmentScopeModel
    item: AnyItemModel


@router.get('/schemes/definition/{annotation_scheme_id}',
            response_model=AnnotationSchemeModel | AnnotationSchemeModelFlat)
async def get_scheme_definition(annotation_scheme_id: str,
                                flat: bool = Query(default=False)) -> AnnotationSchemeModel | AnnotationSchemeModelFlat:
    """
    This endpoint returns the detailed definition of an annotation scheme.

    :param annotation_scheme_id: database id of the annotation scheme.
    :param flat: True to get the flattened scheme
    :return: a single annotation scheme
    """
    scheme = await read_annotation_scheme(annotation_scheme_id=annotation_scheme_id, db_engine=db_engine)
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
    await delete_annotation_scheme(annotation_scheme_id=annotation_scheme_id, db_engine=db_engine)


@router.get('/schemes/list/{project_id}', response_model=list[AnnotationSchemeModel])
async def get_scheme_definitions_for_project(project_id: str) -> list[AnnotationSchemeModel]:
    """
    This endpoint returns the detailed definitions of all annotation schemes associated with a project.

    :param project_id: database id of the project
    :return: list of annotation schemes
    """
    return await read_annotation_schemes_for_project(project_id=project_id, db_engine=db_engine)


async def _construct_annotation_item(assignment: AssignmentModel, project_id: str) -> AnnotationItem:
    if assignment.assignment_id is None:
        raise MissingInformationError('No `assignment_id` set for `assignment`.')
    scope = await read_assignment_scope(assignment_scope_id=assignment.assignment_scope_id, db_engine=db_engine)
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


@router.get('/annotate/next/{assignment_scope_id}', response_model=AnnotationItem)
async def get_next_open_assignment_for_scope_for_user(assignment_scope_id: str,
                                                      permissions=Depends(UserPermissionChecker('annotations_read'))):
    # FIXME response for "all done"
    assignment = await read_next_open_assignment_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                                    user_id=permissions.user.user_id,
                                                                    db_engine=db_engine)
    return await _construct_annotation_item(assignment=assignment, project_id=permissions.permissions.project_id)


@router.get('/annotate/assignment/{assignment_id}', response_model=AnnotationItem)
async def get_assignment(assignment_id: str,
                         permissions=Depends(UserPermissionChecker('annotations_read'))):
    assignment = await read_assignment(assignment_id=assignment_id, db_engine=db_engine)

    if assignment.user_id != permissions.user.user_id:
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
        await delete_assignment_scope(assignment_scope_id=assignment_scope_id, db_engine=db_engine)
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
        print(payload.config)
        try:
            assignments = await random_assignments(assignment_scope_id=payload.scope_id,
                                                   annotation_scheme_id=payload.annotation_scheme_id,
                                                   project_id=permissions.permissions.project_id,
                                                   config=payload.config,
                                                   engine=db_engine)
        except ValueError as e:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                                detail=str(e))
    else:
        raise HTTPException(status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
                            detail=f'Method "{payload.config.config_type}" is unknown.')

    if payload.save:
        await store_assignments(assignments=assignments, db_engine=db_engine)

    return assignments


class ResolutionProposalResponse(BaseModel):
    matrix: AnnotationMatrix
    proposal: list[BotAnnotationModel]


class SavedResolutionResponse(BaseModel):
    bot_annotation_metadata_id: str
    name: str
    project_id: str
    annotation_scope_id: str | None = None
    annotation_scheme_id: str | None = None

    meta: BotMetaResolve
    saved: list[BotAnnotationModel]


@router.get('/config/resolve/', response_model=ResolutionProposalResponse)
async def get_item_annotation_matrix(strategy: ResolutionMethod,
                                     scheme_id: str | None = None,
                                     scope_id: list[str] | None = Query(default=None),
                                     user_id: list[str] | None = Query(default=None),
                                     key: list[str] | None = Query(default=None),
                                     repeat: list[int] | None = Query(default=None),
                                     permissions=Depends(UserPermissionChecker('annotations_edit'))):
    """
    Get all annotations that match the filters (e.g. all annotations made by users in scope with :scope_id).
    Annotations are returned in a 3D matrix:
       rows (dict entries): items (key: item_id)
       columns (list index of dict entry): Label (key in scheme + repeat); index map in matrix.keys
       cells: list of annotations by each user for item/Label combination

    :param strategy
    :param scheme_id:
    :param scope_id:
    :param user_id:
    :param key:
    :param repeat:
    :param permissions:
    :return:
    """
    filters = AnnotationFilters(
        scheme_id=scheme_id,
        scope_id=scope_id,
        user_id=user_id,
        key=key,
        repeat=repeat,
    )
    matrix, proposal = await get_resolved_item_annotations(strategy=strategy,
                                                           filters=AnnotationFilterObject.parse_obj(filters),
                                                           db_engine=db_engine)
    return ResolutionProposalResponse(matrix=matrix,
                                      proposal=[BotAnnotationModel(
                                          item_id=item_id,
                                          key=matrix.labels[i][-1].key,
                                          repeat=matrix.labels[i][-1].repeat,
                                          value_int=p.v_int,
                                          value_str=p.v_str,
                                          value_bool=p.v_bool,
                                          value_float=p.v_float,
                                      ) for item_id, annotations in proposal.items()
                                          for i, p in enumerate(annotations)
                                          if p is not None])


@router.get('/config/resolved/:bot_annotation_meta_id', response_model=SavedResolutionResponse)
async def get_saved_resolved_annotations(bot_annotation_meta_id: str,
                                         permissions=Depends(UserPermissionChecker('annotations_edit'))):
    async with db_engine.session() as session:  # type: AsyncSession
        meta = await session.get(BotAnnotationMetaData, bot_annotation_meta_id)
        if meta is None:
            raise NoDataForKeyError(f'No `BotAnnotationMetaData` for "{bot_annotation_meta_id}"!')
        annotations = (await session.scalars(
            select(BotAnnotation).where(BotAnnotation.bot_annotation_metadata_id == bot_annotation_meta_id))).all()

        return SavedResolutionResponse(
            bot_annotation_metadata_id=meta.bot_annotation_metadata_id,
            name=meta.name,
            project_id=meta.project_id,
            annotation_scope_id=meta.annotation_scope_id,
            annotation_scheme_id=meta.annotation_scheme_id,
            meta=meta.meta,
            saved=annotations
        )
