"""Tests for the /api/prio endpoints (server/api/routes/priority.py).

The artefact endpoints are guarded by ``UserPriorityPermissionChecker`` (server/util/security.py),
which needs both ``X-Project-Id`` and ``X-Priority-Id`` and resolves the header via
``read_priority_by_id`` **in the server.util.security namespace** — that is what gets patched.
The table-peek endpoints would pull full datasets through pandas, so ``wide_export_table`` and
``get_inclusion_mask`` are patched in the route module with small frames.
"""

from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
import pytest

from nacsos_data.db.schemas.priority import Priority
from nacsos_data.models.priority import PriorityModel

from server.util.config import settings
from server.api.routes import priority as priority_route
import server.util.security as security

from tests.factories import FORBIDDEN_PROJECT_ID, OTHER_PROJECT_ID, PROJECT_ID, PRIORITY_ID, orm_obj
from tests.mocks import FakeResult, patch_crud

OTHER_PRIORITY_ID = '16161616-abcd-abcd-abcd-161616161616'
PRIO_HEADERS = {'X-Priority-Id': PRIORITY_ID}


def make_priority(priority_id: str = PRIORITY_ID, project_id: str = PROJECT_ID, **overrides) -> PriorityModel:
    values: dict = {'priority_id': priority_id, 'project_id': project_id, 'name': 'Test Priority'}
    values.update(overrides)
    return PriorityModel(**values)


def db_priority(priority_id: str = PRIORITY_ID, project_id: str = PROJECT_ID, **columns) -> Priority:
    values: dict = {'priority_id': UUID(priority_id), 'project_id': UUID(project_id), 'name': 'Test Priority'}
    values.update(columns)
    return orm_obj(Priority, **values)


@pytest.fixture
def prio_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the artefact root at tmp_path and create the (empty) `priority` directory."""
    monkeypatch.setattr(settings.PIPES, 'DATA_PATH', tmp_path)
    (tmp_path / 'priority').mkdir()
    return tmp_path


@pytest.fixture
def priority_ok(monkeypatch: pytest.MonkeyPatch) -> PriorityModel:
    """Whatever X-Priority-Id is sent, it resolves to a priority of PROJECT_ID."""
    priority = make_priority()
    patch_crud(monkeypatch, security, read_priority_by_id=priority)
    return priority


@pytest.fixture
def peek_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the pandas/numpy heavy lifting with a canned frame; records the call arguments."""
    seen: dict = {}
    frame = pd.DataFrame({'item_id': ['1', '2'], 'text': ['first text', 'second text'], 'label_sent': [True, False]})

    async def fake_wide(session, project_id, nql_filter, scope_ids, limit):
        seen.update(project_id=project_id, nql_filter=nql_filter, scope_ids=scope_ids, limit=limit)
        return ['item_id'], ['label_sent'], frame

    monkeypatch.setattr(priority_route, 'wide_export_table', fake_wide)
    monkeypatch.setattr(priority_route, 'get_inclusion_mask', lambda rule, df, label_cols, ignore_missing=False: pd.Series([True, False]))
    return seen


class TestReadProjectSetups:
    async def test_returns_dehydrated_rows(self, client, fake_db):
        fake_db.queue(
            FakeResult(
                rows=[
                    {
                        'priority_id': PRIORITY_ID,
                        'project_id': PROJECT_ID,
                        'name': 'Test Priority',
                        'time_created': None,
                        'time_ready': None,
                        'time_started': None,
                        'time_assigned': None,
                        'num_prioritised': 3,
                    }
                ]
            )
        )
        response = await client.get('/api/prio/setups')
        assert response.status_code == 200
        assert response.json() == [
            {
                'priority_id': PRIORITY_ID,
                'project_id': PROJECT_ID,
                'name': 'Test Priority',
                'time_created': None,
                'time_started': None,
                'time_ready': None,
                'time_assigned': None,
                'num_prioritised': 3,
            }
        ]

    async def test_query_is_scoped_to_project_header(self, client, fake_db):
        fake_db.queue(FakeResult(rows=[]))
        response = await client.get('/api/prio/setups')
        assert response.status_code == 200
        assert response.json() == []
        assert fake_db.executions[0].parameters == {'project_id': PROJECT_ID}

    async def test_requires_annotations_prio_permission(self, permissions, client, fake_db):
        permissions.grant(PROJECT_ID, owner=False, annotations_prio=False)
        response = await client.get('/api/prio/setups')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/prio/setups')
        assert response.status_code == 401


