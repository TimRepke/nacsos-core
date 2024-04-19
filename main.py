#!/usr/bin/env python3

from server.util.logging import get_logger

logger = get_logger('nacsos.main')
logger.info('Starting up server')

from server.__main__ import app  # noqa: E402


@app.on_event('startup')
async def hook_event_listeners():
    from server.util.events import eventbus  # noqa: F401

# config = Config()
# config.bind = f'{settings.SERVER.HOST}:{settings.SERVER.PORT}'
# config.debug = settings.SERVER.DEBUG_MODE
# config.accesslog = get_logger('hypercorn.access')
# config.errorlog = get_logger('hypercorn.error')
# config.logconfig_dict = settings.LOGGING_CONF
# config.access_log_format = '%(s)s | "%(R)s" |  Size: %(b)s | Referrer: "%(f)s"'
