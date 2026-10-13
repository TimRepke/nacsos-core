# NACSOS API test suite

`pytest` based tests for the REST API defined in `server/api/routes/`.

Run everything:

```bash
uv run pytest                       # or: .venv/bin/python -m pytest
uv run pytest tests/api/test_users.py -q
uv run pytest --cov=server.api --cov-report=term-missing
```

No PostgreSQL, Redis or network access is required: the data layer and the authentication
helper are replaced by in-process test doubles for every test (autouse fixtures in
[`conftest.py`](conftest.py)). Tests that ever need a live database should be marked
`@pytest.mark.integration` (registered in `pyproject.toml`).

## Layout

| Path                       | Purpose                                                             |
| -------------------------- | ------------------------------------------------------------------- |
| `conftest.py`              | app/client fixtures, fake auth, permission broker, API call recorder |
| `mocks.py`                 | `FakeDatabase` (session/connection double), `patch_crud`            |
| `factories.py`             | fixed IDs plus builders for pydantic models and transient ORM rows  |
| `test_mocks.py`            | tests for the doubles themselves (they guard the whole suite)       |
| `api/test_*.py`            | one module per router module in `server/api/routes/`                |
| `api/test_api_coverage.py` | fails if any OpenAPI operation is never called (see below)          |

## Fixtures

| Fixture             | What you get                                                             |
| ------------------- | ------------------------------------------------------------------------ |
| `client`            | logged in as a regular member of `PROJECT_ID` (`X-Project-Id` set)        |
| `superuser_client`  | logged in as a platform superuser                                         |
| `inactive_client`   | authenticated but deactivated account (403 from the active-user check)    |
| `anon_client`       | no token, `X-Project-Id` present (expect 401)                             |
| `bare_client`       | no token, no headers                                                      |
| `make_client`       | factory: `make_client(token=..., project_id=..., **extra_headers)`        |
| `no_project_client` | authenticated but **without** `X-Project-Id` (expect 422)                 |
| `fake_db`           | scripted database double (see below)                                      |
| `permissions`       | broker controlling `auth_helper.check_permissions` answers                |
| `app`               | the real FastAPI application (session scoped)                             |

Async test functions need no decorator (`asyncio_mode = 'auto'`).

## Scripting the database

Routes reach the database either through `nacsos_data.db.crud` helpers or through raw
sessions. Both are covered:

```python
from server.api.routes import users as users_route
from tests.mocks import FakeResult, patch_crud
from tests.factories import db_user, make_user


async def test_list_all(client, monkeypatch, fake_db):
    # 1) CRUD helpers imported into the route module are patched by name ...
    patch_crud(monkeypatch, users_route, read_users=[make_user()])
    # ... values that are not callables are wrapped in an async function automatically,
    #     pass a coroutine function when you need the call arguments.

    # 2) raw sessions hand out queued results in FIFO order ...
    fake_db.queue(FakeResult(rows=[db_user()], scalar=42))
    fake_db.queue_rows(db_user(), db_user(username='other'))   # shorthand for rows only
    fake_db.set_default_rows(db_user())                        # fallback once the queue drains

    response = await client.get('/api/users/list/all')
    assert response.status_code == 200
    assert fake_db.commits == 0            # also: .flushes, .rollbacks, .added, .deleted
    assert 'user' in fake_db.executions[0].table_name          # statements are recorded
```

`FakeResult` is chainable, so `.scalars()`, `.mappings()`, `.unique()` all work and
`.all()`, `.first()`, `.one_or_none()`, `.one()`, `.scalar()` behave like SQLAlchemy's.

`FakeSession` mirrors `AsyncSession`'s sync/async split, which `tests/test_mocks.py` checks
method by method: `add`/`add_all` are **plain methods** (calling `session.add(row)` without
awaiting is correct), while `execute`, `scalars`, `get`, `delete`, `flush`, `commit`, ... are
coroutines. `session.get(Model, id)` returns the instance (or `None`) and, like every other
statement, is recorded in `fake_db.executions` — so the statement a route runs *after* a `get()`
lookup is `fake_db.executions[-1]`, not `[0]`.

