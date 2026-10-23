"""Tests for the /api/export endpoints (server/api/routes/export.py).

The annotation export writes real files to disk through the ``nacsos_data.util.export.file``
writers (called via ``run_in_threadpool`` from ``build_export_file``). Redirecting
``tempfile.tempdir`` at ``tmp_path`` keeps those writes inside pytest's sandbox and lets the
FileResponse (and its cleanup background task) run for real.
"""

import json
import tempfile

from nacsos_data.models import ScopeInfo
from nacsos_data.models.annotations import AnnotationSchemeModel

from server.api.routes import export as export_route

from tests.factories import FORBIDDEN_PROJECT_ID, ITEM_ID, PROJECT_ID, SCHEME_ID, USER_ID, make_user, project_model
from tests.mocks import patch_crud


# One export row as produced by prepare_export_table (wide format: label values are spread
# over '<key>|<choice>' columns and 'type'/'project_id'/... are dropped later by the route).
def _export_rows():
    return [
        {
            'item_id': ITEM_ID,
            'title': 'Climate Policy Attitudes',
            'doi': '10.1000/xyz',
            'type': 'academic',
            'project_id': PROJECT_ID,
            'time_edited': None,
            'title_slug': 'climatepolicyattitudes',
            'keywords': ['climate'],
            'meta': {'x': 1},
            'authors': [{'name': 'Doe, Jane'}, {'name': 'Roe, Richard'}],
            'topic|1': 1,
            'username': 'testuser',
        }
    ]


def _export_body(**overrides) -> dict:
    body: dict = {'labels': [{'key': 'topic', 'options_int': [1]}]}
    body.update(overrides)
    return body


