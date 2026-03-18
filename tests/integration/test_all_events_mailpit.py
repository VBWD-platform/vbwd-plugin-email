"""Mailpit integration test — fire ALL email events, verify ALL arrive.

Triggers every event that has a registered handler, seeds production-quality
templates from seeds.py, and verifies each email lands in Mailpit with correct
recipient, subject rendering, and body content.

Prerequisites:
  - Mailpit running at smtp://mailpit:1025 and http://mailpit:8025
  - PostgreSQL running

Run:
    docker compose run --rm test python -m pytest \
        plugins/email/tests/integration/test_all_events_mailpit.py -v
"""
from __future__ import annotations

import os
import time

import pytest
import requests

from vbwd.events.bus import EventBus
from plugins.email.src.handlers import register_handlers
from plugins.email.src.models.email_template import EmailTemplate
from plugins.email.src.seeds import DEFAULT_TEMPLATES

# ── Mailpit config ────────────────────────────────────────────────────────────

MAILPIT_API = os.getenv("MAILPIT_API_URL", "http://mailpit:8025")
SMTP_HOST = os.getenv("MAILPIT_SMTP_HOST", "mailpit")
SMTP_PORT = int(os.getenv("MAILPIT_SMTP_PORT", "1025"))

SMTP_CONFIG = {
    "smtp_host": SMTP_HOST,
    "smtp_port": SMTP_PORT,
    "smtp_use_tls": False,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_email": "noreply@vbwd.test",
    "smtp_from_name": "VBWD Platform",
}

# ── All 10 events with realistic payloads ─────────────────────────────────────

ALL_EVENTS = [
    {
        "event_type": "user.registered",
        "payload": {
            "user_email": "event-user-registered@vbwd.test",
            "user_name": "Alice Newuser",
            "login_url": "http://localhost:8080/login",
        },
        "expected_recipient": "event-user-registered@vbwd.test",
        "expected_subject_contains": "Alice Newuser",
        "expected_body_contains": "Alice Newuser",
    },
    {
        "event_type": "user.password_reset",
        "payload": {
            "user_email": "event-password-reset@vbwd.test",
            "user_name": "Bob Reset",
            "reset_url": "http://localhost:8080/reset/token-abc-123",
            "expires_in": "1 hour",
        },
        "expected_recipient": "event-password-reset@vbwd.test",
        "expected_subject_contains": "Reset",
        "expected_body_contains": "token-abc-123",
    },
    {
        "event_type": "subscription.activated",
        "payload": {
            "user_email": "event-sub-activated@vbwd.test",
            "user_name": "Carol Subscriber",
            "plan_name": "Professional",
            "plan_price": "$49.00/mo",
            "billing_period": "monthly",
            "start_date": "2026-03-18",
            "next_billing_date": "2026-04-18",
            "dashboard_url": "http://localhost:8080/dashboard",
        },
        "expected_recipient": "event-sub-activated@vbwd.test",
        "expected_subject_contains": "Professional",
        "expected_body_contains": "Professional",
    },
    {
        "event_type": "subscription.cancelled",
        "payload": {
            "user_email": "event-sub-cancelled@vbwd.test",
            "user_name": "Dave Canceller",
            "plan_name": "Starter",
            "end_date": "2026-04-01",
            "resubscribe_url": "http://localhost:8080/plans",
        },
        "expected_recipient": "event-sub-cancelled@vbwd.test",
        "expected_subject_contains": "Starter",
        "expected_body_contains": "2026-04-01",
    },
    {
        "event_type": "subscription.expired",
        "payload": {
            "user_email": "event-sub-expired@vbwd.test",
            "user_name": "Eve Expired",
            "plan_name": "Enterprise",
            "resubscribe_url": "http://localhost:8080/plans",
        },
        "expected_recipient": "event-sub-expired@vbwd.test",
        "expected_subject_contains": "Enterprise",
        "expected_body_contains": "expired",
    },
    {
        "event_type": "subscription.payment_failed",
        "payload": {
            "user_email": "event-payment-failed@vbwd.test",
            "user_name": "Frank Declined",
            "plan_name": "Pro",
            "amount": "$99.00",
            "retry_date": "2026-03-21",
            "update_payment_url": "http://localhost:8080/settings/payment",
        },
        "expected_recipient": "event-payment-failed@vbwd.test",
        "expected_subject_contains": "Pro",
        "expected_body_contains": "$99.00",
    },
    {
        "event_type": "subscription.renewed",
        "payload": {
            "user_email": "event-sub-renewed@vbwd.test",
            "user_name": "Grace Renewed",
            "plan_name": "Business",
            "amount_charged": "$199.00",
            "next_billing_date": "2026-04-18",
            "invoice_url": "http://localhost:8080/invoices/INV-500",
        },
        "expected_recipient": "event-sub-renewed@vbwd.test",
        "expected_subject_contains": "Business",
        "expected_body_contains": "$199.00",
    },
    {
        "event_type": "invoice.created",
        "payload": {
            "user_email": "event-invoice-created@vbwd.test",
            "user_name": "Hal Invoice",
            "invoice_id": "INV-2026-001",
            "amount": "$49.00",
            "due_date": "2026-04-01",
            "invoice_url": "http://localhost:8080/invoices/INV-2026-001",
        },
        "expected_recipient": "event-invoice-created@vbwd.test",
        "expected_subject_contains": "INV-2026-001",
        "expected_body_contains": "$49.00",
    },
    {
        "event_type": "invoice.paid",
        "payload": {
            "user_email": "event-invoice-paid@vbwd.test",
            "user_name": "Ida Payer",
            "invoice_id": "INV-2026-002",
            "amount": "$99.00",
            "paid_date": "2026-03-18",
            "invoice_url": "http://localhost:8080/invoices/INV-2026-002",
        },
        "expected_recipient": "event-invoice-paid@vbwd.test",
        "expected_subject_contains": "INV-2026-002",
        "expected_body_contains": "$99.00",
    },
    {
        "event_type": "contact_form.received",
        "payload": {
            "widget_slug": "main-contact",
            "recipient_email": "event-contact-form@vbwd.test",
            "remote_ip": "203.0.113.42",
            "fields": [
                {"id": "name", "label": "Full Name", "value": "Jack Visitor"},
                {"id": "email", "label": "Email", "value": "jack@example.com"},
                {
                    "id": "message",
                    "label": "Message",
                    "value": "I want to learn more about your platform.",
                },
            ],
            "fields_text": (
                "Full Name: Jack Visitor\nEmail: jack@example.com\n"
                "Message: I want to learn more about your platform."
            ),
        },
        "expected_recipient": "event-contact-form@vbwd.test",
        "expected_subject_contains": "main-contact",
        "expected_body_contains": "Jack Visitor",
    },
]

