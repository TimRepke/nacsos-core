"""Tests for the /api/mail endpoints (server/api/routes/mailing.py).

No SMTP is ever reached: every route hands the mail over to ``send_message`` via ``BackgroundTasks``, so the tests patch
that helper (as imported into the route module) with a recorder — background tasks run to completion before the ASGI
call underlying ``httpx.ASGITransport`` returns.

Surprising actual behaviours worth knowing (comments repeat them at the tests):
* the route module never consults ``settings.EMAIL.ENABLED``; the gate lives in ``server/util/email.py::send_email``,
  which raises ``EmailNotSentError`` at *background execution* time — i.e. after the response was already produced.
  The ``try/except EmailNotSentError`` around ``background_tasks.add_task`` in ``reset_password`` (mailing.py:35-54)
  is therefore dead code.
* ``reset_password`` is completely unauthenticated and answers plain ``'OK'`` even for unknown usernames to avoid
  leaking which names exist (the NotFoundError raise is commented out, mailing.py:56-58).
* ``news`` filters by ``is_subscribed`` with a truthiness check (``if is_subscribed:``, mailing.py:176) instead of
  ``is not None``, so ``is_subscribed=False`` silently skips the newsletter filter.
"""

from typing import Any

import pytest

from nacsos_data.models.users import UserModel

from server.api.routes import mailing as mailing_route
from server.util import email as email_util
from server.util.config import settings
from server.util.security import auth_helper

from tests.factories import FORBIDDEN_PROJECT_ID, PROJECT_ID, SCOPE_ID, TOKEN_ID, USER_ID, make_token, make_user
from tests.mocks import FakeResult, compiled, patch_crud


