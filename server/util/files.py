import os
from pathlib import Path
from zipfile import ZipFile


class MissingFileError(FileNotFoundError):
    """
    Essentially just a wrapper for FileNotFoundError
    """
    pass


def get_outputs_flat(root: Path, base: Path, include_fsize: bool = True) -> list[tuple[str, int] | str]:
    """
    Get a list of all files associated with task `task_id`—optionally including the filesize.
    The list is not nested, so if there are folders, it will still return a flattened list.
    """
    if not root.exists():
        raise MissingFileError(f'No outputs yet at {root}')
    ret: list[tuple[str, int] | str] = []
    for walk_root, _dirs, files in os.walk(str(root)):
        for file in files:
            if include_fsize:
                ret.append((f'{walk_root[len(str(base)) + 1:]}/{file}',
                            os.path.getsize(f'{walk_root}/{file}')))
            else:
                ret.append(f'{walk_root[len(str(base)) + 1:]}/{file}')
    return ret


def delete_files(base: Path, files: list[str]) -> None:
    """
    Delete all files with a certain name (`files`) related to the task with `task_id`.
    """
    for file in files:
        fp = (base / file).resolve()
        if fp.is_file():
            fp.unlink()
        else:
            raise MissingFileError(f'Can\'t delete missing file: {fp}')


def delete_directory(path: Path) -> None:
    """
    Delete all files (and the folder) related to the task with `task_id`.
    """
    fp = path.resolve()
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


def zip_folder(path: Path, target_file: str) -> None:
    """
    Write all files produces by a task (`task_id`) into a zip archive (`target_file`).
    """
    files_ = []
    base = path.resolve()
    for root, _dirs, files in os.walk(base):
        for file in files:
            files_.append(f'{root}/{file}')
    zip_files(files_, target_file=target_file)
