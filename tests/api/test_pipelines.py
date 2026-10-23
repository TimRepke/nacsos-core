"""Tests for the /api/pipes endpoints (server/api/routes/pipelines.py).

Everything filesystem-related is pointed at ``tmp_path`` by moving ``settings.PIPES.DATA_PATH``,
and the dramatiq/DB touchpoints (``abort``, ``query_tasks``, ``read_task_by_id``) are patched in
the namespace they are used in — the latter lives in ``server.pipelines.security`` (the task
permission checker), not in the route module.
"""

import io
import time
import zipfile
from itertools import islice
from pathlib import Path
from uuid import UUID

import pytest

from nacsos_data.db.schemas.pipeline import Task
from nacsos_data.models.pipeline import TaskModel

from server.pipelines.files import stream_log
from server.util.config import settings
from server.api.routes import pipelines as pipelines_route
import server.pipelines.security as pipelines_security

from tests.factories import FORBIDDEN_PROJECT_ID, OTHER_PROJECT_ID, PROJECT_ID, SUPERUSER_TOKEN, USER_ID, orm_obj
from tests.mocks import FakeResult, patch_crud

TASK_ID = 'dddddddd-1111-2222-3333-444444444444'
TASK_HEADERS = {'X-Task-Id': TASK_ID}


def make_task(task_id: str = TASK_ID, project_id: str = PROJECT_ID, **overrides) -> TaskModel:
    values: dict = {
        'task_id': task_id,
        'project_id': project_id,
        'user_id': USER_ID,
        'function_name': 'pipeline.functions.do_it',
        'fingerprint': 'fp-1',
        'message_id': 'msg-1',
    }
    values.update(overrides)
    return TaskModel(**values)


def db_task(task_id: str = TASK_ID, project_id: str = PROJECT_ID, **columns) -> Task:
    values: dict = {
        'task_id': UUID(task_id),
        'project_id': UUID(project_id),
        'user_id': UUID(USER_ID),
        'function_name': 'pipeline.functions.do_it',
        'fingerprint': 'fp-1',
        'message_id': 'msg-1',
        'status': 'PENDING',
        'params': {'x': 1},
    }
    values.update(columns)
    return orm_obj(Task, **values)


