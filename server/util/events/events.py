from typing import ClassVar

from pydantic import BaseModel


class BaseEvent(BaseModel):
    name: ClassVar[str]

    @classmethod
    def get_subclasses(cls):
        def recurse(sub_cls):
            if hasattr(sub_cls, '__subclasses__'):
                for sc in sub_cls.__subclasses__():
                    return sub_cls.__subclasses__() + recurse(sc)
            return []

        return tuple(set(recurse(cls)))


class ExampleEvent(BaseEvent):
    name = 'Example_*'
    payload_a: str


class ExampleSubEvent(ExampleEvent):
    name = 'Example_sub'
