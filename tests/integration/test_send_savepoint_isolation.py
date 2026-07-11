"""Regression: a failed email send must not poison the shared DB transaction.

The email plugin subscribes to ALL events via ``bus.subscribe_all`` and forwards
each one to ``EmailService.send_event`` on the SHARED ``db.session``. If the
template lookup fails at the psycopg2 level (missing ``email_template`` table in a
given DB, transient DB error), the shared session's transaction enters the
aborted state. Historically ``_safe_send`` caught the Python exception and logged
it but never rolled back, so the transaction stayed poisoned and every later
subscriber — and the primary business flow (payment capture / access grant) —
died with ``InFailedSqlTransaction``.

The fix isolates the email DB work in a SAVEPOINT (``db.session.begin_nested()``)
so a failure rolls back cleanly and leaves the outer transaction usable.

This mirrors the production symptom that broke the dataset one-time-order
integration tests (their ``payment.captured`` → ``invoice.paid`` event fired the
email subscriber; its template query failed; the dataset access-grant query then
failed with ``InFailedSqlTransaction``).
"""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import text

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
)

os.environ["FLASK_ENV"] = "testing"
os.environ["TESTING"] = "true"

from vbwd.events.bus import EventBus  # noqa: E402
from plugins.email.src.handlers import register_handlers  # noqa: E402
from plugins.email.src.services.email_service import EmailService  # noqa: E402


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    dbname = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def app():
    from vbwd.app import create_app

    url = _test_db_url()
    _ensure_test_db(url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "RATELIMIT_ENABLED": False,
        "SECRET_KEY": "test-secret-key",
        "JWT_SECRET_KEY": "test-jwt-secret-key",
        "FLASK_SECRET_KEY": "test-secret-key",
    }
    flask_app = create_app(test_config)

    with flask_app.app_context():
        from vbwd.extensions import db as _db
        from vbwd.testing.integration_db import ensure_schema_and_baseline

        # Register the email-template model so the shared schema includes it.
        from plugins.email.src.models.email_template import (  # noqa: F401
            EmailTemplate,
        )

        ensure_schema_and_baseline(_db)

    yield flask_app


@pytest.fixture
def db(app):
    """Isolate each test in a rolled-back transaction (self-cleaning, no wipe)."""
    from vbwd.extensions import db

    with app.app_context():
        from vbwd.testing.integration_db import rollback_isolation

        with rollback_isolation(db):
            yield db


def test_failed_send_does_not_poison_transaction(db, monkeypatch):
    """A send whose template query hits a missing relation must roll back
    cleanly and leave the shared session usable for the primary flow."""

    def _boom(self, event_type):
        # A real psycopg2 UndefinedTable that aborts the DB transaction — the
        # exact production symptom (missing/absent template relation).
        return self._session.execute(
            text("SELECT 1 FROM __definitely_missing_table__")
        ).first()

    monkeypatch.setattr(EmailService, "_get_template", _boom)

    bus = EventBus()
    register_handlers(bus, cfg={})

    # The generic subscriber resolves the recipient from ``user_email`` and
    # then calls into EmailService — which is where the failing query fires.
    bus.publish("some.event", {"user_email": "buyer@example.com"})

    # Without the savepoint isolation this raises InFailedSqlTransaction because
    # the shared transaction is still aborted.
    assert db.session.execute(text("SELECT 1")).scalar() == 1
