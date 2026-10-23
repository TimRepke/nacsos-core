"""Tests for the /api/item endpoints (server/api/routes/item.py).

Despite the typed item models, the router is hard-wired to academic items (see the FIXME
comments in item.py): every endpoint reads the ``academic_item`` tables through raw
``db_engine.session()`` queries, so everything here is scripted with ``fake_db``.
"""

from datetime import datetime
from uuid import UUID

from nacsos_data.db.schemas import AcademicItemVariant, AcademicItem, Annotation, BotAnnotation, ItemType

from tests.factories import (
    ASSIGNMENT_ID,
    FORBIDDEN_PROJECT_ID,
    IMPORT_ID,
    ITEM_ID,
    PROJECT_ID,
    SCHEME_ID,
    USER_ID,
    new_id,
    orm_obj,
)

VARIANT_ID = '17171717-1717-1717-1717-171717171717'
ANNOTATION_ID = '18181818-1818-1818-1818-181818181818'
BOT_ANNOTATION_ID = '19191919-1919-1919-1919-191919191919'


def db_academic_item(**columns) -> AcademicItem:
    values: dict = {'item_id': UUID(ITEM_ID), 'project_id': UUID(PROJECT_ID), 'type': ItemType.academic, 'title': 'Old Title'}
    values.update(columns)
    return orm_obj(AcademicItem, **values)


def db_academic_item_variant(**columns) -> AcademicItemVariant:
    values: dict = {'item_variant_id': UUID(VARIANT_ID), 'item_id': UUID(ITEM_ID), 'import_id': UUID(IMPORT_ID), 'import_revision': 1, 'title': 'Variant title'}
    values.update(columns)
    return orm_obj(AcademicItemVariant, **values)


def db_annotation(**columns) -> Annotation:
    values: dict = {
        'annotation_id': UUID(ANNOTATION_ID),
        'assignment_id': UUID(ASSIGNMENT_ID),
        'user_id': UUID(USER_ID),
        'item_id': UUID(ITEM_ID),
        'annotation_scheme_id': UUID(SCHEME_ID),
        'key': 'topic',
        'value_bool': True,
        'repeat': 1,
    }
    values.update(columns)
    return orm_obj(Annotation, **values)


def db_bot_annotation(**columns) -> BotAnnotation:
    values: dict = {'bot_annotation_id': UUID(BOT_ANNOTATION_ID), 'item_id': UUID(ITEM_ID), 'key': 'topic', 'value_int': 2, 'repeat': 1, 'confidence': 0.9}
    values.update(columns)
    return orm_obj(BotAnnotation, **values)


def _update_payload(**overrides) -> dict:
    payload: dict = {'item_id': ITEM_ID, 'title': 'New Title!'}
    payload.update(overrides)
    return payload


class TestGetItemInfo:
    async def test_returns_academic_item(self, client, fake_db):
        fake_db.queue_rows(db_academic_item(doi='10.1000/xyz', publication_year=2024, source='J. Tests'))

        response = await client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 200
        body = response.json()
        assert body['item_id'] == ITEM_ID
        assert body['project_id'] == PROJECT_ID
        assert body['title'] == 'Old Title'
        assert body['doi'] == '10.1000/xyz'
        assert body['publication_year'] == 2024
        assert body['type'] == 'academic'

    async def test_unknown_item_is_a_400(self, client, fake_db):
        """MissingIdError is a plain KeyError without a status -> the middleware defaults to 400, and the
        message comes out quoted because str(KeyError(...)) reprs its argument."""
        response = await client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 400
        detail = response.json()['detail']
        assert detail['type'] == 'MissingIdError'
        assert detail['message'] == "'No item with this ID.'"

    async def test_no_item_type_dispatch_happens(self, client, fake_db):
        """The route never dispatches on the item type: whatever is in academic_item is serialised through
        AcademicItemModel, even if its discriminator claims to be something else (FIXME in item.py)."""
        fake_db.queue_rows(db_academic_item(type=ItemType.lexis))
        response = await client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json()['type'] == 'lexis'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 401

    async def test_requires_dataset_read_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_read=False)
        response = await client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 403

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.get(f'/api/item/info/{ITEM_ID}')
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/item/info/{ITEM_ID}')
            assert response.status_code == 403


