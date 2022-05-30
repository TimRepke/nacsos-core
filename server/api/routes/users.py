from fastapi import APIRouter, Depends
from server.util.logging import get_logger
from nacsos_data.models.users import UserModel, UserInDBModel
from nacsos_data.db.crud.users import read_all_users
from server.util.security import get_current_active_superuser
from server.data import db_engine

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/list', response_model=list[UserModel])
async def get_all_users(current_user=Depends(get_current_active_superuser)) -> list[UserInDBModel]:
    result = await read_all_users(engine=db_engine)
    return result

