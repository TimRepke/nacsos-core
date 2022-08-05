from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from server.util.logging import get_logger
import time

logger = get_logger('nacsos.server.middlewares')
try:
    from resource import getrusage, RUSAGE_SELF
except ImportError as e:
    logger.warning(e)

    RUSAGE_SELF = None


    def getrusage(*args):  # noqa:E303
        return 0.0, 0.0


class TimingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.time()
        start_cpu_time = self._get_cpu_time()

        response = await call_next(request)

        used_cpu_time = self._get_cpu_time() - start_cpu_time
        used_time = time.time() - start_time

        response.headers['X-CPU-Time'] = f'{used_cpu_time:.8f}s'
        response.headers['X-WallTime'] = f'{used_time:.8f}s'

        request.scope['timing_stats'] = {
            'cpu_time': f'{used_cpu_time:.8f}s',
            'wall_time': f'{used_time:.8f}s'
        }

        return response

    @staticmethod
    def _get_cpu_time():
        resources = getrusage(RUSAGE_SELF)
        # add up user time (ru_utime) and system time (ru_stime)
        return resources[0] + resources[1]


# TODO authenticated user middleware

__all__ = ['TimingMiddleware']