@pytest.fixture
def sent_mail(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records the ``send_message`` calls the routes schedule, instead of touching SMTP."""
    calls: list[dict[str, Any]] = []

    async def record(sender: str | None, recipients: list[str], bcc: list[str], subject: str, message: str) -> None:
        calls.append({'sender': sender, 'recipients': recipients, 'bcc': bcc, 'subject': subject, 'message': message})

    monkeypatch.setattr(mailing_route, 'send_message', record)
    return calls


def _reminder_row(username: str = 'alice', email: str = 'alice@example.org', full_name: str = 'Alice', **overrides: Any) -> dict[str, Any]:
    """One grouped row of the reminder's aggregate query (``.mappings().all()`` hands out plain dicts)."""
    row: dict[str, Any] = {'username': username, 'email': email, 'full_name': full_name, 'num_assignments': 5, 'num_open': 3, 'num_done': 1, 'num_part': 1}
    row.update(overrides)
    return row


def _news_row(username: str = 'alice', email: str = 'alice@example.org', full_name: str = 'Alice', **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {'username': username, 'email': email, 'full_name': full_name}
    row.update(overrides)
    return row


def _user_without_email() -> UserModel:
    """factories.make_user always fills an address, so build the model directly to keep email None."""
    return UserModel(user_id=USER_ID, username='testuser')


class TestResetPassword:
    async def test_schedules_reset_mail_for_known_user(self, anon_client, monkeypatch, sent_mail):
        """The route has no auth dependency at all — an anonymous caller gets a real 200 (mailing.py:25)."""
        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())

        response = await anon_client.post('/api/mail/reset-password/testuser')

        assert response.status_code == 200
        assert response.text == 'OK'
        assert len(sent_mail) == 1
        call = sent_mail[0]
        assert call['recipients'] == ['testuser@example.org']
        assert call['bcc'] == []
        assert call['subject'] == '[NACSOS] Reset password'
        assert 'Dear Testuser,' in call['message']
        assert f'{settings.SERVER.WEB_URL}/#/password-reset/' in call['message']

    async def test_closes_sessions_and_embeds_fresh_token(self, anon_client, monkeypatch, sent_mail):
        cleared: list[str] = []
        created: list[dict[str, Any]] = []

        async def fake_clear_tokens_by_user(username: str) -> None:
            cleared.append(username)

        async def fake_refresh_or_create_token(username: str | None = None, token_id: Any = None, **kwargs: Any) -> Any:
            created.append({'username': username, **kwargs})
            return make_token(token_id=TOKEN_ID)

        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())
        monkeypatch.setattr(auth_helper, 'clear_tokens_by_user', fake_clear_tokens_by_user)
        monkeypatch.setattr(auth_helper, 'refresh_or_create_token', fake_refresh_or_create_token)

        response = await anon_client.post('/api/mail/reset-password/testuser')

        assert response.status_code == 200
        assert cleared == ['testuser']
        assert created == [{'username': 'testuser', 'token_lifetime_minutes': 24 * 60}]
        assert f'/password-reset/{TOKEN_ID}' in sent_mail[0]['message']
        assert 'closed all your active sessions' in sent_mail[0]['message']

    async def test_unknown_user_still_answers_ok(self, anon_client, monkeypatch, sent_mail):
        """The NotFoundError raise is commented out on purpose (mailing.py:56-58) to prevent username enumeration."""
        patch_crud(monkeypatch, mailing_route, read_user_by_name=None)

        response = await anon_client.post('/api/mail/reset-password/ghost')

        assert response.status_code == 200
        assert response.text == 'OK'
        assert sent_mail == []

    async def test_user_without_email_is_skipped(self, anon_client, monkeypatch, sent_mail):
        patch_crud(monkeypatch, mailing_route, read_user_by_name=_user_without_email())

        response = await anon_client.post('/api/mail/reset-password/testuser')

        assert response.status_code == 200
        assert response.text == 'OK'
        assert sent_mail == []

    async def test_mailing_disabled_schedules_task_but_never_sends(self, anon_client, monkeypatch):
        """With ENABLED=False the *route* still returns 200 and schedules the task, but the real send helper
        refuses to deliver (mailing.py never consults settings.EMAIL.ENABLED; email.py:65-66 is the gate)."""
        monkeypatch.setattr(settings.EMAIL, 'ENABLED', False)  # the configured default
        blocked: list[str] = []

        async def try_real_send(**kwargs: Any) -> None:
            try:
                await email_util.send_message(**kwargs)  # raises before any SMTP connection is opened
            except email_util.EmailNotSentError as exc:
                blocked.append(str(exc))

        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())
        monkeypatch.setattr(mailing_route, 'send_message', try_real_send)

        response = await anon_client.post('/api/mail/reset-password/testuser')

        assert response.status_code == 200
        assert response.text == 'OK'
        assert len(blocked) == 1
        assert 'Mailing system inactive' in blocked[0]
        assert '[NACSOS] Reset password' in blocked[0]

    async def test_mailing_enabled_variant_schedules_the_same_task(self, anon_client, monkeypatch, sent_mail):
        """The route behaves identically whether mail is enabled or not — it only schedules the background task."""
        monkeypatch.setattr(settings.EMAIL, 'ENABLED', True)
        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())

        response = await anon_client.post('/api/mail/reset-password/testuser')

        assert response.status_code == 200
        assert response.text == 'OK'
        assert [call['subject'] for call in sent_mail] == ['[NACSOS] Reset password']


