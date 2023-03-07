from fastapi import APIRouter, Depends
from nacsos_data.db.crud import upsert_orm
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from nacsos_data.models.users import UserBaseModel
from nacsos_data.models.projects import ProjectPermissionsModel
from nacsos_data.db.schemas import ProjectPermissions
from nacsos_data.db.crud.projects import \
    read_project_permissions_for_project, \
    read_project_permissions_by_id, \
    delete_project_permissions

from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.project')
router = APIRouter()


@router.get('/me', response_model=ProjectPermissionsModel)
async def get_project_permissions_current_user(permission: UserPermissions = Depends(UserPermissionChecker())) \
        -> ProjectPermissionsModel:
    return permission.permissions


@router.get('/list/{project_id}', response_model=list[ProjectPermissionsModel])
async def get_all_project_permissions(project_id: str, permission=Depends(UserPermissionChecker('owner'))) \
        -> list[ProjectPermissionsModel] | None:
    if permission:
        return await read_project_permissions_for_project(project_id=project_id, engine=db_engine)
    return None


class UserPermission(ProjectPermissionsModel):
    user: UserBaseModel


@router.get('/list-users', response_model=list[UserPermission])
async def get_all_user_permissions(permission=Depends(UserPermissionChecker('owner'))):
    async with db_engine.session() as session:
        stmt = (select(ProjectPermissions)
                .where(ProjectPermissions.project_id == permission.permissions.project_id)
                .options(selectinload(ProjectPermissions.user)))
        result = (await session.execute(stmt)).scalars().all()

        return [UserPermission.parse_obj({
            'user': row.user.__dict__,
            **row.__dict__
        }) for row in result]


@router.put('/permission', response_model=str)
async def save_project_permission(project_permission: ProjectPermissionsModel,
                                  permission=Depends(UserPermissionChecker('owner'))) -> str:
    pkey = await upsert_orm(upsert_model=project_permission, Schema=ProjectPermissions,
                            primary_key='project_permission_id',
                            skip_update=['project_id', 'user_id', 'project_permission_id'],
                            db_engine=db_engine)
    return str(pkey)


@router.delete('/permission')
async def remove_project_permission(project_permission_id: str,
                                    permission=Depends(UserPermissionChecker('owner'))):
    await delete_project_permissions(project_permission_id=project_permission_id, engine=db_engine)


@router.get('/{project_permission_id}', response_model=ProjectPermissionsModel)
async def get_project_permissions_by_id(project_permission_id: str,
                                        permission=Depends(UserPermissionChecker('owner'))) \
        -> ProjectPermissionsModel | None:
    if permission:
        return await read_project_permissions_by_id(permissions_id=project_permission_id, engine=db_engine)
    return None

# TODO create project permissions (project owner and superuser only)
# TODO edit project permissions (project owner and superuser only)
# TODO delete project permissions (project owner and superuser only)
