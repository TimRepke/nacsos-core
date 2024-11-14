import logging

logger = logging.getLogger('server.util.pipelines')


class SameFingerprintWarning(UserWarning):
    """
    Thrown when a task is submitted but another task with the same fingerprint already exists.
    """
    pass


class TaskNotPendingWarning(UserWarning):
    """
    Thrown when a task is attempted to be run, but it's not pending.
    """
    pass


class TaskSubmissionFailed(Exception):
    """
    Thrown if it seems like the submission of a new task to the queue failed.
    """
    pass


class UnknownTaskID(Exception):
    """
    Thrown when the requested task_id is not in the database.
    """
    pass


class UnknownLibraryFunction(Exception):
    """
    Thrown when a library lookup fails because the requested function name is not found.
    """
    pass


class InvalidTaskInstance(Exception):
    """
    Thrown when the SubmittedTask or TaskInDB is not valid (e.g. missing or conflicting attributes).
    """
    pass


class ProcessCancelled(Exception):
    """
    Thrown when a process is supposed to be resolved but was cancelled before.
    """
    pass


class ProcessNotDone(Exception):
    """
    Thrown when a process is supposed to be resolved but is not done yet.
    """
    pass


class ProcessIncomplete(Exception):
    """
    Thrown when a process is supposed to be resolved but is set as "COMPLETED".
    """
    pass


class LibraryFunctionNotFoundError(Exception):
    """
    Thrown when a library function lookup failed.
    """
    pass
