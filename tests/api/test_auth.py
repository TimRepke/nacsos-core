"""Tests for the /api/login endpoints (server/api/routes/auth.py)."""

from tests.factories import OTHER_TOKEN_ID, TOKEN_ID, USER_ID, USER_TOKEN, db_auth_token

LOGIN_FORM = {'username': 'testuser', 'password': 'testpassword'}


class TestLoginToken:
    async def test_valid_credentials_return_a_token(self, bare_client):
        response = await bare_client.post('/api/login/token', data=LOGIN_FORM)
        assert response.status_code == 200
        body = response.json()
        assert body['token_id'] == USER_TOKEN
        assert body['username'] == 'testuser'

    async def test_wrong_password_is_unauthenticated(self, bare_client):
        response = await bare_client.post('/api/login/token', data={**LOGIN_FORM, 'password': 'wrong'})
        assert response.status_code == 401
        assert response.json()['detail']['type'] == 'NotAuthenticated'

    async def test_missing_password_field_fails_validation(self, bare_client):
        response = await bare_client.post('/api/login/token', data={'username': 'testuser'})
        assert response.status_code == 422

    async def test_no_credentials_at_all_fails_validation(self, bare_client):
        response = await bare_client.post('/api/login/token')
        assert response.status_code == 422


class TestRefreshToken:
    async def test_refresh_returns_token_for_current_user(self, client):
        response = await client.put(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 200
        assert response.json()['token_id'] == TOKEN_ID

    async def test_refresh_requires_authentication(self, anon_client):
        response = await anon_client.put(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 401

    async def test_refresh_rejects_invalid_token_id(self, client, monkeypatch):
        from server.api.routes import auth

        async def failing_refresh(*args, **kwargs):
            raise AssertionError('token not owned by user')

        monkeypatch.setattr(auth.auth_helper, 'refresh_or_create_token', failing_refresh)
        response = await client.put(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 401

    async def test_refresh_rejects_inactive_user(self, inactive_client):
        response = await inactive_client.put(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'


class TestRevokeToken:
    async def test_revoke_token_succeeds(self, client):
        response = await client.delete(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 200

    async def test_revoke_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 401

    async def test_revoke_propagates_errors(self, client, monkeypatch):
        from server.api.routes import auth

        async def failing_clear(*args, **kwargs):
            raise RuntimeError('delete failed')

        monkeypatch.setattr(auth.auth_helper, 'clear_token_by_id', failing_clear)
        response = await client.delete(f'/api/login/token/{TOKEN_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'RuntimeError'


class TestMyTokens:
    async def test_lists_tokens_of_current_user(self, client, fake_db):
        fake_db.queue_rows(db_auth_token(token_id=TOKEN_ID), db_auth_token(token_id=OTHER_TOKEN_ID))
        response = await client.get('/api/login/my-tokens')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 2
        assert {token['token_id'] for token in body} == {TOKEN_ID, OTHER_TOKEN_ID}
        assert all(token['username'] == 'testuser' for token in body)

    async def test_no_tokens_gives_gone(self, client, fake_db):
        response = await client.get('/api/login/my-tokens')
        assert response.status_code == 410
        assert response.json()['detail']['type'] == 'NoDataForKeyError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/login/my-tokens')
        assert response.status_code == 401


class TestCurrentUser:
    async def test_me_returns_the_authenticated_user(self, client):
        response = await client.get('/api/login/me')
        assert response.status_code == 200
        body = response.json()
        assert body['user_id'] == USER_ID
        assert body['username'] == 'testuser'
        assert body['is_superuser'] is False
        # the API must never leak the password hash
        assert 'password' not in body

    async def test_me_for_superuser(self, superuser_client):
        response = await superuser_client.get('/api/login/me')
        assert response.status_code == 200
        assert response.json()['is_superuser'] is True

    async def test_me_without_token_is_unauthenticated(self, bare_client):
        response = await bare_client.get('/api/login/me')
        assert response.status_code == 401
        assert response.headers['www-authenticate'] == 'Bearer'

    async def test_me_with_garbage_token_is_unauthenticated(self, make_client):
        async with make_client('not-a-real-token') as client:
            response = await client.get('/api/login/me')
            assert response.status_code == 401

    async def test_me_for_inactive_user_is_forbidden(self, inactive_client):
        response = await inactive_client.get('/api/login/me')
        assert response.status_code == 403


class TestLogout:
    async def test_logout_clears_tokens(self, client, monkeypatch):
        cleared: list[str] = []

        from server.api.routes import auth

        async def capture(username):
            cleared.append(username)

        monkeypatch.setattr(auth.auth_helper, 'clear_tokens_by_user', capture)
        response = await client.get('/api/login/logout')
        assert response.status_code == 200
        assert cleared == ['testuser']

    async def test_logout_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/login/logout')
        assert response.status_code == 401
