"""Tests for the /api/search endpoints (server/api/routes/search.py).

The OpenAlex/Solr endpoints talk to a remote search core, so the Solr client and the module level
``httpx`` usage are replaced by recording doubles: no test here opens a socket.
"""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from nacsos_data.db.schemas.items.academic import AcademicItem
from nacsos_data.util.academic.apis.openalex.solr import SearchResult

from server.api.routes import search as search_route

from tests.factories import FORBIDDEN_PROJECT_ID, ITEM_ID, PROJECT_ID, orm_obj
from tests.mocks import FakeResult

VALID_FILTER: dict[str, Any] = {'filter': 'field', 'field': 'title', 'value': 'climate'}


def _academic_item(item_id: str = ITEM_ID, **columns: Any) -> AcademicItem:
    values: dict[str, Any] = {
        'item_id': UUID(item_id),
        'project_id': UUID(PROJECT_ID),
        'type': 'academic',
        'title': 'Adaptation finance in West Africa',
        'publication_year': 2021,
    }
    values.update(columns)
    return orm_obj(AcademicItem, **values)


class _RecordingSolrAPI:
    """
    Stand-in for ``OpenAlexSolrAPI``: calling it plays the constructor (and hands back itself),
    so the route's "instantiate and query in one expression" is fully recorded.
    """

    def __init__(self) -> None:
        self.init_kwargs: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.result = SearchResult(query_time=3, num_found=2, docs=[{'item_id': ITEM_ID, 'title': 'Adaptation finance'}], histogram={'2021': 2})

    def __call__(self, **kwargs: Any) -> '_RecordingSolrAPI':
        self.init_kwargs = kwargs
        return self

    def query(self, **kwargs: Any) -> SearchResult:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> _RecordingSolrAPI:
    recorder = _RecordingSolrAPI()
    monkeypatch.setattr(search_route, 'OpenAlexSolrAPI', recorder)
    return recorder


