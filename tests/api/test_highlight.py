"""Tests for the /api/highlighters endpoints (server/api/routes/highlight.py).

All three routes work off raw sessions, so the rows are scripted via ``fake_db``; the not-found path of
``GET /api/highlighters/{highlighter_id}`` raises ``NoDataForKeyError``, which — despite its name — carries
status **410 Gone** (server/api/errors.py:12-13), not 404.
"""

from typing import Any
from uuid import UUID


from nacsos_data.db.schemas.highlight import Highlighter

from tests.factories import FORBIDDEN_PROJECT_ID, HIGHLIGHTER_ID, OTHER_PROJECT_ID, PROJECT_ID, orm_obj
from tests.mocks import compiled

OTHER_HIGHLIGHTER_ID = 'd4d4d4d4-d4d4-d4d4-d4d4-d4d4d4d4d4d4'


def db_highlighter(
    highlighter_id: str = HIGHLIGHTER_ID,
    project_id: str = PROJECT_ID,
    name: str = 'Query terms',
    keywords: list[str] | None = None,
    style: str | None = 'background-color: #123456',
    **columns: Any,
) -> Highlighter:
    values: dict[str, Any] = {
        'highlighter_id': UUID(highlighter_id),
        'project_id': UUID(project_id),
        'name': name,
        'keywords': list(keywords or ['climate', 'health']),
        'style': style,
    }
    values.update(columns)
    return orm_obj(Highlighter, **values)


def highlighter_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'highlighter_id': HIGHLIGHTER_ID,
        'project_id': PROJECT_ID,
        'name': 'Query terms',
        'keywords': ['climate', 'health'],
        'style': 'color: red',
    }
    payload.update(overrides)
    return payload


class TestGetProjectHighlighters:
    async def test_returns_all_project_highlighters(self, client, fake_db):
        fake_db.queue_rows(db_highlighter(), db_highlighter(highlighter_id=OTHER_HIGHLIGHTER_ID, name='Other', keywords=['x'], style=None))

        response = await client.get('/api/highlighters/project')

        assert response.status_code == 200
        assert response.json() == [
            {
                'highlighter_id': HIGHLIGHTER_ID,
                'project_id': PROJECT_ID,
                'name': 'Query terms',
                'keywords': ['climate', 'health'],
                'style': 'background-color: #123456',
            },
            {'highlighter_id': OTHER_HIGHLIGHTER_ID, 'project_id': PROJECT_ID, 'name': 'Other', 'keywords': ['x'], 'style': None},
        ]
        assert 'FROM highlighters' in compiled(fake_db.executions[0].statement)
        assert fake_db.commits == 0

    async def test_no_highlighters_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/highlighters/project')

        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_annotations_read_flag(self, client, permissions):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False)
        response = await client.get('/api/highlighters/project')
        assert response.status_code == 403

        permissions.grant(PROJECT_ID, owner=False, annotations_read=True)
        response = await client.get('/api/highlighters/project')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/highlighters/project')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get('/api/highlighters/project')
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/highlighters/project')
        assert response.status_code == 401


class TestUpsertProjectHighlighter:
    async def test_update_existing_row(self, client, fake_db):
        stored = db_highlighter(name='Old name', keywords=['old'], style=None)
        fake_db.queue_rows(stored)

        response = await client.put('/api/highlighters/project', json=highlighter_payload())

        assert response.status_code == 200
        assert response.json() == HIGHLIGHTER_ID  # response_model=str echoes the highlighter_id
        assert stored.name == 'Query terms'  # the route mutates the loaded ORM row in place
        assert stored.keywords == ['climate', 'health']
        assert stored.style == 'color: red'
        assert fake_db.added == []
        assert fake_db.commits == 1

    async def test_create_when_id_is_unknown(self, client, fake_db):
        fake_db.queue_rows()  # nothing matches project_id + highlighter_id -> insert path

        response = await client.put('/api/highlighters/project', json=highlighter_payload())

        assert response.status_code == 200
        assert response.json() == HIGHLIGHTER_ID
        assert len(fake_db.added) == 1
        new_row = fake_db.added[0]
        assert isinstance(new_row, Highlighter)
        assert str(new_row.highlighter_id) == HIGHLIGHTER_ID
        assert str(new_row.project_id) == PROJECT_ID
        assert new_row.name == 'Query terms'
        assert new_row.keywords == ['climate', 'health']
        assert new_row.style == 'color: red'
        assert fake_db.commits == 1

    async def test_payload_for_another_project_is_forbidden(self, client, fake_db):
        """The permission check only sees the X-Project-Id header; the route then compares it with the body."""
        response = await client.put('/api/highlighters/project', json=highlighter_payload(project_id=OTHER_PROJECT_ID))

        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'
        assert response.json()['detail']['message'] == "Project IDs don't match!"
        assert fake_db.commits == 0

    async def test_requires_edit_permission_flag(self, client, permissions, fake_db):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.put('/api/highlighters/project', json=highlighter_payload())
        assert response.status_code == 403
        assert fake_db.commits == 0

    async def test_missing_required_fields_fail_validation(self, client):
        response = await client.put('/api/highlighters/project', json={'highlighter_id': HIGHLIGHTER_ID})

        assert response.status_code == 422
        missing = {tuple(err['loc']) for err in response.json()['detail'] if err['type'] == 'missing'}
        assert ('body', 'project_id') in missing
        assert ('body', 'name') in missing
        assert ('body', 'keywords') in missing

    async def test_wrong_keyword_type_fails_validation(self, client):
        """keywords is list[str]; a JSON string is not coerced into a list of one character strings."""
        response = await client.put('/api/highlighters/project', json=highlighter_payload(keywords='climate'))

        assert response.status_code == 422
        assert any(err['type'] == 'list_type' for err in response.json()['detail'])

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.put('/api/highlighters/project', json=highlighter_payload())
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/highlighters/project', json=highlighter_payload())
        assert response.status_code == 401


class TestGetHighlighter:
    async def test_returns_highlighter_by_id(self, client, fake_db):
        fake_db.queue_rows(db_highlighter())

        response = await client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')

        assert response.status_code == 200
        assert response.json() == {
            'highlighter_id': HIGHLIGHTER_ID,
            'project_id': PROJECT_ID,
            'name': 'Query terms',
            'keywords': ['climate', 'health'],
            'style': 'background-color: #123456',
        }
        assert 'FROM highlighters' in compiled(fake_db.executions[0].statement)

    async def test_unknown_id_is_gone_not_missing(self, client, fake_db):
        """NoDataForKeyError has status 410 — so an unknown highlighter answers Gone, not 404 (errors.py:12-13)."""
        response = await client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')

        assert response.status_code == 410
        detail = response.json()['detail']
        assert detail['type'] == 'NoDataForKeyError'
        assert f'No highlighter in project {PROJECT_ID} with id {HIGHLIGHTER_ID}!' == detail['message']

    async def test_non_uuid_id_is_not_validated(self, client, fake_db):
        """The path param is a plain str handed to the query (highlight.py:51) — no UUID validation, still 410 here."""
        response = await client.get('/api/highlighters/not-a-uuid')

        assert response.status_code == 410
        assert 'not-a-uuid' in response.json()['detail']['message']

    async def test_requires_annotations_read_flag(self, client, permissions, fake_db):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False)
        response = await client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')
        assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/highlighters/{HIGHLIGHTER_ID}')
        assert response.status_code == 401
