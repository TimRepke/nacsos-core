"""Tests for the /api/eval endpoints (server/api/routes/evaluation.py)."""

from typing import Any
from uuid import UUID

import pytest

from nacsos_data.db.schemas import AnnotationQuality, AnnotationTracker, BotAnnotationMetaData
from nacsos_data.models.annotation_quality import AnnotationQualityModel
from nacsos_data.models.annotation_tracker import AnnotationTrackerModel

from server.api.routes import evaluation as evaluation_route

from tests.factories import FORBIDDEN_PROJECT_ID, ITEM_ID, PROJECT_ID, SCOPE_ID, SCHEME_ID, TRACKER_ID, orm_obj
from tests.mocks import FakeResult, patch_crud

OTHER_ITEM_ID = '24242424-2424-2424-2424-242424242424'


def _tracker_row(**columns: Any) -> AnnotationTracker:
    """Transient AnnotationTracker row with all non-optional model fields set."""
    values: dict[str, Any] = {
        'annotation_tracking_id': UUID(TRACKER_ID),
        'project_id': UUID(PROJECT_ID),
        'name': 'Test Tracker',
        'inclusion_rule': 'relevant = 1',
        'majority': True,
        'n_items_total': 10,
        'batch_size': 5,
        'recall_target': 0.9,
        'confidence_level': 0.9,
        'bias': 1.0,
        'source_ids': None,
        'labels': None,
        'recall': None,
        'buscar': None,
        'buscar_frontier': None,
    }
    values.update(columns)
    return orm_obj(AnnotationTracker, **values)


def _tracker_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'annotation_tracking_id': TRACKER_ID,
        'project_id': PROJECT_ID,
        'name': 'Test Tracker',
        'inclusion_rule': 'relevant = 1',
        'majority': True,
        'n_items_total': 100,
        'batch_size': 500,
        'recall_target': 0.9,
        'source_ids': [SCOPE_ID],
    }
    payload.update(overrides)
    return payload


def _quality_row(**columns: Any) -> AnnotationQuality:
    values: dict[str, Any] = {
        'annotation_quality_id': UUID(TRACKER_ID),
        'project_id': UUID(PROJECT_ID),
        'assignment_scope_id': UUID(SCOPE_ID),
        'label_key': 'relevant',
        'cohen': 0.5,
        'fleiss': 0.75,
        'num_items': 10,
        'num_overlap': 4,
        'num_agree': 3,
        'num_disagree': 1,
        'perc_agree': 75.0,
    }
    values.update(columns)
    return orm_obj(AnnotationQuality, **values)


def _annotation_row(item_id: str = ITEM_ID, value_int: int = 1, **columns: Any) -> dict[str, Any]:
    """A row as returned by ``get_annotations`` (raw SQL, mapped through ``SortedAnnotation``)."""
    row: dict[str, Any] = {
        'source_order': 1,
        'source_id': SCOPE_ID,
        'source_type': 'H',
        'user_id': '11111111-1111-1111-1111-111111111111',
        'item_order': 1,
        'item_id': item_id,
        'labels': {'relevant': {'value_int': value_int}},
    }
    row.update(columns)
    return row


