"""Tests for the /api/ping endpoints (server/api/routes/ping.py)."""

import pytest

from server.pipelines import tasks
from server.api.routes import ping
from tests.mocks import FakeResult


async def test_ping_returns_pong(client):
    response = await client.get('/api/ping/')
    assert response.status_code == 200
    assert response.text == 'pong'


async def test_ping_is_public(bare_client):
    """Ping does not require authentication."""
    response = await bare_client.get('/api/ping/')
    assert response.status_code == 200
    assert response.text == 'pong'


async def test_ping_name_greeting(client):
    response = await client.post('/api/ping/nacsos')
    assert response.status_code == 200
    assert response.text == 'Hello nacsos'


async def test_ping_error_is_reported_as_400(client):
    """Unhandled route exceptions are converted into 400 with an ErrorDetail body."""
    response = await client.get('/api/ping/error')
    assert response.status_code == 400
    detail = response.json()['detail']
    assert detail['type'] == 'ExampleError'
    assert detail['level'] == 'ERROR'
    assert detail['message'] == 'Error in your face!'


async def test_ping_warning_reports_warning_level(client):
    """Warnings are reported with WARNING level (and the default 400 status)."""
    response = await client.get('/api/ping/warn')
    assert response.status_code == 400
    detail = response.json()['detail']
    assert detail['type'] == 'ExampleWarning'
    assert detail['level'] == 'WARNING'


async def test_ping_permission_endpoint_is_forbidden(client):
    """InsufficientPermissions maps to 403 including the WWW-Authenticate header."""
    response = await client.get('/api/ping/permission')
    assert response.status_code == 403
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.json()['detail']['type'] == 'InsufficientPermissions'


async def test_ping_database_returns_project_count(client, fake_db):
    fake_db.queue(FakeResult(scalar=7))
    response = await client.get('/api/ping/database')
    assert response.status_code == 200
    assert response.json() == 7
    # the query really went to the (fake) engine against the projects table
    assert fake_db.executions[0].statement is not None


async def test_ping_database_fails_when_query_returns_no_scalar(client, fake_db):
    """Degenerate case: the count query yields no value at all, which the route cannot format."""
    response = await client.get('/api/ping/database')
    assert response.status_code == 400
    assert response.json()['detail']['type'] == 'TypeError'


@pytest.fixture
def sent_messages(monkeypatch):
    """Collect dramatiq messages instead of publishing them to Redis."""
    calls: list[dict] = []

    def recorder(task):
        def send(*args, **kwargs):
            calls.append({'task': task, 'args': args, 'kwargs': kwargs})

        return send

    monkeypatch.setattr(tasks.sleepy.sleep_task, 'send', recorder('sleep_task'))
    monkeypatch.setattr(tasks.sleepy.tracked_sleep_task, 'send', recorder('tracked_sleep_task'))
    return calls


async def test_sleep_task_schedules_task(client, sent_messages):
    response = await client.get('/api/ping/sleep-task', params={'sleep_time': 3})
    assert response.status_code == 200
    assert sent_messages == [{'task': 'sleep_task', 'args': (), 'kwargs': {'sleep_time': 3}}]


async def test_sleep_task_uses_default_sleep_time(client, sent_messages):
    response = await client.get('/api/ping/sleep-task')
    assert response.status_code == 200
    assert sent_messages[0]['kwargs'] == {'sleep_time': 10}


async def test_tracked_sleep_task_schedules_tracked_task(client, sent_messages):
    response = await client.get('/api/ping/tracked-sleep-task', params={'sleep_time': 1})
    assert response.status_code == 200
    message = sent_messages[0]
    assert message['task'] == 'tracked_sleep_task'
    assert message['kwargs']['sleep_time'] == 1
    assert message['kwargs']['comment'] == 'Pinged sleeping task'
    assert message['kwargs']['project_id']


async def test_ping_endpoints_are_tagged_and_documented(app):
    """Sanity check on how the ping router is mounted into the API."""
    schema = app.openapi()
    assert '/api/ping/' in schema['paths']
    assert '/api/ping/{name}' in schema['paths']


async def test_ping_route_uses_plain_text_response():
    routes = {route.path: route for route in ping.router.routes}
    assert routes['/'].response_class.__name__ == 'PlainTextResponse'
