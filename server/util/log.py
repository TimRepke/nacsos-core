from server.util.config import conf
import math
import traceback
from uvicorn.logging import AccessFormatter, DefaultFormatter
import yaml
import os
import logging
import logging.config


def init_logging():
    with open(os.environ.get('LOGGING_CONF', 'config/logging.conf'), 'r') as f:
        config = yaml.safe_load(f.read())
        logging.config.dictConfig(config)


class AccessLogFormatter(AccessFormatter):
    def formatMessage(self, record):
        try:
            record.__dict__.update({
                'wall_time': record.__dict__['scope']['timing_stats']['wall_time'],
                'cpu_time': record.__dict__['scope']['timing_stats']['cpu_time']
            })
        except KeyError:
            record.__dict__.update({
                'wall_time': '0.0?s',
                'cpu_time': '0.0?s'
            })
        return super().formatMessage(record)


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
    if conf.server.debug_mode:
        tb = traceback.format_exc()
        if logger:
            logger.error(tb)
        return tb
    return f'{type(e).__name__}: {e}'
