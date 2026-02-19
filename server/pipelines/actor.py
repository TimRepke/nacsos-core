import datetime
import logging
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar, Callable, Awaitable, Any, AsyncIterator
from typing_extensions import ParamSpec

from dramatiq import Actor, Broker, Message
from dramatiq.middleware import CurrentMessage

from sqlalchemy.orm import Session  # noqa F401
from sqlalchemy.ext.asyncio import AsyncSession  # noqa F401
from nacsos_data.models.pipeline import compute_fingerprint, TaskStatus
from nacsos_data.db.schemas import Task

from server.util.config import settings, DatabaseConfig
from server.util.logging import get_file_logger, LogRedirector

logger = logging.getLogger('nacsos.pipelines.actor')

R = TypeVar('R')
P = ParamSpec('P')


class NacsosActor(Actor[P, R]):
    def __init__(self, fn: Callable[P, R | Awaitable[R]], *, broker: Broker, actor_name: str, queue_name: str, priority: int, options: dict[str, Any]):
        actor_name = f'{fn.__module__[len("server.") :]}.{fn.__name__}'
        super().__init__(fn, broker=broker, actor_name=actor_name, queue_name=queue_name, priority=priority, options=options)

        self.message_id: str | None = None
        self.task_id: str | None = None

    @property
    def rec_expunge(self) -> datetime.datetime:
        """
        Date util which to keep artefacts for this task.
        Number of days from now can be adjusted via decorator option:

        ```
        @dramatic.actor(keep_days=21)
        def task():
            ...
        ```

        :return:
        """
        return datetime.datetime.now() + datetime.timedelta(days=self.options.get('keep_days', 14))

    def send(  # type: ignore[valid-type, override]
        self,
        project_id: str,
        *args: P.args,
        user_id: str | None = None,
        comment: str | None = None,
        **kwargs: P.kwargs,
    ) -> Message[R]:
        from nacsos_data.db import get_engine

        self.task_id = str(uuid.uuid4())

        params = {**kwargs}
        for i, arg in enumerate(args):
            params[self.fn.__code__.co_varnames[i]] = arg

        fingerprint = compute_fingerprint(full_name=self.actor_name, params=params)

        message = super().send_with_options(
            args=args, kwargs=kwargs, nacsos_actor_name=self.actor_name, nacsos_task_id=self.task_id, max_retries=0, time_limit=129600000
        )  # 24h in ms => 24*60*60*1000

        db_engine = get_engine(settings=settings.DB)
        with db_engine.session() as session:  # type: Session
            task = Task(
                task_id=self.task_id,
                user_id=user_id,
                project_id=project_id,
                function_name=self.actor_name,
                params=params,
                fingerprint=fingerprint,
                comment=comment,
                message_id=message.message_id,
                rec_expunge=self.rec_expunge,
                status=TaskStatus.PENDING,
            )
            session.add(task)
            session.commit()
            self.logger.info('Wrote task info to database.')

        return message

    @classmethod
    @asynccontextmanager
    async def exec_context(cls) -> AsyncIterator[tuple[DatabaseConfig, logging.Logger, Path, str, str | None, str | None]]:
        logger.info('Opening execution context')

        from nacsos_data.db import get_engine_async

        db_engine = get_engine_async(settings=settings.DB)

        actor_name: str = 'anonymous_actor'
        task_id: str | None = None
        message_id: str | None = None
        message: Message[R] = CurrentMessage.get_current_message()  # type: ignore[assignment]
        if message:
            message_id = message.message_id
            actor_name = message.options.get('nacsos_actor_name')  # type: ignore[assignment]
            task_id = message.options.get('nacsos_task_id')
            logger.info(f'message_id: {message_id}, task_id: {task_id}, actor_name: {actor_name}')

        target_dir = settings.PIPES.target_dir / str(task_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        task_logger_ = get_file_logger(name=actor_name, out_file=target_dir / 'progress.log', level='DEBUG', stdio=True)
        task_logger_.warning('warn')
        task_logger = task_logger_.getChild(task_id or 'child')
        task_logger.warning('warn')

        async with db_engine.session() as session:  # type: AsyncSession
            task = await session.get(Task, task_id)

            if task:
                task.status = TaskStatus.RUNNING
                task.time_started = datetime.datetime.now()
                await session.commit()
                task_logger.info('Wrote task info to database.')
            else:
                task_logger.warning(f'Task {task_id} not found in database.')

        status: TaskStatus | None = None
        with (
            TemporaryDirectory(dir=settings.PIPES.WORKING_DIR) as work_dir,
            LogRedirector(task_logger, level='INFO', stream='stdout'),
            LogRedirector(task_logger, level='ERROR', stream='stderr'),
        ):
            try:
                # Yielding this info implicitly executes everything in the `with:` context.
                yield settings.DB, task_logger, target_dir, work_dir, task_id, message_id
            except (Exception, Warning) as e:
                # Oh no, something failed. Do some post-mortem logging
                logger.error('Big drama from an actor!')
                logger.exception(e)
                tb = traceback.format_exc()
                task_logger.fatal(tb)
                task_logger.fatal(f'{type(e).__name__}: {e}')
                status = TaskStatus.FAILED
            finally:
                async with db_engine.session() as session:  # type: AsyncSession
                    task = await session.get(Task, task_id)
                    logger.debug(f'Pre-set actor status: {status}')
                    if status is None:
                        status = TaskStatus.COMPLETED
                    if task:
                        task.status = status
                        task.time_finished = datetime.datetime.now()
                        await session.commit()
                        task_logger.info(f'Wrote task finish info ({status}) to database.')
                    else:
                        task_logger.warning(f'Task {task_id} not found in database; failed to write finish info ({status}).')


# TODO: Handle `abort()` in task update
