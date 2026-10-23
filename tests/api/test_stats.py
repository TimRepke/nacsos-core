"""Tests for the /api/stats endpoints (server/api/routes/stats.py)."""

import datetime
from typing import Any

import pytest

from nacsos_data.db.schemas import AcademicItem, LexisNexisItem, LexisNexisItemSource, TwitterItem

from tests.factories import FORBIDDEN_PROJECT_ID, PROJECT_ID, SCHEME_ID, USER_ID, USERNAME, db_project
from tests.mocks import FakeResult


def _queue_project(fake_db: Any, **columns: Any) -> Any:
    """Script the ``session.get(Project, ...)`` lookup of the histogram route."""
    project = db_project(**columns)
    fake_db.queue(FakeResult(rows=[project]))
    return project


def _rank_row(user_id: str = USER_ID, username: str = USERNAME, **columns: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'user_id': user_id,
        'username': username,
        'full_name': username.title(),
        'email': f'{username}@example.org',
        'affiliation': 'ACME',
        'num_labels': 12,
        'num_labeled_items': 7,
    }
    row.update(columns)
    return row


def _label_count_row(**columns: Any) -> dict[str, Any]:
    row: dict[str, Any] = {'scheme': SCHEME_ID, 'key': 'relevant', 'num_items': 5, 'value_bool': None, 'value_int': 1, 'value_float': None, 'multi': None}
    row.update(columns)
    return row


VALID_FILTER: dict[str, Any] = {'filter': 'field', 'field': 'title', 'value': 'climate'}


class TestBasicStats:
    async def test_returns_all_counters(self, client, fake_db):
        fake_db.queue(
            FakeResult(scalar=120),  # items
            FakeResult(scalar=4),  # imports
            FakeResult(scalar=3),  # schemes
            FakeResult(scalar=6),  # scopes
            FakeResult(scalar=420),  # labels
            FakeResult(scalar=95),  # labeled items
        )

        response = await client.get('/api/stats/basics')

        assert response.status_code == 200
        assert response.json() == {
            'num_items': 120,
            'num_imports': 4,
            'num_schemes': 3,
            'num_scopes': 6,
            'num_labels': 420,
            'num_labeled_items': 95,
        }
        # one count query per counter, all scoped to the current project
        statements = fake_db.statements()
        assert len(statements) == 6
        assert 'count(item.item_id)' in statements[0]
        assert 'import' in statements[1]
        assert 'annotation_scheme' in statements[2]
        assert 'assignment_scope' in statements[3]
        assert 'annotation' in statements[4]

    async def test_missing_counts_are_reported_as_zero(self, client, fake_db):
        """Every counter is guarded by ``or 0``, so empty scalar results degrade to 0."""
        response = await client.get('/api/stats/basics')

        assert response.status_code == 200
        assert response.json() == {'num_items': 0, 'num_imports': 0, 'num_schemes': 0, 'num_scopes': 0, 'num_labels': 0, 'num_labeled_items': 0}

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/stats/basics')
        assert response.status_code == 401

    async def test_requires_dataset_read_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.get('/api/stats/basics')
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/stats/basics')
        assert response.status_code == 422


