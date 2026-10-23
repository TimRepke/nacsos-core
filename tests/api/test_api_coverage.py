"""
Guard: every operation published by the OpenAPI schema must be exercised by the suite.

This is what keeps "we have tests for the API" true as routes are added: a new endpoint without
any test makes this fail. Requests are recorded by the client fixtures (see ``API_CALLS`` in
conftest.py), and concrete paths are matched against the schema's path templates, so
``/api/project/items/academic/list`` counts for ``/api/project/items/{item_type}/list`` but not for
the paging variant ``/api/project/items/{item_type}/list/{page}/{page_size}``.

conftest.py moves this test to the end of the run, since the record is only complete afterwards.
"""

import re
from typing import Any

import pytest

from tests.conftest import API_CALLS


def _template_matcher(template: str) -> re.Pattern[str]:
    """Compile an OpenAPI path template into a regex where every {parameter} is one path segment."""
    parts = ['[^/]+' if segment.startswith('{') else re.escape(segment) for segment in template.split('/')]
    return re.compile('^' + '/'.join(parts) + '$')


def _operations(app: Any) -> list[tuple[str, str]]:
    paths = app.openapi()['paths']
    return sorted((method.upper(), path) for path, operations in paths.items() for method in operations)


def test_openapi_schema_lists_the_api_routes(app):
    operations = _operations(app)
    assert len(operations) > 100, 'expected the full API surface in the schema'
    assert ('GET', '/api/ping/') in operations
    assert ('PUT', '/api/annotations/schemes/definition/') in operations


def test_every_documented_operation_is_exercised(app: Any) -> None:
    """Every (method, path) of the schema must have been called at least once by some test."""
    operations = _operations(app)
    calls = sorted(API_CALLS)
    matchers = [(method, path, _template_matcher(path)) for method, path in operations]

    unexercised = [
        f'{method} {path}'
        for method, path, matcher in matchers
        if not any(call_method == method and matcher.match(call_path) for call_method, call_path in calls)
    ]

    assert not unexercised, f'{len(unexercised)} of {len(operations)} API operations are not exercised by any test:\n  ' + '\n  '.join(unexercised)


@pytest.fixture(autouse=True)
def _recorded_calls_marker(request: pytest.FixtureRequest) -> None:
    """Document on the report which operations have been called so far (useful with -s)."""
    if request.config.getoption('verbose') and request.node.name == 'test_every_documented_operation_is_exercised':
        print(f'\n{len(API_CALLS)} distinct (method, path) calls recorded for the coverage check')
