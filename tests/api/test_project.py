"""Tests for the /api/project endpoints (server/api/routes/project/__init__.py).

The sub-routers mounted here (``/items``, ``/permissions``) are covered by
test_project_items.py and test_project_permissions.py respectively.
"""

import uuid

from nacsos_data.db.schemas import Project

from server.api.routes import project as project_route
from server.data import db_engine

from tests.factories import FORBIDDEN_PROJECT_ID, PROJECT_ID, db_project, project_model
from tests.mocks import FakeResult, patch_crud


class TestGetProjectInfo:
    async def test_returns_project_for_current_project(self, client, monkeypatch):
        patch_crud(monkeypatch, project_route, read_project_by_id=project_model(name='Research I'))
        response = await client.get('/api/project/info')
        assert response.status_code == 200
        assert response.json()['name'] == 'Research I'
        assert response.json()['project_id'] == PROJECT_ID

    async def test_unpatched_read_goes_through_the_session(self, client, fake_db):
        # read_project_by_id selects the Project ORM row and model-validates it.
        fake_db.queue_rows(db_project(name='From The Session'))
        response = await client.get('/api/project/info')
        assert response.status_code == 200
        assert response.json()['name'] == 'From The Session'
        # FakeExecution.table_name / is_select_of resolve to nothing for these selects, so check the FROM clause text.
        assert 'FROM project' in str(fake_db.executions[0].statement)

    async def test_unknown_project_is_an_error(self, client, monkeypatch):
        patch_crud(monkeypatch, project_route, read_project_by_id=None)
        response = await client.get('/api/project/info')
        assert response.status_code == 400  # ProjectNotFoundError declares status 400
        assert response.json()['detail']['type'] == 'ProjectNotFoundError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/project/info')
        assert response.status_code == 401

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/project/info')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get('/api/project/info')
            assert response.status_code == 403
            assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_inactive_user_is_rejected(self, inactive_client):
        response = await inactive_client.get('/api/project/info')
        assert response.status_code == 403


class TestSaveProject:
    async def test_upsert_arguments_are_forwarded(self, client, monkeypatch):
        captured = {}

        async def fake_upsert(**kwargs):
            captured.update(kwargs)
            return PROJECT_ID

        patch_crud(monkeypatch, project_route, upsert_orm=fake_upsert)
        response = await client.put('/api/project/info', json={'project_id': PROJECT_ID, 'name': 'Renamed', 'type': 'academic'})
        assert response.status_code == 200
        assert response.json() == PROJECT_ID
        assert captured['Schema'] is Project
        assert captured['primary_key'] == 'project_id'
        assert captured['skip_update'] == ['project_id']
        assert captured['use_commit'] is True
        assert captured['db_engine'] is db_engine
        assert captured['upsert_model'].name == 'Renamed'

    async def test_update_existing_project_overwrites_all_fields(self, client, fake_db):
        stored = db_project(name='Old Name', type='academic', description='old description')
        fake_db.queue_rows(stored)
        response = await client.put('/api/project/info', json={'project_id': PROJECT_ID, 'name': 'Renamed', 'type': 'academic'})
        assert response.status_code == 200
        assert response.json() == PROJECT_ID
        assert stored.name == 'Renamed'
        # upsert_orm writes every model field, so optional values absent from the payload wipe the stored ones.
        assert stored.description is None
        # FakeSession.flush_or_commit() counts as a flush; the real DBSession would commit here (use_commit=True).
        assert fake_db.flushes == 1
        assert fake_db.commits == 0

    async def test_new_project_is_inserted(self, client, fake_db, monkeypatch):
        response = await client.put('/api/project/info', json={'name': 'Fresh', 'type': 'generic'})
        assert response.status_code == 200
        new_id = response.json()
        uuid.UUID(new_id)  # project_id absent from the payload -> server generates one
        assert len(fake_db.added) == 1
        assert isinstance(fake_db.added[0], Project)
        assert str(fake_db.added[0].project_id) == new_id
        assert fake_db.added[0].name == 'Fresh'
        assert fake_db.flushes == 1

    async def test_requires_owner_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=True)  # owner=True required by the checker
        response = await client.put('/api/project/info', json={'name': 'Nope', 'type': 'academic'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/project/info', json={'name': 'Nope', 'type': 'academic'})
        assert response.status_code == 401

    async def test_invalid_body_fails_validation(self, client):
        response = await client.put('/api/project/info', json={'project_id': PROJECT_ID})
        assert response.status_code == 422


class TestResetImportMutex:
    async def test_resets_mutex_on_stored_project(self, client, fake_db):
        stored = db_project(import_mutex=True)
        # set_session_mutex() first rolls back, selects the project (.scalar()), then commits.
        fake_db.queue(FakeResult(rows=[stored], scalar=stored))
        response = await client.put('/api/project/import_mutex')
        assert response.status_code == 200
        assert response.json() is None  # the route returns None -> JSON literal 'null'
        assert stored.import_mutex is None  # lock=False maps to NULL
        assert fake_db.rollbacks == 1
        assert fake_db.commits == 1

    async def test_unknown_project_reports_missing_id(self, client, fake_db):
        response = await client.put('/api/project/import_mutex')
        assert response.status_code == 400  # MissingIdError (a KeyError) has no status -> middleware default
        assert response.json()['detail']['type'] == 'MissingIdError'

    async def test_forwards_call_to_set_session_mutex(self, client, monkeypatch):
        captured = {}

        async def fake_set_mutex(**kwargs):
            captured.update(kwargs)

        patch_crud(monkeypatch, project_route, set_session_mutex=fake_set_mutex)
        response = await client.put('/api/project/import_mutex')
        assert response.status_code == 200
        assert captured['project_id'] == PROJECT_ID
        assert captured['lock'] is False
        assert captured['session'] is not None

    async def test_requires_imports_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, imports_edit=False, dataset_read=True)
        response = await client.put('/api/project/import_mutex')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_imports_edit_permission_is_enough(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, imports_edit=True)
        patch_crud(monkeypatch, project_route, set_session_mutex=None)
        response = await client.put('/api/project/import_mutex')
        assert response.status_code == 200

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/project/import_mutex')
        assert response.status_code == 401

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.put('/api/project/import_mutex')
        assert response.status_code == 422
