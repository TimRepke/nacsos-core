from fastapi import APIRouter, Depends, Query
from server.util.logging import get_logger
from nacsos_data.models.users import UserModel, UserInDBModel
from nacsos_data.db.crud.users import \
    read_all_users, \
    read_user_by_id, \
    read_users_by_ids, \
    read_project_users
from server.util.security import UserPermissionChecker, UserPermissions
from server.data import db_engine

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


# FIXME refine required permission
@router.get('/list/all', response_model=list[UserModel])
async def get_all_users(permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserInDBModel]:
    result = await read_all_users(engine=db_engine)
    return result


# FIXME refine required permission
@router.get('/list/project/{project_id}', response_model=list[UserModel])
async def get_all_users(project_id: str,
                        permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserInDBModel]:
    result = await read_project_users(project_id=project_id, engine=db_engine)
    return result


# FIXME refine required permission
@router.get('/details/{user_id}', response_model=UserModel)
async def get_user_by_id(user_id: str,
                         permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> UserInDBModel:
    result = await read_user_by_id(user_id=user_id, engine=db_engine)
    return result


# FIXME refine required permission
@router.get('/details', response_model=list[UserModel])
async def get_users_by_ids(user_id: list[str] = Query(),
                           permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserInDBModel]:
    print(user_id)
    result = await read_users_by_ids(user_ids=user_id, engine=db_engine)
    return result

