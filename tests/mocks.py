"""
Test doubles for the NACSOS data layer.

The API routes access persistence in two ways:

1. via ``nacsos_data.db.crud`` helper functions (``read_users(..., engine=db_engine)`` ...)
2. via raw sessions/connections (``async with db_engine.session() as session: ...``)

Neither of them should require a running PostgreSQL instance during unit tests, so this module
provides a scripted in-memory stand-in for :class:`nacsos_data.db.DatabaseEngineAsync`
(:class:`FakeDatabase`) plus a convenience helper to patch CRUD functions
(:func:`patch_crud`).
"""

from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Iterable, Iterator, Sequence

import sqlalchemy as sa

from server.data import db_engine

_SENTINEL = object()


class MultipleResultsFound(Exception):
    """Stand-in for ``sqlalchemy.exc.MultipleResultsFound`` raised by scripted results."""


class FakeResult:
    """
    Mimics the subset of SQLAlchemy's ``Result``/``ScalarResult``/``MappingResult``
    interface that the API routes use. The result object is chainable, so
    ``.scalars()``/``.mappings()``/``.unique()`` return the very same object.
    """

    def __init__(self, rows: Iterable[Any] | None = None, scalar: Any = _SENTINEL) -> None:
        self._rows: list[Any] = list(rows) if rows is not None else []
        self._scalar = scalar

    # --- chainable views -------------------------------------------------
    def scalars(self) -> 'FakeResult':
        return self

    def scalar_items(self) -> 'FakeResult':
        return self

    def mappings(self) -> 'FakeResult':
        return self

    def tuples(self) -> 'FakeResult':
        return self

    def unique(self) -> 'FakeResult':
        return self

    def partitions(self) -> 'FakeResult':
        return self

    # --- terminal accessors ----------------------------------------------
    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> Any:
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise MultipleResultsFound(f'Scenario returned {len(self._rows)} rows, expected at most one')
        return self._rows[0]

    def one(self) -> Any:
        if not self._rows:
            raise MultipleResultsFound('Scenario returned no rows, expected exactly one')
        if len(self._rows) > 1:
            raise MultipleResultsFound(f'Scenario returned {len(self._rows)} rows, expected exactly one')
        return self._rows[0]

    def scalar_one_or_none(self) -> Any:
        return self.one_or_none()

    def scalar(self) -> Any:
        if self._scalar is not _SENTINEL:
            return self._scalar
        return self.first()

    def keys(self) -> list[str]:
        return []

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __bool__(self) -> bool:
        return bool(self._rows)


class FakeExecution:
    """Records what was executed against a fake session/connection."""

    def __init__(self, statement: Any, parameters: Any, kwargs: dict[str, Any]) -> None:
        self.statement = statement
        self.parameters = parameters
        self.kwargs = kwargs

    @property
    def table_name(self) -> str | None:
        """Best effort extraction of the primary table a statement talks to (None for text()/complex joins)."""
        return _primary_table(self.statement)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f'<FakeExecution {self.table_name} :: {str(self.statement)[:120]}>'


class FakeConnection:
    """Fake for an ``AsyncConnection`` (``db_engine.engine.connect()``)."""

    def __init__(self, database: 'FakeDatabase') -> None:
        self._db = database

    async def __aenter__(self) -> 'FakeConnection':
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute(self, statement: Any, parameters: Any = None, **kwargs: Any) -> FakeResult:
        return self._db._next_result(FakeExecution(statement, parameters, kwargs))

    async def scalars(self, statement: Any, parameters: Any = None) -> FakeResult:
        result = self._db._next_result(FakeExecution(statement, parameters, {}))
        return result

    async def scalar(self, statement: Any, parameters: Any = None) -> Any:
        return self._db._next_result(FakeExecution(statement, parameters, {})).scalar()

    async def commit(self) -> None:
        self._db.commits += 1

    async def rollback(self) -> None:
        self._db.rollbacks += 1

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> Iterator['FakeConnection']:
        yield self


class FakeSession(FakeConnection):
    """
    Fake for ``DBSession`` (``db_engine.session()``).

    Mirrors ``AsyncSession``'s sync/async split: ``add``/``add_all`` are plain methods,
    everything that touches the database is a coroutine.
    """

    def add(self, instance: Any) -> None:
        self._db.added.append(instance)

    def add_all(self, instances: Iterable[Any]) -> None:
        self._db.added.extend(instances)

    async def delete(self, instance: Any) -> None:
        self._db.deleted.append(instance)

    async def flush(self, object_list: Any = None) -> None:
        self._db.flushes += 1

    async def flush_or_commit(self) -> None:
        self._db.flushes += 1

    async def refresh(self, instance: Any, attribute_names: Any = None) -> None:
        return None

    async def merge(self, instance: Any) -> Any:
        return instance

    async def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        """Like ``AsyncSession.get``: the instance itself, or None."""
        result = self._db._next_result(FakeExecution(entity, ident, kwargs))
        return result.first()

    def __await__(self) -> Any:  # pragma: no cover - safety net for `await session` patterns
        async def _self() -> 'FakeSession':
            return self

        return _self().__await__()


