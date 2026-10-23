"""Tests for the /api/imports endpoints (server/api/routes/imports.py).

The trigger endpoint hands work off to dramatiq (Redis!) via ``tasks.imports.import_task.send``;
that submission is patched with a recorder so nothing is ever published. The CRUD helpers are
patched via ``patch_crud`` and the raw ``db_engine.session()`` queries are scripted with ``fake_db``.
"""

from datetime import datetime
from uuid import UUID

from nacsos_data.db.schemas import Task
from nacsos_data.db.schemas.imports import ImportRevision
from nacsos_data.models.imports import ImportModel

from server.api.routes import imports as imports_route
from server.pipelines import tasks

from tests.factories import (
    FORBIDDEN_PROJECT_ID,
    IMPORT_ID,
    OTHER_PROJECT_ID,
    PROJECT_ID,
    PRIORITY_ID,
    USER_ID,
    db_import,
    new_id,
    orm_obj,
)
from tests.mocks import FakeResult, patch_crud

TIME_CREATED = datetime(2026, 1, 1, 12, 0, 0)


def db_import_revision(import_id: str = IMPORT_ID, counter: int = 1, **columns) -> ImportRevision:
    values = {
        'import_revision_id': UUID(new_id()),
        'import_revision_counter': counter,
        'time_created': TIME_CREATED,
        'import_id': UUID(import_id),
        'num_items_retrieved': 10,
        'num_items': 10,
        'num_items_new': 10,
        'num_items_updated': 0,
        'num_items_removed': 0,
    }
    values.update(columns)
    return orm_obj(ImportRevision, **values)


def db_task(**columns) -> Task:
    values = {
        'task_id': UUID(PRIORITY_ID),
        'user_id': UUID(USER_ID),
        'project_id': UUID(PROJECT_ID),
        'function_name': 'pipelines.tasks.imports.import_task',
        'fingerprint': 'deadbeef',
        'status': 'PENDING',
        'comment': 'Import for "Test Import"',
        'time_created': TIME_CREATED,
    }
    values.update(columns)
    return orm_obj(Task, **values)


def _import_model(import_id: str = IMPORT_ID, project_id: str = PROJECT_ID, **overrides) -> ImportModel:
    values: dict = {
        'import_id': import_id,
        'user_id': USER_ID,
        'project_id': project_id,
        'name': 'WoS import',
        'description': 'Import of the Web of Science export',
        'type': 'academic',
        'time_created': TIME_CREATED,
    }
    values.update(overrides)
    return ImportModel(**values)


def _import_payload(**overrides) -> dict:
    payload: dict = {
        'import_id': IMPORT_ID,
        'user_id': USER_ID,
        'project_id': PROJECT_ID,
        'name': 'WoS import',
        'description': 'Import of the Web of Science export',
        'type': 'academic',
        'config': {'kind': 'academic', 'sources': ['export.csv']},
    }
    payload.update(overrides)
    return payload


class TestListImports:
    async def test_returns_imports_with_revision_counts(self, client, fake_db):
        fake_db.queue(
            FakeResult(
                rows=[
                    {
                        'import_id': IMPORT_ID,
                        'user_id': USER_ID,
                        'project_id': PROJECT_ID,
                        'name': 'WoS import',
                        'description': 'desc',
                        'type': 'academic',
                        'time_created': TIME_CREATED,
                        'config': None,
                        'num_revisions': 2,
                        'num_items': 7,
                    }
                ]
            )
        )

        response = await client.get('/api/imports/list')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['name'] == 'WoS import'
        assert body[0]['num_revisions'] == 2
        assert body[0]['num_items'] == 7
        # the raw text query is scoped to the project of the permission context
        assert fake_db.executions[0].parameters == {'project_id': PROJECT_ID}

    async def test_empty_project_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/imports/list')
        assert response.status_code == 200
        assert response.json() == []

    async def test_row_without_num_revisions_is_a_400(self, client, fake_db):
        """The count column is not optional in ImportInfo, so a malformed DB row surfaces as a 400 ValidationError."""
        fake_db.queue_rows({'import_id': IMPORT_ID, 'project_id': PROJECT_ID, 'name': 'broken', 'description': '', 'type': 'academic'})
        response = await client.get('/api/imports/list')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ValidationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/imports/list')
        assert response.status_code == 401

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/imports/list')
        assert response.status_code == 422

    async def test_requires_imports_read_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get('/api/imports/list')
            assert response.status_code == 403


