from fastapi import Depends, status as http_status, Header
from fastapi.security import OAuth2PasswordBearer

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectPermission
from nacsos_data.util.auth import Authentication, InsufficientPermissionError, InvalidCredentialsError, UserPermissions

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


auth_helper = Authentication(engine=db_engine,
                             token_lifetime_minutes=settings.SERVER.ACCESS_TOKEN_EXPIRE_MINUTES,
                             default_user=settings.USERS.DEFAULT_USER)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='api/login/token', auto_error=False)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserModel:
    try:
        return auth_helper.get_current_user(token_id=token)
    except InvalidCredentialsError as e:
        raise NotAuthenticated(str(e))
    except InsufficientPermissionError as e:
        raise InsufficientPermissions(str(e))


async def get_current_active_user(current_user: UserModel = Depends(get_current_user)) -> UserModel:
    if not current_user.is_active:
        raise InsufficientPermissions('Inactive user')
    return current_user


def get_current_active_superuser(current_user: UserModel = Depends(get_current_active_user)) -> UserModel:
    if not current_user.is_superuser:
        raise InsufficientPermissions('The user doesn\'t have enough privileges')
    return current_user


class UserPermissionChecker:
    def __init__(self,
                 permissions: list[ProjectPermission] | ProjectPermission | None = None,
                 fulfill_all: bool = True):
        self.permissions = permissions
        self.fulfill_all = fulfill_all

        # convert singular permission to list for unified processing later
        if type(self.permissions) is str:
            self.permissions = [self.permissions]

    async def __call__(self,
                       x_project_id: str = Header(),
                       current_user: UserModel = Depends(get_current_active_user)) -> UserPermissions:
        """
        This function checks the whether a set of required permissions is fulfilled
        for the given project for the currently active user.
        The list of `permissions` corresponds to boolean fields in the respective `ProjectPermissions` instance.
        If left empty, only the existence of such an instance is checked – meaning whether or not the user
        is allowed to see or access the project in one way or another.

        If at least one permission is not fulfilled or no instance exists, this function raises a 403 HTTPException

        :return: `ProjectPermissions` if permissions are fulfilled, exception otherwise
        :raises HTTPException if permissions are not fulfilled
        """
        try:
            return auth_helper.check_permissions(project_id=x_project_id,
                                                 user=current_user,
                                                 required_permissions=self.permissions,
                                                 fulfill_all=self.fulfill_all)

        except (InvalidCredentialsError, InsufficientPermissionError) as e:
            raise InsufficientPermissions(repr(e))


__all__ = ['InsufficientPermissionError', 'InvalidCredentialsError', 'InsufficientPermissions',
           'auth_helper', 'oauth2_scheme', 'UserPermissionChecker', 'UserPermissions', 'NotAuthenticated',
           'get_current_user', 'get_current_active_user', 'get_current_active_superuser']
