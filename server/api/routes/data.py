from typing import Any
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from nacsos_data.models.users import UserModel
from nacsos_data.models.items import ItemModel
from nacsos_data.models.items.twitter import TwitterItemModel

from server.util.security import get_current_active_user, get_current_user_project_permissions
from server.util.config import settings
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.data')
router = APIRouter()

logger.info('Setting up data route')


@router.get("/project/{project_id}/items", response_model=list[ItemModel])
async def read_users_me(project_id: str, current_user: UserModel = Depends(get_current_active_user)):
    return current_user
