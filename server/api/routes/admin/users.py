from fastapi import APIRouter
from server.util.logging import get_logger
from nacsos_data.models.users import UserModel, UserInDBModel
from nacsos_data.db.crud.users import get_all_users as crud_get_all_users
from server.data import db_engine

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/', response_model=list[UserModel])
async def get_all_users() -> list[UserInDBModel]:
    result = await crud_get_all_users(engine=db_engine)
    return result
