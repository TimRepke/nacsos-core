import asyncio
import datetime
import logging
import traceback
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, TypeVar, NamedTuple, ParamSpec, Concatenate, Protocol, Awaitable, Coroutine, Generic

from celery.app import Celery
from celery.app.task import Task as CeleryTask
from sqlalchemy.ext.asyncio import AsyncSession

from nacsos_data.models.pipeline import TaskModel, compute_fingerprint, TaskStatus
from nacsos_data.db.schemas import Task

from server.util.config import settings
from server.util.logging import get_file_logger, LogRedirector

logger = logging.getLogger('nacsos.pipelines.task')
app = Celery('nacsos',
             broker=settings.PIPES.REDIS_URL, backend=settings.PIPES.REDIS_URL,
             broker_connection_retry_on_startup=True)


class TaskContext(NamedTuple):
    session: AsyncSession
    target_dir: Path
    logger: logging.Logger | None = None
    work_dir: Path | None = None  # directory for temporary files
    task: TaskModel | None = None
    celery: CeleryTask | None = None


def unpack_context(ctx: TaskContext, name: str):
    logger_ = ctx.logger
    if logger_ is None:
        logger_ = logging.getLogger(name)
    work_dir = ctx.work_dir
    if work_dir is None:
        work_dir = Path(TemporaryDirectory().name)

    return ctx.session, ctx.target_dir, logger_, work_dir, ctx.task, ctx.celery


T = TypeVar('T', covariant=True)
P = ParamSpec('P')


class SubmitFunction(Protocol[T]):
    async def __call__(self, project_id: str, *args: P.args, user_id: str | None = None,  # type: ignore[valid-type]
                       comment: str | None = None, **kwargs: P.kwargs) -> T: ...


class TaskFunction(Protocol[T]):
    async def __call__(self, ctx: TaskContext, *args: P.args, **kwargs: P.kwargs) -> T: ...


def celery_task(func: TaskFunction[T]) -> SubmitFunction[T]:
    @app.task(bind=True)
    def app_task(self: CeleryTask,
                 task_id: str,
                 *args: P.args,
                 **kwargs: P.kwargs) -> T:
        celery_id = self.request.id
        func_name = f'{func.__module__}.{func.__name__}'  # type: ignore[attr-defined]

        target_dir = settings.PIPES.target_dir / str(task_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        task_logger = get_file_logger(name=func_name, out_file=target_dir / 'progress.log', level='DEBUG', stdio=False)
        task_logger.info(f'Entered task wrapper for {task_id}')

        async def inner() -> T:
            task_logger.info(f'Entered async inner for {task_id}')
            from server.data import db_engine
            async with db_engine.session() as session:  # type: AsyncSession
                task = await session.get_one(Task, task_id)
                task.status = TaskStatus.RUNNING
                task.time_started = datetime.datetime.now()
                task.celery_id = celery_id
                await session.flush()
                task_logger.info('Wrote task info to database.')

                try:
                    with TemporaryDirectory(dir=settings.PIPES.WORKING_DIR) as work_dir, \
                            LogRedirector(task_logger, level='INFO', stream='stdout'), \
                            LogRedirector(task_logger, level='ERROR', stream='stderr'):
                        ctx = TaskContext(
                            task=TaskModel.model_validate(task.__dict__),
                            logger=task_logger,
                            session=session,
                            work_dir=Path(work_dir),
                            target_dir=target_dir,
                            celery=self,
                        )

                        # Do the actual function call
                        task_logger.info(f'Running {func_name}...')
                        result: T = await func(ctx, *args, **kwargs)

                        task.status = TaskStatus.COMPLETED
                        task.time_finished = datetime.datetime.now()
                        await session.flush()

                        task_logger.info(f'Task {task_id} finished successfully')
                        return result

                except (Exception, Warning) as e:
                    # Oh no, something failed. Do some post-mortem logging
                    tb = traceback.format_exc()
                    task_logger.fatal(tb)
                    task_logger.fatal(f'{type(e).__name__}: {e}')

                    task.status = TaskStatus.FAILED
                    task.time_finished = datetime.datetime.now()
                    await session.flush()

                    task_logger.info(f'Task {task_id} failed')
                    raise e

        return asyncio.run(inner())

    async def submit(project_id: str,  # type: ignore[valid-type]
                     *args: P.args,
                     user_id: str | None = None,
                     comment: str | None = None,
                     **kwargs: P.kwargs) -> None:
        from server.data import db_engine
        async with db_engine.session() as session:  # type: AsyncSession

            task_id = str(uuid.uuid4())
            func_name = f'{func.__module__}.{func.__name__}'  # type: ignore[attr-defined]
            params = {**kwargs}
            for i, arg in enumerate(args):
                params[func.__code__.co_varnames[i]] = arg  # type: ignore[attr-defined]

            fingerprint = compute_fingerprint(full_name=func_name, params=params)

            task = Task(task_id=task_id, user_id=user_id, project_id=project_id, function_name=func_name,
                        params=params, fingerprint=fingerprint, comment=comment,
                        rec_expunge=datetime.datetime.now() + datetime.timedelta(days=14))
            session.add(task)
            await session.flush()
            logger.info('Wrote task info to database.')

        logger.info('Queueing task for celery...')
        app_task.delay(task_id, *args, **kwargs)

    return submit  # type: ignore[return-value]  # FIXME


# # Import the module
# module = importlib.import_module(exec_info.module, package=exec_info.package_path)
# module = importlib.reload(module)
#
# # Get the function from the string
# func = getattr(module, exec_info.function)

# Do the actual function call
# if not asyncio.iscoroutinefunction(func):
#     logger.info(f'Running {func_name} with asyncio...')
#     loop = asyncio.new_event_loop()
#     result = loop.run_until_complete(func(*args, ctx=ctx, **kwargs))
# else:
#     logger.info(f'Running {func_name} normally...')
#     result = func(*args, ctx=ctx, **kwargs)


from . import imports
