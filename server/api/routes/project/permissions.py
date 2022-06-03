from fastapi import APIRouter, Depends

from nacsos_data.models.projects import ProjectPermissionsModel
from nacsos_data.db.crud.projects import read_project_permissions_for_project, read_project_permissions_by_id

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.project')
router = APIRouter()


@router.get('/me', response_model=ProjectPermissionsModel)
async def get_project_permissions_current_user(permission=Depends(UserPermissionChecker())) \
        -> ProjectPermissionsModel:
    return permission


@router.get('/list', response_model=list[ProjectPermissionsModel])
async def get_all_project_permissions(project_id: str, permission=Depends(UserPermissionChecker('owner'))) \
        -> list[ProjectPermissionsModel]:
    if permission:
        return await read_project_permissions_for_project(project_id=project_id, engine=db_engine)


@router.get('/{project_permission_id}', response_model=ProjectPermissionsModel)
async def get_project_permissions_by_id(project_permission_id: str,
                                        permission=Depends(UserPermissionChecker('owner'))) \
        -> ProjectPermissionsModel:
    if permission:
        return await read_project_permissions_by_id(permissions_id=project_permission_id, engine=db_engine)

# TODO create project permissions (project owner and superuser only)
# TODO edit project permissions (project owner and superuser only)
# TODO delete project permissions (project owner and superuser only)
