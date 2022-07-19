from fastapi import APIRouter, Depends

from nacsos_data.models.projects import ProjectModel
from nacsos_data.db.crud.projects import read_project_by_id

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

from . import permissions
from . import items

logger = get_logger('nacsos.api.route.project')
router = APIRouter()

logger.info('Setting up projects route')


@router.get('/{project_id}/info/', response_model=ProjectModel)
async def get_project(project_id: str, permission=Depends(UserPermissionChecker())) -> ProjectModel:
    return await read_project_by_id(project_id=project_id, engine=db_engine)


# TODO create project (superuser only)
# TODO edit project (project owner and superuser only)
# TODO delete project (project owner and superuser only)

# sub-router for everything related to project-level permission management
router.include_router(permissions.router, prefix='/{project_id}/permissions')

# sub-router for everything related to project-level items
router.include_router(items.router, prefix='/{project_id}/items')