class FakeDatabase:
    """
    Scripted stand-in for ``DatabaseEngineAsync``.

    Results are handed out in the order they are queued (FIFO) and fall back to
    ``default`` (an empty result) once the queue is exhausted:

        fake_db.queue(FakeResult(rows=[user_orm]))   # first query in the request
        fake_db.queue(FakeResult(rows=[]))           # second query in the request

    Every statement executed during the request is recorded in ``fake_db.executions``.
    """

    def __init__(self, default: FakeResult | None = None) -> None:
        self.queued: deque[FakeResult] = deque()
        self.executions: list[FakeExecution] = []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self._default = default

    # --- scripting -------------------------------------------------------
    def queue(self, *results: FakeResult | Sequence[Any]) -> 'FakeDatabase':
        """Queue results (or plain row sequences) to be returned by upcoming queries."""
        for result in results:
            self.queued.append(result if isinstance(result, FakeResult) else FakeResult(rows=result))
        return self

    def queue_rows(self, *rows: Any) -> 'FakeDatabase':
        """Queue a single result containing the given rows."""
        return self.queue(FakeResult(rows=list(rows)))

    def default_result(self) -> FakeResult:
        return self._default if self._default is not None else FakeResult(rows=[])

    def set_default_rows(self, *rows: Any) -> 'FakeDatabase':
        self._default = FakeResult(rows=list(rows))
        return self

    def statements(self) -> list[str]:
        return [str(e.statement) for e in self.executions]

    # --- engine interface ------------------------------------------------
    def _next_result(self, execution: FakeExecution) -> FakeResult:
        self.executions.append(execution)
        if self.queued:
            return self.queued.popleft()
        return self.default_result()

    @asynccontextmanager
    async def session(self, use_commit: bool = False) -> Iterator[FakeSession]:
        session = FakeSession(self)
        try:
            yield session
        except Exception:
            self.rollbacks += 1
            raise

    @property
    def engine(self) -> 'FakeEngine':
        if not hasattr(self, '_engine'):
            self._engine = FakeEngine(self)
        return self._engine

    async def startup(self) -> None:
        return None


class FakeEngine:
    """Fake for the SQLAlchemy engine exposed as ``db_engine.engine``."""

    def __init__(self, database: FakeDatabase) -> None:
        self._db = database

    def connect(self) -> FakeConnection:
        return FakeConnection(self._db)


def install_fake_database(monkeypatch: Any, database: FakeDatabase) -> FakeDatabase:
    """
    Route the shared ``server.data.db_engine`` singleton through ``database``.

    The engine object itself is kept (routes import the instance, not the name),
    only its session/engine accessors are replaced.
    """
    monkeypatch.setattr(db_engine, 'session', database.session)
    monkeypatch.setattr(db_engine, 'engine', database.engine)
    monkeypatch.setattr(db_engine, 'startup', database.startup)
    return database


def patch_crud(monkeypatch: Any, target_module: Any, **replacements: Any) -> dict[str, Any]:
    """
    Replace CRUD helpers as imported into ``target_module`` (the route module).

    A replacement may be
      * an awaitable/callable returning the value to be returned,
      * any other value, which is wrapped into an async function returning it.

    Returns the mapping of patched attribute names (originals are restored by monkeypatch).
    """
    patched: dict[str, Any] = {}
    for name, replacement in replacements.items():
        original = getattr(target_module, name, None)
        if original is None:
            raise AttributeError(f'{target_module.__name__} has no attribute {name!r} to patch')
        if callable(replacement) or hasattr(replacement, '__await__'):
            new_value = replacement
        else:
            new_value = _async_constant(replacement)
        monkeypatch.setattr(target_module, name, new_value)
        patched[name] = original
    return patched


def _async_constant(value: Any) -> Any:
    async def _constant(*args: Any, **kwargs: Any) -> Any:
        return value

    return _constant


def _primary_table(statement: Any) -> str | None:
    """Name of the table a statement primarily operates on (None for text()/unresolvable statements)."""
    sources: Any = None
    get_final_froms = getattr(statement, 'get_final_froms', None)
    if callable(get_final_froms):
        try:
            sources = get_final_froms()
        except Exception:
            sources = None
    if not sources:
        sources = getattr(statement, 'froms', None)
    try:
        sources = list(sources or [])
    except TypeError:
        sources = [sources] if sources is not None else []
    for source in sources:
        for candidate in (getattr(source, 'table', None), getattr(source, '__table__', None), source if hasattr(source, 'columns') else None):
            name = getattr(candidate, 'name', None)
            if isinstance(name, str):
                return name
    # INSERT/UPDATE/DELETE statements carry their table directly
    name = getattr(getattr(statement, 'table', None), 'name', None)
    return name if isinstance(name, str) else None


def compiled(statement: Any, literal: bool = True) -> str:
    """Compile a statement to SQL text (useful in assertions and debugging)."""
    if isinstance(statement, str):
        return statement
    try:
        return str(statement.compile(dialect=sa.dialects.postycopg.dialect(), compile_kwargs={'literal_binds': literal}))
    except Exception:
        return str(statement)
