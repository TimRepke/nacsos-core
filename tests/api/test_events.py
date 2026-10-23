"""Tests for the /api/events endpoints (server/api/routes/events.py).

The event bus (pymitter ``EventEmitter``) is patched at ``eventbus.emit_async`` so tests observe what the route emits
without any listeners firing. Note that the route carries *no* authentication (FIXME in events.py:25) — the tests
document that openly.
"""

from typing import Any

import pytest

from server.api.routes import events as events_route
from server.util.events import events


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Records (event_name, event) pairs handed to the event bus."""
    calls: list[tuple[str, Any]] = []

    async def record(event_name: str, event: Any) -> None:
        calls.append((event_name, event))

    monkeypatch.setattr(events_route.eventbus, 'emit_async', record)
    return calls


class TestEmit:
    async def test_emit_reaches_the_eventbus(self, superuser_client, emitted):
        response = await superuser_client.post('/api/events/emit', json={'event': 'ExampleEvent', 'payload': {'payload_a': 'x'}})

        assert response.status_code == 200
        assert response.text == 'null'  # the route returns None
        assert len(emitted) == 1
        event_name, event = emitted[0]
        assert isinstance(event, events.ExampleEvent)
        assert event.payload_a == 'x'
        assert event_name == event.name
        # The emitted name is *not* reliably 'Example_*': the payload is first validated against Union[AnyEvent] and
        # pydantic's smart union may materialise {'payload_a': ...} as the more specific ExampleSubEvent (the union
        # order comes from a set() in BaseEvent.get_subclasses), and re-validating into ExampleEvent keeps that
        # subclass instance as-is — so the name that reaches the bus is either ExampleEvent.name or ExampleSubEvent.name.
        assert event_name in {events.ExampleEvent.name, events.ExampleSubEvent.name}

    async def test_emit_sub_event_by_name(self, superuser_client, emitted):
        """
        Latent race in the app (server/util/events/__init__.py:14-15 + events.py:39): the payload union is built from
        ``BaseEvent.get_subclasses()``, which iterates a *set*, and pydantic's smart union materialises
        ``{'payload_a': ...}`` as the first exactly-matching member. If that is the base ExampleEvent, re-validating the
        already-validated model instance into ExampleSubEvent raises a pydantic ValidationError -> middleware default
        400. Which of the two outcomes happens changes between interpreter runs, so assert both branches faithfully.
        """
        response = await superuser_client.post('/api/events/emit', json={'event': 'ExampleSubEvent', 'payload': {'payload_a': 'y'}})

        if response.status_code == 400:
            assert response.json()['detail']['type'] == 'ValidationError'
            assert emitted == []
        else:
            assert response.status_code == 200
            assert response.text == 'null'
            assert len(emitted) == 1
            event_name, event = emitted[0]
            # events.ExampleSubEvent.name is a ClassVar and the route re-validates into the requested type, so
            # whenever the bus is reached at all, it receives the sub event's name.
            assert event_name == 'Example_sub'
            assert isinstance(event, events.ExampleEvent)  # ExampleSubEvent subclasses ExampleEvent
            assert event.payload_a == 'y'

    async def test_route_is_not_authenticated_at_all(self, bare_client, emitted):
        """No token, no project header — still emits (the FIXME at events.py:25 is accurate: nothing guards this)."""
        response = await bare_client.post('/api/events/emit', json={'event': 'ExampleEvent', 'payload': {'payload_a': 'anonymous'}})

        assert response.status_code == 200
        assert len(emitted) == 1
        assert emitted[0][1].payload_a == 'anonymous'

    async def test_unknown_event_name_fails_validation(self, anon_client, emitted):
        """'event' is a Literal of the known class names, so unknown names are rejected by pydantic with 422;
        the UnknownEventError branch (events.py:34-37, middleware default status 400) is unreachable dead code."""
        response = await anon_client.post('/api/events/emit', json={'event': 'SomeUnknownEvent', 'payload': {'payload_a': 'x'}})

        assert response.status_code == 422
        assert response.json()['detail'][0]['type'] == 'literal_error'
        assert emitted == []

    async def test_missing_payload_field_fails_validation(self, anon_client, emitted):
        response = await anon_client.post('/api/events/emit', json={'event': 'ExampleEvent', 'payload': {}})

        assert response.status_code == 422
        assert any(err['loc'][-1] == 'payload_a' for err in response.json()['detail'])
        assert emitted == []

    async def test_missing_event_field_fails_validation(self, anon_client, emitted):
        response = await anon_client.post('/api/events/emit', json={'payload': {'payload_a': 'x'}})

        assert response.status_code == 422
        assert emitted == []

    async def test_empty_body_fails_validation(self, anon_client, emitted):
        response = await anon_client.post('/api/events/emit')

        assert response.status_code == 422
        assert emitted == []
