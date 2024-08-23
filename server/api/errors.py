from fastapi import status as http_status


class UserPermissionError(Exception):
    status = http_status.HTTP_403_FORBIDDEN


class DataNotFoundWarning(Warning):
    status = http_status.HTTP_204_NO_CONTENT


class NoDataForKeyError(Exception):
    status = http_status.HTTP_410_GONE


class ItemNotFoundError(Exception):
    status = http_status.HTTP_404_NOT_FOUND


class ProjectNotFoundError(Exception):
    status = http_status.HTTP_400_BAD_REQUEST


class UserNotFoundError(Exception):
    pass


class AnnotationSchemeNotFoundError(Exception):
    pass


class NoNextAssignmentWarning(Warning):
    status = http_status.HTTP_204_NO_CONTENT


class RemainingDependencyWarning(Warning):
    status = http_status.HTTP_412_PRECONDITION_FAILED


class AssignmentScopeNotFoundError(Exception):
    pass


class SaveFailedError(Exception):
    pass


class UnknownEventError(Exception):
    pass


class MissingInformationError(Exception):
    pass