class TestListImportDetails:
    async def test_returns_imports_with_nested_revisions(self, client, fake_db):
        fake_db.queue(
            FakeResult(
                rows=[
                    {
                        'Import': db_import(),
                        'revisions': [
                            {'import_revision_id': new_id(), 'import_revision_counter': 1, 'time_created': TIME_CREATED, 'import_id': IMPORT_ID, 'num_items': 4}
                        ],
                    }
                ]
            )
        )

        response = await client.get('/api/imports/list/details')
        assert response.status_code == 200
        body = response.json()
        assert body[0]['name'] == 'Test Import'
        assert [rev['import_revision_counter'] for rev in body[0]['revisions']] == [1]
        assert body[0]['revisions'][0]['num_items'] == 4

    async def test_import_without_revisions_gets_empty_list(self, client, fake_db):
        fake_db.queue(FakeResult(rows=[{'Import': db_import(), 'revisions': []}]))
        response = await client.get('/api/imports/list/details')
        assert response.status_code == 200
        assert response.json()[0]['revisions'] == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/imports/list/details')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get('/api/imports/list/details')
            assert response.status_code == 403


class TestGetImportDetails:
    async def test_returns_import_of_own_project(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=_import_model())
        response = await client.get(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 200
        body = response.json()
        assert body['import_id'] == IMPORT_ID
        assert body['project_id'] == PROJECT_ID
        assert body['name'] == 'WoS import'

    async def test_import_of_another_project_is_forbidden(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=_import_model(project_id=OTHER_PROJECT_ID))
        response = await client.get(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_unknown_import_is_forbidden_not_missing(self, client, monkeypatch):
        """Surprising actual behaviour: a non-existent import id reports 403 (InsufficientPermissions), not 404."""
        patch_crud(monkeypatch, imports_route, read_import=None)
        response = await client.get(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/imports/import/{IMPORT_ID}')
            assert response.status_code == 403


class TestGetImportCounts:
    async def test_returns_item_count(self, client, monkeypatch):
        seen: dict = {}

        async def fake_count(**kwargs):
            seen.update(kwargs)
            return 42

        patch_crud(monkeypatch, imports_route, read_item_count_for_import=fake_count)
        response = await client.get(f'/api/imports/import/{IMPORT_ID}/count/')
        assert response.status_code == 200
        assert response.json() == 42
        assert seen['import_id'] == IMPORT_ID

    async def test_no_project_ownership_check(self, client, monkeypatch):
        """Documented gap (FIXME in imports.py): the count is returned without verifying the import's project."""
        patch_crud(monkeypatch, imports_route, read_item_count_for_import=7)
        response = await client.get(f'/api/imports/import/{IMPORT_ID}/count/')
        assert response.status_code == 200
        assert response.json() == 7

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/imports/import/{IMPORT_ID}/count/')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/imports/import/{IMPORT_ID}/count/')
            assert response.status_code == 403


class TestGetImportRevisions:
    async def test_returns_revisions_with_task(self, client, fake_db):
        # Note the FIXME in imports.py:115: like the count endpoint, revisions of *any* import are returned;
        # the import's own project is never compared to the permission context.
        fake_db.queue(FakeResult(rows=[{'ImportRevision': db_import_revision(pipeline_task_id=PRIORITY_ID), 'Task': db_task()}]))

        response = await client.get(f'/api/imports/import/{IMPORT_ID}/revisions')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['import_revision_counter'] == 1
        assert body[0]['import_id'] == IMPORT_ID
        assert body[0]['num_items'] == 10
        assert body[0]['task']['function_name'] == 'pipelines.tasks.imports.import_task'
        assert body[0]['task']['status'] == 'PENDING'
        # ordered by revision counter descending
        assert 'DESC' in fake_db.statements()[0].upper()

    async def test_revision_without_task(self, client, fake_db):
        fake_db.queue(FakeResult(rows=[{'ImportRevision': db_import_revision(counter=3), 'Task': None}]))
        response = await client.get(f'/api/imports/import/{IMPORT_ID}/revisions')
        assert response.status_code == 200
        body = response.json()
        assert body[0]['import_revision_counter'] == 3
        assert body[0]['task'] is None

    async def test_no_revisions_returns_empty_list(self, client, fake_db):
        response = await client.get(f'/api/imports/import/{IMPORT_ID}/revisions')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/imports/import/{IMPORT_ID}/revisions')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/imports/import/{IMPORT_ID}/revisions')
            assert response.status_code == 403


class TestPutImportDetails:
    async def test_creates_import_of_own_project(self, client, monkeypatch):
        calls: list = []

        async def fake_upsert(**kwargs):
            calls.append(kwargs)
            return IMPORT_ID

        patch_crud(monkeypatch, imports_route, upsert_import=fake_upsert)
        response = await client.put('/api/imports/import', json=_import_payload())
        assert response.status_code == 200
        assert response.json() == IMPORT_ID
        assert len(calls) == 1
        assert calls[0]['use_commit'] is True
        assert calls[0]['import_model'].project_id == PROJECT_ID
        assert calls[0]['import_model'].config.kind == 'academic'

    async def test_import_for_another_project_is_forbidden(self, client, monkeypatch):
        calls: list = []
        patch_crud(monkeypatch, imports_route, upsert_import=lambda **kwargs: calls.append(kwargs))
        response = await client.put('/api/imports/import', json=_import_payload(project_id=OTHER_PROJECT_ID))
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'
        assert calls == []  # nothing was upserted

    async def test_minimal_payload_is_accepted(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, upsert_import=IMPORT_ID)
        payload = _import_payload()
        del payload['import_id'], payload['user_id'], payload['config']
        response = await client.put('/api/imports/import', json=payload)
        assert response.status_code == 200
        assert response.json() == IMPORT_ID

    async def test_body_without_required_fields_fails_validation(self, client):
        response = await client.put('/api/imports/import', json={'name': 'incomplete'})
        assert response.status_code == 422

    async def test_invalid_import_config_fails_validation(self, client):
        """ImportConfig is a discriminated union on 'kind' -> unknown kinds fail with 422."""
        response = await client.put('/api/imports/import', json=_import_payload(config={'kind': 'carrier-pigeon'}))
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/imports/import', json=_import_payload())
        assert response.status_code == 401

    async def test_requires_imports_edit_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.put('/api/imports/import', json=_import_payload())
            assert response.status_code == 403


class TestTriggerImport:
    async def test_submits_dramatiq_task(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=_import_model())
        sent: list = []
        monkeypatch.setattr(tasks.imports.import_task, 'send', lambda **kwargs: sent.append(kwargs))

        response = await client.post(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 200
        assert sent == [
            {
                'project_id': PROJECT_ID,
                'user_id': USER_ID,
                'comment': f'Import for "WoS import" ({IMPORT_ID})',
                'import_id': IMPORT_ID,
            }
        ]

    async def test_import_of_another_project_is_not_triggered(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=_import_model(project_id=OTHER_PROJECT_ID))
        sent: list = []
        monkeypatch.setattr(tasks.imports.import_task, 'send', lambda **kwargs: sent.append(kwargs))

        response = await client.post(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'
        assert sent == []

    async def test_unknown_import_is_not_triggered(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=None)
        sent: list = []
        monkeypatch.setattr(tasks.imports.import_task, 'send', lambda **kwargs: sent.append(kwargs))

        response = await client.post(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 403
        assert sent == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post(f'/api/imports/import/{IMPORT_ID}')
        assert response.status_code == 401

    async def test_requires_imports_edit_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.post(f'/api/imports/import/{IMPORT_ID}')
            assert response.status_code == 403


class TestDeleteImportDetails:
    async def test_deletes_import_of_own_project(self, client, monkeypatch):
        patch_crud(monkeypatch, imports_route, read_import=_import_model())
        calls: list = []

        async def fake_delete(**kwargs):
            calls.append(kwargs)
            return None

        patch_crud(monkeypatch, imports_route, delete_import=fake_delete)

        response = await client.delete(f'/api/imports/import/delete/{IMPORT_ID}')
        assert response.status_code == 200
        assert response.json() == IMPORT_ID
        assert [c['import_id'] for c in calls] == [IMPORT_ID]
        assert calls[0]['use_commit'] is True

    async def test_import_of_another_project_is_not_deleted(self, client, monkeypatch):
        calls: list = []

        async def fake_delete(**kwargs):
            calls.append(kwargs)

        patch_crud(monkeypatch, imports_route, read_import=_import_model(project_id=OTHER_PROJECT_ID), delete_import=fake_delete)
        response = await client.delete(f'/api/imports/import/delete/{IMPORT_ID}')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'
        assert calls == []  # nothing was deleted

    async def test_unknown_import_reports_forbidden(self, client, monkeypatch):
        """Same surprise as the details endpoint: unknown ids are 403, not 404."""
        patch_crud(monkeypatch, imports_route, read_import=None, delete_import=None)
        response = await client.delete(f'/api/imports/import/delete/{IMPORT_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'/api/imports/import/delete/{IMPORT_ID}')
        assert response.status_code == 401

    async def test_requires_imports_edit_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.delete(f'/api/imports/import/delete/{IMPORT_ID}')
            assert response.status_code == 403
