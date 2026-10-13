"""Tests for the /api/users endpoints (server/api/routes/users.py)."""

from server.api.routes import users as users_route
from tests.factories import (
    FORBIDDEN_PROJECT_ID,
    OTHER_PROJECT_ID,
    OTHER_USER_ID,
    PROJECT_ID,
    USER_ID,
    db_user,
    make_user,
)
from tests.mocks import patch_crud


def _user_payload(user_id: str = USER_ID, username: str = 'testuser', **overrides) -> dict:
    payload = {
        'user_id': user_id,
        'username': username,
        'email': f'{username}@example.org',
        'full_name': username.title(),
        'affiliation': 'ACME',
        'is_active': True,
        'is_superuser': False,
    }
    payload.update(overrides)
    return payload


class TestListAllUsers:
    async def test_returns_all_users(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_users=[make_user(), make_user(user_id=OTHER_USER_ID, username='other')])
        response = await client.get('/api/users/list/all')
        assert response.status_code == 200
        assert {user['username'] for user in response.json()} == {'testuser', 'other'}

    async def test_no_users_yields_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_users=None)
        response = await client.get('/api/users/list/all')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/users/list/all')
        assert response.status_code == 401

    async def test_passwords_are_not_exposed(self, client, monkeypatch):
        from nacsos_data.models.users import UserInDBModel

        patch_crud(monkeypatch, users_route, read_users=[UserInDBModel(**_user_payload(), password='secret-hash')])
        response = await client.get('/api/users/list/all')
        assert response.status_code == 200
        assert 'password' not in response.json()[0]


class TestListAllUsersDehydrated:
    async def test_returns_minimal_user_fields(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_users=[make_user()])
        response = await client.get('/api/users/list/all/dehydrated')
        assert response.status_code == 200
        assert response.json() == [{'user_id': USER_ID, 'username': 'testuser', 'full_name': 'Testuser'}]

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/users/list/all/dehydrated')
        assert response.status_code == 401


class TestListProjectAnnotators:
    async def test_returns_users_keyed_by_id(self, client, fake_db):
        fake_db.queue_rows(db_user(), db_user(user_id=OTHER_USER_ID, username='other'))
        response = await client.get(f'/api/users/list/project/annotators/{PROJECT_ID}')
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {USER_ID, OTHER_USER_ID}
        assert body[USER_ID]['username'] == 'testuser'

    async def test_no_annotators_returns_empty_object(self, client, fake_db):
        response = await client.get(f'/api/users/list/project/annotators/{PROJECT_ID}')
        assert response.status_code == 200
        assert response.json() == {}

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'/api/users/list/project/annotators/{PROJECT_ID}')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/users/list/project/annotators/{FORBIDDEN_PROJECT_ID}')
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/users/list/project/annotators/{PROJECT_ID}')
        assert response.status_code == 401


class TestListProjectUsers:
    async def test_returns_users_for_project(self, client, monkeypatch):
        captured: dict = {}

        async def fake_read_users(project_id, order_by_username, engine):
            captured['project_id'] = project_id
            captured['order_by_username'] = order_by_username
            return [make_user()]

        patch_crud(monkeypatch, users_route, read_users=fake_read_users)
        response = await client.get(f'/api/users/list/project/{OTHER_PROJECT_ID}')
        assert response.status_code == 200
        assert [user['user_id'] for user in response.json()] == [USER_ID]
        assert captured == {'project_id': OTHER_PROJECT_ID, 'order_by_username': True}

    async def test_missing_users_gives_no_content(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_users=None)
        response = await client.get(f'/api/users/list/project/{PROJECT_ID}')
        assert response.status_code == 204
        assert response.content == b''

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/users/list/project/{FORBIDDEN_PROJECT_ID}')
            assert response.status_code == 403


