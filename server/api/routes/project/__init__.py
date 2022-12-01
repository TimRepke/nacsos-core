from fastapi import APIRouter, Depends
from nacsos_data.db.crud import update_orm, upsert_orm
from nacsos_data.db.schemas import Project

from nacsos_data.models.projects import ProjectModel
from nacsos_data.db.crud.projects import read_project_by_id, update_project

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

from . import permissions
from . import items
from ...errors import ProjectNotFoundError

logger = get_logger('nacsos.api.route.project')
router = APIRouter()

logger.info('Setting up projects route')


@router.get('/info', response_model=ProjectModel)
async def get_project(permission=Depends(UserPermissionChecker())) -> ProjectModel:
    project_id = permission.permissions.project_id
    project = await read_project_by_id(project_id=project_id, engine=db_engine)
    if project is not None:
        return project
    raise ProjectNotFoundError(f'No project found in the database for id {project_id}')


@router.put('/info', response_model=str)
async def save_project(project_info: ProjectModel,
                       permission=Depends(UserPermissionChecker('owner'))) -> str:
    pkey = await upsert_orm(upsert_model=project_info, Schema=Project, primary_key='project_id',
                            skip_update=['project_id'], db_engine=db_engine)
    return str(pkey)


# TODO create project (superuser only)
# TODO delete project (project owner and superuser only)

# sub-router for everything related to project-level permission management
router.include_router(permissions.router, prefix='/permissions')

# sub-router for everything related to project-level items
router.include_router(items.router, prefix='/items')
