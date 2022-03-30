from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from server.api.middlewares import TimingMiddleware

from server.util.config import conf
from server.api.routes import ping
import logging
import mimetypes

mimetypes.init()

logger = logging.getLogger('nacsos.server')

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
            # '/platforms': platforms,
            # '/graph': graph
        }
        for path, router in self.paths.items():
            self.router.include_router(router.router, prefix=path)


class Server:
    def __init__(self):
        self.app = FastAPI()

        logger.debug('Setting up server and middlewares')
        mimetypes.add_type('application/javascript', '.js')

        if conf.server.header_trusted_host:
            self.app.add_middleware(TrustedHostMiddleware, allowed_hosts=conf.server.hosts)
        if conf.server.header_cors:
            self.app.add_middleware(CORSMiddleware, allow_origins=conf.server.hosts,
                                    allow_methods=['GET', 'POST', 'DELETE'])
        self.app.add_middleware(GZipMiddleware, minimum_size=1000)
        self.app.add_middleware(TimingMiddleware)

        logger.debug('Setup routers')
        self.api_router = APISubRouter()
        self.app.include_router(self.api_router.router, prefix='/api')

        self.app.mount('/', StaticFiles(directory=conf.server.static_files, html=True), name='static')

