import mimetypes

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .util.middlewares import TimingMiddleware, ErrorHandlingMiddleware
from .util.config import settings
from .data import db_engine
from .util.logging import get_logger
from .api import router as api_router


mimetypes.init()

logger = get_logger('nacsos.server')

app = FastAPI(openapi_url=settings.SERVER.OPENAPI_FILE,
              openapi_prefix=settings.SERVER.OPENAPI_PREFIX,
              root_path=settings.SERVER.ROOT_PATH)

logger.debug('Setting up server and middlewares')
mimetypes.add_type('application/javascript', '.js')

app.add_middleware(ErrorHandlingMiddleware)
if settings.SERVER.HEADER_TRUSTED_HOST:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.SERVER.CORS_ORIGINS)
    logger.info(f'TrustedHostMiddleware allows the following hosts: {settings.SERVER.CORS_ORIGINS}')
if settings.SERVER.HEADER_CORS:
    app.add_middleware(CORSMiddleware,
                       allow_origins=settings.SERVER.CORS_ORIGINS,
                       allow_methods=['GET', 'POST', 'DELETE', 'POST', 'PUT'],
                       allow_headers=['*'],
                       allow_credentials=True)
    logger.info(f'CORSMiddleware will accept the following origins: {settings.SERVER.CORS_ORIGINS}')
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TimingMiddleware)

logger.debug('Setup routers')
app.include_router(api_router, prefix='/api')

app.mount('/', StaticFiles(directory=settings.SERVER.STATIC_FILES, html=True), name='static')


@app.on_event("startup")
async def on_startup():
    await db_engine.startup()