# ── Mailpit helpers ───────────────────────────────────────────────────────────


def _mailpit_reachable() -> bool:
    try:
        response = requests.get(f"{MAILPIT_API}/api/v1/messages", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def _clear_mailpit() -> None:
    requests.delete(f"{MAILPIT_API}/api/v1/messages", timeout=5)


def _get_all_messages() -> list[dict]:
    response = requests.get(f"{MAILPIT_API}/api/v1/messages", timeout=5)
    return response.json().get("messages") or []


def _get_message_body(message_id: str) -> dict:
    response = requests.get(f"{MAILPIT_API}/api/v1/message/{message_id}", timeout=5)
    return response.json()


def _wait_for_messages(
    expected_count: int, timeout: float = 15.0, poll_interval: float = 0.5
) -> list[dict]:
    """Poll Mailpit until at least expected_count messages arrive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = _get_all_messages()
        if len(messages) >= expected_count:
            return messages
        time.sleep(poll_interval)
    return _get_all_messages()


def _find_message_for_recipient(messages: list[dict], recipient: str) -> dict | None:
    """Find a message by recipient address in the message list."""
    for message in messages:
        recipients = [
            address.get("Address", "") for address in (message.get("To") or [])
        ]
        if recipient in recipients:
            return message
    return None


# ── DB helpers ────────────────────────────────────────────────────────────────


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, database_name = base.rpartition("/")
    database_name = database_name.split("?")[0]
    return f"{prefix}/{database_name}_all_events_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    database_name = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


# ── Fixtures ──────────────────────────────────────────────────────────────────

requires_mailpit = pytest.mark.skipif(
    not _mailpit_reachable(),
    reason="Mailpit not reachable — start docker compose first",
)


@pytest.fixture(scope="module")
def app():
    from vbwd.app import create_app

    database_url = _test_db_url()
    _ensure_test_db(database_url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "RATELIMIT_ENABLED": False,
        "SECRET_KEY": "test-secret",
        "JWT_SECRET_KEY": "test-jwt-secret",
        "FLASK_SECRET_KEY": "test-secret",
    }
    return create_app(test_config)


@pytest.fixture(scope="module")
def database(app):
    from vbwd.extensions import db as database_instance

    with app.app_context():
        database_instance.create_all()
        yield database_instance
        database_instance.session.remove()
        database_instance.drop_all()


@pytest.fixture(scope="module")
def event_bus(app):
    bus = EventBus()
    with app.app_context():
        register_handlers(bus, SMTP_CONFIG)
    return bus


@pytest.fixture(scope="module")
def seeded_templates(app, database):
    """Seed all 12 production templates from seeds.py."""
    with app.app_context():
        for template_data in DEFAULT_TEMPLATES:
            existing = (
                database.session.query(EmailTemplate)
                .filter_by(event_type=template_data["event_type"])
                .first()
            )
            if existing is None:
                database.session.add(
                    EmailTemplate(
                        event_type=template_data["event_type"],
                        subject=template_data["subject"],
                        html_body=template_data["html_body"],
                        text_body=template_data["text_body"],
                        is_active=template_data["is_active"],
                    )
                )
        database.session.commit()
    return True


# ── The main test ─────────────────────────────────────────────────────────────


@requires_mailpit
class TestAllEmailEvents:
    """Fire every email event, verify every email lands in Mailpit."""

    def test_all_events_produce_emails(
        self, app, database, event_bus, seeded_templates
    ):
        """Emit all 10 events, wait, verify 10 emails arrive with correct
        recipients, subjects, and body content."""
        _clear_mailpit()

        # Fire all events
        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        # Wait for all emails to arrive
        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )

        # Build lookup: recipient → full message body
        message_bodies = {}
        for message in delivered_messages:
            message_id = message["ID"]
            full_message = _get_message_body(message_id)
            recipients = [
                address.get("Address", "") for address in (full_message.get("To") or [])
            ]
            for recipient in recipients:
                message_bodies[recipient] = full_message

        # Verify each event produced a correctly rendered email
        missing_events = []
        failed_assertions = []

        for event in ALL_EVENTS:
            recipient = event["expected_recipient"]
            full_message = message_bodies.get(recipient)

            if full_message is None:
                missing_events.append(f"{event['event_type']} → {recipient}")
                continue

            # Check subject
            subject = full_message.get("Subject", "")
            expected_subject_fragment = event["expected_subject_contains"]
            if expected_subject_fragment not in subject:
                failed_assertions.append(
                    f"{event['event_type']}: expected '{expected_subject_fragment}' "
                    f"in subject, got '{subject}'"
                )

            # Check body (HTML or Text)
            html_body = full_message.get("HTML", "")
            text_body = full_message.get("Text", "")
            combined_body = html_body + text_body
            expected_body_fragment = event["expected_body_contains"]
            if expected_body_fragment not in combined_body:
                failed_assertions.append(
                    f"{event['event_type']}: expected '{expected_body_fragment}' "
                    f"in body, not found"
                )

        # Report results
        error_lines = []
        if missing_events:
            error_lines.append(
                f"MISSING EMAILS ({len(missing_events)}/{len(ALL_EVENTS)}):"
            )
            for missing in missing_events:
                error_lines.append(f"  - {missing}")

        if failed_assertions:
            error_lines.append(f"FAILED ASSERTIONS ({len(failed_assertions)}):")
            for failure in failed_assertions:
                error_lines.append(f"  - {failure}")

        if error_lines:
            delivered_recipients = list(message_bodies.keys())
            error_lines.append(
                f"\nDelivered to ({len(delivered_recipients)}): "
                + ", ".join(delivered_recipients)
            )

        assert not error_lines, "\n".join(error_lines)

    def test_total_email_count_matches(
        self, app, database, event_bus, seeded_templates
    ):
        """Exactly 10 emails should be in Mailpit (one per event)."""
        _clear_mailpit()

        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )
        assert len(delivered_messages) == len(
            ALL_EVENTS
        ), f"Expected {len(ALL_EVENTS)} emails, got {len(delivered_messages)}"

    def test_no_duplicate_emails(self, app, database, event_bus, seeded_templates):
        """Each event should produce exactly one email — no duplicates."""
        _clear_mailpit()

        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )

        # Collect all recipient addresses
        all_recipients = []
        for message in delivered_messages:
            for address in message.get("To") or []:
                all_recipients.append(address.get("Address", ""))

        duplicates = [
            recipient
            for recipient in all_recipients
            if all_recipients.count(recipient) > 1
        ]
        assert not duplicates, f"Duplicate emails sent to: {set(duplicates)}"

    def test_all_emails_have_html_body(
        self, app, database, event_bus, seeded_templates
    ):
        """Every delivered email should have a non-empty HTML body."""
        _clear_mailpit()

        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )

        empty_html_events = []
        for message in delivered_messages:
            full_message = _get_message_body(message["ID"])
            html_body = full_message.get("HTML", "").strip()
            if not html_body:
                subject = full_message.get("Subject", "unknown")
                empty_html_events.append(subject)

        assert (
            not empty_html_events
        ), f"Emails with empty HTML body: {empty_html_events}"

    def test_sender_address_is_configured(
        self, app, database, event_bus, seeded_templates
    ):
        """All emails should come from the configured sender address."""
        _clear_mailpit()

        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )

        wrong_senders = []
        for message in delivered_messages:
            full_message = _get_message_body(message["ID"])
            from_address = ""
            from_data = full_message.get("From", {})
            if isinstance(from_data, dict):
                from_address = from_data.get("Address", "")
            elif isinstance(from_data, list) and from_data:
                from_address = from_data[0].get("Address", "")

            if from_address != SMTP_CONFIG["smtp_from_email"]:
                wrong_senders.append(
                    f"{full_message.get('Subject', '?')} — from: {from_address}"
                )

        assert not wrong_senders, (
            f"Expected sender '{SMTP_CONFIG['smtp_from_email']}', "
            f"wrong in: {wrong_senders}"
        )


# ── Individual event verification (parametrized) ─────────────────────────────


@requires_mailpit
class TestEachEventIndividually:
    """Test each event type in isolation — ensures templates render correctly."""

    @pytest.fixture(autouse=True)
    def clear_inbox(self):
        _clear_mailpit()
        yield
        # Do NOT clear after — leave emails in Mailpit for visual inspection

    @pytest.mark.parametrize(
        "event_spec",
        ALL_EVENTS,
        ids=[event["event_type"] for event in ALL_EVENTS],
    )
    def test_event_delivers_email(
        self, app, database, event_bus, seeded_templates, event_spec
    ):
        """Each event type delivers an email to the correct recipient."""
        with app.app_context():
            event_bus.publish(event_spec["event_type"], event_spec["payload"])

        delivered_messages = _wait_for_messages(expected_count=1, timeout=10.0)
        recipient = event_spec["expected_recipient"]
        target_message = _find_message_for_recipient(delivered_messages, recipient)

        assert target_message is not None, (
            f"No email received for {event_spec['event_type']} → {recipient}. "
            f"Mailpit has {len(delivered_messages)} message(s)."
        )

        full_message = _get_message_body(target_message["ID"])

        # Verify subject
        subject = full_message.get("Subject", "")
        assert event_spec["expected_subject_contains"] in subject, (
            f"[{event_spec['event_type']}] expected '{event_spec['expected_subject_contains']}' "
            f"in subject '{subject}'"
        )

        # Verify body content
        html_body = full_message.get("HTML", "")
        text_body = full_message.get("Text", "")
        combined_body = html_body + text_body
        assert event_spec["expected_body_contains"] in combined_body, (
            f"[{event_spec['event_type']}] expected '{event_spec['expected_body_contains']}' "
            f"in body, not found"
        )


# ── Visual inspection — fire all events, leave emails in Mailpit ─────────────


@requires_mailpit
class TestFireAllAndLeaveInMailpit:
    """Fire all 10 events and leave every email in Mailpit for visual inspection.

    After this test, open http://localhost:8025 to see all emails.
    Run this test last (or in isolation):
        docker compose run --rm test python -m pytest \
            plugins/email/tests/integration/test_all_events_mailpit.py::TestFireAllAndLeaveInMailpit -v
    """

    def test_fire_all_events_and_keep_in_mailpit(
        self, app, database, event_bus, seeded_templates
    ):
        """Fire all 10 events. Emails stay in Mailpit for browser inspection."""
        _clear_mailpit()

        with app.app_context():
            for event in ALL_EVENTS:
                event_bus.publish(event["event_type"], event["payload"])

        delivered_messages = _wait_for_messages(
            expected_count=len(ALL_EVENTS), timeout=20.0
        )

        separator = "=" * 60
        print(f"\n{separator}")
        print(f"  {len(delivered_messages)} emails delivered to Mailpit")
        print("  Open http://localhost:8025 to inspect them")
        print(separator)

        for message in delivered_messages:
            recipients = ", ".join(
                address.get("Address", "") for address in (message.get("To") or [])
            )
            print(f"  [{message.get('Subject', '?')}] → {recipients}")

        print()

        assert len(delivered_messages) == len(
            ALL_EVENTS
        ), f"Expected {len(ALL_EVENTS)} emails, got {len(delivered_messages)}"