class TestComputeIrr:
    async def test_recomputes_metrics_and_returns_stored_rows(self, client, fake_db, monkeypatch):
        patch_crud(monkeypatch, evaluation_route, compute_irr_scores=[AnnotationQualityModel(label_key='relevant', fleiss=0.42)])
        fake_db.queue(FakeResult(rows=[]), FakeResult(rows=[_quality_row(fleiss=0.42)]))

        response = await client.get('/api/eval/quality/compute', params={'assignment_scope_id': SCOPE_ID})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['assignment_scope_id'] == SCOPE_ID
        assert body[0]['fleiss'] == 0.42
        # stale rows are deleted, new ones are inserted in the same session
        statement = str(fake_db.executions[0].statement)
        assert statement.upper().startswith('DELETE FROM')
        assert 'annotation_quality' in statement
        assert fake_db.commits == 1
        # AsyncSession.add/add_all are synchronous, so the recomputed rows are handed to the session
        assert len(fake_db.added) == 1
        assert fake_db.added[0].fleiss == 0.42

    async def test_passes_scope_resolution_and_project_to_helper(self, client, fake_db, monkeypatch):
        seen: dict[str, Any] = {}

        async def fake_compute(session: Any, assignment_scope_id: str, resolution_id: str | None, project_id: str) -> list[AnnotationQualityModel]:
            seen.update(assignment_scope_id=assignment_scope_id, resolution_id=resolution_id, project_id=project_id)
            return []

        patch_crud(monkeypatch, evaluation_route, compute_irr_scores=fake_compute)
        fake_db.queue(FakeResult(rows=[]), FakeResult(rows=[]))

        response = await client.get(
            '/api/eval/quality/compute',
            params={'assignment_scope_id': SCOPE_ID, 'bot_annotation_metadata_id': TRACKER_ID},
        )

        assert response.status_code == 200
        assert response.json() == []
        assert seen == {'assignment_scope_id': SCOPE_ID, 'resolution_id': TRACKER_ID, 'project_id': PROJECT_ID}

    async def test_empty_scope_returns_empty_list(self, client, fake_db, monkeypatch):
        patch_crud(monkeypatch, evaluation_route, compute_irr_scores=[])
        fake_db.queue(FakeResult(rows=[]), FakeResult(rows=[]))
        response = await client.get('/api/eval/quality/compute', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 200
        assert response.json() == []
        assert fake_db.added == []

    async def test_requires_assignment_scope_id(self, client):
        response = await client.get('/api/eval/quality/compute')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/eval/quality/compute', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get('/api/eval/quality/compute', params={'assignment_scope_id': SCOPE_ID})
            assert response.status_code == 403


class TestLoadIrr:
    async def test_returns_stored_metrics(self, client, fake_db):
        fake_db.queue_rows(_quality_row(), _quality_row(annotation_quality_id=UUID(TRACKER_ID), label_key='sentiment'))
        response = await client.get(f'/api/eval/quality/load/{SCOPE_ID}')

        assert response.status_code == 200
        assert [entry['label_key'] for entry in response.json()] == ['relevant', 'sentiment']
        assert response.json()[0]['perc_agree'] == 75.0
        assert 'annotation_quality' in str(fake_db.executions[0].statement)

    async def test_unknown_scope_yields_empty_list(self, client, fake_db):
        response = await client.get(f'/api/eval/quality/load/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/eval/quality/load/{SCOPE_ID}')
        assert response.status_code == 401

    async def test_requires_read_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False)
        response = await client.get(f'/api/eval/quality/load/{SCOPE_ID}')
        assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'/api/eval/quality/load/{SCOPE_ID}')
        assert response.status_code == 422


class TestResolutions:
    async def test_returns_bot_metadata_for_scope(self, client, fake_db):
        fake_db.queue_rows(
            orm_obj(
                BotAnnotationMetaData,
                bot_annotation_metadata_id=UUID(TRACKER_ID),
                name='Majority resolution',
                kind='RESOLVE',
                project_id=UUID(PROJECT_ID),
                assignment_scope_id=UUID(SCOPE_ID),
                annotation_scheme_id=UUID(SCHEME_ID),
            )
        )
        response = await client.get('/api/eval/resolutions', params={'assignment_scope_id': SCOPE_ID})

        assert response.status_code == 200
        assert response.json() == [
            {
                'bot_annotation_metadata_id': TRACKER_ID,
                'name': 'Majority resolution',
                'kind': 'RESOLVE',
                'project_id': PROJECT_ID,
                'time_created': None,
                'time_updated': None,
                'assignment_scope_id': SCOPE_ID,
                'annotation_scheme_id': SCHEME_ID,
            }
        ]

    async def test_scope_without_resolutions_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/eval/resolutions', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_assignment_scope_id(self, client):
        response = await client.get('/api/eval/resolutions')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/eval/resolutions', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 401


