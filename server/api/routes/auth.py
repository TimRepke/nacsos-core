from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from nacsos_data.models.users import UserModel, AuthTokenModel

from server.util.security import get_current_active_user, auth_helper, InvalidCredentialsError
from server.util.logging import get_logger

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=repr(e),
            headers={'WWW-Authenticate': 'Bearer'},
        )


@router.get('/me', response_model=UserModel)
async def read_users_me(current_user: UserModel = Depends(get_current_active_user)):
    return current_user


@router.get('/logout')
async def logout(current_user: UserModel = Depends(get_current_active_user)):
    username = current_user.username

    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='RuntimeError(empty username)',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    await auth_helper.clear_tokens_by_user(username=username)

# TODO forgot password route
# TODO update user info (separate route for password updates?) /
#      only the non-admin stuff, e.g. what users can do themselves
