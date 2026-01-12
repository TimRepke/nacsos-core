from fastapi import Depends, status as http_status, Header
from fastapi.security import OAuth2PasswordBearer
from nacsos_data.db.crud.priority import read_priority_by_id
from nacsos_data.models.priority import PriorityModel

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectPermission
from nacsos_data.util.auth import Authentication, InsufficientPermissionError, InvalidCredentialsError, UserPermissions
from nacsos_data.util.errors import MissingIdError

from server.data import db_engine
from server.util.config import settings

from server.util.logging import get_logger

logger = get_logger('nacsos.util.security')


class InsufficientPermissions(Exception):
    status = http_status.HTTP_403_FORBIDDEN
    headers = {'WWW-Authenticate': 'Bearer'}


class NotAuthenticated(Exception):
    status = http_status.HTTP_401_UNAUTHORIZED
    headers = {'WWW-Authenticate': 'Bearer'}


class UserPriorityPermissions(UserPermissions):
    priority: PriorityModel


auth_helper = Authentication(
    engine=db_engine,
    token_lifetime_minutes=settings.SERVER.ACCESS_TOKEN_EXPIRE_MINUTES,
    default_user=settings.USERS.DEFAULT_USER,
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='api/login/token', auto_error=False)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserModel:
    try:
        return await auth_helper.get_user(token_id=token)
    except InvalidCredentialsError as e:
        raise NotAuthenticated(str(e))
    except InsufficientPermissionError as e:
        raise InsufficientPermissions(str(e))
    except Exception as e:
        raise NotAuthenticated(str(e))


async def get_current_active_user(current_user: UserModel = Depends(get_current_user)) -> UserModel:
    if not current_user.is_active:
        raise InsufficientPermissions('Inactive user')
    return current_user


def get_current_active_superuser(current_user: UserModel = Depends(get_current_active_user)) -> UserModel:
    if not current_user.is_superuser:
        raise InsufficientPermissions("The user doesn't have enough privileges")
    return current_user


class UserPermissionChecker:
    def __init__(self, permissions: list[ProjectPermission] | ProjectPermission | None = None, fulfill_all: bool = True):
        self.permissions: list[ProjectPermission] | None = None
        if type(permissions) is str and permissions is not None:
            # convert singular permission to list for unified processing later
            self.permissions = [permissions]
        elif type(permissions) is list and permissions is not None:
            self.permissions = permissions

        self.fulfill_all = fulfill_all

    async def __call__(self, x_project_id: str = Header(), current_user: UserModel = Depends(get_current_active_user)) -> UserPermissions:
        """
        This function checks that a set of required permissions is fulfilled
        for the given project for the currently active user.
        The list of `permissions` corresponds to boolean fields in the respective `ProjectPermissions` instance.
        If left empty, only the existence of such an instance is checked—meaning that the user
        is allowed to see or access the project in one way or another.

        If at least one permission is not fulfilled or no instance exists, this function raises a 403 HTTPException

        :return: `ProjectPermissions` if permissions are fulfilled, exception otherwise
        :raises HTTPException if permissions are not fulfilled
        """
        try:
            return await auth_helper.check_permissions(
                project_id=x_project_id, user=current_user, required_permissions=self.permissions, fulfill_all=self.fulfill_all
            )

        except (InvalidCredentialsError, InsufficientPermissionError) as e:
            raise InsufficientPermissions(repr(e))


class UserPriorityPermissionChecker(UserPermissionChecker):
    def __init__(self, permissions: list[ProjectPermission] | ProjectPermission | None = None, fulfill_all: bool = True):
        super().__init__(permissions, fulfill_all)

    async def __call__(  # type: ignore[override]
        self,
        x_priority_id: str = Header(),
        x_project_id: str = Header(),
        current_user: UserModel = Depends(get_current_active_user),
    ) -> UserPriorityPermissions:
        permissions = await super().__call__(x_project_id=x_project_id, current_user=current_user)
        try:
            priority: PriorityModel | None = await read_priority_by_id(priority_id=x_priority_id, db_engine=db_engine)

            if priority is None:
                raise MissingIdError(f'Priority setup does not exist with ID {x_priority_id}')

            if str(priority.project_id) != str(permissions.permissions.project_id):
                raise InsufficientPermissionError('Invalid priority or project permissions.')

            return UserPriorityPermissions(user=permissions.user, permissions=permissions.permissions, priority=priority)

        except (InvalidCredentialsError, InsufficientPermissionError) as e:
            raise InsufficientPermissions(repr(e))


__all__ = [
    'InsufficientPermissionError',
    'InvalidCredentialsError',
    'InsufficientPermissions',
    'auth_helper',
    'oauth2_scheme',
    'UserPermissionChecker',
    'UserPermissions',
    'NotAuthenticated',
    'get_current_user',
    'get_current_active_user',
    'get_current_active_superuser',
    'UserPriorityPermissionChecker',
    'UserPriorityPermissions',
]
