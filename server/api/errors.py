class DataNotFoundWarning(Warning):
    pass


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
    pass


class AssignmentScopeNotFoundError(Exception):
    pass


class SaveFailedError(Exception):
    pass


class UnknownEventError(Exception):
    pass


class MissingInformationError(Exception):
    pass
