import time
from typing import Literal

from pydantic import BaseModel
from fastapi import HTTPException, status as http_status
from fastapi.exception_handlers import http_exception_handler
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from server.util.logging import get_logger

logger = get_logger('nacsos.server.middlewares')
try:
    from resource import getrusage, RUSAGE_SELF
except ImportError as e:
    logger.warning(e)

    RUSAGE_SELF = None


    def getrusage(*args, **kwargs):  # noqa:E303
        return 0.0, 0.0


class ErrorDetail(BaseModel):
    # The type of exception
    type: str
    # Whether it was a warning or Error/Exception
    level: Literal['WARNING', 'ERROR']
    # The message/cause of the Warning/Exception
    message: str


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    @classmethod
    def _resolve_args(cls, ew: Exception | Warning):
        if hasattr(ew, 'args') and ew.args is not None and len(ew.args) > 0:
            return ' | '.join([str(arg) for arg in ew.args])
        return repr(ew)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            return response
        except Warning as w:
            logger.exception(w)
            return await http_exception_handler(request,
                                                exc=HTTPException(
                                                    status_code=http_status.HTTP_400_BAD_REQUEST,
                                                    detail=ErrorDetail(level='WARNING', type=w.__class__.__name__,
                                                                       message=self._resolve_args(w)).dict()))
        except Exception as ex:
            logger.exception(ex)
            return await http_exception_handler(request,
                                                exc=HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                                                                  detail=ErrorDetail(level='ERROR',
                                                                                     type=ex.__class__.__name__,
                                                                                     message=self._resolve_args(
                                                                                         ex)).dict()))


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


__all__ = ['TimingMiddleware']
