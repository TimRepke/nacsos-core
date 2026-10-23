"""Tests for the /api/annotations endpoints (server/api/routes/annotations.py)."""

from hashlib import md5
from typing import Any
from uuid import UUID

import pytest

from nacsos_data.db.schemas import Assignment, BotAnnotationMetaData
from nacsos_data.models.annotations import AnnotationSchemeModel, AssignmentModel, AssignmentScopeModel, AssignmentStatus
from nacsos_data.models.bot_annotations import BotAnnotationResolution, BotKind, ResolutionProposal
from nacsos_data.models.items.academic import AcademicItemModel
from server.api.routes import annotations as annotations_route
from tests.factories import (
    ASSIGNMENT_ID,
    FORBIDDEN_PROJECT_ID,
    ITEM_ID,
    OTHER_USER_ID,
    PROJECT_ID,
    SCHEME_ID,
    SCOPE_ID,
    USER_ID,
    db_user,
    orm_obj,
    project_model,
)
from tests.mocks import FakeResult, FakeSession, compiled, patch_crud

# local ids (not shared with other test modules)
BOT_META_ID = 'abababab-abab-abab-abab-abababababab'
NEW_ITEM_ID = '20202020-2020-2020-2020-202020202020'

_UNSET = object()


# ------------------------------------------------------------- local builders
def make_scheme(**overrides: Any) -> AnnotationSchemeModel:
    defaults: dict[str, Any] = {
        'name': 'Test Scheme',
        'labels': [{'name': 'L', 'key': 'k', 'kind': 'single', 'choices': [{'name': 'Yes', 'value': 1}]}],
    }
    defaults.update(overrides)
    return AnnotationSchemeModel(**defaults)


def make_scope_model(**overrides: Any) -> AssignmentScopeModel:
    defaults: dict[str, Any] = {'assignment_scope_id': UUID(SCOPE_ID), 'annotation_scheme_id': UUID(SCHEME_ID), 'name': 'Test Scope'}
    defaults.update(overrides)
    return AssignmentScopeModel(**defaults)


def make_assignment_model(**overrides: Any) -> AssignmentModel:
    """Mirrors the harness user: the faked authenticator hands out UserModel with a *string* user_id."""
    defaults: dict[str, Any] = {
        'assignment_id': UUID(ASSIGNMENT_ID),
        'assignment_scope_id': UUID(SCOPE_ID),
        'user_id': USER_ID,
        'item_id': UUID(ITEM_ID),
        'annotation_scheme_id': UUID(SCHEME_ID),
        'status': AssignmentStatus.OPEN,
        'order': 1,
    }
    defaults.update(overrides)
    return AssignmentModel(**defaults)


def make_item() -> AcademicItemModel:
    return AcademicItemModel(item_id=UUID(ITEM_ID), title='Test Item', type='academic', text='body')


def make_proposal() -> ResolutionProposal:
    return ResolutionProposal(
        scheme_info={'name': 'Test Scheme', 'annotation_scheme_id': SCHEME_ID},
        labels=[
            {'path': [{'key': 'k', 'repeat': 1}], 'repeat': 1, 'path_key': 'k', 'key': 'k', 'name': 'L', 'required': True, 'max_repeat': 1, 'kind': 'bool'}
        ],
        annotators=[{'user_id': USER_ID, 'username': 'testuser', 'full_name': 'Testuser'}],
        ordering=[{'identifier': 1, 'first_occurrence': 1, 'item_id': ITEM_ID}],
        matrix={'testuser': {'k': {'labels': {}, 'resolution': {'item_id': ITEM_ID, 'key': 'k'}}}},
    )


def make_resolution_meta() -> BotAnnotationResolution:
    return BotAnnotationResolution(
        bot_annotation_metadata_id=BOT_META_ID,
        name='Saved Resolution',
        kind=BotKind.RESOLVE,
        project_id=PROJECT_ID,
        assignment_scope_id=SCOPE_ID,
        annotation_scheme_id=SCHEME_ID,
        meta={'algorithm': 'majority', 'ignore_hierarchy': False, 'ignore_repeat': False, 'snapshot': [], 'resolutions': []},
    )


def db_bot_meta(
    bot_annotation_metadata_id: str = BOT_META_ID,
    name: str = 'Test Bot',
    kind: BotKind = BotKind.CLASSIFICATION,
    project_id: str = PROJECT_ID,
    **columns: Any,
) -> BotAnnotationMetaData:
    return orm_obj(
        BotAnnotationMetaData,
        bot_annotation_metadata_id=UUID(bot_annotation_metadata_id),
        name=name,
        kind=kind,
        project_id=UUID(project_id),
        **columns,
    )


def db_assignment_row(**columns: Any) -> Assignment:
    """Transient Assignment row (needs an explicit status for the model validation inside the route)."""
    values: dict[str, Any] = {
        'assignment_id': UUID(ASSIGNMENT_ID),
        'assignment_scope_id': UUID(SCOPE_ID),
        'user_id': UUID(USER_ID),
        'item_id': UUID(ITEM_ID),
        'annotation_scheme_id': UUID(SCHEME_ID),
        'status': AssignmentStatus.OPEN.value,
        'order': 1,
    }
    values.update(columns)
    return orm_obj(Assignment, **values)


def _scope_payload(**overrides: Any) -> dict:
    payload = {'assignment_scope_id': SCOPE_ID, 'annotation_scheme_id': SCHEME_ID, 'name': 'Test Scope', 'description': 'Test Scope'}
    payload.update(overrides)
    return payload


def _save_payload(**overrides: Any) -> dict:
    """Client-side payloads carry plain string ids (the route compares them against str() of the DB row)."""
    payload = {
        'scheme': {'name': 'Test Scheme', 'labels': []},
        'assignment': {
            'assignment_id': ASSIGNMENT_ID,
            'assignment_scope_id': SCOPE_ID,
            'user_id': USER_ID,
            'item_id': ITEM_ID,
            'annotation_scheme_id': SCHEME_ID,
            'status': 'OPEN',
        },
    }
    payload.update(overrides)
    return payload


def _edit_payload(**overrides: Any) -> dict:
    payload = {'scope_id': SCOPE_ID, 'scheme_id': SCHEME_ID, 'item_id': ITEM_ID, 'user_id': USER_ID, 'order': 3}
    payload.update(overrides)
    return payload


def _bulk_add_payload(**overrides: Any) -> dict:
    payload = {'user_id': USER_ID, 'scope_id': SCOPE_ID, 'scheme_id': SCHEME_ID, 'item_ids': [ITEM_ID, NEW_ITEM_ID]}
    payload.update(overrides)
    return payload


def _resolve_settings() -> dict:
    return {'algorithm': 'majority', 'ignore_hierarchy': True, 'ignore_repeat': True}


