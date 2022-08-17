from typing import Literal, ClassVar

from pydantic import BaseModel


class BaseEvent(BaseModel):
    _name = ClassVar[str]

    @classmethod
    def get_subclasses(cls):
        def recurse(sub_cls):
            if hasattr(sub_cls, '__subclasses__'):
                for sc in sub_cls.__subclasses__():
                    return sub_cls.__subclasses__() + recurse(sc)
            return []

        return tuple(set(recurse(cls)))


TaskStatus = Literal['PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED']  # FIXME move somewhere else


class PipelineTaskStatusChangedEvent(BaseEvent):
    """
    Emitted when the pipeline service calls the nacsos-core API and tells it about a status change of a task
    """
    _name = 'PipelineTaskStatus_*'
    task_id: str
    status: TaskStatus
    project_id: str
    user_id: str
    function_name: str  # incl module path


class PipelineTaskStatusCompletedEvent(PipelineTaskStatusChangedEvent):
    """
    More specific version of `PipelineTaskStatusChangedEvent` emitted when a task finished (successfully/completed)
    """
    _name = 'PipelineTask_completed'


class PipelineTaskStatusStartedEvent(PipelineTaskStatusChangedEvent):
    """
    More specific version of `PipelineTaskStatusChangedEvent` emitted when a task started
    """
    _name = 'PipelineTask_started'


class PipelineTaskStatusFailedEvent(PipelineTaskStatusChangedEvent):
    """
    More specific version of `PipelineTaskStatusChangedEvent` emitted when a task finished (failed)
    """
    _name = 'PipelineTask_failed'