class TestReadPrioSetup:
    async def test_returns_setup(self, client, fake_db):
        fake_db.queue(FakeResult(scalar=db_priority(name='My Setup')))
        response = await client.get('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 200
        body = response.json()
        assert body['priority_id'] == PRIORITY_ID
        assert body['name'] == 'My Setup'
        assert body['source_scopes'] is None

    async def test_unknown_priority_is_null(self, client, fake_db):
        """No 404 here: the route simply returns None, serialized as `null` with status 200."""
        fake_db.queue(FakeResult(scalar=None))
        response = await client.get('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 200
        assert response.json() is None

    async def test_setup_of_another_project_is_returned(self, client, fake_db):
        """Latent bug (priority.py:97-103): the SELECT has no project filter, so any user with
        annotations_prio on *some* project can read another project's setup by guessing its ID."""
        fake_db.queue(FakeResult(scalar=db_priority(project_id=OTHER_PROJECT_ID, name='Foreign Setup')))
        response = await client.get('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 200
        assert response.json()['project_id'] == OTHER_PROJECT_ID

    async def test_priority_id_is_required(self, client):
        response = await client.get('/api/prio/setup')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 401


class TestSavePrioSetup:
    async def test_upsert_is_executed_and_committed(self, client, fake_db):
        response = await client.put('/api/prio/setup', json={'priority_id': PRIORITY_ID, 'project_id': PROJECT_ID, 'name': 'New Setup'})
        assert response.status_code == 200
        assert response.json() is None
        assert len(fake_db.executions) == 1
        statement = str(fake_db.executions[0].statement)
        assert 'INSERT INTO priorities' in statement
        assert 'ON CONFLICT' in statement  # upsert on the priorities_pkey constraint
        assert fake_db.commits == 1

    async def test_invalid_field_type_is_422(self, client, fake_db):
        response = await client.put('/api/prio/setup', json={'priority_id': PRIORITY_ID, 'train_split': 'not-a-float'})
        assert response.status_code == 422
        assert fake_db.executions == []

    async def test_minimal_body_is_accepted(self, client, fake_db):
        """Quirk: every PriorityModel field is optional, so even a body without IDs passes and commits."""
        response = await client.put('/api/prio/setup', json={'name': 'no-ids'})
        assert response.status_code == 200
        assert fake_db.commits == 1

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/prio/setup', json={'priority_id': PRIORITY_ID, 'name': 'x'})
        assert response.status_code == 401


class TestDropPrioSetup:
    async def test_delete_is_executed_and_committed(self, client, fake_db):
        response = await client.delete('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 200
        assert response.json() is None
        assert len(fake_db.executions) == 1
        assert 'DELETE FROM priorities' in str(fake_db.executions[0].statement)
        assert fake_db.commits == 1

    async def test_priority_id_is_required(self, client):
        response = await client.delete('/api/prio/setup')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete('/api/prio/setup', params={'priority_id': PRIORITY_ID})
        assert response.status_code == 401


class TestPrioArtefactList:
    async def test_lists_files_with_sizes(self, client, priority_ok, prio_data):
        directory = prio_data / 'priority' / PRIORITY_ID
        directory.mkdir()
        (directory / 'scores.csv').write_text('a,b')
        response = await client.get('/api/prio/artefacts/list', headers=PRIO_HEADERS)
        assert response.status_code == 200
        assert response.json() == [{'path': f'{PRIORITY_ID}/scores.csv', 'size': 3}]

    async def test_missing_directory_is_400(self, client, priority_ok, prio_data):
        response = await client.get('/api/prio/artefacts/list', headers=PRIO_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingFileError'

    async def test_unknown_priority_is_400(self, client, monkeypatch, prio_data):
        # MissingIdError subclasses KeyError and has no `status` -> 400 via the middleware default.
        patch_crud(monkeypatch, security, read_priority_by_id=None)
        response = await client.get('/api/prio/artefacts/list', headers=PRIO_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingIdError'
        assert PRIORITY_ID in response.json()['detail']['message']

    async def test_priority_from_another_project_is_403(self, client, monkeypatch, prio_data):
        patch_crud(monkeypatch, security, read_priority_by_id=make_priority(priority_id=OTHER_PRIORITY_ID, project_id=OTHER_PROJECT_ID))
        response = await client.get('/api/prio/artefacts/list', headers=PRIO_HEADERS)
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_priority_header(self, client, priority_ok, prio_data):
        response = await client.get('/api/prio/artefacts/list')
        assert response.status_code == 422

    async def test_requires_project_header(self, make_client, priority_ok, prio_data):
        async with make_client(project_id=None, **PRIO_HEADERS) as client:
            response = await client.get('/api/prio/artefacts/list')
            assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client, priority_ok, prio_data):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID, **PRIO_HEADERS) as client:
            response = await client.get('/api/prio/artefacts/list')
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/prio/artefacts/list', headers=PRIO_HEADERS)
        assert response.status_code == 401


class TestPrioArtefactFile:
    async def test_returns_file_content(self, client, priority_ok, prio_data):
        (prio_data / 'priority' / 'predictions.html').write_text('<html>ok</html>')
        response = await client.get('/api/prio/artefacts/file', params={'filename': 'predictions.html'}, headers=PRIO_HEADERS)
        assert response.status_code == 200
        assert response.text == '<html>ok</html>'

    async def test_missing_file_is_400(self, client, priority_ok, prio_data):
        response = await client.get('/api/prio/artefacts/file', params={'filename': 'gone.csv'}, headers=PRIO_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'RuntimeError'

    async def test_filename_is_required(self, client, priority_ok, prio_data):
        response = await client.get('/api/prio/artefacts/file', headers=PRIO_HEADERS)
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/prio/artefacts/file', params={'filename': 'x.csv'}, headers=PRIO_HEADERS)
        assert response.status_code == 401


class TestTablePeek:
    async def test_returns_rows_and_inclusion_counts(self, client, peek_env):
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['scope-1'], 'incl': 'label_sent:1'})
        assert response.status_code == 200
        body = response.json()
        assert body['n_total'] == 2
        assert body['n_incl'] == 1
        assert body['n_excl'] == 1
        # the `text` column is dropped and the computed `incl` column is appended
        assert body['data'] == [
            {'item_id': '1', 'label_sent': True, 'incl': True},
            {'item_id': '2', 'label_sent': False, 'incl': False},
        ]

    async def test_forwards_params_to_export(self, client, peek_env):
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['s1', 's2'], 'limit': 5})
        assert response.status_code == 200
        assert peek_env['project_id'] == PROJECT_ID
        assert peek_env['scope_ids'] == ['s1', 's2']
        assert peek_env['nql_filter'] is None
        assert peek_env['limit'] == 5

    async def test_limit_is_capped_at_500(self, client, peek_env):
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['s1'], 'limit': 10_000})
        assert response.status_code == 200
        assert peek_env['limit'] == 500

    async def test_broken_inclusion_rule_marks_rows_as_error(self, client, monkeypatch):
        async def fake_wide(session, project_id, nql_filter, scope_ids, limit):
            return ['item_id'], ['label_sent'], pd.DataFrame({'item_id': ['1'], 'text': ['t']})

        def broken_mask(rule, df, label_cols, ignore_missing=False):
            raise KeyError('label_sent')

        monkeypatch.setattr(priority_route, 'wide_export_table', fake_wide)
        monkeypatch.setattr(priority_route, 'get_inclusion_mask', broken_mask)
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['s1']})
        assert response.status_code == 200
        body = response.json()
        assert body['data'] == [{'item_id': '1', 'incl': 'ERROR'}]
        # neither True nor False matches, so the ERROR rows count as neither included nor excluded
        assert (body['n_incl'], body['n_excl']) == (0, 0)

    async def test_scope_ids_are_required(self, client):
        response = await client.post('/api/prio/table/peek', json={'limit': 3})
        assert response.status_code == 422

    async def test_invalid_nql_filter_is_422(self, client, peek_env):
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['s1'], 'query': {'filter': 'not-a-filter-type'}})
        assert response.status_code == 422

    async def test_requires_dataset_read_permission(self, permissions, client, peek_env):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.post('/api/prio/table/peek', json={'scope_ids': ['s1']})
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/prio/table/peek', json={'scope_ids': ['s1']})
        assert response.status_code == 401


