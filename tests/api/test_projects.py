"""Tests for the /api/projects endpoints (server/api/routes/projects.py)."""

import uuid

import pytest

from nacsos_data.db.schemas import Project

from server.util.security import auth_helper

from tests.factories import PROJECT_ID, USER_ID, db_project, make_user


def _mapping_row(project=None, owners='default'):
    """One row of the projects/list query: the ORM Project plus a JSON-ified owners list."""
    if owners == 'default':
        owners = [{'user_id': USER_ID, 'username': 'testuser', 'full_name': 'Testuser'}]
    return {'Project': project or db_project(), 'owners': owners}


class TestListProjects:
    async def test_returns_projects_with_owners(self, client, fake_db):
        fake_db.queue_rows(_mapping_row())
        response = await client.get('/api/projects/list')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['project_id'] == PROJECT_ID
        assert body[0]['name'] == 'Test Project'
        assert body[0]['setting_motivational_quotes'] is True
        assert body[0]['owners'] == [
            {
                'user_id': USER_ID,
                'username': 'testuser',
                'full_name': 'Testuser',
                'email': None,
                'affiliation': None,
                'is_superuser': False,
                'is_active': True,
                'setting_newsletter': False,
                'time_created': None,
                'time_updated': None,
            }
        ]

    async def test_projects_without_owners_get_empty_list(self, client, fake_db):
        # SQL array_agg over an outer join yields NULL -> the route maps that to [].
        fake_db.queue_rows(_mapping_row(owners=None))
        response = await client.get('/api/projects/list')
        assert response.status_code == 200
        assert response.json()[0]['owners'] == []

    async def test_regular_users_get_permission_filtered_query(self, client, fake_db):
        fake_db.queue_rows()
        response = await client.get('/api/projects/list')
        assert response.status_code == 200
        assert response.json() == []
        statement = fake_db.executions[0].statement
        sql = str(statement)
        # The query joins the permissions table to limit results to the user's own projects...
        assert 'JOIN project_permissions ON project.project_id = project_permissions.project_id' in sql
        assert 'project_permissions.user_id' in sql
        # ... filtered on the current user's id.
        assert str(USER_ID) in str(statement.compile().params)

    async def test_superuser_query_skips_permission_join(self, superuser_client, fake_db):
        fake_db.queue_rows(_mapping_row())
        response = await superuser_client.get('/api/projects/list')
        assert response.status_code == 200
        assert len(response.json()) == 1
        statement = fake_db.executions[0].statement
        sql = str(statement)
        # The owners CTE still mentions project_permissions, but the per-user filter join is gone.
        assert 'JOIN project_permissions' not in sql
        assert str(USER_ID) not in str(statement.compile().params)

    async def test_user_without_id_is_an_error(self, client, fake_db, monkeypatch):
        async def ghost_get_user(token_id=None, **kwargs):
            return make_user(user_id=None, username='ghost')

        monkeypatch.setattr(auth_helper, 'get_user', ghost_get_user)
        response = await client.get('/api/projects/list')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingInformationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/projects/list')
        assert response.status_code == 401


class TestCreateProject:
    async def test_superuser_creates_project(self, superuser_client, fake_db, monkeypatch):
        response = await superuser_client.put('/api/projects/create', json={'name': 'My New Project', 'type': 'generic'})
        assert response.status_code == 200
        new_id = response.json()
        uuid.UUID(new_id)  # server-generated id
        assert len(fake_db.added) == 1
        added = fake_db.added[0]
        assert isinstance(added, Project)
        assert added.name == 'My New Project'
        assert added.type == 'generic'
        assert str(added.project_id) == new_id
        assert fake_db.commits == 1

    async def test_explicit_project_id_is_kept(self, superuser_client, fake_db, monkeypatch):
        response = await superuser_client.put('/api/projects/create', json={'project_id': PROJECT_ID, 'name': 'Fixed Id', 'type': 'academic'})
        assert response.status_code == 200
        assert response.json() == PROJECT_ID
        assert fake_db.added[0].project_id == PROJECT_ID
        assert fake_db.commits == 1

    async def test_regular_user_is_rejected(self, client):
        response = await client.put('/api/projects/create', json={'name': 'Nope', 'type': 'academic'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_inactive_user_is_rejected(self, inactive_client):
        response = await inactive_client.put('/api/projects/create', json={'name': 'Nope', 'type': 'academic'})
        assert response.status_code == 403
        assert 'Inactive' in response.json()['detail']['message']

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/projects/create', json={'name': 'Nope', 'type': 'academic'})
        assert response.status_code == 401

    @pytest.mark.parametrize('payload', [{'type': 'academic'}, {'name': 'No Type'}, {'name': 'Bad Type', 'type': 'banana'}])
    async def test_invalid_body_fails_validation(self, superuser_client, payload):
        # ProjectModel requires both `name` and `type`, and `type` is constrained to the item-type literals.
        response = await superuser_client.put('/api/projects/create', json=payload)
        assert response.status_code == 422
