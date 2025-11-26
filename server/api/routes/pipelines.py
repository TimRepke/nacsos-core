import re
import unicodedata
from shutil import rmtree
from typing import AsyncGenerator, Annotated, Any
from uuid import uuid4
from pathlib import Path

from nacsos_data.db.crud.pipeline import query_tasks
from nacsos_data.models.pipeline import TaskModel, TaskStatus
from nacsos_data.models.users import UserModel
from typing_extensions import TypedDict

import aiofiles
from dramatiq_abort import abort
from fastapi import APIRouter, UploadFile, Depends, Query
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse
from nacsos_data.util.auth import UserPermissions
from pydantic import StringConstraints
from tempfile import TemporaryDirectory

from server.pipelines.tasks import broker
from server.util.files import delete_directory, zip_folder, get_outputs_flat
from server.util.security import UserPermissionChecker, get_current_active_superuser
from server.util.logging import get_logger
from server.util.config import settings
from server.data import db_engine

from server.pipelines.security import UserTaskPermissionChecker, UserTaskProjectPermissions
from server.pipelines.files import get_log, stream_log

logger = get_logger('nacsos.api.route.pipelines')
router = APIRouter()

logger.info('Setting up pipelines route')


class FileOnDisk(TypedDict):
    path: str
    size: int


@router.get('/artefacts/list', response_model=list[FileOnDisk])
def get_artefacts(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
) -> list[FileOnDisk]:
    task_id = permissions.task.task_id

    return [
        FileOnDisk(path=file[0],
                   size=file[1])  # type: ignore[typeddict-item]
        for file in get_outputs_flat(root=settings.PIPES.target_dir / str(task_id),
                                     base=settings.PIPES.target_dir,
                                     include_fsize=True)
    ]


@router.get('/artefacts/log', response_model=str)
def get_task_log(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
) -> str | None:
    task_id = permissions.task.task_id

    # TODO stream the log instead of sending the full file
    #  via: https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse
    #  or: https://fastapi.tiangolo.com/advanced/websockets/
    # probably best to return tail by default
    return get_log(task_id=str(task_id))


@router.get('/artefacts/log-stream', response_class=StreamingResponse)
def stream_task_log(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
) -> StreamingResponse:
    return StreamingResponse(stream_log(str(permissions.task.task_id)),
                             media_type='text/plain',
                             headers={'X-Content-Type-Options': 'nosniff'})


@router.get('/artefacts/file', response_class=FileResponse)
def get_file(
        filename: str,
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
) -> FileResponse:
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
def get_archive(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('artefacts_read')),
        tmp_dir: Path = Depends(tmp_path),
) -> FileResponse:
    task_id = permissions.task.task_id
    zip_folder(settings.PIPES.target_dir / str(task_id), target_file=str(tmp_dir / 'archive.zip'))
    return FileResponse(str(tmp_dir / 'archive.zip'))


@router.post('/artefacts/files/upload', response_model=str)
async def upload_file(
        file: UploadFile,
        folder: str | None = None,
        permissions: UserPermissions = Depends(UserPermissionChecker('artefacts_edit')),
) -> str:
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
async def upload_files(
        file: list[UploadFile],
        folder: str | None = None,
        permissions: UserPermissions = Depends(UserPermissionChecker('artefacts_edit')),
) -> list[str]:
    if folder is None:
        folder = str(uuid4())
    return [await upload_file(file=f, folder=folder) for f in file]


OrderBy = Annotated[str, StringConstraints(pattern=r'^[A-Za-z\-_]+,(asc|desc)$')]


@router.get('/tasks', response_model=list[TaskModel])
async def search_tasks(
        function_name: str | None = Query(default=None),
        fingerprint: str | None = Query(default=None),
        user_id: str | None = Query(default=None),
        location: str | None = Query(default=None),
        status: TaskStatus | None = Query(default=None),
        order_by_fields: list[OrderBy] | None = Query(default=None),
        permissions: UserPermissions = Depends(UserPermissionChecker('pipelines_read')),
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


@router.get('/task', response_model=TaskModel)
async def get_task(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('pipelines_edit')),
) -> TaskModel:
    return permissions.task


@router.delete('/task')
async def delete_task(
        permissions: UserTaskProjectPermissions = Depends(UserTaskPermissionChecker('pipelines_edit')),
) -> None:
    task_id = permissions.task.task_id
    delete_directory(settings.PIPES.target_dir / str(task_id))
    # TODO delete task from db


# @router.post('/celery/workers')
# async def welcome_mail(superuser: UserModel = Depends(get_current_active_superuser)) -> str:
#     app.events.

@router.delete('/dramatiq/task')
async def terminate_task(
        message_id: str = Query(),
        superuser: UserModel = Depends(get_current_active_superuser),
) -> None:
    abort(message_id)


@router.get('/dramatiq/workers')
async def get_d_workers() -> list[Any]:
    from dramatiq_dashboard.interface import RedisInterface
    inter = RedisInterface(broker=broker)
    return inter.workers  # type: ignore[no-any-return]


@router.get('/dramatiq/tasks')
async def get_d_tasks() -> list[Any]:
    from dramatiq_dashboard.interface import RedisInterface
    inter = RedisInterface(broker=broker)
    print(inter.get_jobs('nacsos-pipes'))
    print(inter.get_queue('nacsos-pipes'))
    return inter.queues  # type: ignore[no-any-return]
