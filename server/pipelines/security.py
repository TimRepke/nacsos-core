from fastapi import Depends, Header, status as http_status
from nacsos_data.db.crud.pipeline import read_task_by_id
from nacsos_data.models.pipeline import TaskModel

from nacsos_data.models.users import UserModel
from nacsos_data.models.projects import ProjectPermission
from nacsos_data.util.auth import InsufficientPermissionError, InvalidCredentialsError, UserPermissions

from server.util.security import get_current_active_user, UserPermissionChecker
from server.data import db_engine

from server.pipelines.errors import UnknownTaskID


class UserTaskProjectPermissions(UserPermissions):
    task: TaskModel


class InsufficientPermissions(Exception):
    status = http_status.HTTP_403_FORBIDDEN


class UserTaskPermissionChecker(UserPermissionChecker):
    def __init__(self, permissions: list[ProjectPermission] | ProjectPermission | None = None,
                 fulfill_all: bool = True):
        super().__init__(permissions, fulfill_all)

    async def __call__(self,  # type: ignore[override]
                       x_task_id: str = Header(),
                       x_project_id: str = Header(),
                       current_user: UserModel = Depends(get_current_active_user)) -> UserTaskProjectPermissions:
        permissions = await super().__call__(x_project_id=x_project_id, current_user=current_user)
        try:
            task: TaskModel | None = await read_task_by_id(task_id=x_task_id, db_engine=db_engine)

            if task is None:
                raise UnknownTaskID(f'Task does not exist with ID {x_task_id}')

            if str(task.project_id) != str(permissions.permissions.project_id):
                # TODO: do we also want to check if the user_id overlaps?
                raise InsufficientPermissionError('Invalid task or project permissions.')

            return UserTaskProjectPermissions(user=permissions.user,
                                              permissions=permissions.permissions,
                                              task=task)

        except (InvalidCredentialsError, InsufficientPermissionError) as e:
            raise InsufficientPermissions(repr(e))
