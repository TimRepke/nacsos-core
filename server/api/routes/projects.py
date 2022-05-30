from fastapi import APIRouter, Depends

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectModel
from nacsos_data.db.crud.projects import read_all_projects, read_all_projects_for_user

from server.data import db_engine
from server.util.security import get_current_active_user
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.projects')
router = APIRouter()

logger.info('Setting up projects route')


@router.get('/list', response_model=list[ProjectModel])
async def get_all_projects(current_user: UserModel = Depends(get_current_active_user)) -> list[ProjectModel]:
    """
    This endpoint returns all projects the currently logged-in user can see.
    For regular users, this includes all projects for which an entry in ProjectPermissions exists.
    For SuperUsers, this returns all projects on the platform.

    :return: List of projects
    """
    if current_user.is_superuser:
        return await read_all_projects(engine=db_engine)
    return await read_all_projects_for_user(current_user.user_id, engine=db_engine)
