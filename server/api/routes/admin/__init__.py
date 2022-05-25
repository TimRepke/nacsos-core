from fastapi import APIRouter, Depends

from server.util.security import get_current_active_superuser
from . import users

router = APIRouter(dependencies=[Depends(get_current_active_superuser)])

router.include_router(users.router, prefix='/users')
