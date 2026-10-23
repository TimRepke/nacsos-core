"""Tests for the /api/project/items endpoints (server/api/routes/project/items.py).

The router is included under ``/api/project`` with prefix ``/items`` (server/api/routes/project/__init__.py),
so all paths below live under ``/api/project/items``. ``item_type`` path params are validated against
``ItemTypeLiteral`` = {'generic', 'twitter', 'academic', 'patents', 'lexis'}: unknown values yield 422,
while 'patents' passes validation but has no implementation (HTTP 501).
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.sql.dml import Insert

from nacsos_data.db.schemas import ItemType
from nacsos_data.models.items import AcademicItemModel, GenericItemModel, TwitterItemModel

from server.api.routes.project import items as project_items_route
from server.data import db_engine

from tests.factories import IMPORT_ID, ITEM_ID, PROJECT_ID, db_project
from tests.mocks import FakeResult, patch_crud

BASE = '/api/project/items'


def _tweet_payload(**overrides):
    payload = {
        'text': 'Hello NACSOS',
        'created_at': '2024-05-04T12:00:00Z',
        'retweet_count': 1,
        'reply_count': 2,
        'like_count': 3,
        'quote_count': 4,
        'twitter_id': '1717171717171717',
    }
    payload.update(overrides)
    return payload


def _tweet_model(**overrides):
    return TwitterItemModel(
        text='Hello NACSOS',
        created_at=datetime(2024, 5, 4, 12, 0, tzinfo=timezone.utc),
        retweet_count=1,
        reply_count=2,
        like_count=3,
        quote_count=4,
        **overrides,
    )


class TestCountProjectItems:
    async def test_forwards_project_to_crud(self, client, monkeypatch):
        captured = {}

        async def fake_count(**kwargs):
            captured.update(kwargs)
            return 42

        patch_crud(monkeypatch, project_items_route, read_item_count_for_project=fake_count)
        response = await client.get(f'{BASE}/count')
        assert response.status_code == 200
        assert response.json() == 42
        assert captured['project_id'] == PROJECT_ID
        assert captured['engine'] is db_engine

    async def test_unpatched_count_goes_through_the_session(self, client, fake_db):
        fake_db.queue(FakeResult(scalar=7))
        response = await client.get(f'{BASE}/count')
        assert response.status_code == 200
        assert response.json() == 7
        sql = str(fake_db.executions[0].statement)
        assert 'count(item.item_id)' in sql
        assert 'FROM item' in sql

    async def test_requires_dataset_read(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False, annotations_edit=True)
        response = await client.get(f'{BASE}/count')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'{BASE}/count')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/count')
        assert response.status_code == 401


class TestListProjectItems:
    async def test_academic_items(self, client, monkeypatch):
        captured = {}

        async def fake_read_all(**kwargs):
            captured.update(kwargs)
            return [AcademicItemModel(item_id=ITEM_ID, title='A Paper', doi='10.1234/x')]

        patch_crud(monkeypatch, project_items_route, read_all_for_project=fake_read_all)
        response = await client.get(f'{BASE}/academic/list')
        assert response.status_code == 200
        body = response.json()
        assert body[0]['type'] == 'academic'
        assert body[0]['doi'] == '10.1234/x'
        assert captured['Model'] is AcademicItemModel
        assert captured['project_id'] == PROJECT_ID
        assert captured['engine'] is db_engine

    async def test_generic_items_keep_their_meta(self, client, monkeypatch):
        captured = {}

        async def fake_read_all(**kwargs):
            captured.update(kwargs)
            return [GenericItemModel(item_id=ITEM_ID, text='some text', meta={'source': 'scrape'})]

        patch_crud(monkeypatch, project_items_route, read_all_for_project=fake_read_all)
        response = await client.get(f'{BASE}/generic/list')
        assert response.status_code == 200
        # The AnyItemModelList union serialises instances smartly: no Twitter/Academic coercion.
        assert response.json() == [
            {'item_id': ITEM_ID, 'project_id': None, 'type': 'generic', 'time_edited': None, 'text': 'some text', 'meta': {'source': 'scrape'}}
        ]
        assert captured['Model'] is GenericItemModel

    async def test_twitter_items_use_dedicated_crud_helper(self, client, monkeypatch):
        captured = {}

        async def fake_read_all(**kwargs):
            captured.update(kwargs)
            return [_tweet_model(item_id=ITEM_ID)]

        patch_crud(monkeypatch, project_items_route, read_all_twitter_items_for_project=fake_read_all)
        response = await client.get(f'{BASE}/twitter/list')
        assert response.status_code == 200
        assert response.json()[0]['type'] == 'twitter'
        assert response.json()[0]['like_count'] == 3
        assert captured == {'project_id': PROJECT_ID, 'engine': db_engine}

    async def test_lexis_items_route_to_lexis_schema(self, client, monkeypatch):
        from nacsos_data.db.schemas import LexisNexisItem
        from nacsos_data.models.items import LexisNexisItemModel

        captured = {}

        async def fake_read_all(**kwargs):
            captured.update(kwargs)
            return []

        patch_crud(monkeypatch, project_items_route, read_all_for_project=fake_read_all)
        response = await client.get(f'{BASE}/lexis/list')
        assert response.status_code == 200
        assert response.json() == []
        assert captured['Model'] is LexisNexisItemModel
        assert captured['Schema'] is LexisNexisItem

    async def test_patents_type_is_not_implemented(self, client):
        # 'patents' is a valid ItemTypeLiteral member, so it passes validation but hits the 501 branch.
        response = await client.get(f'{BASE}/patents/list')
        assert response.status_code == 501
        assert response.json()['detail'] == 'Data listing for patents not implemented (yet).'

    async def test_unknown_item_type_fails_validation(self, client):
        response = await client.get(f'{BASE}/banana/list')
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'] == ['path', 'item_type']

    async def test_requires_dataset_read(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.get(f'{BASE}/academic/list')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/academic/list')
        assert response.status_code == 401


class TestListProjectItemsPaged:
    async def test_generic_paging_forwards_parameters(self, client, monkeypatch):
        captured = {}

        async def fake_read_paged(**kwargs):
            captured.update(kwargs)
            return [GenericItemModel(item_id=ITEM_ID, meta={'page': 3})]

        patch_crud(monkeypatch, project_items_route, read_paged_for_project=fake_read_paged)
        response = await client.get(f'{BASE}/generic/list/3/25')
        assert response.status_code == 200
        assert response.json()[0]['meta'] == {'page': 3}
        assert captured['page'] == 3
        assert captured['page_size'] == 25
        assert captured['project_id'] == PROJECT_ID
        assert captured['Model'] is GenericItemModel
        assert captured['engine'] is db_engine

    async def test_academic_paging_selects_academic_model(self, client, monkeypatch):
        captured = {}

        async def fake_read_paged(**kwargs):
            captured.update(kwargs)
            return []

        patch_crud(monkeypatch, project_items_route, read_paged_for_project=fake_read_paged)
        response = await client.get(f'{BASE}/academic/list/2/10')
        assert response.status_code == 200
        assert response.json() == []
        assert captured['Model'] is AcademicItemModel
        assert (captured['page'], captured['page_size']) == (2, 10)

    async def test_twitter_paging_uses_dedicated_helper(self, client, monkeypatch):
        captured = {}

        async def fake_paged(**kwargs):
            captured.update(kwargs)
            return [_tweet_model(item_id=ITEM_ID)]

        patch_crud(monkeypatch, project_items_route, read_all_twitter_items_for_project_paged=fake_paged)
        response = await client.get(f'{BASE}/twitter/list/1/5')
        assert response.status_code == 200
        assert response.json()[0]['created_at'] == '2024-05-04T12:00:00Z'
        assert captured == {'project_id': PROJECT_ID, 'page': 1, 'page_size': 5, 'engine': db_engine}

    async def test_lexis_paging_helper_gets_db_engine_kwarg(self, client, monkeypatch):
        captured = {}

        async def fake_lexis_paged(**kwargs):
            captured.update(kwargs)
            return []

        patch_crud(monkeypatch, project_items_route, read_lexis_paged_for_project=fake_lexis_paged)
        response = await client.get(f'{BASE}/lexis/list/4/15')
        assert response.status_code == 200
        assert response.json() == []
        # NB: this route passes the engine as `db_engine=` (not `engine=` like the other helpers);
        # the real read_lexis_paged_for_project accepts it via the ensure_session_async wrapper.
        assert captured == {'page': 4, 'page_size': 15, 'project_id': PROJECT_ID, 'db_engine': db_engine}

    async def test_patents_paging_is_not_implemented(self, client):
        response = await client.get(f'{BASE}/patents/list/1/10')
        assert response.status_code == 501
        assert 'not implemented' in response.json()['detail']

    async def test_non_numeric_page_fails_validation(self, client):
        response = await client.get(f'{BASE}/generic/list/abc/10')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/generic/list/1/10')
        assert response.status_code == 401


class TestItemDetail:
    async def test_academic_detail_by_explicit_type(self, client, monkeypatch):
        captured = {}

        async def fake_read(item_id, item_type, engine):
            captured.update(item_id=item_id, item_type=item_type, engine=engine)
            return AcademicItemModel(item_id=ITEM_ID, doi='10.1234/y', title='Y')

        patch_crud(monkeypatch, project_items_route, read_any_item_by_item_id=fake_read)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'academic'})
        assert response.status_code == 200
        assert response.json()['doi'] == '10.1234/y'
        # The route passes the ItemType enum (not the raw string) for academic items.
        assert captured['item_type'] is ItemType.academic
        assert captured['item_id'] == ITEM_ID
        assert captured['engine'] is db_engine

    async def test_generic_detail_passes_string_item_type(self, client, monkeypatch):
        captured = {}

        async def fake_read(item_id, item_type, engine):
            captured.update(item_type=item_type)
            return GenericItemModel(item_id=ITEM_ID, meta={})

        patch_crud(monkeypatch, project_items_route, read_any_item_by_item_id=fake_read)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'generic'})
        assert response.status_code == 200
        assert response.json()['type'] == 'generic'
        assert captured['item_type'] == 'generic'  # plain str here, unlike the academic/lexis branches

    async def test_twitter_detail_uses_dedicated_helper(self, client, monkeypatch):
        captured = {}

        async def fake_read(item_id, engine):
            captured.update(item_id=item_id, engine=engine)
            return _tweet_model(item_id=ITEM_ID)

        patch_crud(monkeypatch, project_items_route, read_twitter_item_by_item_id=fake_read)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'twitter'})
        assert response.status_code == 200
        assert response.json()['text'] == 'Hello NACSOS'
        assert captured['item_id'] == ITEM_ID

    async def test_lexis_detail_routes_to_lexis_item_type(self, client, monkeypatch):
        from nacsos_data.models.items import LexisNexisItemModel

        captured = {}

        async def fake_read(item_id, item_type, engine):
            captured.update(item_type=item_type)
            return LexisNexisItemModel(item_id=ITEM_ID, teaser='breaking')

        patch_crud(monkeypatch, project_items_route, read_any_item_by_item_id=fake_read)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'lexis'})
        assert response.status_code == 200
        assert response.json()['teaser'] == 'breaking'
        assert captured['item_type'] is ItemType.lexis

    async def test_item_type_defaults_to_project_type(self, client, fake_db, monkeypatch):
        # session.get(Project, ...) resolves the project type for the item lookup
        fake_db.set_default_rows(db_project(type='generic'))
        captured = {}

        async def fake_read(item_id, item_type, engine):
            captured.update(item_type=item_type)
            return GenericItemModel(item_id=ITEM_ID, meta={})

        patch_crud(monkeypatch, project_items_route, read_any_item_by_item_id=fake_read)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}')
        assert response.status_code == 200
        assert captured['item_type'] == 'generic'  # inherited from the project row

    async def test_missing_project_type_lookup_crashes_with_assert(self, client, fake_db):
        # items.py asserts `project is not None` -> AssertionError without message -> middleware maps to 400.
        response = await client.get(f'{BASE}/detail/{ITEM_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'AssertionError'

    async def test_unknown_item_is_not_found(self, client, monkeypatch):
        patch_crud(monkeypatch, project_items_route, read_any_item_by_item_id=None)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'academic'})
        assert response.status_code == 404
        assert response.json()['detail']['type'] == 'ItemNotFoundError'

    async def test_unknown_item_type_query_param_fails_validation(self, client):
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'banana'})
        assert response.status_code == 422

    async def test_requires_dataset_read(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.get(f'{BASE}/detail/{ITEM_ID}', params={'item_type': 'academic'})
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/detail/{ITEM_ID}')
        assert response.status_code == 401


class TestItemText:
    async def test_returns_text_from_item_row(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='The full item text.'))
        response = await client.get(f'{BASE}/text/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json() == 'The full item text.'
        sql = str(fake_db.executions[0].statement)
        assert 'SELECT item.text' in sql
        assert 'FROM item' in sql

    async def test_item_without_text_is_not_found(self, client, fake_db):
        response = await client.get(f'{BASE}/text/{ITEM_ID}')
        assert response.status_code == 404
        assert response.json()['detail']['type'] == 'ItemNotFoundError'

    async def test_requires_dataset_read(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.get(f'{BASE}/text/{ITEM_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'{BASE}/text/{ITEM_ID}')
        assert response.status_code == 401


class TestAddTweet:
    async def test_imports_tweet_into_current_project(self, client, fake_db, monkeypatch):
        # Not patched: the real import_tweet runs against the fake database and mutates through the session.
        response = await client.post(f'{BASE}/twitter/add', json=_tweet_payload())
        assert response.status_code == 200
        body = response.json()
        assert body['text'] == 'Hello NACSOS'
        assert body['type'] == 'twitter'
        assert body['twitter_id'] == '1717171717171717'
        assert body['project_id'] == PROJECT_ID  # project comes from the permission grant, not the payload
        assert len(fake_db.added) == 1
        assert str(fake_db.added[0].project_id) == PROJECT_ID
        assert fake_db.added[0].text == 'Hello NACSOS'
        assert fake_db.flushes == 1
        assert fake_db.commits == 1

    async def test_import_link_is_written_as_m2m_insert(self, client, fake_db, monkeypatch):
        response = await client.post(f'{BASE}/twitter/add?import_id={IMPORT_ID}', json=_tweet_payload())
        assert response.status_code == 200
        assert len(fake_db.executions) == 1
        statement = fake_db.executions[0].statement
        assert isinstance(statement, Insert)
        assert statement.table.name == 'm2m_import_item'
        assert fake_db.commits == 1

    async def test_forwards_context_to_import_tweet(self, client, monkeypatch):
        captured = {}

        async def fake_import_tweet(tweet, project_id, import_id, engine):
            captured.update(tweet=tweet, project_id=project_id, import_id=import_id, engine=engine)
            return _tweet_model(item_id=ITEM_ID, twitter_id='1717171717171717')

        patch_crud(monkeypatch, project_items_route, import_tweet=fake_import_tweet)
        response = await client.post(f'{BASE}/twitter/add?import_id={IMPORT_ID}', json=_tweet_payload())
        assert response.status_code == 200
        assert response.json()['item_id'] == ITEM_ID  # the id assigned by the CRUD helper is returned
        assert captured['project_id'] == PROJECT_ID
        assert captured['import_id'] == IMPORT_ID
        assert captured['tweet'].text == 'Hello NACSOS'
        assert captured['engine'] is db_engine

    async def test_without_import_id_the_crud_helper_sees_none(self, client, monkeypatch):
        captured = {}

        async def fake_import_tweet(tweet, project_id, import_id, engine):
            captured['import_id'] = import_id
            return _tweet_model(item_id=ITEM_ID)

        patch_crud(monkeypatch, project_items_route, import_tweet=fake_import_tweet)
        response = await client.post(f'{BASE}/twitter/add', json=_tweet_payload())
        assert response.status_code == 200
        assert captured['import_id'] is None

    async def test_requires_dataset_edit(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=False, dataset_read=True)
        response = await client.post(f'{BASE}/twitter/add', json=_tweet_payload())
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    @pytest.mark.parametrize(
        'payload', [{}, {'text': 'no metrics'}, {'created_at': 'not-a-date', 'retweet_count': 0, 'reply_count': 0, 'like_count': 0, 'quote_count': 0}]
    )
    async def test_invalid_tweet_body_fails_validation(self, client, payload):
        # TwitterItemModel requires created_at plus the four engagement counters.
        response = await client.post(f'{BASE}/twitter/add', json=payload)
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post(f'{BASE}/twitter/add', json=_tweet_payload())
        assert response.status_code == 401
