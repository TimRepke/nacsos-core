from fastapi import APIRouter, Depends, Query, HTTPException, status as http_status

from nacsos_data.models.users import UserModel, UserInDBModel, UserBaseModel
from nacsos_data.util.auth import UserPermissions
from nacsos_data.db.crud.users import \
    read_users, \
    read_user_by_id, \
    read_users_by_ids, \
    create_or_update_user

from server.data import db_engine
from server.api.errors import DataNotFoundWarning, UserNotFoundError
from server.util.logging import get_logger
from server.util.security import UserPermissionChecker, get_current_active_user

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/list/all', response_model=list[UserBaseModel])
async def get_all_users(current_user: UserModel = Depends(get_current_active_user)) \
        -> list[UserInDBModel]:
    result = await read_users(project_id=None, order_by_username=True, engine=db_engine)
    if result is None:
        return []
    return result


@router.get('/list/project/{project_id}', response_model=list[UserBaseModel])
async def get_project_users(project_id: str,
                            permissions: UserPermissions = Depends(UserPermissionChecker())) \
        -> list[UserInDBModel]:
    result = await read_users(project_id=project_id, order_by_username=True, engine=db_engine)
    if result is not None:
        return result
    raise DataNotFoundWarning(f'Found no users for project with ID {project_id}')


# FIXME refine required permission
@router.get('/details/{user_id}', response_model=UserModel)
async def get_user_by_id(user_id: str,
                         permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> UserInDBModel:
    result = await read_user_by_id(user_id=user_id, engine=db_engine)
    if result is not None:
        return result
    raise UserNotFoundError(f'User not found in DB for ID {user_id}')


# FIXME refine required permission
@router.get('/details', response_model=list[UserModel])
async def get_users_by_ids(user_id: list[str] = Query(),
                           permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserInDBModel]:
    result = await read_users_by_ids(user_ids=user_id, engine=db_engine)
    if result is not None:
        return result
    raise UserNotFoundError(f'Users not found in DB for IDs {user_id}')


@router.put('/details', response_model=str)
async def save_user(user: UserInDBModel | UserModel, current_user: UserModel = Depends(get_current_active_user)):
    # Users can only edit their own info, admins can edit all.
    if user.user_id != current_user.user_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail='You do not have permission to perform this action.',
        )

    return await create_or_update_user(user, engine=db_engine)
