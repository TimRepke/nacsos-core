from typing import ClassVar

from pydantic import BaseModel


class BaseEvent(BaseModel):
    name: ClassVar[str]

    @classmethod
    def get_subclasses(cls):  # type: ignore[no-untyped-def]
        def recurse(sub_cls):  # type: ignore[no-untyped-def]
            if hasattr(sub_cls, '__subclasses__'):
                for sc in sub_cls.__subclasses__():
                    return sub_cls.__subclasses__() + recurse(sc)  # type: ignore[no-untyped-call]
            return []

        return tuple(set(recurse(cls)))  # type: ignore[no-untyped-call]


class ExampleEvent(BaseEvent):
    name = 'Example_*'
    payload_a: str


class ExampleSubEvent(ExampleEvent):
    name = 'Example_sub'
