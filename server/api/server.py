from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from server.api.middlewares import TimingMiddleware

from server.util.config import settings
from server.util.logging import get_logger
from server.api.routes import ping
from server.api.routes.admin import users
import mimetypes

mimetypes.init()

logger = get_logger('nacsos.server')

try:
    from resource import getrusage, RUSAGE_SELF
except ImportError as e:
    logger.warning(e)

    RUSAGE_SELF = None


    def getrusage(*args):
        return 0.0, 0.0


class APISubRouter:
    def __init__(self):
        self.router = APIRouter()
        self.paths = {
            '/ping': ping,
            '/admin/users': users,
            # '/graph': graph
        }
        for path, router in self.paths.items():
            self.router.include_router(router.router, prefix=path)


class Server:
    def __init__(self):
        self.app = FastAPI()

        logger.debug('Setting up server and middlewares')
        mimetypes.add_type('application/javascript', '.js')

        if settings.SERVER.HEADER_TRUSTED_HOST:
            self.app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.SERVER.CORS_ORIGINS)
        if settings.SERVER.HEADER_CORS:
            self.app.add_middleware(CORSMiddleware, allow_origins=settings.SERVER.CORS_ORIGINS,
                                    allow_methods=['GET', 'POST', 'DELETE', 'POST'])
        self.app.add_middleware(GZipMiddleware, minimum_size=1000)
        # self.app.add_middleware(TimingMiddleware)

        logger.debug('Setup routers')
        self.api_router = APISubRouter()
        self.app.include_router(self.api_router.router, prefix='/api')

        self.app.mount('/', StaticFiles(directory=settings.SERVER.STATIC_FILES, html=True), name='static')
