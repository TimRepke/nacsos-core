from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from nacsos_data.db.schemas.users import AuthToken
from nacsos_data.models.users import UserModel, AuthTokenModel

from server.api.errors import NoDataForKeyError
from server.util.security import get_current_active_user, auth_helper, InvalidCredentialsError, NotAuthenticated
from server.util.logging import get_logger
from server.data import db_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401

logger = get_logger('nacsos.api.route.login')
router = APIRouter()

logger.info('Setting up login route')


@router.post('/token', response_model=AuthTokenModel)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()) -> AuthTokenModel:
    try:
        user = await auth_helper.check_username_password(username=form_data.username,
                                                         plain_password=form_data.password)
        token = await auth_helper.refresh_or_create_token(username=user.username)
        return token
    except InvalidCredentialsError as e:
        raise NotAuthenticated(repr(e))


@router.put('/token/{token_id}', response_model=AuthTokenModel)
async def refresh_token(token_id: str, current_user: UserModel = Depends(get_current_active_user)) -> AuthTokenModel:
    try:
        token = await auth_helper.refresh_or_create_token(token_id=token_id,
                                                          verify_username=current_user.username)
        return token
    except (InvalidCredentialsError, AssertionError) as e:
        raise NotAuthenticated(repr(e))


@router.delete('/token/{token_id}')
async def revoke_token(token_id: str, current_user: UserModel = Depends(get_current_active_user)):
    await auth_helper.clear_token_by_id(token_id=token_id,
                                        verify_username=current_user.username)


@router.get('/my-tokens', response_model=list[AuthTokenModel])
async def read_tokens_me(current_user: UserModel = Depends(get_current_active_user)):
    async with db_engine.session() as session:  # type: AsyncSession
        stmt = select(AuthToken) \
            .where(AuthToken.username == current_user.username) \
            .order_by(AuthToken.valid_till)
        tokens = (await session.scalars(stmt)).all()
        if tokens is None or len(tokens) == 0:
            raise NoDataForKeyError('No auth token for this user (this error should not exist)')
        return [AuthTokenModel.model_validate(token.__dict__) for token in tokens]


@router.get('/me', response_model=UserModel)
async def read_users_me(current_user: UserModel = Depends(get_current_active_user)):
    return current_user


@router.get('/logout')
async def logout(current_user: UserModel = Depends(get_current_active_user)):
    username = current_user.username

    if username is None:
        raise NotAuthenticated('RuntimeError(empty username)')

    await auth_helper.clear_tokens_by_user(username=username)

# TODO forgot password route
# TODO update user info (separate route for password updates?) /
#      only the non-admin stuff, e.g. what users can do themselves