def _patch_annotation_item_stack(
    monkeypatch: pytest.MonkeyPatch,
    scope: Any = _UNSET,
    scheme: Any = _UNSET,
    annotations: Any = _UNSET,
    project: Any = _UNSET,
    item: Any = _UNSET,
) -> None:
    """Patch everything `_construct_annotation_item` touches (utils are patched directly on the route module)."""
    patch_crud(
        monkeypatch,
        annotations_route,
        read_assignment_scope=make_scope_model() if scope is _UNSET else scope,
        read_annotations_for_assignment=[] if annotations is _UNSET else annotations,
        read_project_by_id=project_model() if project is _UNSET else project,
        read_any_item_by_item_id=make_item() if item is _UNSET else item,
    )

    resolved_scheme = make_scheme() if scheme is _UNSET else scheme

    async def fake_read_annotation_scheme(annotation_scheme_id: str, db_engine: Any) -> AnnotationSchemeModel | None:
        return resolved_scheme

    monkeypatch.setattr(annotations_route, 'read_annotation_scheme', fake_read_annotation_scheme)
    # merge_scheme_and_annotations is left real: with no annotations it passes the scheme through unchanged.


# ======================================================== annotate/annotations
class TestGetAnnotations:
    async def test_returns_own_assignments_for_scope(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read(assignment_scope_id, user_id, db_engine):
            seen.update(assignment_scope_id=assignment_scope_id, user_id=user_id)
            return [make_assignment_model()]

        patch_crud(monkeypatch, annotations_route, read_assignments_for_scope_for_user=fake_read)
        response = await client.get(f'/api/annotations/annotate/annotations/{SCOPE_ID}')
        assert response.status_code == 200
        assert [a['assignment_id'] for a in response.json()] == [ASSIGNMENT_ID]
        # the endpoint is user-scoped: the logged in user id is pushed into the query
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert str(seen['user_id']) == USER_ID

    async def test_no_assignments_returns_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignments_for_scope_for_user=[])
        response = await client.get(f'/api/annotations/annotate/annotations/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/annotations/{SCOPE_ID}')
        assert response.status_code == 401


# ================================================= annotate/assignment/progress
class TestAssignmentProgress:
    async def test_returns_overview_entries(self, client, monkeypatch):
        entries = [
            {
                'item_id': ITEM_ID,
                'first_occurrence': 1,
                'identifier': 0,
                'assignments': [
                    {'user_id': USER_ID, 'username': 'testuser', 'order': 0, 'assignment_id': ASSIGNMENT_ID, 'status': 'OPEN'},
                    {'user_id': OTHER_USER_ID, 'username': 'other', 'order': 1, 'assignment_id': ASSIGNMENT_ID, 'status': 'FULL'},
                ],
            }
        ]
        seen: dict = {}

        async def fake_overview(assignment_scope_id, connection):
            seen['assignment_scope_id'] = assignment_scope_id
            return entries

        patch_crud(monkeypatch, annotations_route, read_assignment_overview_for_scope=fake_overview)
        response = await client.get(f'/api/annotations/annotate/assignment/progress/{SCOPE_ID}')
        assert response.status_code == 200
        body = response.json()
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert body[0]['item_id'] == ITEM_ID
        assert {a['username'] for a in body[0]['assignments']} == {'testuser', 'other'}

    async def test_empty_scope_returns_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment_overview_for_scope=[])
        response = await client.get(f'/api/annotations/annotate/assignment/progress/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/assignment/progress/{SCOPE_ID}')
        assert response.status_code == 401


# ======================================================== annotate/assignment/
class TestGetAssignment:
    async def test_returns_constructed_annotation_item(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 200
        body = response.json()
        assert body['assignment']['assignment_id'] == ASSIGNMENT_ID
        assert body['scheme']['name'] == 'Test Scheme'
        assert body['scope']['name'] == 'Test Scope'
        assert body['item']['item_id'] == ITEM_ID
        assert body['item']['type'] == 'academic'

    async def test_unknown_assignment_is_reported_as_401(self, client, monkeypatch):
        """The route cannot distinguish 'not found' from 'not yours' and raises a plain 401 for both."""
        patch_crud(monkeypatch, annotations_route, read_assignment=None)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 401
        assert response.json()['detail'] == 'You do not have permission to handle this assignment, as it is not yours!'

    async def test_foreign_assignment_is_reported_as_401(self, client, monkeypatch):
        """Note: 401 (not 403) even though this is an authorisation failure, and the detail is a bare string."""
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model(user_id=OTHER_USER_ID))
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 401
        assert 'not yours' in response.json()['detail']

    async def test_missing_scope_is_a_404(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch, scope=None)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 404
        assert response.json()['detail']['type'] == 'AnnotationSchemeNotFoundError'

    async def test_missing_scheme_is_a_404(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch, scheme=None)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 404
        assert response.json()['detail']['message'] == f'No annotation scheme found in DB for id {UUID(SCHEME_ID)}'

    async def test_missing_project_is_a_400(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch, project=None)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ProjectNotFoundError'

    async def test_missing_item_is_a_400(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch, item=None)
        response = await client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingInformationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/assignment/{ASSIGNMENT_ID}')
        assert response.status_code == 401


