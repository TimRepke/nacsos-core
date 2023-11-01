from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import PlainTextResponse
from nacsos_data.util.errors import NotFoundError

from sqlalchemy import select, func as F
from sqlalchemy.ext.asyncio import AsyncSession

from nacsos_data.db.crud.users import read_user_by_name
from nacsos_data.db.schemas import User, Assignment, AssignmentScope, Project, AnnotationScheme
from nacsos_data.models.users import UserModel
from nacsos_data.util.auth import UserPermissions

from server.util.config import settings
from server.util.email import EmailNotSentError, send_message, send_message_sync
from server.util.logging import get_logger
from server.util.security import (
    auth_helper,
    get_current_active_superuser,
    UserPermissionChecker
)
from server.data import db_engine

logger = get_logger('nacsos.api.route.mailing')
router = APIRouter()

logger.debug('Setup nacsos.api.route.mailing router')


@router.post('/reset-password/{username}', response_class=PlainTextResponse)
async def reset_password(username: str) -> str:
    user = await read_user_by_name(username, engine=db_engine)

    if user is not None and user.email is not None:
        # First, clear existing auth tokens
        # Note: This can be used to troll users by deactivating their sessions. Trusting sensible use for now.
        await auth_helper.clear_tokens_by_user(username=username)
        # Create new token
        token = await auth_helper.refresh_or_create_token(username=username,
                                                          token_lifetime_minutes=3 * 60)
        try:
            await send_message(recipients=[user.email],
                               subject='[NACSOS] Reset password',
                               message=f'Dear {user.full_name},\n'
                                       f'You are receiving this message because you or someone else '
                                       f'tried to reset your password.\n'
                                       f'We closed all your active sessions, so you will have to log in again.\n'
                                       f'\n'
                                       f'You can use the following link within the next 3h to reset your password:\n'
                                       f'{settings.SERVER.WEB_URL}/#/password-reset/{token.token_id}\n'
                                       f'\n'
                                       f'Sincerely,\n'
                                       f'The Platform')
        except EmailNotSentError:
            pass
    else:
        # Do nothing as to not leak usernames outside by guessing
        # raise NotFoundError(f'No user by that name: {username}')
        pass
    return 'OK'


@router.post('/welcome', response_class=PlainTextResponse)
async def welcome_mail(username: str,
                       password: str,
                       superuser: UserModel = Depends(get_current_active_superuser)) -> str:
    user = await read_user_by_name(username, engine=db_engine)

    if user is not None and user.email is not None:
        # Create new token
        token = await auth_helper.refresh_or_create_token(username=username,
                                                          token_lifetime_minutes=3 * 60)
        await send_message(recipients=[user.email],
                           subject='[NACSOS] Welcome to the platform',
                           message=f'Dear {user.full_name},\n'
                                   f'I created an account on our scoping platform for you.\n '
                                   f'\n'
                                   f'Username: {user.username}.\n'
                                   f'Password: {password}\n'
                                   f'\n'
                                   f'You can change your password after logging in by opening the user menu at '
                                   f'the top right and clicking "edit profile".\n'
                                   f'\n'
                                   f'You can use the following link within the next 3h to reset your password:\n'
                                   f'{settings.SERVER.WEB_URL}/#/password-reset/{token.token_id}\n'
                                   f'\n'
                                   f'We are working on expanding the documentation for the platform here:\n'
                                   f'https://apsis.mcc-berlin.net/nacsos-docs/\n'
                                   f'\n'
                                   f'Sincerely,\n'
                                   f'The Platform')
        return 'OK'


@router.post('/assignment-reminder', response_model=list[str])
async def remind_users_assigment(assignment_scope_id: str,
                                 background_tasks: BackgroundTasks,
                                 permissions: UserPermissions = Depends(UserPermissionChecker('annotations_edit'))) \
        -> list[str]:
    session: AsyncSession
    async with db_engine.session() as session:
        stmt_info = (
            select(AssignmentScope.name.label('scope_name'),
                   Project.name.label('project_name'))
            .join(AnnotationScheme, AnnotationScheme.annotation_scheme_id == AssignmentScope.annotation_scheme_id)
            .join(Project, Project.project_id == AnnotationScheme.project_id)
            .where(AssignmentScope.assignment_scope_id == assignment_scope_id,
                   Project.project_id == permissions.permissions.project_id)
        )
        info = (await session.execute(stmt_info)).mappings().first()

        if info is None:
            raise NotFoundError('No data associated with this project.')

        stmt = (select(User.full_name, User.email, User.username,
                       F.count(Assignment.assignment_id).label('num_assignments'),
                       F.count(Assignment.assignment_id).filter(Assignment.status == 'OPEN').label('num_open'),
                       F.count(Assignment.assignment_id).filter(Assignment.status == 'FULL').label('num_done'),
                       F.count(Assignment.assignment_id).filter(Assignment.status == 'PARTIAL').label('num_part'))
                .join(Assignment, Assignment.user_id == User.user_id)
                .where(Assignment.assignment_scope_id == assignment_scope_id)
                .group_by(User.full_name, User.email, User.username))
        result = (await session.execute(stmt)).mappings().all()

        reminded_users = []
        for res in result:
            if res['num_open'] > 0:
                logger.debug(f'Trying to remind {res}')
                background_tasks.add_task(
                    send_message,
                    sender=None,
                    recipients=[res['email']],
                    subject='[NACSOS] Assignments waiting for you',
                    message=f'Dear {res["full_name"]},\n'
                            f'In the project "{info["project_name"]}", in the scope "{info["scope_name"]}", '
                            f'we created {res["num_assignments"]} assignments for you.\n '
                            f'\n'
                            f'So far, you fully finished {res["num_done"]}, '
                            f'{res["num_part"]} are partially done,'
                            f'and {res["num_open"]} are still open.\n'
                            f'\n'
                            f'Please head over to the platform to annotate the documents assigned to you: '
                            f'{settings.SERVER.WEB_URL}/#/project/annotate\n'
                            f'\n'
                            f'Sincerely,\n'
                            f'The Platform'
                )
                reminded_users.append(res['username'])
            else:
                logger.debug(f'Not reminding {res}')
        return reminded_users
