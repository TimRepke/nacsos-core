from fastapi import APIRouter
from server.util.logging import get_logger
from nacsos_data.models.users import User
from server.data.users import get_users

logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/', response_model=list[User])
async def get_all_users() -> list[User]:
    return get_users()