@pytest.fixture
def pipes_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the artefact/user-data root of the settings at tmp_path (the `artefacts` dir exists)."""
    monkeypatch.setattr(settings.PIPES, 'DATA_PATH', tmp_path)
    (tmp_path / 'artefacts').mkdir()
    return tmp_path


@pytest.fixture
def task_dir(pipes_data: Path) -> Path:
    """The (empty) artefacts directory of TASK_ID."""
    directory = pipes_data / 'artefacts' / TASK_ID
    directory.mkdir()
    return directory


@pytest.fixture
def task_ok(monkeypatch: pytest.MonkeyPatch) -> TaskModel:
    """Any X-Task-Id resolves to a task belonging to PROJECT_ID."""
    task = make_task()
    patch_crud(monkeypatch, pipelines_security, read_task_by_id=task)
    return task


class TestListArtefacts:
    async def test_lists_files_with_sizes(self, client, task_ok, task_dir):
        (task_dir / 'a.txt').write_text('abcde')
        (task_dir / 'sub').mkdir()
        (task_dir / 'sub' / 'b.txt').write_text('xy')
        response = await client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert sorted(response.json(), key=lambda entry: entry['path']) == [
            {'path': f'{TASK_ID}/a.txt', 'size': 5},
            {'path': f'{TASK_ID}/sub/b.txt', 'size': 2},
        ]

    async def test_missing_task_directory_is_400(self, client, task_ok, pipes_data):
        response = await client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingFileError'

    async def test_unknown_task_id_is_400(self, client, monkeypatch, task_dir):
        # UnknownTaskID has no `status` attribute -> ErrorHandlingMiddleware defaults to 400 (not 404).
        patch_crud(monkeypatch, pipelines_security, read_task_by_id=None)
        response = await client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'UnknownTaskID'

    async def test_task_from_another_project_is_403(self, client, monkeypatch, task_dir):
        patch_crud(monkeypatch, pipelines_security, read_task_by_id=make_task(project_id=OTHER_PROJECT_ID))
        response = await client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_task_header(self, client, task_ok, task_dir):
        response = await client.get('/api/pipes/artefacts/list')
        assert response.status_code == 422

    async def test_requires_project_header(self, no_project_client, task_ok, task_dir):
        response = await no_project_client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client, task_ok, task_dir):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID, **{'X-Task-Id': TASK_ID}) as client:
            response = await client.get('/api/pipes/artefacts/list')
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/artefacts/list', headers=TASK_HEADERS)
        assert response.status_code == 401


class TestGetArtefactFile:
    async def test_returns_file_content(self, client, task_ok, task_dir):
        (task_dir / 'result.csv').write_text('a,b\n1,2\n')
        response = await client.get('/api/pipes/artefacts/file', params={'filename': f'{TASK_ID}/result.csv'}, headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.text == 'a,b\n1,2\n'
        assert response.headers['content-type'].startswith('text/csv')

    async def test_missing_file_is_400(self, client, task_ok, task_dir):
        # starlette's FileResponse raises RuntimeError for missing paths -> middleware maps it to 400.
        response = await client.get('/api/pipes/artefacts/file', params={'filename': f'{TASK_ID}/nope.csv'}, headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'RuntimeError'
        assert 'does not exist' in response.json()['detail']['message']

    async def test_filename_is_required(self, client, task_ok, task_dir):
        response = await client.get('/api/pipes/artefacts/file', headers=TASK_HEADERS)
        assert response.status_code == 422

    async def test_path_traversal_is_not_guarded(self, client, task_ok, pipes_data, task_dir):
        """Latent bug: `filename` is joined onto the artefact root without sanitisation, so `..` escapes it."""
        (pipes_data / 'secret.txt').write_text('topsecret')
        response = await client.get('/api/pipes/artefacts/file', params={'filename': '../secret.txt'}, headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.text == 'topsecret'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/artefacts/file', params={'filename': 'x.txt'}, headers=TASK_HEADERS)
        assert response.status_code == 401


class TestGetTaskLog:
    async def test_returns_full_log_content(self, client, task_ok, task_dir):
        (task_dir / 'progress.log').write_text('hello\nworld\n')
        response = await client.get('/api/pipes/artefacts/log', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.json() == 'hello\nworld\n'

    async def test_missing_log_is_400(self, client, task_ok, task_dir):
        """Latent bug: the route may return None but declares `response_model=str` -> ResponseValidationError -> 400."""
        response = await client.get('/api/pipes/artefacts/log', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ResponseValidationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/artefacts/log', headers=TASK_HEADERS)
        assert response.status_code == 401


class TestStreamTaskLog:
    async def test_streams_lines_as_plain_text(self, client, task_ok, task_dir, monkeypatch):
        # The real stream_log never terminates cleanly (see the tests below), so the route-level
        # wiring (media type, header, chunked body) is exercised with a finite generator.
        monkeypatch.setattr(pipelines_route, 'stream_log', lambda task_id: iter(['first\n', 'second\n']))
        response = await client.get('/api/pipes/artefacts/log-stream', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.headers['content-type'] == 'text/plain; charset=utf-8'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.text == 'first\nsecond\n'

    async def test_missing_log_crashes_the_stream(self, client, task_ok, task_dir):
        """Latent bug: stream_log raises StopIteration when the log file is missing; since the response
        headers are already out, the error escapes the middleware and the client sees a dead stream."""
        with pytest.raises(RuntimeError):
            await client.get('/api/pipes/artefacts/log-stream', headers=TASK_HEADERS)

    def test_stream_log_yields_each_line_twice(self, monkeypatch, pipes_data):
        """Unit view: every complete line is yielded raw *and* stripped (server/pipelines/files.py:36-49)."""
        (pipes_data / 'artefacts' / TASK_ID).mkdir()
        (pipes_data / 'artefacts' / TASK_ID / 'progress.log').write_text('l1\nl2\n')
        monkeypatch.setattr(time, 'sleep', lambda seconds: None)
        assert list(islice(stream_log(TASK_ID), 4)) == ['l1\n', 'l1', 'l2\n', 'l2']

    def test_stream_log_finishes_with_runtime_error(self, monkeypatch, pipes_data):
        """Unit view: even a normal "no new lines" termination raises StopIteration inside the generator,
        which PEP 479 turns into a RuntimeError for the consumer."""
        (pipes_data / 'artefacts' / TASK_ID).mkdir()
        (pipes_data / 'artefacts' / TASK_ID / 'progress.log').write_text('l1\n')
        monkeypatch.setattr(time, 'sleep', lambda seconds: None)
        with pytest.raises(RuntimeError, match='generator raised StopIteration'):
            list(stream_log(TASK_ID))


class TestGetArtefactArchive:
    async def test_returns_zip_archive_of_task_directory(self, client, task_ok, task_dir):
        (task_dir / 'out.txt').write_text('abc')
        response = await client.get('/api/pipes/artefacts/files', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/zip'
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        assert len(names) == 1
        assert names[0].endswith(f'{TASK_ID}/out.txt')
        assert archive.read(names[0]) == b'abc'

    async def test_missing_directory_yields_empty_zip(self, client, task_ok, pipes_data):
        """os.walk over a missing task dir silently produces nothing instead of an error."""
        response = await client.get('/api/pipes/artefacts/files', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/artefacts/files', headers=TASK_HEADERS)
        assert response.status_code == 401


class TestUploadFile:
    async def test_upload_stores_sanitized_filename(self, client, pipes_data):
        response = await client.post(
            '/api/pipes/artefacts/files/upload', params={'folder': 'myfolder'}, files={'file': ('my file!!v2.txt', b'content', 'text/plain')}
        )
        assert response.status_code == 200
        assert response.json() == 'myfolder/my-filev2.txt'
        assert (pipes_data / 'user_data' / 'myfolder' / 'my-filev2.txt').read_bytes() == b'content'

    async def test_folder_defaults_to_generated_uuid(self, client, pipes_data):
        response = await client.post('/api/pipes/artefacts/files/upload', files={'file': ('notes.txt', b'x', 'text/plain')})
        assert response.status_code == 200
        folder, filename = response.json().split('/')
        UUID(folder)  # raises if the auto-generated folder is not a uuid
        assert filename == 'notes.txt'
        assert (pipes_data / 'user_data' / folder / 'notes.txt').read_bytes() == b'x'

    async def test_unicode_filenames_are_transliterated(self, client, pipes_data):
        response = await client.post('/api/pipes/artefacts/files/upload', params={'folder': 'f'}, files={'file': ('café ★.txt', b'x', 'text/plain')})
        assert response.status_code == 200
        # NFKD + ascii-ignore drops the accent and the star; the leftover space becomes '-'.
        assert response.json() == 'f/cafe-.txt'

    async def test_folder_is_a_query_parameter_not_a_form_field(self, client, pipes_data):
        """Quirk: `folder` is only bound from the query string; sending it as multipart form data is ignored."""
        response = await client.post('/api/pipes/artefacts/files/upload', data={'folder': 'info-folder'}, files={'file': ('a.txt', b'x', 'text/plain')})
        assert response.status_code == 200
        assert not response.json().startswith('info-folder/')

    async def test_missing_file_part_is_422(self, client, pipes_data):
        response = await client.post('/api/pipes/artefacts/files/upload', data={'something': 'else'})
        assert response.status_code == 422

    async def test_requires_artefacts_edit_permission(self, permissions, client, pipes_data):
        permissions.grant(PROJECT_ID, owner=False, artefacts_edit=False)
        response = await client.post('/api/pipes/artefacts/files/upload', files={'file': ('a.txt', b'x', 'text/plain')})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/pipes/artefacts/files/upload', files={'file': ('a.txt', b'x', 'text/plain')})
        assert response.status_code == 401


class TestUploadMany:
    async def test_uploads_all_files_into_one_folder(self, client, pipes_data):
        response = await client.post(
            '/api/pipes/artefacts/files/upload-many',
            params={'folder': 'bulk'},
            files=[('file', ('a.txt', b'1', 'text/plain')), ('file', ('b.txt', b'22', 'text/plain'))],
        )
        assert response.status_code == 200
        assert response.json() == ['bulk/a.txt', 'bulk/b.txt']
        assert (pipes_data / 'user_data' / 'bulk' / 'a.txt').read_bytes() == b'1'
        assert (pipes_data / 'user_data' / 'bulk' / 'b.txt').read_bytes() == b'22'

    async def test_requires_artefacts_edit_permission(self, permissions, client, pipes_data):
        permissions.grant(PROJECT_ID, owner=False, artefacts_edit=False)
        response = await client.post('/api/pipes/artefacts/files/upload-many', files=[('file', ('a.txt', b'1', 'text/plain'))])
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/pipes/artefacts/files/upload-many', files=[('file', ('a.txt', b'1', 'text/plain'))])
        assert response.status_code == 401


class TestSearchTasks:
    async def test_forwards_filters_to_query_tasks(self, client, monkeypatch):
        seen: dict = {}

        async def fake_query_tasks(db_engine, project_id, order_by_fields, **params):
            seen.update(project_id=project_id, order_by_fields=order_by_fields, params=params)
            return [make_task()]

        patch_crud(monkeypatch, pipelines_route, query_tasks=fake_query_tasks)
        response = await client.get('/api/pipes/tasks', params={'function_name': 'do_it', 'status': 'RUNNING', 'user_id': USER_ID})
        assert response.status_code == 200
        assert seen['project_id'] == PROJECT_ID
        assert seen['params'] == {'function_name': 'do_it', 'user_id': USER_ID, 'status': 'RUNNING'}
        assert seen['order_by_fields'] is None
        body = response.json()
        assert len(body) == 1
        assert body[0]['task_id'] == TASK_ID
        assert body[0]['status'] == 'PENDING'

    async def test_no_tasks_becomes_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, pipelines_route, query_tasks=None)
        response = await client.get('/api/pipes/tasks')
        assert response.status_code == 200
        assert response.json() == []

    async def test_order_by_fields_are_never_forwarded(self, client, monkeypatch):
        """Latent bug (pipelines.py:153): the comprehension guard `if (parts := field.split(',')) is None`
        is always False, so a valid `order_by_fields` parameter is validated but then dropped (sent as [])."""
        seen: dict = {}

        async def fake_query_tasks(db_engine, project_id, order_by_fields, **params):
            seen['order_by_fields'] = order_by_fields
            return None

        patch_crud(monkeypatch, pipelines_route, query_tasks=fake_query_tasks)
        response = await client.get('/api/pipes/tasks', params={'order_by_fields': 'time_created,asc'})
        assert response.status_code == 200
        assert seen['order_by_fields'] == []

    async def test_invalid_status_is_422(self, client, monkeypatch):
        patch_crud(monkeypatch, pipelines_route, query_tasks=None)
        response = await client.get('/api/pipes/tasks', params={'status': 'BOGUS'})
        assert response.status_code == 422

    async def test_invalid_order_by_format_is_422(self, client, monkeypatch):
        patch_crud(monkeypatch, pipelines_route, query_tasks=None)
        response = await client.get('/api/pipes/tasks', params={'order_by_fields': 'not-a-valid-order'})
        assert response.status_code == 422

    async def test_requires_pipelines_read_permission(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, pipelines_read=False)
        patch_crud(monkeypatch, pipelines_route, query_tasks=None)
        response = await client.get('/api/pipes/tasks')
        assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client, monkeypatch):
        patch_crud(monkeypatch, pipelines_route, query_tasks=None)
        response = await no_project_client.get('/api/pipes/tasks')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/tasks')
        assert response.status_code == 401


class TestGetTask:
    async def test_returns_task_via_crud_and_fake_db(self, client, fake_db):
        # Drives the real `read_task_by_id` through the fake session and validates its ORM row.
        fake_db.queue(FakeResult(rows=[db_task()]))
        response = await client.get('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body['task_id'] == TASK_ID
        assert body['project_id'] == PROJECT_ID
        assert body['status'] == 'PENDING'
        assert body['params'] == {'x': 1}

    async def test_unknown_task_is_400(self, client, monkeypatch):
        patch_crud(monkeypatch, pipelines_security, read_task_by_id=None)
        response = await client.get('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'UnknownTaskID'

    async def test_task_from_another_project_is_403(self, client, monkeypatch):
        patch_crud(monkeypatch, pipelines_security, read_task_by_id=make_task(project_id=OTHER_PROJECT_ID))
        response = await client.get('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 403

    async def test_requires_pipelines_edit_permission(self, permissions, client, task_ok):
        permissions.grant(PROJECT_ID, owner=False, pipelines_edit=False)
        response = await client.get('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 403

    async def test_requires_task_header(self, client, task_ok):
        response = await client.get('/api/pipes/task')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 401


class TestDeleteTask:
    async def test_removes_the_task_artefact_directory(self, client, task_ok, task_dir):
        response = await client.delete('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 200
        assert response.json() is None
        assert not task_dir.exists()

    async def test_missing_directory_is_400(self, client, task_ok, pipes_data):
        response = await client.delete('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingFileError'

    async def test_directory_with_artefacts_cannot_be_deleted(self, client, task_ok, task_dir):
        """Latent bug: delete_directory (server/util/files.py:49) uses rmdir, so any non-empty
        artefact directory fails the cascade delete with a bare OSError (400)."""
        (task_dir / 'keepme.txt').write_text('x')
        response = await client.delete('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'OSError'
        assert task_dir.exists()

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete('/api/pipes/task', headers=TASK_HEADERS)
        assert response.status_code == 401


class TestTerminateDramatiqTask:
    async def test_superuser_aborts_the_message(self, superuser_client, monkeypatch):
        aborted: list = []
        monkeypatch.setattr(pipelines_route, 'abort', lambda message_id: aborted.append(message_id))
        response = await superuser_client.delete('/api/pipes/dramatiq/task', params={'message_id': 'msg-42'})
        assert response.status_code == 200
        assert response.json() is None
        assert aborted == ['msg-42']

    async def test_does_not_need_a_project_header(self, make_client, monkeypatch):
        """The endpoint only guards on superuser status, not on project permissions."""
        aborted: list = []
        monkeypatch.setattr(pipelines_route, 'abort', lambda message_id: aborted.append(message_id))
        async with make_client(SUPERUSER_TOKEN, project_id=None) as superuser_no_project:
            response = await superuser_no_project.delete('/api/pipes/dramatiq/task', params={'message_id': 'msg-7'})
            assert response.status_code == 200
        assert aborted == ['msg-7']

    async def test_regular_user_is_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(pipelines_route, 'abort', lambda message_id: None)
        response = await client.delete('/api/pipes/dramatiq/task', params={'message_id': 'msg-42'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_message_id_is_required(self, superuser_client, monkeypatch):
        monkeypatch.setattr(pipelines_route, 'abort', lambda message_id: None)
        response = await superuser_client.delete('/api/pipes/dramatiq/task')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete('/api/pipes/dramatiq/task', params={'message_id': 'msg-42'})
        assert response.status_code == 401
