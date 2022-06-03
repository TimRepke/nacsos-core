from fastapi import APIRouter, Depends
from nacsos_data.models.annotations import AnnotationTaskModel, AnnotationTaskLabel, AnnotationTaskLabelChoice, \
    AssignmentScopeModel, AssignmentModel
from nacsos_data.models.items import ItemModel
from nacsos_data.models.items.twitter import TwitterItemModel
from nacsos_data.db.crud.items.twitter import read_tweet_by_item_id
from nacsos_data.db.crud.annotations import \
    read_assignments_for_scope_for_user, \
    read_assignment_scopes_for_project_for_user, \
    read_annotations_for_assignment, \
    read_annotation_task, \
    read_annotation_tasks_for_project, \
    UserProjectAssignmentScope

from pydantic import BaseModel
from server.util.security import UserPermissionChecker
from server.data import db_engine

router = APIRouter()


class AnnotationItem(BaseModel):
    task: AnnotationTaskModel
    item: ItemModel | TwitterItemModel


@router.get('/tasks/definition/{task_id}', response_model=AnnotationTaskModel)
async def get_task_definition(task_id: str) -> AnnotationTaskModel:
    """
    This endpoint returns the detailed definition of an annotation task.

    :param task_id: database id of the annotation task.
    :return: a single annotation task
    """
    return await read_annotation_task(annotation_task_id=task_id, engine=db_engine)


@router.get('/tasks/list/{project_id}', response_model=list[AnnotationTaskModel])
async def get_task_definitions_for_project(project_id: str) -> list[AnnotationTaskModel]:
    """
    This endpoint returns the detailed definitions of all annotation tasks associated with a project.

    :param project_id: database id of the project
    :return: list of annotation tasks
    """
    return await read_annotation_tasks_for_project(project_id=project_id, engine=db_engine)


@router.get('/annotate/next/{project_id}/{assignment_scope_id}', response_model=AnnotationItem)
async def get_next_assignment_for_scope_for_user(assignment_scope_id: str,
                                                 permissions=Depends(UserPermissionChecker('annotations_edit'))):
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            engine=db_engine)
    # FIXME: magically find the next-to-annotate id
    item = await read_tweet_by_item_id(assignments[0].item, engine=db_engine)
    return AnnotationItem(
        task=ATM('668529f5-1c54-44ef-b665-290ad3632f0d'),
        item=tweet
    )


@router.get('/annotate/scopes/{project_id}', response_model=list[UserProjectAssignmentScope])
async def get_assignment_scopes(project_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[UserProjectAssignmentScope]:
    scopes = await read_assignment_scopes_for_project_for_user(project_id=project_id,
                                                               user_id=permissions.user.user_id,
                                                               engine=db_engine)
    return scopes


@router.get('/annotate/assignments/{project_id}/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_assignments(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    # FIXME: would be more elegant to get the project ID indirectly via the assignment_scope
    #        rather than passing it as a redundant parameter
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            engine=db_engine)
    return assignments


@router.get('/annotate/annotations/{project_id}/{assignment_scope_id}', response_model=list[AssignmentModel])
async def get_annotations(assignment_scope_id: str, permissions=Depends(UserPermissionChecker('annotations_read'))) \
        -> list[AssignmentModel]:
    # FIXME: would be more elegant to get the project ID indirectly via the assignment_scope
    #        rather than passing it as a redundant parameter
    assignments = await read_assignments_for_scope_for_user(assignment_scope_id=assignment_scope_id,
                                                            user_id=permissions.user.user_id,
                                                            engine=db_engine)
    return assignments
