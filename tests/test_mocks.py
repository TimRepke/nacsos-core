"""
Tests for the test doubles themselves.

These guard the semantics that every API test silently relies on — in particular the
sync/async split of ``AsyncSession`` (``add`` is a plain method, everything else is awaited)
and the FIFO result scripting of :class:`tests.mocks.FakeDatabase`.
"""

import inspect
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import delete, select

from nacsos_data.db.engine import DBSession
from nacsos_data.db.schemas import Project
from nacsos_data.models.users import UserBaseModel
from nacsos_data.util.auth import InsufficientPermissionError, InvalidCredentialsError

from server.data import db_engine
from server.util.security import UserPermissions, auth_helper

from tests.factories import PROJECT_ID, USER_ID, USERNAME, USER_TOKEN, SUPERUSER_TOKEN, db_project, db_user, make_user
from tests.mocks import FakeDatabase, FakeResult, FakeSession, MultipleResultsFound, patch_crud


def _is_coroutine_method(cls: type, name: str) -> bool:
    member = getattr(cls, name, None)
    return member is not None and inspect.iscoroutinefunction(member)


class TestFakeResult:
    def test_views_are_chainable(self):
        result = FakeResult(rows=[1, 2, 3])
        assert result.scalars().mappings().unique().all() == [1, 2, 3]
        assert list(result) == [1, 2, 3]
        assert len(result) == 3 and bool(result) is True

    def test_terminal_accessors(self):
        assert FakeResult(rows=['a', 'b']).first() == 'a'
        assert FakeResult(rows=['a']).one_or_none() == 'a'
        assert FakeResult(rows=['a']).one() == 'a'
        assert FakeResult(rows=[]).first() is None
        assert FakeResult(rows=[]).one_or_none() is None
        assert FakeResult().all() == []

    def test_one_or_none_rejects_multiple_rows(self):
        with pytest.raises(MultipleResultsFound):
            FakeResult(rows=['a', 'b']).one_or_none()

    def test_one_requires_exactly_one_row(self):
        with pytest.raises(MultipleResultsFound):
            FakeResult(rows=[]).one()

    def test_scalar_prefers_the_explicit_value_and_falls_back_to_the_first_row(self):
        assert FakeResult(rows=[7, 8]).scalar() == 7
        assert FakeResult(rows=[], scalar=0).scalar() == 0
        assert FakeResult(rows=[]).scalar() is None


class TestFakeDatabase:
    async def test_results_are_handed_out_in_fifo_order(self):
        db = FakeDatabase()
        db.queue(FakeResult(rows=['first']), FakeResult(rows=['second']))
        async with db.session() as session:
            assert (await session.execute(select(Project))).all() == ['first']
            assert (await session.execute(select(Project))).all() == ['second']

    async def test_default_result_is_used_once_the_queue_drains(self):
        db = FakeDatabase()
        db.set_default_rows('fallback')
        async with db.session() as session:
            assert (await session.execute(select(Project))).all() == ['fallback']
            assert (await session.execute(select(Project))).all() == ['fallback']

    async def test_rows_can_be_queued_without_wrapping_them(self):
        db = FakeDatabase()
        db.queue_rows(1, 2)
        async with db.session() as session:
            assert (await session.scalars(select(Project))).all() == [1, 2]

    async def test_every_execution_is_recorded(self):
        db = FakeDatabase()
        async with db.session() as session:
            await session.execute(select(Project).where(Project.project_id == PROJECT_ID))
            await session.execute(delete(Project))

        assert len(db.executions) == 2
        assert [e.table_name for e in db.executions] == ['project', 'project']
        assert db.statements()[0].startswith('SELECT')
        assert db.statements()[1].startswith('DELETE')

    async def test_session_context_manager_counts_rollback_on_error(self):
        db = FakeDatabase()
        with pytest.raises(RuntimeError):
            async with db.session():
                raise RuntimeError('boom')
        assert db.rollbacks == 1

    async def test_transaction_counters(self):
        db = FakeDatabase()
        async with db.session() as session:
            await session.commit()
            await session.flush()
            await session.flush_or_commit()
            await session.delete('row')
        assert (db.commits, db.flushes) == (1, 2)
        assert db.deleted == ['row']

    async def test_engine_connections_share_the_script(self):
        db = FakeDatabase()
        db.queue(FakeResult(scalar=12))
        async with db.engine.connect() as conn:
            assert (await conn.execute(select(Project))).scalar() == 12
        assert len(db.executions) == 1