class TestHistogramYears:
    async def test_returns_buckets_for_academic_projects(self, client, fake_db):
        _queue_project(fake_db, type='academic')
        fake_db.queue(
            FakeResult(
                rows=[
                    {'bucket': datetime.datetime(2019, 1, 1), 'num_items': 3},
                    {'bucket': datetime.datetime(2020, 1, 1), 'num_items': 11},
                ]
            )
        )

        response = await client.get('/api/stats/histogram/years', params={'from_year': 2019, 'to_year': 2020})

        assert response.status_code == 200
        assert response.json() == [
            {'bucket': '2019-01-01T00:00:00', 'num_items': 3},
            {'bucket': '2020-01-01T00:00:00', 'num_items': 11},
        ]
        execution = fake_db.executions[-1]
        assert 'generate_series' in str(execution.statement)
        assert AcademicItem.__tablename__ in str(execution.statement)
        assert execution.parameters == {
            'from_date': datetime.datetime(2019, 1, 1, 0, 0, 0),
            'to_date': datetime.datetime(2020, 12, 31, 23, 59, 59),
            'project_id': PROJECT_ID,
        }

    async def test_default_range_spans_1990_to_2025(self, client, fake_db):
        _queue_project(fake_db, type='academic')
        fake_db.queue(FakeResult(rows=[]))

        response = await client.get('/api/stats/histogram/years')

        assert response.status_code == 200
        assert response.json() == []
        params = fake_db.executions[-1].parameters
        assert params['from_date'] == datetime.datetime(1990, 1, 1)
        assert params['to_date'] == datetime.datetime(2025, 12, 31, 23, 59, 59)

    async def test_twitter_projects_use_the_item_table(self, client, fake_db):
        _queue_project(fake_db, type='twitter')
        fake_db.queue(FakeResult(rows=[{'bucket': datetime.datetime(2021, 1, 1), 'num_items': 1}]))

        response = await client.get('/api/stats/histogram/years')

        assert response.status_code == 200
        assert response.json() == [{'bucket': '2021-01-01T00:00:00', 'num_items': 1}]
        assert TwitterItem.__tablename__ in str(fake_db.executions[-1].statement)

    async def test_lexis_projects_join_the_sources_table(self, client, fake_db):
        _queue_project(fake_db, type='lexis')
        fake_db.queue(FakeResult(rows=[]))

        response = await client.get('/api/stats/histogram/years')

        assert response.status_code == 200
        statement = str(fake_db.executions[-1].statement)
        assert f'{LexisNexisItemSource.__tablename__} itm LEFT JOIN {LexisNexisItem.__tablename__} jn' in statement

    async def test_unsupported_project_type_is_an_error(self, client, fake_db):
        _queue_project(fake_db, type='generic')
        response = await client.get('/api/stats/histogram/years')

        assert response.status_code == 400
        detail = response.json()['detail']
        assert detail['type'] == 'NotImplementedError'
        assert 'academic, lexisnexis, and twitter' in detail['message']

    async def test_unknown_project_is_an_error(self, client, fake_db):
        """No project row (the default result) makes the route give up with ProjectNotFoundError."""
        response = await client.get('/api/stats/histogram/years')

        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ProjectNotFoundError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/stats/histogram/years')
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get('/api/stats/histogram/years')
            assert response.status_code == 403


class TestAnnotatorRank:
    async def test_returns_ranking_rows(self, client, fake_db):
        fake_db.queue_rows(
            _rank_row(),
            _rank_row(user_id='33333333-3333-3333-3333-333333333333', username='other', num_labels=2, num_labeled_items=1),
        )

        response = await client.get('/api/stats/rank')

        assert response.status_code == 200
        assert response.json() == [
            {
                'user_id': USER_ID,
                'username': USERNAME,
                'full_name': 'Testuser',
                'email': 'testuser@example.org',
                'affiliation': 'ACME',
                'num_labels': 12,
                'num_labeled_items': 7,
            },
            {
                'user_id': '33333333-3333-3333-3333-333333333333',
                'username': 'other',
                'full_name': 'Other',
                'email': 'other@example.org',
                'affiliation': 'ACME',
                'num_labels': 2,
                'num_labeled_items': 1,
            },
        ]
        statement = str(fake_db.executions[0].statement)
        assert 'GROUP BY' in statement.upper()
        assert 'ORDER BY num_labeled_items DESC' in statement

    async def test_project_without_annotations_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/stats/rank')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/stats/rank')
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get('/api/stats/rank')
            assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get('/api/stats/rank')
        assert response.status_code == 422


