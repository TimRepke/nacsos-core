from fastapi import APIRouter, Depends, HTTPException, status as http_status, Header
from nacsos_data.models.annotations import AnnotationTaskModel, \
    AnnotationTaskLabel, \
    AnnotationTaskLabelChoice, \
    AssignmentScopeModel, \
    AssignmentModel, \
    AssignmentStatus, \
    AssignmentScopeBaseConfig, \
    AssignmentScopeConfig
from nacsos_data.models.items import ItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items.twitter import read_tweet_by_item_id
from nacsos_data.db.crud.annotations import \
    read_assignment, \
    read_assignments_for_scope, \
    read_assignments_for_scope_for_user, \
    read_assignment_scopes_for_project, \
    read_assignment_scopes_for_project_for_user, \
    read_annotations_for_assignment, \
    read_next_assignment_for_scope_for_user, \
    read_next_open_assignment_for_scope_for_user, \
    read_annotation_task, \
    read_annotation_tasks_for_project, \
    upsert_annotations, \
    read_assignment_scope, \
    upsert_annotation_task, \
    delete_annotation_task, \
    upsert_assignment_scope, \
    delete_assignment_scope, \
    read_item_ids_with_assignment_count_for_project, \
    read_assignment_counts_for_scope, \
    ItemWithCount, \
    AssignmentCounts, \
    UserProjectAssignmentScope, \
    store_assignments
from nacsos_data.util.annotations.validation import merge_task_and_annotations, annotated_task_to_annotations
from nacsos_data.util.annotations.assignments.random import AssignmentScopeRandomConfig, random_assignments

from pydantic import BaseModel
from server.util.security import UserPermissionChecker
from server.data import db_engine

router = APIRouter()


class AnnotatedItem(BaseModel):
    task: AnnotationTaskModel
    assignment: AssignmentModel


class AnnotationItem(AnnotatedItem):
    scope: AssignmentScopeModel
    item: ItemModel | TwitterItemModel


@router.get('/tasks/definition/{task_id}', response_model=AnnotationTaskModel)
async def get_task_definition(task_id: str) -> AnnotationTaskModel:
    """
    This endpoint returns the detailed definition of an annotation task.

    :param task_id: database id of the annotation task.
    :return: a single annotation task
    """
    return await read_annotation_task(annotation_task_id=task_id, engine=db_engine)


@router.put('/tasks/definition/', response_model=str)
async def put_annotation_task(annotation_task: AnnotationTaskModel,
                              permissions=Depends(UserPermissionChecker('annotations_edit'))) -> str:
    key = await upsert_annotation_task(annotation_task=annotation_task, engine=db_engine)
    return str(key)


@router.delete('/tasks/definition/{task_id}')
async def remove_annotation_task(task_id: str) -> None:
    await delete_annotation_task(annotation_task_id=task_id, engine=db_engine)


@router.get('/tasks/list/{project_id}', response_model=list[AnnotationTaskModel])
async def get_task_definitions_for_project(project_id: str) -> list[AnnotationTaskModel]:
    """
    This endpoint returns the detailed definitions of all annotation tasks associated with a project.

    :param project_id: database id of the project
    :return: list of annotation tasks
    """
    return await read_annotation_tasks_for_project(project_id=project_id, engine=db_engine)


@router.get('/annotate/next/{assignment_scope_id}/{current_assignment_id}', response_model=AnnotationItem)
async def get_next_assignment_for_scope_for_user(assignment_scope_id: str,
                                                 current_assignment_id: str,
                                                 permissions=Depends(UserPermissionChecker('annotations_read'))):
    # FIXME response for "last in list"
    assignment = await read_next_assignment_for_scope_for_user(current_assignment_id=current_assignment_id,
                                                               assignment_scope_id=assignment_scope_id,
                                                               user_id=permissions.user.user_id,
                                                               engine=db_engine)
    scope = await read_assignment_scope(assignment_scope_id=assignment_scope_id, engine=db_engine)
    task = await read_annotation_task(annotation_task_id=assignment.task_id, engine=db_engine)

    annotations = await read_annotations_for_assignment(assignment_id=assignment.assignment_id, engine=db_engine)
    task = merge_task_and_annotations(annotation_task=task, annotations=annotations)

    # FIXME: get any item type, not just tweets
    item = await read_tweet_by_item_id(item_id=assignment.item_id, engine=db_engine)

    return AnnotationItem(task=task, assignment=assignment, scope=scope, item=item)


@router.get('/annotate/next/{assignment_scope_id}', response_model=AnnotationItem)
async def get_next_open_assignment_for_scope_for_user(assignment_scope_id: str,
                                                      permissions=Depends(UserPermissionChecker('annotations_read'))):
    # FIXME response for "all done"
    assignment = await read_next_open_assignment_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                                    user_id=permissions.user.user_id,
                                                                    engine=db_engine)
    scope = await read_assignment_scope(assignment_scope_id=assignment_scope_id, engine=db_engine)
    task = await read_annotation_task(annotation_task_id=assignment.task_id, engine=db_engine)

    annotations = await read_annotations_for_assignment(assignment_id=assignment.assignment_id, engine=db_engine)
    task = merge_task_and_annotations(annotation_task=task, annotations=annotations)

    # FIXME: get any item type, not just tweets
    item = await read_tweet_by_item_id(item_id=assignment.item_id, engine=db_engine)

    return AnnotationItem(task=task, assignment=assignment, scope=scope, item=item)