# =================================================== annotate/assignments/scope
class TestGetAssignmentsForScope:
    async def test_returns_all_assignments_of_scope(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read(assignment_scope_id, db_engine):
            seen['assignment_scope_id'] = assignment_scope_id
            return [make_assignment_model(), make_assignment_model(assignment_id=UUID(NEW_ITEM_ID))]

        patch_crud(monkeypatch, annotations_route, read_assignments_for_scope=fake_read)
        response = await client.get(f'/api/annotations/annotate/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 200
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert len(response.json()) == 2

    async def test_empty_scope_returns_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignments_for_scope=[])
        response = await client.get(f'/api/annotations/annotate/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 401


# ===================================================== annotate/assignments/
class TestGetAssignmentsForUser:
    async def test_returns_own_assignments(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read(assignment_scope_id, user_id, db_engine):
            seen.update(assignment_scope_id=assignment_scope_id, user_id=user_id)
            return [make_assignment_model()]

        patch_crud(monkeypatch, annotations_route, read_assignments_for_scope_for_user=fake_read)
        response = await client.get(f'/api/annotations/annotate/assignments/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json()[0]['status'] == 'OPEN'
        assert str(seen['user_id']) == USER_ID

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/assignments/{SCOPE_ID}')
        assert response.status_code == 401

    async def test_requires_project_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/annotations/annotate/assignments/{FORBIDDEN_PROJECT_ID}')
            assert response.status_code == 403


# ============================================= annotate/next/{scope} (open)
class TestNextOpenAssignment:
    async def test_returns_next_open_assignment(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_next_open_assignment_for_scope_for_user=make_assignment_model())
        _patch_annotation_item_stack(monkeypatch)
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json()['assignment']['assignment_id'] == ASSIGNMENT_ID

    async def test_falls_back_to_first_assignment_of_user(self, client, monkeypatch):
        """Everything annotated -> the route replays the first (limit=1) assignment instead of failing."""
        seen: dict = {}

        async def fake_first(assignment_scope_id, user_id, db_engine, limit):
            seen['limit'] = limit
            return [make_assignment_model()]

        patch_crud(monkeypatch, annotations_route, read_next_open_assignment_for_scope_for_user=None, read_assignments_for_scope_for_user=fake_first)
        _patch_annotation_item_stack(monkeypatch)
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}')
        assert response.status_code == 200
        assert seen['limit'] == 1
        assert response.json()['assignment']['assignment_id'] == ASSIGNMENT_ID

    async def test_no_assignments_at_all_is_a_400_warning(self, client, monkeypatch):
        """`NoAssignments` is a bare Warning without `status`, so the middleware uses the default 400."""
        patch_crud(monkeypatch, annotations_route, read_next_open_assignment_for_scope_for_user=None, read_assignments_for_scope_for_user=[])
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}')
        assert response.status_code == 400
        detail = response.json()['detail']
        assert detail['type'] == 'NoAssignments'
        assert detail['level'] == 'WARNING'

    async def test_assignment_without_id_is_a_400(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_next_open_assignment_for_scope_for_user=make_assignment_model(assignment_id=None))
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingInformationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/next/{SCOPE_ID}')
        assert response.status_code == 401


# ============================================= annotate/next/{scope}/{current}
class TestNextAssignment:
    async def test_returns_next_assignment_after_current(self, client, monkeypatch):
        seen: dict = {}

        async def fake_next(current_assignment_id, assignment_scope_id, user_id, db_engine):
            seen.update(current_assignment_id=current_assignment_id, assignment_scope_id=assignment_scope_id, user_id=user_id)
            return make_assignment_model()

        patch_crud(monkeypatch, annotations_route, read_next_assignment_for_scope_for_user=fake_next)
        _patch_annotation_item_stack(monkeypatch)
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}/{ASSIGNMENT_ID}')
        assert response.status_code == 200
        assert seen['current_assignment_id'] == ASSIGNMENT_ID
        assert str(seen['user_id']) == USER_ID
        assert response.json()['item']['item_id'] == ITEM_ID

    async def test_last_assignment_in_scope_is_204(self, client, monkeypatch):
        """NoNextAssignmentWarning carries status 204, and 204 responses are body-less by definition."""
        patch_crud(monkeypatch, annotations_route, read_next_assignment_for_scope_for_user=None)
        response = await client.get(f'/api/annotations/annotate/next/{SCOPE_ID}/{ASSIGNMENT_ID}')
        assert response.status_code == 204
        assert response.content == b''

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/next/{SCOPE_ID}/{ASSIGNMENT_ID}')
        assert response.status_code == 401


# ======================================================== annotate/save
class TestSaveAnnotation:
    async def test_saves_annotations_for_own_assignment(self, client, monkeypatch):
        captured: dict = {}

        def fake_to_annotations(scheme):  # sync helper: the route does not await it
            captured['scheme_name'] = scheme.name
            return [{'key': 'k'}]

        async def fake_upsert(annotations, assignment_id, db_engine):
            captured['num_annotations'] = len(annotations)
            captured['assignment_id'] = assignment_id
            return AssignmentStatus.FULL

        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model(), upsert_annotations=fake_upsert)
        monkeypatch.setattr(annotations_route, 'annotated_scheme_to_annotations', fake_to_annotations)
        response = await client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 200
        assert response.json() == 'FULL'
        assert captured == {'scheme_name': 'Test Scheme', 'num_annotations': 1, 'assignment_id': ASSIGNMENT_ID}

    async def test_writes_only_require_the_read_permission(self, permissions, client, monkeypatch):
        """Latent surprise: POST /annotate/save is guarded by `annotations_read`, not `annotations_edit`."""
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model(), upsert_annotations=AssignmentStatus.PARTIAL)
        monkeypatch.setattr(annotations_route, 'annotated_scheme_to_annotations', lambda scheme: [])
        response = await client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 200
        assert response.json() == 'PARTIAL'

    async def test_missing_assignment_id_is_a_400(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model())
        payload = _save_payload()
        payload['assignment']['assignment_id'] = None
        response = await client.post('/api/annotations/annotate/save', json=payload)
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingInformationError'

    async def test_unknown_assignment_is_a_400(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=None)
        response = await client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 400
        assert response.json()['detail']['message'] == 'No assignment found!'

    async def test_foreign_assignment_is_forbidden_with_plain_detail(self, client, monkeypatch):
        """Mismatch of user/scope/item/scheme -> direct HTTPException 403 with a plain-string detail (no ErrorDetail)."""
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model(user_id=UUID(OTHER_USER_ID)))
        response = await client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 403
        assert response.json()['detail'] == 'The combination of project, assignment, user, task, and item is invalid.'

    async def test_failed_upsert_raises_save_failed_error(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment=make_assignment_model(), upsert_annotations=None)
        monkeypatch.setattr(annotations_route, 'annotated_scheme_to_annotations', lambda scheme: [])
        response = await client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'SaveFailedError'

    async def test_invalid_body_fails_validation(self, client):
        response = await client.post('/api/annotations/annotate/save', json={'scheme': {'name': 'no labels'}})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/annotations/annotate/save', json=_save_payload())
        assert response.status_code == 401


# ================================================= annotate/scope/counts
class TestScopeCounts:
    async def test_returns_counts_for_scope(self, client, monkeypatch):
        seen: dict = {}

        async def fake_counts(assignment_scope_id, db_engine):
            seen['assignment_scope_id'] = assignment_scope_id
            return {'num_total': 10, 'num_open': 4, 'num_partial': 2, 'num_full': 4}

        patch_crud(monkeypatch, annotations_route, read_assignment_counts_for_scope=fake_counts)
        response = await client.get(f'/api/annotations/annotate/scope/counts/{SCOPE_ID}')
        assert response.status_code == 200
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert response.json() == {'num_total': 10, 'num_open': 4, 'num_partial': 2, 'num_full': 4}

    async def test_counts_require_only_one_of_read_or_edit(self, permissions, client, monkeypatch):
        """Checker is built with fulfill_all=False: annotations_edit alone is enough."""
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False, annotations_edit=True)
        patch_crud(monkeypatch, annotations_route, read_assignment_counts_for_scope={'num_total': 1, 'num_open': 1, 'num_partial': 0, 'num_full': 0})
        response = await client.get(f'/api/annotations/annotate/scope/counts/{SCOPE_ID}')
        assert response.status_code == 200

    async def test_without_read_or_edit_permission_is_forbidden(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=False, annotations_edit=False)
        patch_crud(monkeypatch, annotations_route, read_assignment_counts_for_scope={'num_total': 0, 'num_open': 0, 'num_partial': 0, 'num_full': 0})
        response = await client.get(f'/api/annotations/annotate/scope/counts/{SCOPE_ID}')
        assert response.status_code == 403

    async def test_null_counts_collapse_into_a_400_validation_error(self, client, monkeypatch):
        """If the CRUD layer returned None the response model check fails -> ResponseValidationError surfaces as 400."""
        patch_crud(monkeypatch, annotations_route, read_assignment_counts_for_scope=None)
        response = await client.get(f'/api/annotations/annotate/scope/counts/{SCOPE_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ResponseValidationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/annotate/scope/counts/{SCOPE_ID}')
        assert response.status_code == 401


