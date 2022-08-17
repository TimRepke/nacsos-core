from typing import Union, Literal

from pymitter import EventEmitter

from .hooks import imports
from . import events

eventbus = EventEmitter(delimiter='_', wildcard=True)
AnyEvent = Union[events.BaseEvent.get_subclasses()]
AnyEventType = Literal[tuple(sc._name for sc in events.BaseEvent.get_subclasses())] # noqa PyProtectedMember

# Permanent/global listeners
eventbus.on(events.PipelineTaskStatusChangedEvent._name, imports.update_import_status)  # noqa PyProtectedMember

__all__ = ['eventbus', 'events', 'AnyEvent', 'AnyEventType']
