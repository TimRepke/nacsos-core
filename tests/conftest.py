"""
Shared pytest fixtures for the NACSOS API test suite.

The suite runs without PostgreSQL, Redis or any other external service:
the database engine and the authentication helper are replaced by in-process
test doubles for every test (see the autouse fixtures below).
"""

import logging
import os
from typing import Any, AsyncIterator

import httpx
import pytest

from nacsos_data.models.users import AuthTokenModel, UserModel
from nacsos_data.util.auth import InsufficientPermissionError, InvalidCredentialsError

from server.__main__ import app as fastapi_app
from server.util.security import UserPermissions, auth_helper

from tests.factories import (
    INACTIVE_TOKEN,
    NO_PERMISSION_TOKEN,
    PROJECT_ID,
    SUPERUSER_TOKEN,
    USERNAME,
    USER_TOKEN,
    make_project_permissions,
    make_superuser,
    make_user,
)
from tests.mocks import FakeDatabase, install_fake_database

BASE_URL = 'http://testserver'

# Keep the (expected) error tracebacks of the ErrorHandlingMiddleware out of test output
# unless the suite is explicitly run with a log level.
logging.getLogger('nacsos').setLevel(os.environ.get('NACSOS_TEST_LOG_LEVEL', 'CRITICAL'))


@pytest.fixture(scope='session')
def app() -> Any:
    """The real FastAPI application (middlewares and routers included)."""
    return fastapi_app


class PermissionBroker:
    """
    Controls what ``auth_helper.check_permissions`` answers.

    By default the user has full (owner) access to ``PROJECT_ID``; everything else
    is rejected. Tests can widen or narrow access explicitly.
    """

    def __init__(self) -> None:
        self.grants: dict[str, Any] = {}

    def grant(self, project_id: str, **permission_flags: Any) -> Any:
        self.grants[str(project_id)] = make_project_permissions(project_id=project_id, **permission_flags)
        return self.grants[str(project_id)]

    def deny(self, project_id: str) -> None:
        """The user has no permission record for this project at all."""
        self.grants.pop(str(project_id), None)

    def __getitem__(self, project_id: str) -> Any:
        return self.grants[str(project_id)]

    def __contains__(self, project_id: str) -> bool:
        return str(project_id) in self.grants

    async def check(
        self,
        project_id: Any = None,
        user_id: Any = None,
        username: str | None = None,
        user: UserModel | None = None,
        required_permissions: Any = None,
        fulfill_all: bool = True,
    ) -> UserPermissions:
        if user is None:
            raise InvalidCredentialsError('No user given')
        permissions = self.grants.get(str(project_id))
        if permissions is None:
            raise InsufficientPermissionError(f'No permissions for project {project_id}')
        if required_permissions and not permissions.owner:
            required = [required_permissions] if isinstance(required_permissions, str) else list(required_permissions)
            granted = [bool(getattr(permissions, perm, False)) for perm in required]
            if (all(granted) if fulfill_all else any(granted)) is False:
                raise InsufficientPermissionError(f'Missing permissions: {[p for p, ok in zip(required, granted, strict=True) if not ok]}')
        return UserPermissions(user=user, permissions=permissions)


@pytest.fixture
def permissions() -> PermissionBroker:
    broker = PermissionBroker()
    broker.grant(PROJECT_ID)  # everything else, incl. FORBIDDEN_PROJECT_ID, stays ungranted
    return broker


@pytest.fixture(autouse=True)
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeDatabase:
    """Replace the shared db_engine with a scripted in-memory double for every test."""
    database = FakeDatabase()
    return install_fake_database(monkeypatch, database)


PASSWORD = 'testpassword'  # accepted by FakeAuth.check_password