class TestHumanLabelStats:
    async def test_counts_labels_over_all_items(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[_label_count_row(), _label_count_row(key='sentiment', value_int=None)]))

        response = await client.post('/api/stats/labels/human')

        assert response.status_code == 200
        body = response.json()
        assert [entry['key'] for entry in body] == ['relevant', 'sentiment']
        assert body[0] == {
            'scheme': SCHEME_ID,
            'num_items': 5,
            'key': 'relevant',
            'value_bool': None,
            'value_int': 1,
            'value_float': None,
            'value_str': None,
            'multi': None,
        }
        assert 'annotation.item_id' in str(fake_db.executions[1].statement)

    async def test_counts_labels_for_a_filtered_subset(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[_label_count_row(num_items=2)]))

        response = await client.post('/api/stats/labels/human', json=VALID_FILTER)

        assert response.status_code == 200
        assert response.json()[0]['num_items'] == 2
        # the NQL filter is resolved through the project items CTE
        assert 'FROM project' in fake_db.statements()[0]
        assert 'project_items' in fake_db.statements()[1]

    async def test_multi_labels_are_counted_per_value(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[_label_count_row(key='topics', value_int=None, multi=7)]))

        response = await client.post('/api/stats/labels/human', json=VALID_FILTER)

        assert response.status_code == 200
        assert response.json() == [
            {
                'scheme': SCHEME_ID,
                'num_items': 5,
                'key': 'topics',
                'value_bool': None,
                'value_int': None,
                'value_float': None,
                'value_str': None,
                'multi': 7,
            }
        ]

    async def test_no_labels_returns_empty_list(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[]))
        response = await client.post('/api/stats/labels/human', json=VALID_FILTER)
        assert response.status_code == 200
        assert response.json() == []

    async def test_unknown_project_reports_an_error(self, client, fake_db):
        """NQLQuery raises a bare KeyError when the project cannot be resolved, which surfaces as 400."""
        response = await client.post('/api/stats/labels/human', json=VALID_FILTER)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'KeyError'

    async def test_invalid_filter_is_rejected(self, client):
        response = await client.post('/api/stats/labels/human', json={'filter': 'nope'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/stats/labels/human')
        assert response.status_code == 401

    async def test_requires_dataset_read_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.post('/api/stats/labels/human')
        assert response.status_code == 403


class TestResolvedLabelStats:
    async def test_counts_resolved_labels_over_all_items(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[_label_count_row()]))

        response = await client.post('/api/stats/labels/resolved')

        assert response.status_code == 200
        assert response.json() == [
            {
                'scheme': SCHEME_ID,
                'num_items': 5,
                'key': 'relevant',
                'value_bool': None,
                'value_int': 1,
                'value_float': None,
                'value_str': None,
                'multi': None,
            }
        ]
        statement = str(fake_db.executions[1].statement)
        assert 'bot_annotation.bot_annotation_metadata_id = bot_annotation_metadata.bot_annotation_metadata_id' in statement

    async def test_counts_resolved_labels_for_a_filtered_subset(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[_label_count_row(num_items=9, value_bool=True, value_int=None)]))

        response = await client.post('/api/stats/labels/resolved', json=VALID_FILTER)

        assert response.status_code == 200
        assert response.json()[0]['value_bool'] is True
        assert 'project_items' in fake_db.statements()[1]

    async def test_no_resolved_labels_returns_empty_list(self, client, fake_db):
        fake_db.queue(FakeResult(scalar='academic'), FakeResult(rows=[]))
        response = await client.post('/api/stats/labels/resolved')
        assert response.status_code == 200
        assert response.json() == []

    async def test_unsupported_project_type_is_an_error(self, client, fake_db):
        """NQL only knows academic/lexis/generic projects; the route reports anything else as 400."""
        fake_db.queue(FakeResult(scalar='twitter'))
        response = await client.post('/api/stats/labels/resolved')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'NotImplementedError'

    async def test_invalid_filter_is_rejected(self, client):
        response = await client.post('/api/stats/labels/resolved', json={'field': 'title', 'value': 'x'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/stats/labels/resolved')
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.post('/api/stats/labels/resolved')
            assert response.status_code == 403


class TestStatsErrorContract:
    @pytest.mark.parametrize('method', ['human', 'resolved'])
    async def test_malformed_filter_bodies_are_reported_as_validation_errors(self, client, method):
        response = await client.post(f'/api/stats/labels/{method}', json=['not', 'a', 'filter'])
        assert response.status_code == 422
