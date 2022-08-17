from typing import Type
from fastapi import APIRouter
from pydantic import BaseModel

from ...util.events import eventbus, events, AnyEvent, AnyEventType
from ...util.logging import get_logger

logger = get_logger('nacsos.api.route.events')
router = APIRouter()

logger.debug('Setup nacsos.api.route.events router')


class Event(BaseModel):
    event: AnyEventType
    payload: AnyEvent


class UnknownEventError(Exception):
    pass


@router.post('/emit')
async def emit(event: Event) -> None:
    """
    This route can be used to trigger an event on the system.
    FIXME: This should require some sort of authentication!

    :param event: event (incl optional payload) to emit
    :return: void
    """
    logger.info(f'Received external event to be emitted: {event.event}')

    if hasattr(events, event.event):
        EmitEvent: Type[AnyEvent] = getattr(events, event.event)
        emit_event = EmitEvent.parse_obj(event.payload)
        logger.debug(f'Going to emit {EmitEvent} ({emit_event})')
        await eventbus.emit_async(emit_event._name, emit_event)  # noqa PyProtectedMember
    else:
        raise UnknownEventError(f'Event {event.event} not in {AnyEvent}')

# TODO user-configurable triggers (e.g. trigger on event or cron-like)
#      - create schema, model, crud in nacsos-data (probably could just be a JSONB field in `Project`
#      - create @startup function that sets up all listeners
#      - list all triggers
#      - add trigger
#      - remove trigger
#      - update trigger
