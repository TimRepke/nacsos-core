import math
import traceback
import logging
import logging.config
from pathlib import Path
from types import TracebackType
from typing import Literal, Type
from contextlib import redirect_stdout, redirect_stderr

from uvicorn.logging import DefaultFormatter

from server.util.config import settings


def get_logger(name: str | None = None):
    if settings.LOGGING_CONF is not None:
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


def get_file_logger(out_file: str | Path, name: str, level: str = 'DEBUG', stdio: bool = False) -> logging.Logger:
    handler = logging.FileHandler(filename=out_file, mode='w')
    handler.setLevel(level)

    formatter = logging.Formatter(fmt='%(asctime)s (%(process)d) [%(levelname)s] %(name)s: %(message)s')
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)  # logger.setLevel(level if stdio else 100)
    logger.addHandler(handler)
    return logger


class LogRedirector:
    def __init__(self, logger: logging.Logger,
                 level: Literal['INFO', 'ERROR'] = 'INFO',
                 stream: Literal['stdout', 'stderr'] = 'stdout') -> None:
        self.logger = logger
        self.level = getattr(logging, level)
        if stream == 'stdout':
            self._redirector = redirect_stdout(self)  # type: ignore
        else:
            self._redirector = redirect_stderr(self)  # type: ignore

    def write(self, msg: str) -> None:
        if msg and not msg.isspace():
            self.logger.log(self.level, msg)

    def flush(self) -> None:
        pass

    def __enter__(self) -> 'LogRedirector':
        self._redirector.__enter__()
        return self

    def __exit__(self, exc_type: Type[BaseException] | None,
                 exc_value: BaseException | None,
                 trace: TracebackType | None) -> None:
        # let contextlib do any exception handling here
        self._redirector.__exit__(exc_type, exc_value, trace)