class TestWelcome:
    async def test_superuser_gets_welcome_mail_for_known_user(self, superuser_client, monkeypatch, sent_mail):
        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())

        response = await superuser_client.post('/api/mail/welcome', params={'username': 'testuser', 'password': 's3cret'})

        assert response.status_code == 200
        assert response.text == 'OK'
        assert len(sent_mail) == 1
        call = sent_mail[0]
        assert call['recipients'] == ['testuser@example.org']
        assert call['subject'] == '[NACSOS] Welcome to the platform'
        assert 'Username: testuser' in call['message']
        assert 'Password: s3cret' in call['message']
        assert '/#/password-reset/' in call['message']

    async def test_unknown_user_reports_ignore_without_mail(self, superuser_client, monkeypatch, sent_mail):
        patch_crud(monkeypatch, mailing_route, read_user_by_name=None)

        response = await superuser_client.post('/api/mail/welcome', params={'username': 'ghost', 'password': 'x'})

        assert response.status_code == 200
        assert response.text == 'IGNORE'  # unlike reset-password, welcome *does* disclose that the user is unknown
        assert sent_mail == []

    async def test_user_without_email_reports_ignore(self, superuser_client, monkeypatch, sent_mail):
        patch_crud(monkeypatch, mailing_route, read_user_by_name=_user_without_email())

        response = await superuser_client.post('/api/mail/welcome', params={'username': 'testuser', 'password': 'x'})

        assert response.status_code == 200
        assert response.text == 'IGNORE'
        assert sent_mail == []

    async def test_requires_superuser(self, client, monkeypatch):
        patch_crud(monkeypatch, mailing_route, read_user_by_name=make_user())

        response = await client.post('/api/mail/welcome', params={'username': 'testuser', 'password': 'x'})

        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_inactive_account_is_rejected(self, inactive_client):
        response = await inactive_client.post('/api/mail/welcome', params={'username': 'testuser', 'password': 'x'})
        assert response.status_code == 403
        assert response.json()['detail']['message'] == 'Inactive user'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/mail/welcome', params={'username': 'testuser', 'password': 'x'})
        assert response.status_code == 401

    async def test_password_is_a_required_query_parameter(self, superuser_client):
        """username/password are bare str params, so FastAPI reads them from the query string, not the body."""
        response = await superuser_client.post('/api/mail/welcome', params={'username': 'testuser'})
        assert response.status_code == 422


class TestAssignmentReminder:
    async def test_reminds_users_with_open_assignments(self, client, fake_db, sent_mail):
        """Reads happen through raw sessions (fake_db), not CRUD helpers."""
        fake_db.queue(FakeResult(rows=[{'scope_name': 'Scope A', 'project_name': 'Test Project'}]))
        fake_db.queue(
            FakeResult(
                rows=[
                    _reminder_row(),
                    _reminder_row(username='doneuser', email='done@example.org', full_name='Done User', num_open=0),
                ]
            )
        )

        response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})

        assert response.status_code == 200
        assert response.json() == ['alice']  # only users with num_open > 0 are reminded
        assert len(sent_mail) == 1
        call = sent_mail[0]
        assert call['recipients'] == ['alice@example.org']
        assert call['subject'] == '[NACSOS] Assignments waiting for you'
        assert 'Dear Alice,' in call['message']
        assert 'In the project "Test Project", in the scope "Scope A"' in call['message']
        assert 'created 5 assignments' in call['message']
        assert 'fully finished 1' in call['message']
        assert '1 are partially done' in call['message']
        assert '3 are still open' in call['message']
        assert len(fake_db.executions) == 2
        assert fake_db.commits == 0

    async def test_no_open_assignments_reminds_nobody(self, client, fake_db, sent_mail):
        fake_db.queue(FakeResult(rows=[{'scope_name': 'Scope A', 'project_name': 'Test Project'}]))
        fake_db.queue(FakeResult(rows=[_reminder_row(num_open=0)]))

        response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})

        assert response.status_code == 200
        assert response.json() == []
        assert sent_mail == []

    async def test_unknown_scope_raises_not_found(self, client, fake_db, sent_mail):
        """nacsos_data's NotFoundError carries no status attribute, so the middleware default (400) applies."""
        fake_db.queue(FakeResult(rows=[]))  # scope/project info lookup comes up empty

        response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})

        assert response.status_code == 400
        assert response.json()['detail']['type'] == 'NotFoundError'
        assert response.json()['detail']['message'] == 'No data associated with this project.'
        assert sent_mail == []

    async def test_requires_annotations_edit_flag(self, client, permissions, fake_db, sent_mail):
        """The dependency asks for 'annotations_edit'; without owner status that flag decides access."""
        permissions.grant(PROJECT_ID, owner=False, annotations_edit=False)
        response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 403

        permissions.grant(PROJECT_ID, owner=False, annotations_edit=True)
        fake_db.queue(FakeResult(rows=[]))
        response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 400  # permission passes now, the (empty) scope info lookup fails with NotFoundError
        assert sent_mail == []

    async def test_requires_project_header(self, no_project_client):
        response = await no_project_client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 422

    async def test_requires_permission_on_project(self, make_client):
        async with make_client(project_id=FORBIDDEN_PROJECT_ID) as client:
            response = await client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})
            assert response.status_code == 403

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/mail/assignment-reminder', params={'assignment_scope_id': SCOPE_ID})
        assert response.status_code == 401


