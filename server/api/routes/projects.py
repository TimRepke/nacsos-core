import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from nacsos_data.db.schemas import Project, ProjectPermissions, User

from nacsos_data.models.users import UserModel, UserBaseModel
from nacsos_data.models.projects import ProjectModel
from sqlalchemy import select, func, text

from server.api.errors import MissingInformationError
from server.data import db_engine
from server.util.security import get_current_active_user, get_current_active_superuser
from server.util.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

logger = get_logger('nacsos.api.route.projects')
router = APIRouter()

logger.info('Setting up projects route')


class ProjectInfo(ProjectModel):
    owners: list[UserBaseModel]  # list of users with ProjectPermissions.owner==True


@router.get('/list', response_model=list[ProjectInfo])
async def get_all_projects(current_user: UserModel = Depends(get_current_active_user)) -> list[ProjectInfo]:
    """
    This endpoint returns all projects the currently logged-in user can see.
    For regular users, this includes all projects for which an entry in ProjectPermissions exists.
    For SuperUsers, this returns all projects on the platform.

    :return: List of projects
    """
    stmt_owners = select(ProjectPermissions.project_id,
                         func.array_agg(
                             func.row_to_json(text('"user".*'))
                         ).label('owners')) \
        .join(User, ProjectPermissions.user_id == User.user_id) \
        .where(ProjectPermissions.owner == True) \
        .group_by(ProjectPermissions.project_id) \
        .cte()

    stmt_projects = select(Project, stmt_owners.c.owners) \
        .join(stmt_owners, Project.project_id == stmt_owners.c.project_id)

    if current_user.is_superuser:
        # superuser needs no filtering, sees all projects
        pass
    else:
        if current_user.user_id is not None:
            # regular users only see their own projects
            stmt_projects = stmt_projects \
                .join(ProjectPermissions, Project.project_id == ProjectPermissions.project_id) \
                .where(ProjectPermissions.user_id == current_user.user_id)
        else:
            raise MissingInformationError(
                '`current_user` has no `user_id`, which points to a serious issue in the system!')

    async with db_engine.session() as session:  # type: AsyncSession
        result = await session.execute(stmt_projects)

        return [
            ProjectInfo(owners=[
                UserBaseModel.parse_obj(owner)
                for owner in row['owners']
            ],
                **row['Project'].__dict__)
            for row in result.mappings().all()
        ]


@router.put('/create', response_model=str)
async def create_project(project: ProjectModel,
                         superuser: UserModel = Depends(get_current_active_superuser)) -> str:
    async with db_engine.session() as session:  # type: AsyncSession
        if project.project_id is None:
            project.project_id = str(uuid.uuid4())
        session.add(Project(**project.dict()))
        await session.commit()
        return str(project.project_id)
