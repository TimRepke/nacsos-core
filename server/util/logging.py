import math
import traceback
import logging
import logging.config

from uvicorn.logging import DefaultFormatter

from server.util.config import settings


def get_logger(name=None):
    logging.config.dictConfig(settings.LOGGING_CONF)
    return logging.getLogger(name)


class ColourFormatter(DefaultFormatter):
    def formatMessage(self, record):
        pad = (8 - len(record.levelname)) / 2
        levelname = ' ' * math.ceil(pad) + record.levelname + ' ' * math.floor(pad)
        if self.use_colors:
            record.__dict__['levelnamec'] = self.color_level_name(levelname, record.levelno)
        else:
            record.__dict__['levelnamec'] = levelname

        return super().formatMessage(record)


def except2str(e, logger=None):
    if settings.SERVER.DEBUG_MODE:
        tb = traceback.format_exc()
        if logger:
            logger.error(tb)
        return tb
    return f'{type(e).__name__}: {e}'
