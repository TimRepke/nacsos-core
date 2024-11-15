import logging
import os
import time
from typing import Generator

from server.util.config import settings

logger = logging.getLogger('server.pipelines.files')


def stream_log(task_id: str, max_fails: int = 30, lookback: int = 500) -> Generator[str, None, None]:
    # Construct path to logfile
    filename = settings.PIPES.target_dir / task_id / 'progress.log'

    # Check if logfile exists
    if not filename.exists():
        raise StopIteration

    with open(filename, 'r+') as file:
        # find the size of the file
        st_results = os.stat(filename)
        st_size = st_results[6]

        # jump to almost the end of the file
        file.seek(max(0, st_size - lookback))
        # for line in file:
        #     yield line.strip()

        logger.debug(f'Going to stream from {filename} starting at {max(0, st_size - lookback)}/{st_size}')
        fail_count = 0
        while True:
            # remember where we are now
            where = file.tell()
            # try to read a line
            line = file.readline()
            yield line

            # no full new line yet, remember we failed, sleep, and jump back where we last started
            if not line:
                fail_count += 1
                logger.debug(f'No full line, increasing fail count to {fail_count}')
                time.sleep(1)
                file.seek(where)

            # found new line, yield and reset fail counter
            else:
                fail_count = 0
                logger.debug(f'Yielding line: {line.strip()}')
                yield line.strip()

            # we haven't seen anything new for 30 seconds, considering done!
            if fail_count > max_fails:
                logger.debug('Reached max fails, stopping log streaming')
                raise StopIteration


def get_log(task_id: str) -> str | None:
    """
    Get the contents of the log file as a string.
    """
    file_pointer = settings.PIPES.target_dir / task_id / 'progress.log'
    if not file_pointer.exists():
        return None
    with open(file_pointer, 'r') as f:
        return f.read()
