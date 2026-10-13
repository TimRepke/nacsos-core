"""
Builders for the test data used across the API tests.

Everything here works without a database: pydantic models are built directly,
ORM rows are built as transient (never flushed) SQLAlchemy objects.
"""

from typing import Any
from uuid import UUID, uuid4

from nacsos_data.db.schemas import (
    AnnotationScheme,
    Assignment,
    AssignmentScope,
    AuthToken,
    Import,
    Project,
    ProjectPermissions,
    User,
)
from nacsos_data.models.projects import ProjectModel, ProjectPermissionsModel
from nacsos_data.models.users import AuthTokenModel, UserBaseModel, UserModel

from server.util.security import UserPermissions

# ---------------------------------------------------------------- fixed ids
# Stable ids keep assertions readable and failures reproducible.
USER_ID = '11111111-1111-1111-1111-111111111111'
SUPERUSER_ID = '22222222-2222-2222-2222-222222222222'
OTHER_USER_ID = '33333333-3333-3333-3333-333333333333'
BOT_USER_ID = '44444444-4444-4444-4444-444444444444'

PROJECT_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OTHER_PROJECT_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
FORBIDDEN_PROJECT_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'

TOKEN_ID = '55555555-5555-5555-5555-555555555555'
OTHER_TOKEN_ID = '16161616-1616-1616-1616-161616161616'
ITEM_ID = '66666666-6666-6666-6666-666666666666'
SCHEME_ID = '77777777-7777-7777-7777-777777777777'
SCOPE_ID = '88888888-8888-8888-8888-888888888888'
ASSIGNMENT_ID = '99999999-9999-9999-9999-999999999999'
IMPORT_ID = '10101010-1010-1010-1010-101010101010'
HIGHLIGHTER_ID = '12121212-1212-1212-1212-121212121212'
PRIORITY_ID = '13131313-1313-1313-1313-131313131313'
PERMISSION_ID = '14141414-1414-1414-1414-141414141414'
TRACKER_ID = '15151515-1515-1515-1515-151515151515'

# Auth tokens understood by the fake authenticator in conftest.py
USER_TOKEN = 'test-user-token'
SUPERUSER_TOKEN = 'test-superuser-token'
INACTIVE_TOKEN = 'test-inactive-token'
NO_PERMISSION_TOKEN = 'test-no-permission-token'

USERNAME = 'testuser'
SUPERUSER_NAME = 'testadmin'


def new_id() -> str:
    return str(uuid4())


# ------------------------------------------------------------------- models
def make_user(
    user_id: str = USER_ID,
    username: str = USERNAME,
    is_superuser: bool = False,
    is_active: bool = True,
    **overrides: Any,
) -> UserModel:
    return UserModel(
        user_id=user_id,
        username=username,
        email=f'{username}@example.org',
        full_name=username.title(),
        affiliation='ACME',
        is_superuser=is_superuser,
        is_active=is_active,
        setting_newsletter=False,
        **overrides,
    )


def make_superuser(**overrides: Any) -> UserModel:
    defaults: dict[str, Any] = {'user_id': SUPERUSER_ID, 'username': SUPERUSER_NAME, 'is_superuser': True}
    defaults.update(overrides)
    return make_user(**defaults)


def make_dehydrated_user(user_id: str = USER_ID, username: str = USERNAME, **overrides: Any) -> UserBaseModel:
    return UserBaseModel(user_id=user_id, username=username, full_name=username.title(), **overrides)


def make_token(token_id: str = TOKEN_ID, username: str = USERNAME, **overrides: Any) -> AuthTokenModel:
    return AuthTokenModel(token_id=token_id, username=username, **overrides)


def make_project_permissions(
    project_id: str = PROJECT_ID,
    user_id: str = USER_ID,
    owner: bool = True,
    **overrides: Any,
) -> ProjectPermissionsModel:
    """Full access by default; pass explicit flags (owner=False) to narrow them down."""
    permissions: dict[str, Any] = {
        'project_permission_id': PERMISSION_ID,
        'project_id': project_id,
        'user_id': user_id,
        'owner': owner,
        'dataset_read': True,
        'dataset_edit': True,
        'imports_read': True,
        'imports_edit': True,
        'annotations_read': True,
        'annotations_edit': True,
        'annotations_prio': True,
        'pipelines_read': True,
        'pipelines_edit': True,
        'artefacts_read': True,
        'artefacts_edit': True,
        'search_dimensions': True,
        'search_oa': True,
        'import_limit_oa': 1000,
    }
    permissions.update(overrides)
    return ProjectPermissionsModel(**permissions)


