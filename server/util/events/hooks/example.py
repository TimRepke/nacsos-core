import logging

from ..events import ExampleEvent

logger = logging.getLogger('nacsos.event-hooks.test')


def test_listener(event: ExampleEvent):
    logger.debug(f'Received event {event}')
