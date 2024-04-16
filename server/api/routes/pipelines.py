import json
import re
import unicodedata
from shutil import rmtree
from typing import AsyncGenerator, Annotated
from uuid import uuid4
from pathlib import Path

from nacsos_data.db.crud.pipeline import query_tasks, read_task_by_id
from nacsos_data.models.pipelines import TaskModel, TaskStatus
from nacsos_data.models.users import UserModel
from typing_extensions import TypedDict

import aiofiles
from fastapi import APIRouter, UploadFile, Depends, Query, HTTPException, status as http_status
from fastapi.responses import FileResponse
from nacsos_data.util.auth import UserPermissions
from pydantic import BaseModel, StringConstraints
from tempfile import TemporaryDirectory

from server import db_engine
from server.api.errors import MissingInformationError
from server.util.logging import get_logger
from server.util.pipelines.errors import UnknownTaskID, TaskSubmissionFailed
from server.util.pipelines.security import UserTaskPermissionChecker, UserTaskProjectPermissions
from server.util.pipelines.files import get_outputs_flat, get_log, zip_folder, delete_files, delete_task_directory
from server.util.security import UserPermissionChecker, get_current_active_superuser
from server.util.config import settings

logger = get_logger('nacsos.api.route.pipelines')
router = APIRouter()

logger.info('Setting up pipelines route')


class FileOnDisk(TypedDict):
    path: str
    size: int