class TestTrackingScopes:
    async def test_combines_assignment_scopes_and_bot_scopes(self, client, fake_db):
        fake_db.queue(
            FakeResult(rows=[{'scope_id': SCOPE_ID, 'name': 'Human Scope', 'scope_type': 'H'}]),
            FakeResult(rows=[{'scope_id': TRACKER_ID, 'name': 'Resolved Scope', 'scope_type': 'R'}]),
        )
        response = await client.get('/api/eval/tracking/scopes')

        assert response.status_code == 200
        assert response.json() == [
            {'scope_id': SCOPE_ID, 'name': 'Human Scope', 'scope_type': 'H'},
            {'scope_id': TRACKER_ID, 'name': 'Resolved Scope', 'scope_type': 'R'},
        ]
        # one query against assignment_scope (joined to annotation_scheme), one against bot_annotation_metadata
        statements = fake_db.statements()
        assert len(statements) == 2
        assert 'assignment_scope' in statements[0]
        assert 'bot_annotation_metadata' in statements[1]

    async def test_project_without_scopes_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/eval/tracking/scopes')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/eval/tracking/scopes')
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get('/api/eval/tracking/scopes')
            assert response.status_code == 403


class TestSaveTracker:
    async def test_upserts_the_tracker(self, client, monkeypatch):
        seen: dict[str, Any] = {}

        async def fake_upsert(**kwargs: Any) -> UUID:
            seen.update(kwargs)
            return UUID(TRACKER_ID)

        patch_crud(monkeypatch, evaluation_route, upsert_orm=fake_upsert)
        response = await client.put('/api/eval/tracking/tracker', json=_tracker_payload())

        assert response.status_code == 200
        assert response.json() == TRACKER_ID
        assert seen['primary_key'] == 'annotation_tracking_id'
        assert seen['use_commit'] is True
        assert seen['Schema'] is AnnotationTracker
        # derived metrics must not be overwritten by the (possibly stale) client copy
        assert seen['skip_update'] == ['labels', 'recall', 'buscar']
        assert seen['upsert_model'] == AnnotationTrackerModel.model_validate(_tracker_payload())

    async def test_incomplete_payload_is_rejected(self, client):
        response = await client.put('/api/eval/tracking/tracker', json={'name': 'incomplete'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/eval/tracking/tracker', json=_tracker_payload())
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.put('/api/eval/tracking/tracker', json=_tracker_payload())
            assert response.status_code == 403


class TestRefreshTracker:
    async def test_recomputes_labels_and_schedules_background_metrics(self, client, fake_db, monkeypatch):
        scheduled: list[tuple[Any, Any]] = []

        async def fake_bg(tracker_id: str, labels: list[list[int]] | None) -> None:
            scheduled.append((tracker_id, labels))

        tracker = _tracker_row(source_ids=[UUID(SCOPE_ID)])
        monkeypatch.setattr(evaluation_route, 'bg_populate_tracker', fake_bg)
        fake_db.queue(
            FakeResult(rows=[tracker]),  # read_tracker
            FakeResult(rows=[_annotation_row(item_id=ITEM_ID, value_int=1), _annotation_row(item_id=OTHER_ITEM_ID, value_int=0)]),  # get_annotations
        )

        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})

        assert response.status_code == 200
        body = response.json()
        # the inclusion rule 'relevant = 1' turns the two annotated items into a 0/1 sequence
        assert body['labels'] == [[1, 0]]
        assert body['recall'] is None
        assert body['buscar'] is None
        assert scheduled == [(TRACKER_ID, [[1, 0]])]
        assert fake_db.commits == 1

    async def test_background_task_populates_recall_and_buscar(self, client, fake_db):
        tracker = _tracker_row(source_ids=[UUID(SCOPE_ID)])
        fake_db.queue(
            FakeResult(rows=[tracker]),  # read_tracker in the route
            FakeResult(rows=[_annotation_row(value_int=1), _annotation_row(item_id=OTHER_ITEM_ID, value_int=0)]),  # get_annotations
            FakeResult(rows=[tracker]),  # read_tracker inside the background task
        )

        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})

        assert response.status_code == 200
        # both the route and the (awaited) background task commit
        assert fake_db.commits == 2
        assert tracker.recall == [1.0, 1.0]
        assert tracker.buscar == [(2, pytest.approx(8 / 9))]
        assert tracker.buscar_frontier[0] == pytest.approx((0.99, 8 / 9))

    async def test_sources_without_annotations_reset_labels(self, client, fake_db, monkeypatch):
        scheduled: list[Any] = []
        monkeypatch.setattr(evaluation_route, 'bg_populate_tracker', lambda tracker_id, labels: scheduled.append((tracker_id, labels)))
        fake_db.queue(FakeResult(rows=[_tracker_row(source_ids=[UUID(SCOPE_ID)])]), FakeResult(rows=[]))

        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})

        assert response.status_code == 200
        assert response.json()['labels'] == []
        assert scheduled == [(TRACKER_ID, [])]

    async def test_reset_flag_must_be_a_bare_boolean_body(self, client):
        """`reset` is a scalar Body parameter, so the wrapped form most clients would send is rejected."""
        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID}, json={'reset': False})
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'] == ['body']

    async def test_tracker_without_source_ids_fails(self, client, fake_db):
        """NOTE (latent bug): evaluation.py iterates `tracker.source_ids`, which is nullable -> generic 400 TypeError."""
        fake_db.queue_rows(_tracker_row(source_ids=None))

        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})

        assert response.status_code == 400
        detail = response.json()['detail']
        assert detail['type'] == 'TypeError'
        assert 'NoneType' in detail['message']

    async def test_unknown_tracker_reports_no_content(self, client, fake_db):
        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})
        assert response.status_code == 204
        assert response.content == b''

    async def test_requires_tracker_id(self, client):
        response = await client.post('/api/eval/tracking/refresh')
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})
        assert response.status_code == 401

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.post('/api/eval/tracking/refresh', params={'tracker_id': TRACKER_ID})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'