def _patch_prepare(monkeypatch, rows, captured: dict | None = None):
    async def fake_prepare(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return rows

    patch_crud(monkeypatch, export_route, prepare_export_table=fake_prepare)


class TestExportAnnotations:
    async def test_csv_happy_path(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))  # keep NamedTemporaryFile() writes inside tmp
        _patch_prepare(monkeypatch, _export_rows())

        response = await client.post('/api/export/annotations/csv', json=_export_body())
        assert response.status_code == 200
        # Surprising actual behaviour: the media_type class overrides (CSVResponse etc., export.py:68) never take
        # effect - starlette's FileResponse.__init__ infers the media type from the temp file's extension and sets
        # the instance attribute, so clients get 'text/csv' instead of the documented 'application/csv'.
        assert response.headers['content-type'].startswith('text/csv')

        header, *lines = response.text.splitlines()
        # DEFAULT_COLUMNS_TO_DROP stripped type/project_id/time_edited/title_slug/keywords/meta
        assert header == 'item_id,title,doi,authors,topic|1,username,authors_raw'
        assert len(lines) == 1
        # authors were flattened to names and joined; authors_raw keeps the original dicts
        assert 'Doe, Jane; Roe, Richard' in response.text
        assert "'name': 'Doe, Jane'" in response.text  # authors_raw round-trip
        # the BackgroundTask(cleanup) removed the temporary file after the response was sent
        assert list(tmp_path.iterdir()) == []

    async def test_csv_passes_query_context_to_prepare(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        captured: dict = {}
        _patch_prepare(monkeypatch, _export_rows(), captured)

        response = await client.post(
            '/api/export/annotations/csv',
            json=_export_body(user_ids=[USER_ID], assignment_scope_ids=[SCHEME_ID], ignore_repeat=False),
            params={'max_results': 10},
        )
        assert response.status_code == 200
        assert captured['project_id'] == PROJECT_ID
        assert captured['user_ids'] == [USER_ID]
        assert captured['assignment_scope_ids'] == [SCHEME_ID]
        assert captured['ignore_repeat'] is False
        assert captured['max_results'] == 10  # default 15000 overridable via query parameter
        assert captured['labels'][0].key == 'topic'

    async def test_excel_happy_path(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, _export_rows())

        response = await client.post('/api/export/annotations/excel', json=_export_body())
        assert response.status_code == 200
        # only excel ends up with the intended media type, and only because mimetypes knows '.xlsx'
        assert response.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        assert response.content[:2] == b'PK'  # xlsx is a zip container
        assert list(tmp_path.iterdir()) == []

    async def test_ris_happy_path(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, _export_rows())
        mappings_calls: list = []

        async def fake_label_mappings(**kwargs):
            mappings_calls.append(kwargs)
            return {}

        patch_crud(monkeypatch, export_route, get_labels_with_names=fake_label_mappings)

        response = await client.post('/api/export/annotations/ris', json=_export_body())
        assert response.status_code == 200
        # see test_csv_happy_path: mimetypes does not know '.ris', so the RISResponse media type override is
        # silently replaced by the octet-stream fallback (latent bug in export.py:76).
        assert response.headers['content-type'] == 'application/octet-stream'
        assert 'Climate Policy Attitudes' in response.text
        assert 'topic|1' in response.text  # RAW_TAGS format (default): the raw '<key>|<choice>' tag
        assert 'NACSOS ID' in response.text  # id trailer written into the notes field
        # scopes = assignment_scope_ids + bot_annotation_metadata_ids, both empty here
        assert len(mappings_calls) == 1
        assert mappings_calls[0]['scopes'] == []
        assert list(tmp_path.iterdir()) == []

    async def test_ris_resolves_label_names_via_mappings(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, _export_rows())
        captured: dict = {}

        async def fake_label_mappings(**kwargs):
            captured.update(kwargs)
            return {'topic|1': ('Topic label', 'Political framing')}

        patch_crud(monkeypatch, export_route, get_labels_with_names=fake_label_mappings)

        response = await client.post(
            '/api/export/annotations/ris',
            json=_export_body(ris_label_format='LABEL_AND_CHOICE_NAMES', assignment_scope_ids=[SCHEME_ID]),
        )
        assert response.status_code == 200
        assert captured['scopes'] == [SCHEME_ID]
        assert 'Topic label: Political framing' in response.text

    async def test_jsonl_happy_path(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, _export_rows())

        response = await client.post('/api/export/annotations/jsonl', json=_export_body())
        assert response.status_code == 200
        # '.jsonl' is unknown to mimetypes -> the JSONLResponse 'text/plain' override is lost (export.py:80)
        assert response.headers['content-type'] == 'application/octet-stream'
        row = json.loads(response.text)
        assert row['title'] == 'Climate Policy Attitudes'
        assert row['authors'] == ['Doe, Jane', 'Roe, Richard']
        assert row['authors_raw'] == [{'name': 'Doe, Jane'}, {'name': 'Roe, Richard'}]
        assert 'project_id' not in row  # default columns were dropped
        assert list(tmp_path.iterdir()) == []

    async def test_jsonl_with_no_rows_returns_empty_file(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, [])

        response = await client.post('/api/export/annotations/jsonl', json=_export_body())
        assert response.status_code == 200
        assert response.text == ''

    async def test_csv_with_no_rows_crashes(self, client, monkeypatch, tmp_path):
        """Latent bug: write_csv() indexes result[0] unconditionally -> IndexError -> 400 via the middleware."""
        monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
        _patch_prepare(monkeypatch, [])

        response = await client.post('/api/export/annotations/csv', json=_export_body())
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'IndexError'

    async def test_export_failure_inside_prepare_surfaces_as_400(self, client, monkeypatch):
        """prepare_export_table raises RuntimeError when the item count is outside (0, max_results]."""

        async def failing_prepare(**kwargs):
            raise RuntimeError('Found 0 items for query (0 > count > 15,000)')

        patch_crud(monkeypatch, export_route, prepare_export_table=failing_prepare)
        response = await client.post('/api/export/annotations/csv', json=_export_body())
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'RuntimeError'

    async def test_unsupported_format_is_a_validation_error(self, client):
        """{export_format} is typed as ExportTypeEnum, so unknown formats fail FastAPI's path validation with
        422 before the route's own `raise HTTPException(400)` guard (export.py:136) can ever run."""
        response = await client.post('/api/export/annotations/pdf', json=_export_body())
        assert response.status_code == 422
        error = response.json()['detail'][0]
        assert error['loc'][-1] == 'export_format'

    async def test_body_without_labels_fails_validation(self, client):
        response = await client.post('/api/export/annotations/csv', json={})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/export/annotations/csv', json=_export_body())
        assert response.status_code == 401

    async def test_requires_annotation_read_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.post('/api/export/annotations/csv', json=_export_body())
            assert response.status_code == 403


class TestBuildExportFile:
    def test_transforms_rows_before_writing(self, monkeypatch):
        captured: dict = {}

        def fake_write_csv(rows):
            captured['rows'] = rows
            return '/tmp/never-written.csv'

        monkeypatch.setattr(export_route, 'write_csv', fake_write_csv)
        query = export_route.ExportRequest(labels=[{'key': 'topic'}])
        path = export_route.build_export_file('csv', _export_rows()[0:1], query, {})

        assert path == '/tmp/never-written.csv'  # build_export_file just forwards the writer's path
        row = captured['rows'][0]
        assert row['authors'] == ['Doe, Jane', 'Roe, Richard']
        assert row['authors_raw'] == [{'name': 'Doe, Jane'}, {'name': 'Roe, Richard'}]
        assert 'project_id' not in row and 'title_slug' not in row and 'meta' not in row
        assert row['topic|1'] == 1

    def test_columns_to_drop_is_respected(self, monkeypatch):
        """An explicit columns_to_drop fully replaces DEFAULT_COLUMNS_TO_DROP."""
        captured: dict = {}
        monkeypatch.setattr(export_route, 'write_jsonl', lambda rows: captured.setdefault('rows', rows) and '')
        query = export_route.ExportRequest(labels=[{'key': 'topic'}], columns_to_drop=['title', 'username', 'topic|1', 'item_id', 'doi', 'authors_raw'])
        export_route.build_export_file('jsonl', _export_rows()[0:1], query, {})
        row = captured['rows'][0]
        assert 'title' not in row and 'username' not in row and 'authors_raw' not in row
        # ... and the default-drop columns now survive into the export
        assert row['type'] == 'academic' and row['project_id'] == PROJECT_ID and row['meta'] == {'x': 1}
        assert set(row) == {'type', 'project_id', 'time_edited', 'title_slug', 'keywords', 'meta', 'authors'}


class TestProjectBaseInfo:
    async def test_returns_users_and_scopes(self, client, monkeypatch):
        scope = ScopeInfo(scope_id='88888888-8888-8888-8888-888888888888', scope_name='Main', scheme_id=SCHEME_ID, scheme_name='Tech')
        bot_scope = ScopeInfo(scope_id='12121212-1212-1212-1212-121212121212', scope_name='Bot scope', scheme_id=SCHEME_ID, scheme_name='Tech')
        patch_crud(
            monkeypatch,
            export_route,
            read_project_by_id=project_model(),
            read_users=[make_user()],
            read_assignment_scopes_for_project_info=[scope],
            read_resolution_scopes_for_project_info=[bot_scope],
        )

        response = await client.get('/api/export/project/baseinfo')
        assert response.status_code == 200
        body = response.json()
        # DehydratedUser only exposes the minimal user fields
        assert body['users'] == [{'user_id': USER_ID, 'username': 'testuser', 'full_name': 'Testuser'}]
        assert body['scopes'] == [scope.model_dump()]
        assert body['bot_scopes'] == [bot_scope.model_dump()]

    async def test_unknown_users_list_is_tolerated(self, client, monkeypatch):
        patch_crud(
            monkeypatch,
            export_route,
            read_project_by_id=project_model(),
            read_users=None,  # the route coalesces None -> []
            read_assignment_scopes_for_project_info=[],
            read_resolution_scopes_for_project_info=[],
        )
        response = await client.get('/api/export/project/baseinfo')
        assert response.status_code == 200
        assert response.json() == {'users': [], 'scopes': [], 'bot_scopes': []}

    async def test_missing_project_is_a_500ish_runtime_error_as_400(self, client, monkeypatch):
        """Latent bug: an unreadable project raises a bare RuntimeError, which the middleware reports as 400."""
        patch_crud(
            monkeypatch,
            export_route,
            read_project_by_id=None,
            read_users=[],
            read_assignment_scopes_for_project_info=[],
            read_resolution_scopes_for_project_info=[],
        )
        response = await client.get('/api/export/project/baseinfo')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'RuntimeError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/export/project/baseinfo')
        assert response.status_code == 401

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/export/project/baseinfo')
        assert response.status_code == 422

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get('/api/export/project/baseinfo')
            assert response.status_code == 403


