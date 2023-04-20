from fastapi import status as http_status


class DataNotFoundWarning(Warning):
    status = http_status.HTTP_204_NO_CONTENT


class NoDataForKeyError(Exception):
    pass


class ItemNotFoundError(Exception):
    pass


class ProjectNotFoundError(Exception):
    status = 400


class UserNotFoundError(Exception):
    pass


class AnnotationSchemeNotFoundError(Exception):
    pass


class NoNextAssignmentWarning(Warning):
    status = http_status.HTTP_204_NO_CONTENT


class AssignmentScopeNotFoundError(Exception):
    pass


class SaveFailedError(Exception):
    pass


class UnknownEventError(Exception):
    pass


class MissingInformationError(Exception):
    pass
