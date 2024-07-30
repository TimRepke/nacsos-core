import logging
import os
import time
from typing import Generator
from zipfile import ZipFile, Path

from server.util.config import settings
from .errors import MissingFileError

logger = logging.getLogger('server.pipelines.files')


def get_outputs_flat(task_id: str, include_fsize: bool = True) -> list[tuple[str, int] | str]:
    """
    Get a list of all files associated with task `task_id`—optionally including the filesize.
    The list is not nested, so if there are folders, it will still return a flattened list.
    """
    base = settings.PIPES.target_dir / task_id
    if not base.exists():
        raise MissingFileError(f'No outputs yet for {task_id} at {base}')
    ret: list[tuple[str, int] | str] = []
    for root, dirs, files in os.walk(base):
        for file in files:
            if include_fsize:
                ret.append((f'{root[len(str(settings.PIPES.target_dir)) + 1:]}/{file}',
                            os.path.getsize(f'{root}/{file}')))
            else:
                ret.append(f'{root[len(str(settings.PIPES.target_dir)) + 1:]}/{file}')
    return ret


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


def delete_files(task_id: str, files: list[str]) -> None:
    """
    Delete all files with a certain name (`files`) related to the task with `task_id`.
    """
    for file in files:
        fp = (settings.PIPES.target_dir / task_id / file).resolve()
        if fp.is_file():
            fp.unlink()
        else:
            raise MissingFileError(f'Can\'t delete missing file: {fp}')


def delete_task_directory(task_id: str) -> None:
    """
    Delete all files (and the folder) related to the task with `task_id`.
    """
    fp = (settings.PIPES.target_dir / task_id).resolve()
    if fp.is_dir():
        fp.rmdir()
    else:
        raise MissingFileError(f'Can\'t delete missing folder: {fp}')


def zip_files(abs_filenames: list[str], target_file: str | Path) -> None:
    """
    Write the list of files (`abs_filenames`) to an archive (`target_file`) and zip it.
    """
    with ZipFile(file=str(target_file), mode='w') as zip_file:
        for abs_filename in abs_filenames:
            zip_file.write(abs_filename)


def zip_folder(task_id: str, target_file: str) -> None:
    """
    Write all files produces by a task (`task_id`) into a zip archive (`target_file`).
    """
    files_ = []
    base = (settings.PIPES.target_dir / task_id).resolve()
    for root, dirs, files in os.walk(base):
        for file in files:
            files_.append(f'{root}/{file}')
    zip_files(files_, target_file=target_file)