class TestUpdateItemInfo:
    async def test_updates_fields_and_recomputes_slug(self, client, fake_db):
        stored = db_academic_item(title='Old Title', title_slug='oldtitle', doi='10.1000/xyz', time_edited=None)
        fake_db.queue_rows(stored)

        response = await client.put('/api/item/info', json=_update_payload(doi='10.5/new'))
        assert response.status_code == 200
        assert response.json() is None  # the route returns None
        assert stored.title == 'New Title!'
        assert stored.doi == '10.5/new'
        assert stored.title_slug == 'newtitle'  # str_to_title_slug strips non-alpha characters
        assert isinstance(stored.time_edited, datetime)
        assert fake_db.commits == 1

    async def test_only_fields_in_the_payload_are_touched(self, client, fake_db):
        stored = db_academic_item(text='keep me', source='J. Tests')
        fake_db.queue_rows(stored)

        response = await client.put('/api/item/info', json=_update_payload())
        assert response.status_code == 200
        assert stored.text == 'keep me'
        assert stored.source == 'J. Tests'

    async def test_title_slug_is_recomputed_from_the_model_not_the_row(self, client, fake_db):
        """Latent bug: title_slug is derived from item.title even when the payload does not carry a title,
        so a partial update silently wipes the stored slug."""
        stored = db_academic_item(title='Old Title', title_slug='oldtitle')
        fake_db.queue_rows(stored)

        response = await client.put('/api/item/info', json={'item_id': ITEM_ID, 'doi': '10.5/only-doi'})
        assert response.status_code == 200
        assert stored.title == 'Old Title'  # untouched ...
        assert stored.title_slug is None  # ... but the slug was nulled because item.title defaulted to None

    async def test_unknown_item_is_a_400(self, client, fake_db):
        response = await client.put('/api/item/info', json=_update_payload())
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingIdError'

    async def test_payload_without_item_id_is_a_400(self, client, fake_db):
        """item_id is optional on AcademicItemModel; in the real DB `item_id IS NULL` can never match the
        (non-null) primary key, so the lookup misses -> 400. (The fake DB ignores WHERE clauses, hence we
        simply queue nothing instead of relying on a mismatch.)"""
        response = await client.put('/api/item/info', json={'title': 'No id given'})
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingIdError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.put('/api/item/info', json=_update_payload())
        assert response.status_code == 401

    async def test_requires_dataset_edit_permission(self, permissions, client):
        permissions.grant(PROJECT_ID, owner=False, dataset_edit=False)
        response = await client.put('/api/item/info', json=_update_payload())
        assert response.status_code == 403

    async def test_invalid_payload_fails_validation(self, client):
        response = await client.put('/api/item/info', json={'item_id': ITEM_ID, 'publication_year': 'not-an-int'})
        assert response.status_code == 422

    async def test_non_uuid_item_id_passes_validation_but_fails_lookup(self, client, fake_db):
        """item_id is typed str | UUID, so arbitrary strings are accepted by validation and only fail the row lookup (400)."""
        response = await client.put('/api/item/info', json={'item_id': 'not-a-uuid', 'title': 'x'})
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'MissingIdError'


class TestGetItemLabels:
    async def test_returns_user_and_bot_annotations(self, client, fake_db):
        fake_db.queue_rows(db_annotation())
        fake_db.queue_rows(db_bot_annotation())

        response = await client.get(f'/api/item/labels/{ITEM_ID}')
        assert response.status_code == 200
        annotations, bot_annotations = response.json()
        assert annotations[0]['annotation_id'] == ANNOTATION_ID
        assert annotations[0]['assignment_id'] == ASSIGNMENT_ID
        assert annotations[0]['key'] == 'topic'
        assert annotations[0]['value_bool'] is True
        assert bot_annotations[0]['bot_annotation_id'] == BOT_ANNOTATION_ID
        assert bot_annotations[0]['value_int'] == 2
        assert bot_annotations[0]['confidence'] == 0.9
        assert len(fake_db.executions) == 2  # one query for annotations, one for bot annotations

    async def test_item_without_labels_returns_two_empty_lists(self, client, fake_db):
        response = await client.get(f'/api/item/labels/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json() == [[], []]

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/item/labels/{ITEM_ID}')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/item/labels/{ITEM_ID}')
            assert response.status_code == 403


class TestGetItemM2Ms:
    async def test_returns_import_relations(self, client, fake_db):
        fake_db.queue_rows(
            {
                'm2m_import_item_id': new_id(),  # extra columns from SELECT * are ignored by the model
                'import_id': IMPORT_ID,
                'item_id': ITEM_ID,
                'type': 'explicit',
                'first_revision': 1,
                'latest_revision': 3,
            }
        )

        response = await client.get(f'/api/item/m2ms/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json() == [{'import_id': IMPORT_ID, 'item_id': ITEM_ID, 'type': 'explicit', 'first_revision': 1, 'latest_revision': 3}]
        # the raw statement binds :import_id ... to the item id (the placeholder is misnamed in the route)
        assert fake_db.executions[0].parameters == {'import_id': ITEM_ID}

    async def test_no_relations_returns_empty_list(self, client, fake_db):
        response = await client.get(f'/api/item/m2ms/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_unknown_m2m_type_is_a_400(self, client, fake_db):
        """ImportM2M.type is an enum, so a row with an unexpected type crashes validation -> 400."""
        fake_db.queue_rows({'import_id': IMPORT_ID, 'item_id': ITEM_ID, 'type': 'suspicious', 'first_revision': 1, 'latest_revision': 1})
        response = await client.get(f'/api/item/m2ms/{ITEM_ID}')
        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'ValidationError'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/item/m2ms/{ITEM_ID}')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/item/m2ms/{ITEM_ID}')
            assert response.status_code == 403


class TestGetItemVariants:
    async def test_returns_variants(self, client, fake_db):
        fake_db.queue_rows(db_academic_item_variant(publication_year=2021, doi='10.1000/var'))

        response = await client.get(f'/api/item/variants/{ITEM_ID}')
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]['item_variant_id'] == VARIANT_ID
        assert body[0]['item_id'] == ITEM_ID
        assert body[0]['import_id'] == IMPORT_ID
        assert body[0]['import_revision'] == 1
        assert body[0]['title'] == 'Variant title'
        assert body[0]['doi'] == '10.1000/var'

    async def test_item_without_variants_returns_empty_list(self, client, fake_db):
        response = await client.get(f'/api/item/variants/{ITEM_ID}')
        assert response.status_code == 200
        assert response.json() == []

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.get(f'/api/item/variants/{ITEM_ID}')
        assert response.status_code == 401

    async def test_requires_permission(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.get(f'/api/item/variants/{ITEM_ID}')
            assert response.status_code == 403