@router.get('/annotate/assignment/{assignment_id}', response_model=AnnotationItem)
async def get_assignment(assignment_id: str,
                         permissions=Depends(UserPermissionChecker('annotations_read'))):
    assignment = await read_assignment(assignment_id=assignment_id, engine=db_engine)
    assert assignment.user_id == permissions.user.user_id

    scope = await read_assignment_scope(assignment_scope_id=assignment.assignment_scope_id, engine=db_engine)
    task = await read_annotation_task(annotation_task_id=assignment.task_id, engine=db_engine)

    annotations = await read_annotations_for_assignment(assignment_id=assignment_id, engine=db_engine)
    task = merge_task_and_annotations(annotation_task=task, annotations=annotations)

    # FIXME: get any item type, not just tweets
    item = await read_tweet_by_item_id(item_id=assignment.item_id, engine=db_engine)

    return AnnotationItem(task=task, assignment=assignment, scope=scope, item=item)


@router.get('/annotate/scopes/{project_id}', response_model=list[UserProjectAssignmentScope])
async def get_assignment_scopes_for_user(project_id: str,
                                         permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[UserProjectAssignmentScope]:
    scopes = await read_assignment_scopes_for_project_for_user(project_id=project_id,
                                                               user_id=permissions.user.user_id,
                                                               engine=db_engine)
    return scopes


@router.get('/annotate/scopes/', response_model=list[AssignmentScopeModel])
async def get_assignment_scopes_for_project(permissions=Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[AssignmentScopeModel]:
    scopes = await read_assignment_scopes_for_project(project_id=permissions.permissions.project_id,
                                                      engine=db_engine)

    return scopes


@router.get('/annotate/scope/{assignment_scope_id}', response_model=AssignmentScopeModel)
async def get_assignment_scope(assignment_scope_id: str,
                               permissions=Depends(UserPermissionChecker(['annotations_read', 'annotations_edit'],
                                                                         fulfill_all=False))) \
        -> AssignmentScopeModel:
    scope = await read_assignment_scope(assignment_scope_id=assignment_scope_id, engine=db_engine)
    return scope


@router.put('/annotate/scope/', response_model=str)
async def put_assignment_scope(assignment_scope: AssignmentScopeModel,
                               permissions=Depends(UserPermissionChecker('annotations_edit'))) -> str:
    key = await upsert_assignment_scope(assignment_scope=assignment_scope, engine=db_engine)
    return str(key)


@router.delete('/annotate/scope/{assignment_scope_id}')
async def remove_assignment_scope(assignment_scope_id: str,
                                  permissions=Depends(UserPermissionChecker('annotations_edit'))) -> None:
    try:
        await delete_assignment_scope(assignment_scope_id=assignment_scope_id, engine=db_engine)
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                            detail=str(e))


@router.get('/annotate/scope/counts/{assignment_scope_id}', response_model=AssignmentCounts)
async def get_num_assignments_for_scope(assignment_scope_id: str,
                                        permissions=Depends(
                                            UserPermissionChecker(['annotations_read', 'annotations_edit'],
                                                                  fulfill_all=False))) \
        -> AssignmentCounts:
    scope = await read_assignment_counts_for_scope(assignment_scope_id=assignment_scope_id, engine=db_engine)
    return scope


@router.get('/annotate/assignments/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_assignments(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            engine=db_engine)
    return assignments


@router.get('/annotate/assignments/scope/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_assignments_for_scope(assignment_scope_id: str,
                                    permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope(assignment_scope_id=assignment_scope_id,
                                                   engine=db_engine)
    return assignments


@router.get('/annotate/annotations/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_annotations(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            engine=db_engine)
    return assignments


@router.post('/annotate/save')
async def save_annotation(annotated_item: AnnotatedItem,
                          permissions=Depends(UserPermissionChecker('annotations_read'))) -> AssignmentStatus:
    # double-check, that the supposed assignment actually exists
    assignment_db = await read_assignment(assignment_id=annotated_item.assignment.assignment_id, engine=db_engine)

    if permissions.user.user_id == assignment_db.user_id \
            and str(assignment_db.assignment_scope_id) == annotated_item.assignment.assignment_scope_id \
            and str(assignment_db.item_id) == annotated_item.assignment.item_id \
            and str(assignment_db.task_id) == annotated_item.assignment.task_id:
        annotations = annotated_task_to_annotations(annotated_item.task)
        status = await upsert_annotations(annotations=annotations,
                                          assignment_id=annotated_item.assignment.assignment_id,
                                          engine=db_engine)
        return status
    else:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f'The combination of project, assignment, user, task, and item is invalid.',
        )


@router.get('/config/items/', response_model=list[ItemWithCount])
async def get_annotations(permissions=Depends(UserPermissionChecker('dataset_read'))) \
        -> list[ItemWithCount]:
    items = await read_item_ids_with_assignment_count_for_project(project_id=permissions.permissions.project_id,
                                                                  engine=db_engine)
    return items


class MakeAssignmentsRequestModel(BaseModel):
    task_id: str
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
                                                   annotation_task_id=payload.task_id,
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
        await store_assignments(assignments=assignments, engine=db_engine)

    return assignments
