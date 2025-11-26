import logging

import dramatiq
from dramatiq.middleware import CurrentMessage, AsyncIO
from dramatiq.brokers.redis import RedisBroker
from dramatiq_abort import Abortable, backends

from server.util.config import settings

logger = logging.getLogger('nacsos.pipelines.task')
broker = RedisBroker(url=settings.PIPES.REDIS_URL)  # type: ignore [no-untyped-call]
dramatiq.set_broker(broker)

event_backend = backends.RedisBackend.from_url(settings.PIPES.REDIS_URL)
abortable = Abortable(backend=event_backend)
broker.add_middleware(abortable)  # type: ignore [no-untyped-call]
broker.add_middleware(CurrentMessage())  # type: ignore [no-untyped-call]
broker.add_middleware(AsyncIO())  # type: ignore [no-untyped-call]

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
