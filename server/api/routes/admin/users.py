from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from server.util.logging import get_logger
from nacsos_data.models.users import User
logger = get_logger('nacsos.api.route.admin.users')
router = APIRouter()


@router.get('/', response_class=PlainTextResponse)
async def get_users() -> str:
    return 'pong'

