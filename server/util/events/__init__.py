from typing import Union, Literal, TYPE_CHECKING

from pymitter import EventEmitter

from .hooks import imports
from . import events

eventbus = EventEmitter(delimiter='_', wildcard=True)
if TYPE_CHECKING:
    AnyEvent = events.BaseEvent
    AnyEventType = str
    AnyEventLiteral = str
else:
    AnyEvent = Union[events.BaseEvent.get_subclasses()]
    AnyEventType = Literal[tuple(sc.__name__ for sc in events.BaseEvent.get_subclasses())]  # noqa PyProtectedMember
    AnyEventLiteral = Literal[tuple(sc._name for sc in events.BaseEvent.get_subclasses())]  # noqa PyProtectedMember

# Permanent/global listeners
eventbus.on(events.PipelineTaskStatusChangedEvent._name, imports.update_import_status)  # noqa PyProtectedMember

__all__ = ['eventbus', 'events', 'AnyEvent', 'AnyEventType']