class _FakeSolrHTTP:
    """Serves canned JSON to the route's ``httpx.AsyncClient`` instead of the real Solr core."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.kwargs: list[dict[str, Any]] = []
        self.payload: dict[str, Any] = {'terms': {'title_abstract': []}}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> '_FakeSolrHTTP':
        # patch the name the route looks up, not the httpx module itself
        monkeypatch.setattr(search_route, 'httpx', SimpleNamespace(AsyncClient=self._client))
        return self

    def _client(self, *args: Any, **kwargs: Any) -> '_FakeSolrClient':
        self.kwargs.append(dict(kwargs))
        return _FakeSolrClient(self)


class _FakeSolrResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSolrClient:
    def __init__(self, http: _FakeSolrHTTP) -> None:
        self._http = http

    async def __aenter__(self) -> '_FakeSolrClient':
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeSolrResponse:
        self._http.urls.append(url)
        return _FakeSolrResponse(self._http.payload)


class TestOpenAlexSelect:
    async def test_queries_solr_and_returns_the_result(self, client, solr):
        response = await client.post('/api/search/openalex/select', json={'query': '"climate adaptation"', 'limit': 5, 'offset': 10, 'histogram': True})

        assert response.status_code == 200
        assert response.json() == {
            'query_time': 3,
            'num_found': 2,
            'docs': [
                {
                    'item_id': ITEM_ID,
                    'project_id': None,
                    'type': 'academic',
                    'time_edited': None,
                    'text': None,
                    'doi': None,
                    'wos_id': None,
                    'scopus_id': None,
                    'openalex_id': None,
                    's2_id': None,
                    'pubmed_id': None,
                    'dimensions_id': None,
                    'title': 'Adaptation finance',
                    'title_slug': None,
                    'publication_year': None,
                    'source': None,
                    'keywords': None,
                    'authors': None,
                    'meta': None,
                }
            ],
            'histogram': {'2021': 2},
        }

    async def test_search_options_are_passed_to_the_client(self, client, solr):
        response = await client.post(
            '/api/search/openalex/select',
            json={
                'query': 'governance',
                'limit': 7,
                'offset': 21,
                'def_type': 'dismax',
                'field': 'abstract',
                'op': 'OR',
                'histogram': True,
                'histogram_from': 2000,
                'histogram_to': 2020,
                'params': {'fl': 'id'},
            },
        )

        assert response.status_code == 200
        assert solr.init_kwargs['include_histogram'] is True
        assert solr.init_kwargs['histogram_from'] == 2000
        assert solr.init_kwargs['histogram_to'] == 2020
        assert solr.init_kwargs['def_type'] == 'dismax'
        assert solr.init_kwargs['field'] == 'abstract'
        assert solr.init_kwargs['op'] == 'OR'
        assert solr.init_kwargs['openalex_conf'] is search_route.settings.OPENALEX
        # the payload is forwarded as query/offset/limit/params
        assert solr.calls == [{'query': 'governance', 'offset': 21, 'limit': 7, 'params': {'fl': 'id'}}]

    async def test_defaults_are_applied(self, client, solr):
        response = await client.post('/api/search/openalex/select', json={'query': 'x'})

        assert response.status_code == 200
        assert solr.calls == [{'query': 'x', 'offset': 0, 'limit': 20, 'params': None}]
        assert solr.init_kwargs['include_histogram'] is False

    async def test_query_is_required(self, client):
        response = await client.post('/api/search/openalex/select', json={'limit': 5})
        assert response.status_code == 422

    async def test_unknown_def_type_is_rejected(self, client):
        response = await client.post('/api/search/openalex/select', json={'query': 'x', 'def_type': 'sqlite'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/search/openalex/select', json={'query': 'x'})
        assert response.status_code == 401

    async def test_requires_openalex_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, search_oa=False)
        response = await client.post('/api/search/openalex/select', json={'query': 'x'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.post('/api/search/openalex/select', json={'query': 'x'})
        assert response.status_code == 422


class TestOpenAlexTerms:
    @pytest.fixture
    def solr_http(self, monkeypatch: pytest.MonkeyPatch) -> _FakeSolrHTTP:
        return _FakeSolrHTTP().install(monkeypatch)

    async def test_expands_terms(self, client, solr_http):
        solr_http.payload = {'terms': {'title_abstract': ['climate', {'df': 12, 'ttf': 40}, 'change', {'df': 3, 'ttf': 7}]}}

        response = await client.get('/api/search/openalex/terms', params={'term_prefix': 'clim', 'limit': 2})

        assert response.status_code == 200
        assert response.json() == [
            {'term': 'climate', 'df': 12, 'ttf': 40},
            {'term': 'change', 'df': 3, 'ttf': 7},
        ]
        url = solr_http.urls[0]
        assert url.startswith(f'{search_route.settings.OPENALEX.solr_url}/terms')
        assert 'terms.prefix=clim' in url
        assert 'terms.limit=2' in url
        assert 'terms.fl=title_abstract' in url

    async def test_defaults_to_20_terms(self, client, solr_http):
        await client.get('/api/search/openalex/terms', params={'term_prefix': 'adap'})
        assert 'terms.limit=20' in solr_http.urls[0]

    async def test_no_expansions_returns_empty_list(self, client, solr_http):
        solr_http.payload = {'terms': {'title_abstract': []}}
        response = await client.get('/api/search/openalex/terms', params={'term_prefix': 'zzz'})
        assert response.status_code == 200
        assert response.json() == []

    async def test_unexpected_solr_payload_becomes_an_error(self, client, solr_http):
        """The route indexes into the response without checking it, so a malformed answer surfaces as 400."""
        solr_http.payload = {'response': {}}
        response = await client.get('/api/search/openalex/terms', params={'term_prefix': 'x'})
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'KeyError'

    async def test_requires_term_prefix(self, client, solr_http):
        response = await client.get('/api/search/openalex/terms')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/search/openalex/terms', params={'term_prefix': 'x'})
        assert response.status_code == 401

    async def test_requires_openalex_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get('/api/search/openalex/terms', params={'term_prefix': 'x'})
            assert response.status_code == 403


class TestNQLQuery:
    async def test_returns_count_and_documents(self, client, fake_db):
        fake_db.queue(
            FakeResult(scalar='academic'),  # project type lookup inside NQLQuery.get_query
            FakeResult(scalar=2),  # the count over the query subquery
            FakeResult(rows=[{'AcademicItem': _academic_item()}, {'AcademicItem': _academic_item(item_id='7d7d7d7d-7d7d-7d7d-7d7d-7d7d7d7d7d7d')}]),
        )

        response = await client.post('/api/search/nql/query', json=VALID_FILTER)

        assert response.status_code == 200
        body = response.json()
        assert body['n_docs'] == 2
        assert [doc['title'] for doc in body['docs']] == [
            'Adaptation finance in West Africa',
            'Adaptation finance in West Africa',
        ]
        assert body['docs'][0]['publication_year'] == 2021

        statements = fake_db.statements()
        assert 'FROM project' in statements[0]
        assert 'count(' in statements[1].lower()
        # the NQL filter ends up as a CTE over the project items
        assert 'project_items' in statements[2]
        assert 'LIMIT' in statements[2].upper()

    async def test_pagination_translates_into_limit_and_offset(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(scalar=0), FakeResult(rows=[]))

        response = await client.post('/api/search/nql/query', params={'page': 3, 'limit': 5}, json=VALID_FILTER)

        assert response.status_code == 200
        assert response.json() == {'n_docs': 0, 'docs': []}
        # SQLAlchemy keeps LIMIT/OFFSET as anonymous bind params, so the numbers are not in the statement text
        statement = fake_db.executions[2].statement
        assert statement._limit == 5
        assert statement._offset == 10

    async def test_empty_result(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(scalar=0), FakeResult(rows=[]))
        response = await client.post('/api/search/nql/query', json=VALID_FILTER)
        assert response.status_code == 200
        assert response.json() == {'n_docs': 0, 'docs': []}

    async def test_unknown_project_reports_an_error(self, client, fake_db):
        """Without a project row NQLQuery raises a bare KeyError, which the API reports as 400."""
        response = await client.post('/api/search/nql/query', json=VALID_FILTER)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'KeyError'

    async def test_body_is_required(self, client):
        response = await client.post('/api/search/nql/query')
        assert response.status_code == 422

    async def test_unknown_filter_type_is_rejected(self, client):
        response = await client.post('/api/search/nql/query', json={'filter': 'fuzzy-match', 'value': 'x'})
        assert response.status_code == 422

    async def test_filter_without_discriminator_is_rejected(self, client):
        response = await client.post('/api/search/nql/query', json={'field': 'title', 'value': 'x'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/search/nql/query', json=VALID_FILTER)
        assert response.status_code == 401

    async def test_requires_dataset_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.post('/api/search/nql/query', json=VALID_FILTER)
        assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.post('/api/search/nql/query', json=VALID_FILTER)
        assert response.status_code == 422


class TestNQLCount:
    async def test_counts_whole_project_without_body(self, client, fake_db):
        fake_db.queue(FakeResult(scalar=12))

        response = await client.post('/api/search/nql/count')

        assert response.status_code == 200
        assert response.json() == 12
        # plain SQL short cut, no project type lookup
        assert len(fake_db.executions) == 1
        assert 'SELECT count(item_id) FROM item' in str(fake_db.executions[0].statement)
        assert fake_db.executions[0].parameters == {'project_id': PROJECT_ID}

    async def test_counts_filtered_items(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(scalar=4))

        response = await client.post('/api/search/nql/count', json=VALID_FILTER)

        assert response.status_code == 200
        assert response.json() == 4
        assert 'FROM project' in fake_db.statements()[0]
        # the filter is wrapped in a subquery whose item_id column is counted
        count_stmt = fake_db.executions[1].statement
        assert count_stmt.name == 'count'
        assert 'count(' in str(count_stmt).lower()

    async def test_json_null_body_uses_the_short_cut(self, client, fake_db):
        fake_db.queue(FakeResult(scalar=7))
        response = await client.post('/api/search/nql/count', json=None)
        assert response.status_code == 200
        assert response.json() == 7
        assert len(fake_db.executions) == 1

    async def test_empty_object_is_not_a_valid_filter(self, client):
        """``{}`` is neither ``None`` nor a discriminated NQL filter."""
        response = await client.post('/api/search/nql/count', json={})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/search/nql/count')
        assert response.status_code == 401

    async def test_requires_dataset_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.post('/api/search/nql/count')
            assert response.status_code == 403
