import os
from zipfile import ZipFile, Path
from .errors import MissingFileError
from server.util.config import settings


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