@router.get('/artefacts/list', response_model=list[FileOnDisk])
def get_artefacts(permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read'))) \
        -> list[FileOnDisk]:
    task_id = permissions.task.task_id  # FIXME assert`task_id is not None`
    if task_id is None:
        raise MissingInformationError()

    return [
        FileOnDisk(path=file[0],
                   size=file[1])  # type: ignore[typeddict-item] # FIXME
        for file in get_outputs_flat(task_id=str(task_id), include_fsize=True)
    ]


@router.get('/artefacts/log', response_model=str)
def get_task_log(permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read'))) \
        -> str | None:
    task_id = permissions.task.task_id  # FIXME assert`task_id is not None`

    # TODO stream the log instead of sending the full file
    #  via: https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse
    #  or: https://fastapi.tiangolo.com/advanced/websockets/
    # probably best to return tail by default
    return get_log(task_id=str(task_id))


@router.get('/artefacts/file', response_class=FileResponse)
def get_file(filename: str,
             permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read'))) \
        -> FileResponse:
    return FileResponse(settings.PIPES.target_dir / filename)


async def tmp_path() -> AsyncGenerator[Path, None]:
    td = TemporaryDirectory()
    tpath = Path(td.name)
    tpath.mkdir(exist_ok=True, parents=True)
    try:
        yield tpath
    finally:
        rmtree(tpath)


@router.get('/artefacts/files', response_class=FileResponse)
def get_archive(permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
                tmp_dir: Path = Depends(tmp_path)) -> FileResponse:
    task_id = permissions.task.task_id  # FIXME assert`task_id is not None`
    zip_folder(task_id=str(task_id), target_file=str(tmp_dir / 'archive.zip'))
    return FileResponse(str(tmp_dir / 'archive.zip'))


@router.post('/artefacts/files/upload', response_model=str)
async def upload_file(file: UploadFile,
                      folder: str | None = None,
                      permissions: UserPermissions = Depends(UserPermissionChecker('artefacts_edit'))) \
        -> str:
    if folder is None:
        folder = str(uuid4())

    # make this a safe and clean filename by removing all non-ascii characters and only
    # allowing `a-z`/`A-Z` and `0-9` and `_` and `-` and `.`; all whitespaces will be replaced by `-`.
    filename_ = str(file.filename)
    filename_ = unicodedata.normalize('NFKD', filename_).encode('ascii', 'ignore').decode('ascii')
    filename_ = re.sub(r'[^a-zA-Z0-9_\-.\r\n\t\f\v ]', '', filename_)
    filename_ = re.sub(r'[\r\n\t\f\v ]+', '-', filename_).strip('-_')

    filename = settings.PIPES.user_data_dir / folder / filename_
    filename.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(filename, 'wb') as f:
        while content := await file.read(1048576):  # async read chunk
            await f.write(content)

    return str(Path(folder) / filename_)


@router.post('/artefacts/files/upload-many', response_model=list[str])
async def upload_files(file: list[UploadFile],
                       folder: str | None = None,
                       permissions: UserPermissions = Depends(UserPermissionChecker('artefacts_edit'))) \
        -> list[str]:
    if folder is None:
        folder = str(uuid4())
    return [await upload_file(file=f, folder=folder) for f in file]


class DeletionRequest(BaseModel):
    task_id: str
    files: list[str]


@router.delete('/artefacts/files')
def delete_files_(req: DeletionRequest,
                  permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_edit'))) \
        -> None:
    if str(permissions.task.task_id) == str(req.task_id):
        # TODO do this for user_data_files as well
        delete_files(task_id=req.task_id, files=req.files)


@router.delete('/artefacts/task')
def delete_task_files(permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_edit'))) \
        -> None:
    task_id = permissions.task.task_id  # FIXME assert`task_id is not None`
    delete_task_directory(task_id=str(task_id))


@router.get('/queue/list', response_model=list[TaskModel])
async def get_all(superuser: UserModel = Depends(get_current_active_superuser)) -> list[TaskModel]:
    tasks = await query_tasks(db_engine=db_engine)
    if tasks is None:
        return []
    return tasks


@router.get('/queue/list/{status}', response_model=list[TaskModel])
async def get_by_status(status: TaskStatus,
                        superuser: UserModel = Depends(get_current_active_superuser)) -> list[TaskModel]:
    tasks = await query_tasks(db_engine=db_engine, status=status)
    if tasks is None:
        return []
    return tasks


@router.get('/queue/project/list', response_model=list[TaskModel])
async def get_all_for_project(permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_read'))) \
        -> list[TaskModel]:
    tasks = await query_tasks(db_engine=db_engine, project_id=permissions.permissions.project_id)
    if tasks is None:
        return []
    return tasks


@router.get('/queue/project/list/{status}', response_model=list[TaskModel])
async def get_by_status_for_project(status: TaskStatus,
                                    permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_read'))) \
        -> list[TaskModel]:
    tasks = await query_tasks(db_engine=db_engine, status=status, project_id=permissions.permissions.project_id)
    if tasks is None:
        return []
    return tasks


OrderBy = Annotated[str, StringConstraints(pattern=r'^[A-Za-z\-_]+,(asc|desc)$')]


@router.get('/queue/search', response_model=list[TaskModel])
async def search_tasks(function_name: str | None = None,
                       fingerprint: str | None = None,
                       user_id: str | None = None,
                       location: str | None = None,
                       status: str | None = None,
                       order_by_fields: list[OrderBy] | None = Query(None),
                       permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_read'))
                       ) -> list[TaskModel]:
    order_by_fields_parsed = None
    if order_by_fields is not None:
        order_by_fields_parsed = [
            (parts[0], parts[1] == 'asc')
            for field in order_by_fields
            if (parts := field.split(',')) is None
        ]

    params = {}
    if function_name is not None:
        params['function_name'] = function_name
    if fingerprint is not None:
        params['fingerprint'] = fingerprint
    if user_id is not None:
        params['user_id'] = user_id
    if location is not None:
        params['location'] = location
    if status is not None:
        params['status'] = status

    tasks = await query_tasks(db_engine=db_engine,
                              project_id=permissions.permissions.project_id,
                              order_by_fields=order_by_fields_parsed,
                              **params)

    if tasks is None:
        return []
    return tasks


@router.get('/queue/task/{task_id}', response_model=TaskModel)
async def get_task(task_id: str,
                   permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_edit'))) -> TaskModel:
    task = await read_task_by_id(task_id=task_id, db_engine=db_engine)

    if task is None or str(task.project_id) != str(permissions.permissions.project_id):
        # TODO: do we also want to check if the user_id overlaps?
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Nope.')

    if task is None:
        raise UnknownTaskID(f'Task does not exist with ID {task_id}')
    return task


@router.get('/queue/status/{task_id}', response_model=TaskStatus)
async def get_status(task_id: str,
                     permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_read'))) \
        -> TaskStatus | str:
    task = await read_task_by_id(task_id=task_id, db_engine=db_engine)

    if task is None or str(task.project_id) != str(permissions.permissions.project_id):
        # TODO: do we also want to check if the user_id overlaps?
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Nope.')

    if task.status is None:
        raise UnknownTaskID(f'Task does not exist with ID {task_id}')
    return task.status


@router.put('/queue/submit/task', response_model=str)
async def submit(task: TaskModel,
                 permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_edit'))) -> str:
    if not str(task.project_id) == str(permissions.permissions.project_id):
        # TODO: do we also want to check if the user_id overlaps?
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Nope.')

    if type(task.params) is str:
        task.params = json.loads(task.params)

    # TODO
    task_id: str | None = None
    # task_id = await task_queue.add_task(
    #     task=task,
    #     check_fingerprint=not task.force_run
    # )
    if task_id is not None:
        return task_id
    raise TaskSubmissionFailed('Did not successfully submit the task to the queue.')


@router.put('/queue/cancel/{task_id}')
async def cancel_task(task_id: str,
                      permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_edit'))) -> None:
    task = await read_task_by_id(task_id=task_id, db_engine=db_engine)

    if task is None or str(task.project_id) != str(permissions.permissions.project_id):
        # TODO: do we also want to check if the user_id overlaps?
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Nope.')
    # TODO
    # task_queue.cancel_task(task_id=task_id)


@router.delete('/queue/task/{task_id}')
async def delete_task(task_id: str,
                      permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_edit'))) -> None:
    task = await read_task_by_id(task_id=task_id, db_engine=db_engine)

    if task is None or str(task.project_id) != str(permissions.permissions.project_id):
        # TODO: do we also want to check if the user_id overlaps?
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail='Nope.')
    # TODO
    # task_queue.remove_task(task_id=task_id)