class TestExportLabelOptions:
    def _scheme(self) -> AnnotationSchemeModel:
        return AnnotationSchemeModel(
            annotation_scheme_id=SCHEME_ID,
            project_id=PROJECT_ID,
            name='Tech scheme',
            labels=[
                {
                    'name': 'Technology',
                    'key': 'tech',
                    'kind': 'single',
                    'choices': [{'name': 'Solar', 'value': 1}, {'name': 'Wind', 'value': 2}],
                },
                {'name': 'Relevant', 'key': 'relevant', 'kind': 'bool'},
            ],
        )

    async def test_returns_label_options_per_key(self, client, monkeypatch):
        patch_crud(monkeypatch, export_route, read_annotation_scheme=self._scheme())
        response = await client.get(f'/api/export/project/label_options/{SCHEME_ID}')
        assert response.status_code == 200
        assert response.json() == {
            'tech': {'key': 'tech', 'options_int': [1, 2], 'options_bool': None, 'options_multi': None, 'strings': None},
            'relevant': {'key': 'relevant', 'options_int': None, 'options_bool': [True, False], 'options_multi': None, 'strings': None},
        }

    async def test_unknown_scheme_is_a_404(self, client, monkeypatch):
        patch_crud(monkeypatch, export_route, read_annotation_scheme=None)
        response = await client.get(f'/api/export/project/label_options/{SCHEME_ID}')
        assert response.status_code == 404
        detail = response.json()['detail']
        assert detail['type'] == 'AnnotationSchemeNotFoundError'
        assert SCHEME_ID in detail['message']

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/export/project/label_options/{SCHEME_ID}')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/export/project/label_options/{SCHEME_ID}')
            assert response.status_code == 403