class TestSessionMirrorsAsyncSession:
    """The double must not differ from SQLAlchemy where routes touch it."""

    @pytest.mark.parametrize('method', ['add', 'add_all'])
    def test_collection_methods_are_synchronous(self, method: str):
        assert _is_coroutine_method(DBSession, method) is False, 'AsyncSession.add/add_all are not coroutines'
        assert _is_coroutine_method(FakeSession, method) is False

    def test_add_records_instances_without_awaiting(self):
        db = FakeDatabase()
        session = FakeSession(db)
        session.add('one')
        session.add_all(['two', 'three'])
        assert db.added == ['one', 'two', 'three']

    @pytest.mark.parametrize(
        'method',
        ['execute', 'scalars', 'scalar', 'commit', 'flush', 'flush_or_commit', 'refresh', 'merge', 'get', 'delete'],
    )
    def test_awaitable_methods_match_the_real_session(self, method: str):
        assert _is_coroutine_method(DBSession, method) is _is_coroutine_method(FakeSession, method), f'DBSession.{method} mismatch'

    async def test_get_returns_the_object_not_a_result_wrapper(self):
        db = FakeDatabase()
        project = db_project()
        db.queue(FakeResult(rows=[project]))
        async with db.session() as session:
            assert await session.get(Project, PROJECT_ID) is project

    async def test_get_returns_none_when_nothing_is_queued(self):
        db = FakeDatabase()
        async with db.session() as session:
            assert await session.get(Project, PROJECT_ID) is None

    def test_transient_rows_work_like_orm_rows_for_pydantic(self):
        """Routes do ``SomeModel.model_validate(orm_row.__dict__)``; transient rows must support that."""
        row = db_user(username='jane')
        assert UserBaseModel(**row.__dict__).username == 'jane'
        assert UserBaseModel.model_validate(row.__dict__).username == 'jane'


class TestPatchCrud:
    @pytest.fixture
    def crud_module(self) -> Any:
        module = ModuleType('fake_crud_module')

        async def read_users(**kwargs: Any) -> str:
            return 'original'

        module.read_users = read_users  # type: ignore[attr-defined]
        return module

    async def test_constants_are_wrapped_into_a_coroutine(self, monkeypatch, crud_module):
        patch_crud(monkeypatch, crud_module, read_users=['x'])
        assert await crud_module.read_users(project_id=PROJECT_ID) == ['x']

    async def test_coroutine_replacements_are_used_as_is(self, monkeypatch, crud_module):
        seen: dict = {}

        async def replacement(**kwargs: Any) -> str:
            seen.update(kwargs)
            return 'called'

        patch_crud(monkeypatch, crud_module, read_users=replacement)
        assert await crud_module.read_users(project_id='p1') == 'called'
        assert seen == {'project_id': 'p1'}

    async def test_originals_are_reported_back(self, monkeypatch, crud_module):
        original = crud_module.read_users
        assert patch_crud(monkeypatch, crud_module, read_users='replaced') == {'read_users': original}
        assert crud_module.read_users is not original

    def test_unknown_name_is_an_error(self, monkeypatch, crud_module):
        with pytest.raises(AttributeError, match='no attribute'):
            patch_crud(monkeypatch, crud_module, not_a_crud_function=True)


class TestInstalledDoubles:
    async def test_shared_engine_singleton_points_at_the_double(self, fake_db):
        # bound methods are rebuilt on every attribute access, so compare the underlying function
        assert db_engine.session.__func__ is FakeDatabase.session
        assert db_engine.engine is fake_db.engine
        await db_engine.startup()

    def test_auth_fake_serves_the_fixed_token_table(self, fake_auth):
        assert {USER_TOKEN, SUPERUSER_TOKEN} <= set(fake_auth)
        assert fake_auth[USER_TOKEN].username == USERNAME
        assert fake_auth[SUPERUSER_TOKEN].is_superuser is True

    async def test_unknown_credentials_are_rejected(self):
        with pytest.raises(InvalidCredentialsError):
            await auth_helper.get_user(token_id='nope')
        with pytest.raises(InvalidCredentialsError):
            await auth_helper.check_password(plain_password='wrong', username=USERNAME)


class TestPermissionBroker:
    async def test_granted_project_returns_user_permissions(self, permissions):
        result = await permissions.check(project_id=PROJECT_ID, user=make_user(), required_permissions=['dataset_read'])
        assert isinstance(result, UserPermissions)
        assert result.permissions.dataset_read is True

    async def test_ungranted_project_is_rejected(self, permissions):
        with pytest.raises(InsufficientPermissionError):
            await permissions.check(project_id='00000000-0000-0000-0000-000000000000', user=make_user())

    async def test_deny_removes_an_existing_grant(self, permissions):
        permissions.deny(PROJECT_ID)
        assert PROJECT_ID not in permissions
        with pytest.raises(InsufficientPermissionError):
            await permissions.check(project_id=PROJECT_ID, user=make_user())

    async def test_missing_flag_is_enforced_unless_owner(self, permissions):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=False)
        with pytest.raises(InsufficientPermissionError):
            await permissions.check(project_id=PROJECT_ID, user=make_user(), required_permissions=['dataset_edit'])

    async def test_owner_bypasses_the_flag_check(self, permissions):
        permissions.grant(PROJECT_ID, owner=True, dataset_edit=False)
        result = await permissions.check(project_id=PROJECT_ID, user=make_user(), required_permissions=['dataset_edit'])
        assert result.permissions.owner is True

    async def test_any_semantics_with_fulfill_all_false(self, permissions):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=False, dataset_read=True)
        result = await permissions.check(project_id=PROJECT_ID, user=make_user(), required_permissions=['dataset_edit', 'dataset_read'], fulfill_all=False)
        assert str(result.permissions.user_id) == USER_ID

    async def test_missing_user_is_invalid_credentials(self, permissions):
        with pytest.raises(InvalidCredentialsError):
            await permissions.check(project_id=PROJECT_ID, user=None)