class TestGetTracker:
    async def test_returns_the_tracker(self, client, fake_db):
        fake_db.queue_rows(_tracker_row(labels=[[1, 0]], recall=[1.0, 1.0], source_ids=[UUID(SCOPE_ID)]))
        response = await client.get(f'/api/eval/tracking/tracker/{TRACKER_ID}')

        assert response.status_code == 200
        body = response.json()
        assert body['annotation_tracking_id'] == TRACKER_ID
        assert body['project_id'] == PROJECT_ID
        assert body['name'] == 'Test Tracker'
        assert body['inclusion_rule'] == 'relevant = 1'
        assert body['n_items_total'] == 10
        assert body['labels'] == [[1, 0]]
        assert body['source_ids'] == [SCOPE_ID]

    async def test_unknown_tracker_reports_no_content(self, client, fake_db):
        """DataNotFoundWarning has status 204, and 204 responses are body-less by definition."""
        response = await client.get(f'/api/eval/tracking/tracker/{TRACKER_ID}')
        assert response.status_code == 204
        assert response.content == b''

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/eval/tracking/tracker/{TRACKER_ID}')
        assert response.status_code == 401

    async def test_requires_project_access(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as forbidden:
            response = await forbidden.get(f'/api/eval/tracking/tracker/{TRACKER_ID}')
            assert response.status_code == 403


class TestListTrackers:
    async def test_returns_dehydrated_trackers(self, client, fake_db):
        fake_db.queue_rows({'annotation_tracking_id': TRACKER_ID, 'name': 'Test Tracker'})
        response = await client.get('/api/eval/tracking/trackers')

        assert response.status_code == 200
        assert response.json() == [{'annotation_tracking_id': TRACKER_ID, 'name': 'Test Tracker'}]
        assert 'annotation_tracker' in str(fake_db.executions[0].statement)

    async def test_project_without_trackers_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/eval/tracking/trackers')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/eval/tracking/trackers')
        assert response.status_code == 401

    async def test_requires_read_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False)
        response = await client.get('/api/eval/tracking/trackers')
        assert response.status_code == 403