class TestTablePeekHtml:
    async def test_returns_dataframe_html(self, client, monkeypatch):
        frame = pd.DataFrame({'item_id': ['1', '2'], 'text': ['secret text', 'more text'], 'label_sent': [1.0, np.nan]})

        async def fake_wide(session, project_id, nql_filter, scope_ids, limit):
            return ['item_id'], ['label_sent'], frame

        monkeypatch.setattr(priority_route, 'wide_export_table', fake_wide)
        monkeypatch.setattr(priority_route, 'get_inclusion_mask', lambda rule, df, label_cols, ignore_missing=False: pd.Series([True, False]))
        response = await client.post('/api/prio/table/peek/html', json={'scope_ids': ['s1']})
        assert response.status_code == 200
        # Surprising but real: response_model=str -> the HTML travels as a JSON *string*, not as text/html.
        assert response.headers['content-type'] == 'application/json'
        html = response.json()
        assert '<table' in html
        assert 'label_sent' in html
        assert 'secret text' not in html  # the `text` column is dropped before rendering
        assert html.count('<td') == 6  # 3 cells (item_id, label_sent, incl) for each of the two rows; the NaN renders as an empty cell
        assert '<td></td>' in html

    async def test_scope_ids_are_required(self, client):
        response = await client.post('/api/prio/table/peek/html', json={})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/prio/table/peek/html', json={'scope_ids': ['s1']})
        assert response.status_code == 401