class TestUserDetails:
    async def test_get_user_by_id(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_user_by_id=make_user())
        response = await client.get(f'/api/users/details/{USER_ID}')
        assert response.status_code == 200
        assert response.json()['user_id'] == USER_ID

    async def test_unknown_user_id_is_an_error(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_user_by_id=None)
        response = await client.get(f'/api/users/details/{USER_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'UserNotFoundError'

    async def test_get_user_requires_edit_permission(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        patch_crud(monkeypatch, users_route, read_user_by_id=make_user())
        response = await client.get(f'/api/users/details/{USER_ID}')
        assert response.status_code == 403

    async def test_get_users_by_ids(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read_users_by_ids(user_ids, engine):
            seen['user_ids'] = list(user_ids)
            return [make_user()]

        patch_crud(monkeypatch, users_route, read_users_by_ids=fake_read_users_by_ids)
        response = await client.get('/api/users/details', params={'user_id': [USER_ID, OTHER_USER_ID]})
        assert response.status_code == 200
        assert seen['user_ids'] == [USER_ID, OTHER_USER_ID]
        assert len(response.json()) == 1

    async def test_get_users_by_ids_requires_the_query_parameter(self, client):
        response = await client.get('/api/users/details')
        assert response.status_code == 422

    async def test_get_unknown_users_is_an_error(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, read_users_by_ids=None)
        response = await client.get('/api/users/details', params={'user_id': [USER_ID]})
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'UserNotFoundError'


class TestSaveUser:
    async def test_user_may_update_self(self, client, monkeypatch):
        calls: list = []

        async def fake_create_or_update_user(user, engine):
            calls.append(user)
            return str(user.user_id)

        patch_crud(monkeypatch, users_route, create_or_update_user=fake_create_or_update_user)
        response = await client.put('/api/users/details', json=_user_payload())
        assert response.status_code == 200
        assert response.json() == USER_ID
        assert len(calls) == 1

    async def test_updating_another_user_requires_superuser(self, client, monkeypatch):
        patch_crud(monkeypatch, users_route, create_or_update_user='ignored')
        response = await client.put('/api/users/details', json=_user_payload(user_id=OTHER_USER_ID, username='other'))
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'UserPermissionError'

    async def test_superuser_may_update_any_user(self, superuser_client, monkeypatch):
        async def fake_create_or_update_user(user, engine):
            return 'saved'

        patch_crud(monkeypatch, users_route, create_or_update_user=fake_create_or_update_user)
        response = await superuser_client.put('/api/users/details', json=_user_payload(user_id=OTHER_USER_ID, username='other'))
        assert response.status_code == 200
        assert response.json() == 'saved'

    async def test_non_object_payload_fails_validation(self, client):
        response = await client.put('/api/users/details', json=['not', 'an', 'object'])
        assert response.status_code == 422

    async def test_partial_payload_without_user_id_is_rejected(self, client):
        """All model fields are optional, so a body without user_id fails on the ownership check instead."""
        response = await client.put('/api/users/details', json={'username': 'someone-else'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'UserPermissionError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/users/details', json=_user_payload())
        assert response.status_code == 401


class TestSaveOwnDetails:
    async def test_updates_only_own_record(self, client, fake_db):
        stored = db_user(email='old@example.org', full_name='Old Name', affiliation='Old', setting_newsletter=False)
        fake_db.queue_rows(stored)
        payload = _user_payload(email='new@example.org', full_name='New Name')
        payload['affiliation'] = 'New Affiliation'
        payload['setting_newsletter'] = True

        response = await client.put('/api/users/my-details', json=payload)
        assert response.status_code == 200
        assert response.json() == USER_ID
        assert stored.email == 'new@example.org'
        assert stored.full_name == 'New Name'
        assert stored.affiliation == 'New Affiliation'
        assert stored.setting_newsletter is True
        assert fake_db.commits == 1

    async def test_password_is_hashed_not_stored_verbatim(self, client, fake_db):
        stored = db_user()
        fake_db.queue_rows(stored)
        payload = {**_user_payload(), 'password': 'plain-text-password'}

        response = await client.put('/api/users/my-details', json=payload)
        assert response.status_code == 200
        assert stored.password is not None
        assert stored.password != 'plain-text-password'

    async def test_cannot_edit_another_account(self, client, fake_db):
        fake_db.queue_rows(db_user())
        response = await client.put('/api/users/my-details', json=_user_payload(user_id=OTHER_USER_ID, username='other'))
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'UserPermissionError'

    async def test_missing_own_record_reports_no_content(self, client, fake_db):
        """DataNotFoundWarning carries status 204, and 204 responses are body-less by definition."""
        response = await client.put('/api/users/my-details', json=_user_payload())
        assert response.status_code == 204
        assert response.content == b''
