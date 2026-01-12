from uuid import uuid4
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from nacsos_data.models.users import UserBaseModel
from nacsos_data.models.projects import ProjectPermissionsModel
from nacsos_data.db.schemas import ProjectPermissions
from nacsos_data.db.crud.projects import read_project_permissions_for_project, read_project_permissions_by_id, delete_project_permissions
from server.data import db_engine
from server.util.security import UserPermissionChecker, UserPermissions, InsufficientPermissions
from server.util.logging import get_logger

logger = get_logger('nacsos.api.route.project')
router = APIRouter()


@router.get('/me', response_model=ProjectPermissionsModel)
async def get_project_permissions_current_user(
    permission: UserPermissions = Depends(UserPermissionChecker()),
) -> ProjectPermissionsModel:
    return permission.permissions


@router.get('/list/{project_id}', response_model=list[ProjectPermissionsModel])
async def get_all_project_permissions(
    project_id: str,
    permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> list[ProjectPermissionsModel] | None:
    if permission:
        return await read_project_permissions_for_project(project_id=project_id, engine=db_engine)
    return None


class UserPermission(ProjectPermissionsModel):
    user: UserBaseModel


@router.get('/list-users', response_model=list[UserPermission])
async def get_all_user_permissions(
    permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> list[UserPermission]:
    async with db_engine.session() as session:
        stmt = (
            select(ProjectPermissions).where(ProjectPermissions.project_id == permission.permissions.project_id).options(selectinload(ProjectPermissions.user))
        )
        result = (await session.execute(stmt)).scalars().all()

        return [UserPermission(**{**row.__dict__, 'user': row.user.__dict__}) for row in result]


@router.put('/permission', response_model=str)
async def save_project_permission(
    project_permission: ProjectPermissionsModel,
    permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> str:
    async with db_engine.session() as session:
        logger.debug('Updating project permissions')

        # Some permissions can only be given by superusers of the platform.
        is_su = permission.user.is_superuser

        if project_permission.project_permission_id is None:
            logger.debug('No existing project_permissions found, creating new!')
            project_permission.project_permission_id = uuid4()
        else:
            # fetch existing model from the database
            stmt = select(ProjectPermissions).filter_by(project_permission_id=project_permission.project_permission_id)
            existing_perms: ProjectPermissions | None = (await session.scalars(stmt)).one_or_none()
            if existing_perms is not None:
                logger.debug('Existing project_permissions found, attempting to UPDATE!')

                # Assert that the current user is even allowed to hand out these permissions
                if not is_su and (
                    (project_permission.annotations_prio is True and existing_perms.annotations_prio is False)
                    or (project_permission.search_oa is True and existing_perms.search_oa is False)
                    or (project_permission.import_limit_oa > 0 and existing_perms.import_limit_oa < 1)
                    or (project_permission.search_dimensions is True and existing_perms.search_dimensions is False)
                ):
                    raise InsufficientPermissions('Only super-admins are allowed to change this setting.')

                # Update values
                for key, value in project_permission.model_dump().items():
                    if key not in {'project_id', 'user_id', 'project_permission_id'}:
                        setattr(existing_perms, key, value)

                # Save
                await session.commit()
                return str(project_permission.project_permission_id)

        # Create new permission

        # Assert that the current user is even allowed to hand out these permissions
        if not is_su and (
            project_permission.annotations_prio is True
            or project_permission.search_oa is True
            or project_permission.import_limit_oa > 0
            or project_permission.search_dimensions is True
        ):
            raise InsufficientPermissions('Only super-admins are allowed to change this setting.')

        # Write new permissions to database
        pp_orm = ProjectPermissions(**project_permission.model_dump())
        session.add(pp_orm)
        await session.commit()

        new_id = str(project_permission.project_permission_id)

    return new_id


@router.delete('/permission')
async def remove_project_permission(
    project_permission_id: str,
    permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> None:
    await delete_project_permissions(project_permission_id=project_permission_id, engine=db_engine)


@router.get('/{project_permission_id}', response_model=ProjectPermissionsModel)
async def get_project_permissions_by_id(
    project_permission_id: str,
    permission: UserPermissions = Depends(UserPermissionChecker('owner')),
) -> ProjectPermissionsModel | None:
    if permission:
        return await read_project_permissions_by_id(permissions_id=project_permission_id, engine=db_engine)
    return None


# TODO create project permissions (project owner and superuser only)
# TODO edit project permissions (project owner and superuser only)
# TODO delete project permissions (project owner and superuser only)
