from fastapi import APIRouter, Depends
from nacsos_data.db.crud import upsert_orm
from nacsos_data.db.crud.imports import set_session_mutex
from nacsos_data.db.schemas import Project

from nacsos_data.models.projects import ProjectModel
from nacsos_data.db.crud.projects import read_project_by_id
from nacsos_data.util.auth import UserPermissions
from sqlalchemy.ext.asyncio import AsyncSession

from server.data import db_engine
from server.util.security import UserPermissionChecker
from server.util.logging import get_logger

from . import permissions
from . import items
from .permissions import UserPermission
from ...errors import ProjectNotFoundError

logger = get_logger('nacsos.api.route.project')
router = APIRouter()

logger.info('Setting up projects route')


@router.get('/info', response_model=ProjectModel)
async def get_project(permission: UserPermissions = Depends(UserPermissionChecker())) -> ProjectModel:
    project_id = permission.permissions.project_id
    project = await read_project_by_id(project_id=project_id, engine=db_engine)
    if project is not None:
        return project
    raise ProjectNotFoundError(f'No project found in the database for id {project_id}')


@router.put('/info', response_model=str)
async def save_project(
        project_info: ProjectModel,
        permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> str:
    pkey = await upsert_orm(upsert_model=project_info, Schema=Project, primary_key='project_id',
                            skip_update=['project_id'], db_engine=db_engine, use_commit=True)
    return str(pkey)


@router.put('/import_mutex')
async def reset_import_mutex(
        permission: UserPermissions = Depends(UserPermissionChecker('imports_edit')),
) -> None:
    async with db_engine.session() as session:  # type: AsyncSession
        await set_session_mutex(session=session,
                                project_id=permission.permissions.project_id,
                                lock=False)
    return None


# TODO delete project (project owner and superuser only)

# sub-router for everything related to project-level permission management
router.include_router(permissions.router, prefix='/permissions')

# sub-router for everything related to project-level items
router.include_router(items.router, prefix='/items')