## Transient ORM rows

`tests/factories.py` builds rows without a session (`db_user`, `db_project`, `db_scheme`,
`db_scope`, `db_assignment`, `db_import`, `db_auth_token`, `db_project_permissions`) plus a
generic `orm_obj(SomeSchema, **columns)`. They are real `nacsos_data.db.schemas` objects, so
`SomeModel.model_validate(row.__dict__)` inside a route works exactly like in production — and
mutating them is how you assert that a write path really changed something.

Module-specific builders belong in the test module that needs them; keep `factories.py` for
things several modules share.

## Authentication and permissions

`auth_helper.get_user` / `check_password` / token methods are faked against a fixed token table
(`USER_TOKEN`, `SUPERUSER_TOKEN`, `INACTIVE_TOKEN`, `NO_PERMISSION_TOKEN`), so unknown tokens
produce the API's real 401 behaviour. Permission checks go through the `permissions` broker:

```python
async def test_needs_edit_permission(client, permissions):
    permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)   # default is owner=True
    response = await client.get(f'/api/users/details/{USER_ID}')
    assert response.status_code == 403

    permissions.grant(PROJECT_ID)          # restore full access
    permissions.deny(PROJECT_ID)           # no permission record at all -> also 403
```

`FORBIDDEN_PROJECT_ID` has no grant by default, which is the quickest way to assert the 403 path.

## Error contract to assert against

`ErrorHandlingMiddleware` (`server/util/middlewares.py`) turns any exception/warning escaping a
route into an `HTTPException` whose status is the exception's `status` attribute, defaulting to
**400** (`server/api/errors.py` documents those codes). The body is

```json
{"detail": {"level": "ERROR", "type": "UserNotFoundError", "message": "...", "args": [...]}}
```

Warnings get `level == "WARNING"`. Two gotchas worth remembering:

* exceptions raised by *dependencies* (e.g. `InsufficientPermissions` -> 403) travel the same path;
* `DataNotFoundWarning` has status **204**, and starlette strips the body of 204 responses, so
  assert `response.content == b''` instead of parsing JSON.

Routes guarded by `UserPermissionChecker` require the `X-Project-Id` header; `UserPriorityPermissionChecker`
additionally requires `X-Priority-Id`. A missing required header results in **422**.

## Patching other external I/O

Nothing but the database is faked automatically, so patch what a route touches:

```python
# dramatiq task submission (would need Redis)
monkeypatch.setattr(tasks.sleepy.sleep_task, 'send', recorder)
# outbound mail -> patch the send helper from server.util.email
# OpenAlex/Solr HTTP calls in search.py -> patch OpenAlexSolrAPI / the module level httpx client
```

## Every endpoint must be exercised

`api/test_api_coverage.py` compares the OpenAPI schema against the requests actually made through
the client fixtures (`API_CALLS` in `conftest.py`) and fails if any operation was never called.
Concrete paths are matched against the templates, so hitting `/api/project/items/academic/list`
does not count for the paging variant `/api/project/items/{item_type}/list/{page}/{page_size}`.
The record is only complete once everything else ran, so `pytest_collection_modifyitems` moves
that test to the end of the session — running it on its own therefore fails by design.

To find out which endpoints a single module covers, run it together with the guard:

```bash
uv run pytest tests/api/test_users.py tests/api/test_api_coverage.py -q   # lists the gaps
```

## Conventions

* One test module per router module, named `test_<router>.py`, with a module docstring pointing
  at the route file it covers.
* Group tests per endpoint with a `class Test<Endpoint>` when a module grows large.
* Cover, per endpoint: the happy path with canned data, authentication (401), and the interesting
  failure modes (403 / 404-or-400 / 422 / 204). Assert on response bodies, not just status codes.
* Tests must not touch the network, Redis, SMTP or PostgreSQL, must not sleep, and must be
  order-independent.
* Follow the project's ruff settings (`uv run ruff check tests --fix`, `uv run ruff format tests`):
  single quotes, 160 columns.
