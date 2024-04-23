import asyncio
import datetime
import logging
import traceback
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar, NamedTuple, ParamSpec, Protocol, Callable, Awaitable, Any, TYPE_CHECKING, Generic

import dramatiq
from dramatiq import Actor, Broker
from dramatiq.middleware import CurrentMessage, AsyncIO
from dramatiq.brokers.redis import RedisBroker
from dramatiq_abort import Abortable, backends

from sqlalchemy.ext.asyncio import AsyncSession

from nacsos_data.models.pipeline import TaskModel, compute_fingerprint, TaskStatus
from nacsos_data.db.schemas import Task

from server.util.config import settings
from server.util.logging import get_file_logger, LogRedirector

logger = logging.getLogger('nacsos.pipelines.task')
broker = RedisBroker(url=settings.PIPES.REDIS_URL)
dramatiq.set_broker(broker)

event_backend = backends.RedisBackend.from_url(settings.PIPES.REDIS_URL)
abortable = Abortable(backend=event_backend)
broker.add_middleware(abortable)
broker.add_middleware(CurrentMessage())
broker.add_middleware(AsyncIO())

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
from . import sleepy
