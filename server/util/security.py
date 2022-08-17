from typing import Optional
from datetime import timedelta, datetime
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status as http_status, Header
from fastapi.security import OAuth2PasswordBearer

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectPermissionsModel, ProjectPermission
from nacsos_data.db.crud.users import read_user_by_name as crud_get_user_by_name, read_user_by_id
from nacsos_data.db.crud.projects import read_project_permissions_for_user as crud_get_project_permissions_for_user

from server.data import db_engine
from server.util.config import settings

from server.util.logging import get_logger

logger = get_logger('nacsos.util.security')


class InsufficientPermissions(Exception):
    status = http_status.HTTP_403_FORBIDDEN


class UserPermissions(BaseModel):
    user: UserModel
    permissions: ProjectPermissionsModel


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='api/login/token', auto_error=False)


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


async def authenticate_user(username: str, plain_password: str):
    user = await crud_get_user_by_name(username=username, engine=db_engine)
    if not user:
        return False
    if not verify_password(plain_password, user.password):
        return False
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({'exp': expire})
    encoded_jwt = jwt.encode(to_encode, settings.SERVER.SECRET_KEY, algorithm=settings.SERVER.HASH_ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=http_status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    if settings.USERS.DEFAULT_USER is None:
        try:
            if token is None:
                raise credentials_exception
            payload = jwt.decode(token, settings.SERVER.SECRET_KEY, algorithms=[settings.SERVER.HASH_ALGORITHM])
            username: str = payload.get('sub')
            if username is None:
                raise credentials_exception
            token_data = TokenData(username=username)
        except JWTError:
            raise credentials_exception
        user = await crud_get_user_by_name(username=token_data.username, engine=db_engine)
    else:
        user = await read_user_by_id(user_id=settings.USERS.DEFAULT_USER, engine=db_engine)
        logger.warning('Authentication using fake user!')

    logger.debug(f'Current user: user_id: {user.user_id} {user.username}')

    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: UserModel = Depends(get_current_user)) -> UserModel:
    if not current_user.is_active:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Inactive user')
    return current_user


def get_current_active_superuser(current_user: UserModel = Depends(get_current_active_user)) -> UserModel:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="The user doesn't have enough privileges"
        )
    return current_user


async def get_project_permissions_for_user(project_id: str, current_user: UserModel) -> ProjectPermissionsModel | None:
    if current_user.is_superuser:
        # admin gets to do anything always, so return with simulated full permissions
        return ProjectPermissionsModel.get_virtual_admin(project_id=project_id,
                                                         user_id=current_user.user_id)

    return await crud_get_project_permissions_for_user(user_id=current_user.user_id,
                                                       project_id=project_id,
                                                       engine=db_engine)


class UserPermissionChecker:
    def __init__(self, permissions: list[ProjectPermission] | ProjectPermission = None, fulfill_all: bool = True):
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
        project_permissions = await get_project_permissions_for_user(project_id=x_project_id,
                                                                     current_user=current_user)
        user_permissions = UserPermissions(user=current_user, permissions=project_permissions)
        if project_permissions is not None:
            # no specific permissions were required (only basic access to the project) -> permitted!
            if self.permissions is None:
                return user_permissions

            any_permission_fulfilled = False

            # check that each required permission is fulfilled
            for permission in self.permissions:
                if self.fulfill_all and not project_permissions[permission]:
                    raise InsufficientPermissions(
                        f'User does not have permission "{permission}" for project "{x_project_id}".'
                    )
                any_permission_fulfilled = any_permission_fulfilled or project_permissions[permission]

            if not any_permission_fulfilled and not self.fulfill_all:
                raise InsufficientPermissions(
                    f'User does not have any of the required permissions ({self.permissions}) '
                    f'for project "{x_project_id}".'
                )

            return user_permissions

        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=f'User does not have permission to access project "{x_project_id}".',
        )
