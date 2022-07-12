from fastapi import APIRouter, Depends
from server.util.logging import get_logger
from nacsos_data.models.users import UserModel, UserInDBModel
from nacsos_data.db.crud.users import read_all_users
from server.util.security import UserPermissionChecker, UserPermissions
from server.data import db_engine

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/list', response_model=list[UserModel])
async def get_all_users(permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[UserInDBModel]:
    if permissions.permissions.annotations_edit:
        result = await read_all_users(engine=db_engine)
        return result
