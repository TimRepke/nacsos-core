"""Tests for the /api/project/permissions endpoints (server/api/routes/project/permissions.py).

The router is included under ``/api/project`` with prefix ``/permissions``
(server/api/routes/project/__init__.py), so all paths below live under ``/api/project/permissions``.
"""

import uuid

import pytest

from nacsos_data.db.schemas import ProjectPermissions

from server.api.routes.project import permissions as project_permissions_route
from server.data import db_engine

from tests.factories import FORBIDDEN_PROJECT_ID, OTHER_USER_ID, PERMISSION_ID, PROJECT_ID, USER_ID, db_project_permissions, make_project_permissions
from tests.mocks import patch_crud

BASE = '/api/project/permissions'


def _perm_row(user_id: str = OTHER_USER_ID, **flags):
    """Transient project_permissions row; the factory hard-codes the flags, so tweak them afterwards."""
    row = db_project_permissions(user_id=user_id)
    for key, value in flags.items():
        setattr(row, key, value)
    return row


def _perm_payload(**overrides):
    payload = {'project_id': PROJECT_ID, 'user_id': OTHER_USER_ID}
    payload.update(overrides)
    return payload


class TestGetMyPermissions:
    async def test_returns_the_current_grant(self, client):
        response = await client.get(f'{BASE}/me')
        assert response.status_code == 200
        body = response.json()
        assert body['project_id'] == PROJECT_ID
        assert body['user_id'] == USER_ID
        assert body['project_permission_id'] == PERMISSION_ID
        assert body['owner'] is True
        assert body['import_limit_oa'] == 1000

    async def test_narrowed_grant_is_reflected(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=True, annotations_edit=False, import_limit_oa=0)
        response = await client.get(f'{BASE}/me')
        assert response.status_code == 200
        body = response.json()
        assert body['owner'] is False
        assert body['dataset_read'] is True
        assert body['annotations_edit'] is False

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/me')
        assert response.status_code == 401

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'{BASE}/me')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'{BASE}/me')
            assert response.status_code == 403
            assert response.json()['detail']['type'] == 'InsufficientPermissions'


class TestListProjectPermissions:
    async def test_returns_permissions_of_path_project(self, client, monkeypatch):
        listed_project_id = 'dddddddd-1111-2222-3333-444444444444'
        captured = {}

        async def fake_read(**kwargs):
            captured.update(kwargs)
            return [
                make_project_permissions(),
                make_project_permissions(project_id=listed_project_id, user_id=OTHER_USER_ID, owner=False),
            ]

        patch_crud(monkeypatch, project_permissions_route, read_project_permissions_for_project=fake_read)
        response = await client.get(f'{BASE}/list/{listed_project_id}')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert body[0]['owner'] is True
        assert body[1]['owner'] is False
        # The path parameter (not the X-Project-Id header) selects the permissions that are listed.
        assert captured['project_id'] == listed_project_id
        assert captured['engine'] is db_engine

    async def test_empty_result_is_an_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, project_permissions_route, read_project_permissions_for_project=[])
        response = await client.get(f'{BASE}/list/{PROJECT_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_unpatched_read_fails_validating_orm_rows(self, client, fake_db):
        # Latent bug in nacsos_data.db.crud.projects.read_project_permissions_for_project:
        # `ProjectPermissionsModel.model_validate(res)` receives ORM instances but the model
        # has no from_attributes config -> pydantic rejects them (400 via the error middleware).
        fake_db.queue_rows(_perm_row())
        response = await client.get(f'{BASE}/list/{PROJECT_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ValidationError'

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=True, imports_edit=True)
        response = await client.get(f'{BASE}/list/{PROJECT_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/list/{PROJECT_ID}')
        assert response.status_code == 401


class TestListUserPermissions:
    async def test_returns_permissions_with_embedded_user(self, client, fake_db):
        from tests.factories import db_user

        row = _perm_row(owner=False)
        row.user = db_user(user_id=OTHER_USER_ID, username='other')
        fake_db.queue_rows(row)
        response = await client.get(f'{BASE}/list-users')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['user_id'] == OTHER_USER_ID
        assert body[0]['owner'] is False
        assert body[0]['project_id'] == PROJECT_ID
        assert body[0]['user']['username'] == 'other'

    async def test_no_rows_yield_empty_list(self, client, fake_db):
        response = await client.get(f'{BASE}/list-users')
        assert response.status_code == 200
        assert response.json() == []

    async def test_query_filters_on_current_project(self, client, fake_db):
        fake_db.queue_rows()
        response = await client.get(f'{BASE}/list-users')
        assert response.status_code == 200
        # selectinload(...) on the statement confuses FakeExecution.table_name, so look at the SQL text.
        statement = fake_db.executions[0].statement
        assert 'FROM project_permissions' in str(statement)
        # The WHERE clause binds the X-Project-Id header value (the current grant, not a path id).
        assert str(PROJECT_ID) in str(statement.compile().params)

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=True)
        response = await client.get(f'{BASE}/list-users')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/list-users')
        assert response.status_code == 401