def make_user_permissions(
    project_id: str = PROJECT_ID,
    user: UserModel | None = None,
    **permission_overrides: Any,
) -> UserPermissions:
    resolved = user or make_user()
    return UserPermissions(
        user=resolved,
        permissions=make_project_permissions(project_id=project_id, user_id=str(resolved.user_id or USER_ID), **permission_overrides),
    )


# ------------------------------------------------------------------ ORM rows
def orm_obj(model: Any, **columns: Any) -> Any:
    """Build a transient ORM object (never attached to a session)."""
    return model(**columns)


def db_user(
    user_id: str = USER_ID,
    username: str = USERNAME,
    email: str | None = None,
    full_name: str | None = None,
    **columns: Any,
) -> User:
    values: dict[str, Any] = {
        'user_id': UUID(user_id),
        'username': username,
        'email': email or f'{username}@example.org',
        'full_name': full_name or username.title(),
        'affiliation': 'ACME',
        'is_active': True,
        'is_superuser': False,
        'setting_newsletter': False,
    }
    values.update(columns)
    return orm_obj(User, **values)


def db_superuser(**columns: Any) -> User:
    defaults: dict[str, Any] = {'user_id': SUPERUSER_ID, 'username': SUPERUSER_NAME, 'is_superuser': True}
    defaults.update(columns)
    return db_user(**defaults)


def db_auth_token(token_id: str = TOKEN_ID, username: str = USERNAME, **columns: Any) -> AuthToken:
    return orm_obj(AuthToken, token_id=UUID(token_id), username=username, **columns)


def db_project(project_id: str = PROJECT_ID, name: str = 'Test Project', type: str = 'academic', **columns: Any) -> Project:
    return orm_obj(Project, project_id=UUID(project_id), name=name, type=type, **columns)


def db_project_permissions(project_id: str = PROJECT_ID, user_id: str = USER_ID, **columns: Any) -> ProjectPermissions:
    values: dict[str, Any] = {
        'project_id': UUID(project_id),
        'user_id': UUID(user_id),
        'owner': True,
        'dataset_read': True,
        'dataset_edit': True,
        'imports_read': True,
        'imports_edit': True,
        'annotations_read': True,
        'annotations_edit': True,
        'annotations_prio': False,
        'pipelines_read': False,
        'pipelines_edit': False,
        'artefacts_read': False,
        'artefacts_edit': False,
        'search_dimensions': False,
        'search_oa': False,
        'import_limit_oa': 0,
    }
    values.update(columns)
    return orm_obj(ProjectPermissions, **values)


def db_scheme(project_id: str = PROJECT_ID, annotation_scheme_id: str = SCHEME_ID, **columns: Any) -> AnnotationScheme:
    values: dict[str, Any] = {
        'annotation_scheme_id': UUID(annotation_scheme_id),
        'project_id': UUID(project_id),
        'name': 'Test Scheme',
    }
    values.update(columns)
    return orm_obj(AnnotationScheme, **values)


def db_scope(annotation_scheme_id: str = SCHEME_ID, assignment_scope_id: str = SCOPE_ID, **columns: Any) -> AssignmentScope:
    values: dict[str, Any] = {
        'assignment_scope_id': UUID(assignment_scope_id),
        'annotation_scheme_id': UUID(annotation_scheme_id),
        'name': 'Test Scope',
        'description': 'Test Scope',
    }
    values.update(columns)
    return orm_obj(AssignmentScope, **values)


def db_assignment(
    assignment_id: str = ASSIGNMENT_ID,
    assignment_scope_id: str = SCOPE_ID,
    user_id: str = USER_ID,
    item_id: str = ITEM_ID,
    annotation_scheme_id: str = SCHEME_ID,
    **columns: Any,
) -> Assignment:
    values: dict[str, Any] = {
        'assignment_id': UUID(assignment_id),
        'assignment_scope_id': UUID(assignment_scope_id),
        'annotation_scheme_id': UUID(annotation_scheme_id),
        'user_id': UUID(user_id),
        'item_id': UUID(item_id),
    }
    values.update(columns)
    return orm_obj(Assignment, **values)


def db_import(
    import_id: str = IMPORT_ID,
    project_id: str = PROJECT_ID,
    user_id: str = USER_ID,
    name: str = 'Test Import',
    type: str = 'academic',
    **columns: Any,
) -> Import:
    values: dict[str, Any] = {
        'import_id': UUID(import_id),
        'project_id': UUID(project_id),
        'user_id': UUID(user_id),
        'name': name,
        'type': type,
        'description': 'Test Import',
    }
    values.update(columns)
    return orm_obj(Import, **values)


def project_model(project_id: str = PROJECT_ID, name: str = 'Test Project', type: str = 'academic', **overrides: Any) -> ProjectModel:
    return ProjectModel(project_id=project_id, name=name, type=type, **overrides)