class TestNews:
    async def test_sends_news_to_every_user(self, superuser_client, fake_db, sent_mail):
        fake_db.queue_rows(_news_row(), _news_row(username='bob', email='bob@example.org', full_name='Bob'))

        response = await superuser_client.post('/api/mail/news', params={'is_active': True}, json={'subject': 'Release 1.0', 'body': 'We shipped it.'})

        assert response.status_code == 200
        assert response.json() == ['alice', 'bob']
        assert [call['recipients'] for call in sent_mail] == [['alice@example.org'], ['bob@example.org']]
        assert all(call['subject'] == '[NACSOS] -NEWS- | Release 1.0' for call in sent_mail)
        assert 'Dear Alice,' in sent_mail[0]['message']
        assert 'We shipped it.' in sent_mail[0]['message']

    async def test_no_users_sends_nothing(self, superuser_client, fake_db, sent_mail):
        response = await superuser_client.post('/api/mail/news', json={'subject': 'Hello', 'body': 'Nothing to tell.'})

        assert response.status_code == 200
        assert response.json() == []
        assert sent_mail == []

    async def test_is_active_filter_is_applied(self, superuser_client, fake_db):
        response = await superuser_client.post('/api/mail/news', params={'is_active': False}, json={'subject': 'Hello', 'body': 'x'})

        assert response.status_code == 200
        sql = compiled(fake_db.executions[0].statement)
        assert 'is_active' in sql

    async def test_is_subscribed_false_silently_skips_the_filter(self, superuser_client, fake_db):
        """Latent bug (mailing.py:176): ``if is_subscribed:`` instead of ``if is_subscribed is not None:`` — passing
        False does NOT restrict the mail to newsletter unsubscribers."""
        response = await superuser_client.post('/api/mail/news', params={'is_subscribed': False}, json={'subject': 'Hello', 'body': 'x'})

        assert response.status_code == 200
        assert 'setting_newsletter' not in compiled(fake_db.executions[0].statement)

    async def test_is_subscribed_true_filters_on_the_newsletter_flag(self, superuser_client, fake_db):
        response = await superuser_client.post('/api/mail/news', params={'is_subscribed': True}, json={'subject': 'Hello', 'body': 'x'})

        assert response.status_code == 200
        assert 'setting_newsletter' in compiled(fake_db.executions[0].statement)

    async def test_body_fields_are_required(self, superuser_client):
        response = await superuser_client.post('/api/mail/news', json={'subject': 'only subject'})
        assert response.status_code == 422

    async def test_requires_superuser(self, client):
        response = await client.post('/api/mail/news', json={'subject': 'Hello', 'body': 'x'})
        assert response.status_code == 403
        assert response.json()['detail']['type'] == 'InsufficientPermissions'

    async def test_requires_authentication(self, anon_client):
        response = await anon_client.post('/api/mail/news', json={'subject': 'Hello', 'body': 'x'})
        assert response.status_code == 401