class TestSaveProjectPermission:
    async def test_creates_new_permission(self, client, fake_db, monkeypatch):
        response = await client.put(f'{BASE}/permission', json=_perm_payload(dataset_read=True))
        assert response.status_code == 200
        new_id = response.json()
        uuid.UUID(new_id)  # the route generates an id when the payload has none
        assert len(fake_db.added) == 1
        added = fake_db.added[0]
        assert isinstance(added, ProjectPermissions)
        assert str(added.project_permission_id) == new_id
        assert added.dataset_read is True
        assert str(added.user_id) == OTHER_USER_ID
        assert fake_db.commits == 1

    @pytest.mark.parametrize('flags', [{'annotations_prio': True}, {'search_dimensions': True}, {'import_limit_oa': 500}])
    async def test_regular_owner_cannot_grant_super_only_flags(self, client, fake_db, flags):
        response = await client.put(f'{BASE}/permission', json=_perm_payload(**flags))
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'
        assert fake_db.commits == 0

    async def test_superuser_may_grant_super_only_flags(self, superuser_client, fake_db, monkeypatch):
        response = await superuser_client.put(f'{BASE}/permission', json=_perm_payload(annotations_prio=True))
        assert response.status_code == 200
        assert fake_db.added[0].annotations_prio is True
        assert fake_db.commits == 1

    async def test_updates_existing_permission(self, client, fake_db):
        existing = _perm_row(owner=False)
        existing.dataset_edit = False
        existing.project_permission_id = uuid.UUID(PERMISSION_ID)
        fake_db.queue_rows(existing)
        response = await client.put(f'{BASE}/permission', json=_perm_payload(project_permission_id=PERMISSION_ID, dataset_edit=True, dataset_read=False))
        assert response.status_code == 200
        assert response.json() == PERMISSION_ID
        assert existing.dataset_edit is True
        assert existing.dataset_read is False
        # Fields missing from the payload are reset to their model defaults (owner is False on the row already).
        assert existing.owner is False
        assert fake_db.commits == 1
        assert fake_db.added == []

    async def test_regular_owner_cannot_escalate_existing_permission(self, client, fake_db):
        existing = _perm_row(owner=False)
        existing.project_permission_id = uuid.UUID(PERMISSION_ID)
        fake_db.queue_rows(existing)
        response = await client.put(f'{BASE}/permission', json=_perm_payload(project_permission_id=PERMISSION_ID, annotations_prio=True))
        assert response.status_code == 403
        assert existing.annotations_prio is False
        assert fake_db.commits == 0

    async def test_update_of_unknown_id_falls_back_to_create(self, client, fake_db, monkeypatch):
        # No existing row found -> the route drops into the "create" branch using the client-supplied id.
        response = await client.put(f'{BASE}/permission', json=_perm_payload(project_permission_id=PERMISSION_ID))
        assert response.status_code == 200
        assert response.json() == PERMISSION_ID
        assert str(fake_db.added[0].project_permission_id) == PERMISSION_ID
        assert fake_db.commits == 1

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=True)
        response = await client.put(f'{BASE}/permission', json=_perm_payload())
        assert response.status_code == 403

    async def test_body_without_ids_fails_validation(self, client):
        response = await client.put(f'{BASE}/permission', json={'dataset_read': True})
        assert response.status_code == 422
        missing = {issue['loc'][1] for issue in response.json()['detail'] if issue['type'] == 'missing'}
        assert missing == {'project_id', 'user_id'}

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put(f'{BASE}/permission', json=_perm_payload())
        assert response.status_code == 401


class TestDeleteProjectPermission:
    async def test_delegates_to_crud(self, client, monkeypatch):
        captured = {}

        async def fake_delete(**kwargs):
            captured.update(kwargs)

        patch_crud(monkeypatch, project_permissions_route, delete_project_permissions=fake_delete)
        response = await client.delete(f'{BASE}/permission', params={'project_permission_id': PERMISSION_ID})
        assert response.status_code == 200
        assert response.json() is None  # route returns None -> JSON literal 'null'
        assert captured['project_permission_id'] == PERMISSION_ID
        assert captured['engine'] is db_engine

    async def test_requires_the_query_parameter(self, client):
        response = await client.delete(f'{BASE}/permission')
        assert response.status_code == 422

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=True)
        response = await client.delete(f'{BASE}/permission', params={'project_permission_id': PERMISSION_ID})
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'{BASE}/permission', params={'project_permission_id': PERMISSION_ID})
        assert response.status_code == 401


class TestGetPermissionById:
    async def test_returns_permission_by_id(self, client, monkeypatch):
        captured = {}

        async def fake_read(**kwargs):
            captured.update(kwargs)
            return make_project_permissions(project_permission_id=PERMISSION_ID, user_id=OTHER_USER_ID, owner=False)

        patch_crud(monkeypatch, project_permissions_route, read_project_permissions_by_id=fake_read)
        response = await client.get(f'{BASE}/{PERMISSION_ID}')
        assert response.status_code == 200
        body = response.json()
        assert body['project_permission_id'] == PERMISSION_ID
        assert body['user_id'] == OTHER_USER_ID
        assert body['owner'] is False
        assert captured['permissions_id'] == PERMISSION_ID
        assert captured['engine'] is db_engine

    async def test_missing_record_fails_response_validation(self, client, monkeypatch):
        # NB: despite `-> ProjectPermissionsModel | None`, the explicit response_model rejects the None the
        # CRUD helper returns for unknown ids -> FastAPI ResponseValidationError (400 via the middleware).
        patch_crud(monkeypatch, project_permissions_route, read_project_permissions_by_id=None)
        response = await client.get(f'{BASE}/{PERMISSION_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ResponseValidationError'

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=True)
        response = await client.get(f'{BASE}/{PERMISSION_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/{PERMISSION_ID}')
        assert response.status_code == 401