class FakeAuth:
    """Token-table based stand-in for ``auth_helper`` (no database lookups)."""

    def __init__(self, permissions: PermissionBroker) -> None:
        self.users: dict[str, UserModel] = {
            USER_TOKEN: make_user(),
            SUPERUSER_TOKEN: make_superuser(),
            INACTIVE_TOKEN: make_user(username='inactiveuser', is_active=False),
            NO_PERMISSION_TOKEN: make_user(user_id='cccccccc-3333-4444-aaaa-000000000000', username='outsider'),
        }
        self.permissions = permissions

    async def get_user(self, token_id: Any = None, username: str | None = None, user_id: Any = None) -> UserModel:
        if token_id is not None and str(token_id) in self.users:
            return self.users[str(token_id)]
        for user in self.users.values():
            if (username is not None and user.username == username) or (user_id is not None and str(user.user_id) == str(user_id)):
                return user
        raise InvalidCredentialsError('Invalid token')

    async def check_password(self, plain_password: str, username: str | None = None, user_id: Any = None) -> UserModel:
        if username == USERNAME and plain_password == PASSWORD:
            return self.users[USER_TOKEN]
        raise InvalidCredentialsError('username, user_id, or password wrong!')

    async def refresh_or_create_token(self, username: str | None = None, token_id: Any = None, **kwargs: Any) -> AuthTokenModel:
        return AuthTokenModel(token_id=token_id or USER_TOKEN, username=username or USERNAME)

    async def clear_token_by_id(self, token_id: Any = None, verify_username: str | None = None) -> None:
        return None

    async def clear_tokens_by_user(self, username: str) -> None:
        return None


def install_fake_auth(monkeypatch: pytest.MonkeyPatch, auth: FakeAuth) -> FakeAuth:
    for name in ('get_user', 'check_password', 'refresh_or_create_token', 'clear_token_by_id', 'clear_tokens_by_user'):
        monkeypatch.setattr(auth_helper, name, getattr(auth, name))
    monkeypatch.setattr(auth_helper, 'check_permissions', auth.permissions.check)
    return auth


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch: pytest.MonkeyPatch, permissions: PermissionBroker) -> dict[str, UserModel]:
    """
    Make authentication work off a fixed token table instead of the database.

    Unknown or missing tokens raise InvalidCredentialsError, which the API turns into a 401.
    """
    auth = install_fake_auth(monkeypatch, FakeAuth(permissions))
    return auth.users


def _make_client(app: Any, token: str | None = None, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    final_headers: dict[str, str] = dict(headers or {})
    if token is not None:
        final_headers['Authorization'] = f'Bearer {token}'
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        headers=final_headers,
        follow_redirects=True,
        event_hooks={'request': [_record_api_call]},
    )


# Filled with (METHOD, concrete path) for every request issued through the client fixtures.
# tests/api/test_api_coverage.py asserts that this covers every operation of the OpenAPI schema.
API_CALLS: set[tuple[str, str]] = set()

# name of the coverage assertion, kept last by pytest_collection_modifyitems below
COVERAGE_TEST_NAME = 'test_every_documented_operation_is_exercised'


async def _record_api_call(request: httpx.Request) -> None:
    API_CALLS.add((request.method, request.url.path))


@pytest.fixture
def make_client(app: Any) -> Any:
    """Factory for authenticated clients: ``make_client(token, project_id=...)``."""

    def factory(token: str | None = USER_TOKEN, project_id: str | None = PROJECT_ID, **headers: str) -> httpx.AsyncClient:
        final: dict[str, str] = dict(headers)
        if project_id is not None:
            final['X-Project-Id'] = project_id
        return _make_client(app, token=token, headers=final)

    return factory


@pytest.fixture
async def client(make_client: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Logged in as the regular project member, with X-Project-Id set."""
    async with make_client() as instance:
        yield instance


@pytest.fixture
async def superuser_client(make_client: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Logged in as a platform superuser, with X-Project-Id set."""
    async with make_client(SUPERUSER_TOKEN) as instance:
        yield instance


@pytest.fixture
async def inactive_client(make_client: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Logged in with a deactivated account (passes authentication, fails the active check)."""
    async with make_client(INACTIVE_TOKEN) as instance:
        yield instance


@pytest.fixture
async def anon_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """No credentials at all (project header still sent, so auth failures surface as 401)."""
    async with _make_client(app, token=None, headers={'X-Project-Id': PROJECT_ID}) as instance:
        yield instance


@pytest.fixture
async def bare_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """No credentials and no project header."""
    async with _make_client(app, token=None) as instance:
        yield instance


@pytest.fixture
def no_project_client(make_client: Any) -> Any:
    """Authenticated client without the X-Project-Id header (for 422 permission-dependency tests)."""
    return make_client(project_id=None)


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """
    Run the endpoint-coverage check last, after every other test has issued its requests.

    ``API_CALLS`` is only complete once the whole suite has run, so the coverage assertion
    must not be subject to the default file/test ordering.
    """
    coverage = [item for item in items if item.name == COVERAGE_TEST_NAME]
    if coverage:
        items[:] = [item for item in items if item.name != COVERAGE_TEST_NAME] + coverage