# ================================================= delete annotate/scope
class TestDeleteAssignmentScope:
    async def test_deletes_scope_with_commit(self, client, monkeypatch):
        seen: dict = {}

        async def fake_delete(assignment_scope_id, db_engine, use_commit):
            seen.update(assignment_scope_id=assignment_scope_id, use_commit=use_commit)

        patch_crud(monkeypatch, annotations_route, delete_assignment_scope=fake_delete)
        response = await client.delete(f'/api/annotations/annotate/scope/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() is None
        assert seen == {'assignment_scope_id': SCOPE_ID, 'use_commit': True}

    async def test_value_error_from_crud_becomes_400(self, client, monkeypatch):
        async def fake_delete(assignment_scope_id, db_engine, use_commit):
            raise ValueError('scope still has assignments')

        patch_crud(monkeypatch, annotations_route, delete_assignment_scope=fake_delete)
        response = await client.delete(f'/api/annotations/annotate/scope/{SCOPE_ID}')
        assert response.status_code == 400
        # converted by the route into a plain HTTPException -> bare string detail
        assert response.json()['detail'] == 'scope still has assignments'

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.delete(f'/api/annotations/annotate/scope/{SCOPE_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'/api/annotations/annotate/scope/{SCOPE_ID}')
        assert response.status_code == 401


# ==================================================== put assignments/scope
class TestPutAssignmentScope:
    async def test_upserts_scope_via_insert_on_conflict(self, client, fake_db):
        response = await client.put('/api/annotations/assignments/scope/', json=_scope_payload())
        assert response.status_code == 200
        assert response.json() is None  # no response_model -> literal `null` body
        assert len(fake_db.executions) == 1
        sql = str(fake_db.executions[0].statement).upper()
        assert sql.startswith('INSERT INTO ASSIGNMENT_SCOPE')
        assert 'ON CONFLICT' in sql  # upsert via pg insert-on-conflict
        assert fake_db.commits == 1

    async def test_body_without_required_fields_fails_validation(self, client):
        response = await client.put('/api/annotations/assignments/scope/', json={'name': 'incomplete'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/annotations/assignments/scope/', json=_scope_payload())
        assert response.status_code == 401


# ==================================================== get assignments/scope
class TestGetAssignmentScope:
    async def test_returns_scope(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment_scope=make_scope_model())
        response = await client.get(f'/api/annotations/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json()['name'] == 'Test Scope'
        assert response.json()['assignment_scope_id'] == SCOPE_ID

    async def test_unknown_scope_returns_200_with_null_body(self, client, monkeypatch):
        """Latent surprise: response_model is `AssignmentScopeModel | None`, so a missing scope is `null` (not 404)."""
        patch_crud(monkeypatch, annotations_route, read_assignment_scope=None)
        response = await client.get(f'/api/annotations/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() is None

    async def test_read_permission_alone_is_enough(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, annotations_read=True, annotations_edit=False)
        patch_crud(monkeypatch, annotations_route, read_assignment_scope=make_scope_model())
        response = await client.get(f'/api/annotations/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 200

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 401


# ==================================================== get assignments/scopes/
class TestGetAssignmentScopesForProject:
    async def test_returns_scopes_of_project(self, client, monkeypatch):
        seen: dict = {}

        async def fake_scopes(project_id, db_engine):
            seen['project_id'] = project_id
            return [make_scope_model()]

        patch_crud(monkeypatch, annotations_route, read_assignment_scopes_for_project=fake_scopes)
        response = await client.get('/api/annotations/assignments/scopes/')
        assert response.status_code == 200
        assert seen['project_id'] == PROJECT_ID  # taken from the X-Project-Id permission record
        assert [s['name'] for s in response.json()] == ['Test Scope']

    async def test_no_scopes_returns_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_assignment_scopes_for_project=[])
        response = await client.get('/api/annotations/assignments/scopes/')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/assignments/scopes/')
        assert response.status_code == 401


# ================================================ get assignments/scopes/{project}
class TestGetAssignmentScopesForUser:
    async def test_returns_scopes_with_counters(self, client, monkeypatch):
        entry = {
            'scope': {'assignment_scope_id': SCOPE_ID, 'annotation_scheme_id': SCHEME_ID, 'name': 'Test Scope', 'description': 'Test Scope'},
            'scheme_name': 'Test Scheme',
            'scheme_description': 'desc',
            'num_assignments': 5,
            'num_open': 2,
            'num_partial': 1,
            'num_completed': 2,
        }
        seen: dict = {}

        async def fake_scopes(project_id, user_id, db_engine):
            seen.update(project_id=project_id, user_id=user_id)
            return [entry]

        patch_crud(monkeypatch, annotations_route, read_assignment_scopes_for_project_for_user=fake_scopes)
        response = await client.get(f'/api/annotations/assignments/scopes/{PROJECT_ID}')
        assert response.status_code == 200
        assert str(seen['user_id']) == USER_ID
        body = response.json()[0]
        assert body['scheme_name'] == 'Test Scheme'
        assert body['num_open'] == 2
        assert body['scope']['assignment_scope_id'] == SCOPE_ID

    async def test_path_project_id_is_not_permission_checked(self, client, monkeypatch):
        """Latent issue: only the *header* project is permission-checked, the path project id goes straight to the DB."""
        patch_crud(monkeypatch, annotations_route, read_assignment_scopes_for_project_for_user=[])
        response = await client.get(f'/api/annotations/assignments/scopes/{FORBIDDEN_PROJECT_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/assignments/scopes/{PROJECT_ID}')
        assert response.status_code == 401


# ==================================================== get bot/annotations
class TestBotAnnotations:
    async def test_returns_bot_meta_with_counts(self, client, fake_db):
        fake_db.queue(
            FakeResult(rows=[{'BotAnnotationMetaData': db_bot_meta(), 'num_annotations': 5, 'num_annotated_items': 2}]),
        )
        response = await client.get('/api/annotations/bot/annotations')
        assert response.status_code == 200
        body = response.json()[0]
        assert body['name'] == 'Test Bot'
        assert body['kind'] == 'CLASSIFICATION'
        assert body['bot_annotation_metadata_id'] == BOT_META_ID
        assert body['num_annotations'] == 5
        assert body['num_annotated_items'] == 2

    async def test_scoped_to_header_project(self, client, fake_db):
        response = await client.get('/api/annotations/bot/annotations')
        assert response.status_code == 200
        assert response.json() == []
        statement = fake_db.executions[0].statement
        assert 'bot_annotation_metadata' in str(statement)
        # the filter value rides along as a bind parameter on the statement
        assert [str(v) for v in statement.compile().params.values()] == [PROJECT_ID]

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/bot/annotations')
        assert response.status_code == 401


# ==================================================== get bot/scopes
class TestBotScopes:
    async def test_returns_bot_meta_rows(self, client, fake_db):
        fake_db.queue_rows(db_bot_meta(), db_bot_meta(bot_annotation_metadata_id='cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd', name='Other Bot'))
        response = await client.get('/api/annotations/bot/scopes')
        assert response.status_code == 200
        assert {b['name'] for b in response.json()} == {'Test Bot', 'Other Bot'}

    async def test_only_resolve_filter_is_ignored(self, client, fake_db):
        """Latent bug: `stmt.where(...)` result is discarded (annotations.py:754-755), so the filter never applies."""
        fake_db.queue_rows(db_bot_meta())
        response = await client.get('/api/annotations/bot/scopes', params={'only_resolve': True})
        assert response.status_code == 200
        assert [b['kind'] for b in response.json()] == ['CLASSIFICATION']  # unfiltered!

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/bot/scopes')
        assert response.status_code == 401


# ==================================================== get config/annotators
class TestAnnotatorsForScheme:
    async def test_returns_users_who_annotated(self, client, fake_db):
        fake_db.queue_rows(db_user(), db_user(user_id=OTHER_USER_ID, username='other'))
        response = await client.get(f'/api/annotations/config/annotators/{SCHEME_ID}')
        assert response.status_code == 200
        assert {u['username'] for u in response.json()} == {'testuser', 'other'}
        assert 'JOIN annotation' in str(fake_db.executions[0].statement)  # users reachable only via annotations

    async def test_no_annotators_returns_empty_list(self, client, fake_db):
        response = await client.get(f'/api/annotations/config/annotators/{SCHEME_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_edit_permission_not_just_read(self, permissions, client):
        """Contrast with the read-only endpoints: listing annotators requires `annotations_edit`."""
        permissions.grant(PROJECT_ID, owner=False, annotations_read=True, annotations_edit=False)
        response = await client.get(f'/api/annotations/config/annotators/{SCHEME_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/config/annotators/{SCHEME_ID}')
        assert response.status_code == 401


# ==================================================== put config/assignments/edit
class TestEditAssignment:
    async def test_creates_assignment_when_none_exists(self, client, monkeypatch, fake_db):
        fake_db.queue(FakeResult(rows=[]), FakeResult(scalar=0))
        response = await client.put('/api/annotations/config/assignments/edit/', json=_edit_payload())
        assert response.status_code == 200
        body = response.json()
        assert body['status'] == 'OPEN'
        assert body['order'] == 3
        assert body['item_id'] == ITEM_ID
        assert body['assignment_id']  # freshly generated uuid4
        assert body['assignment_id'] != ASSIGNMENT_ID
        assert len(fake_db.added) == 1
        assert fake_db.added[0].annotation_scheme_id == SCHEME_ID
        assert fake_db.commits == 1

    async def test_deletes_existing_assignment_without_annotations(self, client, monkeypatch, fake_db):
        existing = db_assignment_row()
        fake_db.queue(FakeResult(rows=[existing]), FakeResult(scalar=0))
        response = await client.put('/api/annotations/config/assignments/edit/', json=_edit_payload())
        assert response.status_code == 200
        assert response.json()['assignment_id'] == ASSIGNMENT_ID
        assert fake_db.deleted == [existing]
        assert fake_db.commits == 1
        assert fake_db.added == []

    async def test_existing_assignment_with_annotations_is_a_412_warning(self, client, monkeypatch, fake_db):
        """RemainingDependencyWarning carries status 412, level WARNING; nothing is deleted."""
        fake_db.queue(FakeResult(rows=[db_assignment_row()]), FakeResult(scalar=2))
        response = await client.put('/api/annotations/config/assignments/edit/', json=_edit_payload())
        assert response.status_code == 412
        detail = response.json()['detail']
        assert detail['type'] == 'RemainingDependencyWarning'
        assert detail['level'] == 'WARNING'
        assert fake_db.deleted == []

    async def test_incomplete_payload_fails_validation(self, client):
        response = await client.put('/api/annotations/config/assignments/edit/', json={'scope_id': SCOPE_ID})
        assert response.status_code == 422

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.put('/api/annotations/config/assignments/edit/', json=_edit_payload())
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/annotations/config/assignments/edit/', json=_edit_payload())
        assert response.status_code == 401


# ==================================================== put config/assignments/{scope}
class TestMakeAssignments:
    async def test_invokes_create_assignments(self, client, monkeypatch):
        calls: list = []

        async def fake_create(session, assignment_scope_id, project_id):
            calls.append({'assignment_scope_id': assignment_scope_id, 'project_id': project_id, 'session': session})
            return []

        monkeypatch.setattr(annotations_route, 'create_assignments', fake_create)
        response = await client.put(f'/api/annotations/config/assignments/{SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() is None
        assert calls[0]['assignment_scope_id'] == SCOPE_ID
        assert calls[0]['project_id'] == PROJECT_ID
        assert isinstance(calls[0]['session'], FakeSession)

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.put(f'/api/annotations/config/assignments/{SCOPE_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put(f'/api/annotations/config/assignments/{SCOPE_ID}')
        assert response.status_code == 401


# ==================================================== get config/items
class TestItemsWithCount:
    async def test_returns_items_with_assignment_counts(self, client, monkeypatch):
        rows = [
            {'item_id': ITEM_ID, 'num_total': 3, 'num_open': 1, 'num_partial': 1, 'num_full': 1},
            {'item_id': NEW_ITEM_ID, 'num_total': 1, 'num_open': 1, 'num_partial': 0, 'num_full': 0},
        ]
        seen: dict = {}

        async def fake_read(project_id, db_engine):
            seen['project_id'] = project_id
            return rows

        patch_crud(monkeypatch, annotations_route, read_item_ids_with_assignment_count_for_project=fake_read)
        response = await client.get('/api/annotations/config/items/')
        assert response.status_code == 200
        assert seen['project_id'] == PROJECT_ID
        assert response.json() == rows

    async def test_needs_dataset_read_permission(self, permissions, client, monkeypatch):
        """Guarded by `dataset_read`, not the annotation permissions."""
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        patch_crud(monkeypatch, annotations_route, read_item_ids_with_assignment_count_for_project=[])
        response = await client.get('/api/annotations/config/items/')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/config/items/')
        assert response.status_code == 401


# ==================================================== post config/resolve
class TestConfigResolve:
    async def test_proposals_for_scope(self, client, monkeypatch):
        seen: dict = {}

        async def fake_resolve(**kwargs):
            seen.update(kwargs)
            return make_proposal()

        monkeypatch.setattr(annotations_route, 'get_resolved_item_annotations', fake_resolve)
        response = await client.post(f'/api/annotations/config/resolve?assignment_scope_id={SCOPE_ID}', json=_resolve_settings())
        assert response.status_code == 200
        body = response.json()
        assert body['scheme_info']['name'] == 'Test Scheme'
        assert body['ordering'][0]['item_id'] == ITEM_ID
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert str(seen['strategy']) == 'ResolutionMethod.majority'
        assert seen['ignore_hierarchy'] is True
        assert seen['include_empty'] is False  # query defaults

    async def test_existing_meta_id_shortcuts_to_stored_resolution(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read_existing(db_engine, existing_resolution, include_new, include_empty, update_existing):
            seen.update(existing_resolution=existing_resolution, include_new=include_new)
            return make_proposal()

        patch_crud(monkeypatch, annotations_route, read_resolved_bot_annotations=fake_read_existing)
        response = await client.post(f'/api/annotations/config/resolve?bot_annotation_metadat_id={BOT_META_ID}', json=_resolve_settings())
        assert response.status_code == 200
        # note the misspelled query parameter name: bot_annotation_metadat_id
        assert seen['existing_resolution'] == BOT_META_ID
        assert response.json()['scheme_info']['name'] == 'Test Scheme'

    async def test_without_scope_or_meta_id_raises_value_error(self, client):
        response = await client.post('/api/annotations/config/resolve', json=_resolve_settings())
        assert response.status_code == 400
        detail = response.json()['detail']
        assert detail['type'] == 'ValueError'
        assert detail['message'] == 'Missing assignment scope'

    async def test_unknown_algorithm_fails_validation(self, client):
        response = await client.post(f'/api/annotations/config/resolve?assignment_scope_id={SCOPE_ID}', json={'algorithm': 'nope'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post(f'/api/annotations/config/resolve?assignment_scope_id={SCOPE_ID}', json=_resolve_settings())
        assert response.status_code == 401


# ==================================================== put config/resolve/
class TestSaveResolvedAnnotations:
    async def test_stores_resolution_and_returns_meta_id(self, client, monkeypatch):
        seen: dict = {}

        async def fake_store(**kwargs):
            seen.update(kwargs)
            # NOTE: the route returns the raw CRUD result without str()-ing it (annotations.py:649),
            # so a non-string id trips the `str` response_model -> ResponseValidationError (see below).
            return BOT_META_ID

        patch_crud(monkeypatch, annotations_route, store_resolved_bot_annotations=fake_store)
        response = await client.put(
            f'/api/annotations/config/resolve/?name=My%20Resolution&assignment_scope_id={SCOPE_ID}&annotation_scheme_id={SCHEME_ID}',
            json={'settings': _resolve_settings(), 'matrix': {}},
        )
        assert response.status_code == 200
        assert response.json() == BOT_META_ID
        assert seen['name'] == 'My Resolution'
        assert seen['assignment_scope_id'] == SCOPE_ID
        assert seen['annotation_scheme_id'] == SCHEME_ID
        assert seen['project_id'] == PROJECT_ID
        assert seen['use_commit'] is True
        assert seen['matrix'] == {}

    async def test_scalar_arguments_are_query_parameters(self, client, monkeypatch):
        """name/assignment_scope_id/annotation_scheme_id are scalars -> query, only settings/matrix go in the body."""
        patch_crud(monkeypatch, annotations_route, store_resolved_bot_annotations=UUID(BOT_META_ID))
        response = await client.put(
            f'/api/annotations/config/resolve/?assignment_scope_id={SCOPE_ID}&annotation_scheme_id={SCHEME_ID}',
            json={'settings': _resolve_settings(), 'matrix': {}},
        )
        assert response.status_code == 422  # missing the required `name` query parameter
        assert response.json()['detail'][0]['loc'] == ['query', 'name']

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put(
            f'/api/annotations/config/resolve/?name=x&assignment_scope_id={SCOPE_ID}&annotation_scheme_id={SCHEME_ID}',
            json={'settings': _resolve_settings(), 'matrix': {}},
        )
        assert response.status_code == 401


# ==================================================== put config/resolve/update
class TestUpdateResolvedAnnotations:
    async def test_updates_name_and_matrix(self, client, monkeypatch):
        seen: dict = {}

        async def fake_update(**kwargs):
            seen.update(kwargs)

        patch_crud(monkeypatch, annotations_route, update_resolved_bot_annotations=fake_update)
        response = await client.put(
            f'/api/annotations/config/resolve/update?bot_annotation_metadata_id={BOT_META_ID}&name=Renamed',
            json={'x': {'k': {'labels': {}, 'resolution': {'item_id': ITEM_ID}}}},
        )
        assert response.status_code == 200
        assert response.json() is None
        assert seen['bot_annotation_metadata_id'] == BOT_META_ID
        assert seen['name'] == 'Renamed'
        assert seen['use_commit'] is True
        assert set(seen['matrix']) == {'x'}

    async def test_missing_query_parameters_fail_validation(self, client):
        response = await client.put('/api/annotations/config/resolve/update', json={'x': {'k': {'labels': {}, 'resolution': {'item_id': ITEM_ID}}}})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put(
            f'/api/annotations/config/resolve/update?bot_annotation_metadata_id={BOT_META_ID}&name=x',
            json={'x': {'k': {'labels': {}, 'resolution': {'item_id': ITEM_ID}}}},
        )
        assert response.status_code == 401


# ==================================================== get config/resolved-list
class TestResolvedList:
    async def test_lists_saved_resolutions_of_project(self, client, fake_db):
        fake_db.queue_rows(db_bot_meta(kind=BotKind.RESOLVE, name='Resolution A'))
        response = await client.get('/api/annotations/config/resolved-list/')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['name'] == 'Resolution A'
        assert body[0]['kind'] == 'RESOLVE'
        assert body[0]['bot_annotation_metadata_id'] == BOT_META_ID
        assert 'bot_annotation_metadata' in compiled(fake_db.executions[0].statement)

    async def test_scheme_filter_is_part_of_the_query(self, client, fake_db):
        fake_db.queue_rows()
        response = await client.get('/api/annotations/config/resolved-list/', params={'annotation_scheme_id': SCHEME_ID})
        assert response.status_code == 200
        assert response.json() == []
        statement = fake_db.executions[0].statement
        # project id, scheme id and the RESOLVE kind ride along as bind parameters when the filter is used
        values = {str(getattr(v, 'value', v)) for v in statement.compile().params.values()}
        assert values == {PROJECT_ID, SCHEME_ID, 'RESOLVE'}

    async def test_no_resolutions_returns_empty_list(self, client, fake_db):
        response = await client.get('/api/annotations/config/resolved-list/')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/config/resolved-list/')
        assert response.status_code == 401


# ==================================================== delete config/resolved
class TestDeleteResolvedAnnotations:
    async def test_deletes_existing_meta(self, client, fake_db):
        """
        Latent bug (annotations.py:692-694): the path template is `{bot_annotation_meta_id}` but the
        function parameter is `bot_annotation_metadata_id` -> FastAPI ignores the path segment and
        demands the id as a required *query* parameter instead.
        """
        meta = db_bot_meta()
        fake_db.queue_rows(meta)
        response = await client.delete(f'/api/annotations/config/resolved/{BOT_META_ID}?bot_annotation_metadata_id={BOT_META_ID}')
        assert response.status_code == 200
        assert response.json() is None
        assert fake_db.deleted == [meta]
        assert fake_db.commits == 1

    async def test_unknown_id_deletes_nothing_but_still_200(self, client, fake_db):
        response = await client.delete(f'/api/annotations/config/resolved/{BOT_META_ID}?bot_annotation_metadata_id={BOT_META_ID}')
        assert response.status_code == 200
        assert fake_db.deleted == []
        assert fake_db.commits == 0

    async def test_path_parameter_alone_is_rejected(self, client, fake_db):
        """Because of the path/signature mismatch, the id in the URL alone is not enough (422)."""
        response = await client.delete(f'/api/annotations/config/resolved/{BOT_META_ID}')
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'] == ['query', 'bot_annotation_metadata_id']

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'/api/annotations/config/resolved/{BOT_META_ID}?bot_annotation_metadata_id={BOT_META_ID}')
        assert response.status_code == 401


# ==================================================== get config/resolved/{id}
class TestGetResolvedAnnotations:
    async def test_returns_meta_and_proposal(self, client, monkeypatch):
        meta = make_resolution_meta()
        captured: dict = {}

        async def fake_read_for_meta(session, bot_meta, include_new, include_empty, update_existing):
            captured['bot_meta'] = bot_meta
            return make_proposal()

        patch_crud(monkeypatch, annotations_route, read_resolved_bot_annotation_meta=meta, read_resolved_bot_annotations_for_meta=fake_read_for_meta)
        response = await client.get(f'/api/annotations/config/resolved/{BOT_META_ID}')
        assert response.status_code == 200
        body = response.json()
        assert body['meta']['name'] == 'Saved Resolution'
        assert body['meta']['kind'] == 'RESOLVE'
        assert body['proposal']['scheme_info']['name'] == 'Test Scheme'
        assert captured['bot_meta'] is meta

    async def test_requires_edit_permission(self, permissions, client, monkeypatch):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        patch_crud(monkeypatch, annotations_route, read_resolved_bot_annotation_meta=make_resolution_meta())
        response = await client.get(f'/api/annotations/config/resolved/{BOT_META_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/config/resolved/{BOT_META_ID}')
        assert response.status_code == 401


# ==================================================== put config/scopes/bulk-add
class TestBulkAddAssignments:
    async def test_skips_existing_items_and_keeps_order(self, client, fake_db):
        fake_db.queue(FakeResult(rows=[UUID(ITEM_ID)]))  # ITEM_ID already assigned
        response = await client.put('/api/annotations/config/scopes/bulk-add/', json=_bulk_add_payload())
        assert response.status_code == 200
        assert response.json() is None
        assert len(fake_db.added) == 1
        created = fake_db.added[0]
        assert str(created.item_id) == NEW_ITEM_ID
        # `order` is the raw enumerate index, so the skipped ITEM_ID(0) leaves the new assignment at order=1
        assert created.order == 1
        assert created.status == AssignmentStatus.OPEN
        assert created.assignment_scope_id == SCOPE_ID
        assert fake_db.commits == 1

    async def test_all_items_new_creates_every_assignment(self, client, fake_db):
        fake_db.queue(FakeResult(rows=[]))
        response = await client.put('/api/annotations/config/scopes/bulk-add/', json=_bulk_add_payload())
        assert response.status_code == 200
        assert [a.order for a in fake_db.added] == [0, 1]
        assert len({str(a.assignment_id) for a in fake_db.added}) == 2

    async def test_payload_without_item_ids_fails_validation(self, client):
        response = await client.put('/api/annotations/config/scopes/bulk-add/', json={'user_id': USER_ID})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/annotations/config/scopes/bulk-add/', json=_bulk_add_payload())
        assert response.status_code == 401


# ==================================================== post config/scopes/clear
class TestClearEmptyAssignments:
    async def test_deletes_empty_assignments(self, client, fake_db):
        """
        Latent bug (annotations.py:396-399): the path segment is `{scheme_id}` but the function
        parameter is `scope_id` -> the real scope id has to be passed as a required query parameter.
        """
        response = await client.post(f'/api/annotations/config/scopes/clear/{SCOPE_ID}?scope_id={SCOPE_ID}')
        assert response.status_code == 200
        assert response.json() is None
        assert len(fake_db.executions) == 1
        assert 'DELETE' in str(fake_db.executions[0].statement)
        assert fake_db.executions[0].parameters == {'scope_id': SCOPE_ID, 'user_id': None}
        assert 'AND ass.user_id' not in str(fake_db.executions[0].statement)
        assert fake_db.commits == 1

    async def test_user_id_filter_is_added_when_given(self, client, fake_db):
        response = await client.post(f'/api/annotations/config/scopes/clear/{SCOPE_ID}?scope_id={SCOPE_ID}&user_id={USER_ID}')
        assert response.status_code == 200
        assert 'AND ass.user_id = :user_id' in str(fake_db.executions[0].statement)
        assert fake_db.executions[0].parameters == {'scope_id': SCOPE_ID, 'user_id': USER_ID}

    async def test_path_segment_without_query_parameter_is_a_422(self, client):
        response = await client.post(f'/api/annotations/config/scopes/clear/{SCOPE_ID}')
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'] == ['query', 'scope_id']

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.post(f'/api/annotations/config/scopes/clear/{SCOPE_ID}?scope_id={SCOPE_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post(f'/api/annotations/config/scopes/clear/{SCOPE_ID}?scope_id={SCOPE_ID}')
        assert response.status_code == 401


# ==================================================== get config/scopes/{scheme}
class TestGetScopesForScheme:
    async def test_returns_scopes_of_scheme(self, client, fake_db):
        fake_db.queue_rows(
            {'assignment_scope_id': SCOPE_ID, 'annotation_scheme_id': SCHEME_ID, 'name': 'Test Scope', 'description': 'Test Scope', 'config': None}
        )
        response = await client.get(f'/api/annotations/config/scopes/{SCHEME_ID}')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['name'] == 'Test Scope'
        assert body[0]['assignment_scope_id'] == SCOPE_ID

    async def test_no_scopes_returns_empty_list(self, client, fake_db):
        response = await client.get(f'/api/annotations/config/scopes/{SCHEME_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/config/scopes/{SCHEME_ID}')
        assert response.status_code == 401


# ==================================================== schemes/definition
class TestPutSchemeDefinition:
    async def test_upserts_scheme_and_returns_key(self, client, monkeypatch):
        captured: dict = {}

        async def fake_upsert(annotation_scheme, db_engine):
            captured['scheme'] = annotation_scheme
            return UUID(SCHEME_ID)

        patch_crud(monkeypatch, annotations_route, upsert_annotation_scheme=fake_upsert)
        response = await client.put('/api/annotations/schemes/definition/', json={'name': 'Test Scheme', 'labels': []})
        assert response.status_code == 200
        assert response.json() == SCHEME_ID  # the route str()-ifies the returned key
        assert captured['scheme'].name == 'Test Scheme'

    async def test_body_without_labels_fails_validation(self, client):
        response = await client.put('/api/annotations/schemes/definition/', json={'name': 'incomplete'})
        assert response.status_code == 422

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/annotations/schemes/definition/', json={'name': 'x', 'labels': []})
        assert response.status_code == 401


class TestGetSchemeDefinition:
    async def test_returns_scheme(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read(annotation_scheme_id, db_engine):
            seen['annotation_scheme_id'] = annotation_scheme_id
            return make_scheme()

        monkeypatch.setattr(annotations_route, 'read_annotation_scheme', fake_read)
        response = await client.get(f'/api/annotations/schemes/definition/{SCHEME_ID}')
        assert response.status_code == 200
        body = response.json()
        assert seen['annotation_scheme_id'] == SCHEME_ID
        assert body['name'] == 'Test Scheme'
        assert body['labels'][0]['choices'][0]['value'] == 1
        assert 'implicit_max_repeat' not in body['labels'][0]  # nested (non-flat) label shape

    async def test_flat_scheme_uses_the_flattened_label_shape(self, client, monkeypatch):
        async def fake_read(annotation_scheme_id, db_engine):
            return make_scheme(name='Bool Scheme', labels=[{'name': 'L', 'key': 'k', 'kind': 'bool'}])

        monkeypatch.setattr(annotations_route, 'read_annotation_scheme', fake_read)
        response = await client.get(f'/api/annotations/schemes/definition/{SCHEME_ID}', params={'flat': True})
        assert response.status_code == 200
        label = response.json()['labels'][0]
        assert label['key'] == 'k'
        assert label['implicit_max_repeat'] == 1  # only present on FlattenedAnnotationSchemeLabel
        assert label['parent_label'] is None

    async def test_unknown_scheme_is_a_404(self, client, monkeypatch):
        async def fake_read(annotation_scheme_id, db_engine):
            return None

        monkeypatch.setattr(annotations_route, 'read_annotation_scheme', fake_read)
        response = await client.get(f'/api/annotations/schemes/definition/{SCHEME_ID}')
        assert response.status_code == 404
        detail = response.json()['detail']
        assert detail['type'] == 'AnnotationSchemeNotFoundError'
        assert detail['message'] == f'No `AnnotationScheme` found in DB for id {SCHEME_ID}'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/annotations/schemes/definition/{SCHEME_ID}')
        assert response.status_code == 401


class TestDeleteSchemeDefinition:
    async def test_deletes_scheme_with_commit(self, client, monkeypatch):
        """
        Latent bug (annotations.py:117-119): path template is `{scheme_id}` but the function parameter
        is `annotation_scheme_id` -> FastAPI ignores the path segment and requires a query parameter.
        """
        seen: dict = {}

        async def fake_delete(annotation_scheme_id, db_engine, use_commit):
            seen.update(annotation_scheme_id=annotation_scheme_id, use_commit=use_commit)

        patch_crud(monkeypatch, annotations_route, delete_annotation_scheme=fake_delete)
        response = await client.delete(f'/api/annotations/schemes/definition/{SCHEME_ID}?annotation_scheme_id={SCHEME_ID}')
        assert response.status_code == 200
        assert response.json() is None
        assert seen == {'annotation_scheme_id': SCHEME_ID, 'use_commit': True}

    async def test_path_segment_without_query_parameter_is_a_422(self, client):
        response = await client.delete(f'/api/annotations/schemes/definition/{SCHEME_ID}')
        assert response.status_code == 422
        assert response.json()['detail'][0]['loc'] == ['query', 'annotation_scheme_id']

    async def test_requires_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.delete(f'/api/annotations/schemes/definition/{SCHEME_ID}?annotation_scheme_id={SCHEME_ID}')
        assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.delete(f'/api/annotations/schemes/definition/{SCHEME_ID}?annotation_scheme_id={SCHEME_ID}')
        assert response.status_code == 401


# ==================================================== schemes/fingerprints
class TestSchemeFingerprints:
    async def test_returns_hash_per_scheme(self, client, fake_db):
        fake_db.queue_rows({'annotation_scheme_id': SCHEME_ID, 'hash': 'aaa'}, {'annotation_scheme_id': NEW_ITEM_ID, 'hash': 'bbb'})
        response = await client.get('/api/annotations/schemes/fingerprints')
        assert response.status_code == 200
        assert response.json() == {SCHEME_ID: 'aaa', NEW_ITEM_ID: 'bbb'}

    async def test_merged_returns_combined_md5(self, client, fake_db):
        fake_db.queue_rows({'annotation_scheme_id': SCHEME_ID, 'hash': 'aaa'}, {'annotation_scheme_id': NEW_ITEM_ID, 'hash': 'bbb'})
        response = await client.get('/api/annotations/schemes/fingerprints', params={'merged': True})
        assert response.status_code == 200
        assert response.json() == md5(b'aaabbb').hexdigest()

    async def test_no_schemes_merged_is_md5_of_empty_string(self, client, fake_db):
        response = await client.get('/api/annotations/schemes/fingerprints', params={'merged': True})
        assert response.status_code == 200
        assert response.json() == md5(b'').hexdigest()

    async def test_query_is_scoped_to_project(self, client, fake_db):
        fake_db.queue()
        response = await client.get('/api/annotations/schemes/fingerprints')
        assert response.json() == {}
        assert 'annotation_scheme' in compiled(fake_db.executions[0].statement)
        assert fake_db.executions[0].parameters == {'project_id': PROJECT_ID}

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/schemes/fingerprints')
        assert response.status_code == 401


# ==================================================== schemes/list
class TestSchemeList:
    async def test_returns_all_schemes_of_project(self, client, monkeypatch):
        seen: dict = {}

        async def fake_read(project_id, db_engine):
            seen['project_id'] = project_id
            return [make_scheme(), make_scheme(name='Second Scheme')]

        patch_crud(monkeypatch, annotations_route, read_annotation_schemes_for_project=fake_read)
        response = await client.get('/api/annotations/schemes/list')
        assert response.status_code == 200
        assert seen['project_id'] == PROJECT_ID
        assert {s['name'] for s in response.json()} == {'Test Scheme', 'Second Scheme'}

    async def test_no_schemes_returns_empty_list(self, client, monkeypatch):
        patch_crud(monkeypatch, annotations_route, read_annotation_schemes_for_project=[])
        response = await client.get('/api/annotations/schemes/list')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get('/api/annotations/schemes/list')
        assert response.status_code == 401


# ==================================================== cross-cutting dependency checks
class TestProjectHeaderRequirement:
    async def test_missing_project_header_is_a_422(self, no_project_client):
        response = await no_project_client.get('/api/annotations/schemes/list')
        assert response.status_code == 422

    async def test_missing_project_header_for_scope_endpoint(self, no_project_client):
        response = await no_project_client.get(f'/api/annotations/assignments/scope/{SCOPE_ID}')
        assert response.status_code == 422
