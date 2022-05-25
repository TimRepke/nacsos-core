from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectModel, ProjectPermissionsModel
from nacsos_data.db.crud.projects import \
    get_all_projects as crud_get_all_projects, \
    get_all_projects_for_user as crud_get_all_projects_for_user, \
    get_project_by_id as crud_get_project_by_id, \
    get_project_permissions_for_project as crud_get_project_permissions_for_project, \
    get_project_permissions_for_user as crud_get_project_permissions_for_user, \
    get_project_permissions_by_id as crud_get_project_permissions_by_id

from server.data import db_engine
from server.util.security import get_current_active_user, UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.projects')
router = APIRouter()

logger.info('Setting up projects route')


@router.get('/list', response_model=list[ProjectModel])
async def get_all_projects(current_user: UserModel = Depends(get_current_active_user)) -> list[ProjectModel]:
    """
    This endpoint returns all projects the currently logged-in user can see.
    For regular users, this includes all projects for which an entry in ProjectPermissions exists.
    For SuperUsers, this returns all projects on the platform.

    :return: List of projects
    """
    if current_user.is_superuser:
        return await crud_get_all_projects(engine=db_engine)
    return await crud_get_all_projects_for_user(current_user.user_id, engine=db_engine)


@router.get('/{project_id}/info/', response_model=ProjectModel)
async def get_project(project_id: str, permission=Depends(UserPermissionChecker())) -> ProjectModel:
    return await crud_get_project_by_id(project_id=project_id, engine=db_engine)


@router.get('/{project_id}/permissions/me', response_model=ProjectPermissionsModel)
async def get_project_permissions_current_user(permission=Depends(UserPermissionChecker()))\
        -> ProjectPermissionsModel:
    return permission


@router.get('/{project_id}/permissions/all', response_model=list[ProjectPermissionsModel])
async def get_all_project_permissions(project_id: str, permission=Depends(UserPermissionChecker('owner'))) \
        -> list[ProjectPermissionsModel]:
    return await crud_get_project_permissions_for_project(project_id=project_id, engine=db_engine)


@router.get('/{project_id}/permissions/{project_permission_id}', response_model=ProjectPermissionsModel)
async def get_project_permissions_by_id(project_permission_id: str,
                                        permission=Depends(UserPermissionChecker('owner'))) \
        -> ProjectPermissionsModel:
    return await crud_get_project_permissions_by_id(permissions_id=project_permission_id, engine=db_engine)

# TODO create project (superuser only)
# TODO edit project (project owner and superuser only)
# TODO delete project (project owner and superuser only)

# TODO create project permissions (project owner and superuser only)
# TODO edit project permissions (project owner and superuser only)
# TODO delete project permissions (project owner and superuser only)
