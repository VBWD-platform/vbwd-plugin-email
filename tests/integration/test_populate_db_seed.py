"""Integration test — durable seeding of core email templates.

Asserts ``plugins/email/populate_db.py`` seeds the shipped
``core-email-templates.json`` through the EmailTemplate model and is idempotent:
running it twice yields each template exactly once (upsert by ``event_type``),
and ``contact_form.received`` is among the seeded set.
"""
import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
)

os.environ["FLASK_ENV"] = "testing"
os.environ["TESTING"] = "true"

from plugins.email.populate_db import (  # noqa: E402
    load_core_templates,
    seed_core_templates,
)


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

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


@pytest.fixture(scope="module")
def app():
    from vbwd.app import create_app

    url = _test_db_url()
    _ensure_test_db(url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SECRET_KEY": "test-secret-key",
        "JWT_SECRET_KEY": "test-jwt-secret-key",
        "FLASK_SECRET_KEY": "test-secret-key",
    }
    return create_app(test_config)


@pytest.fixture
def db(app):
    from vbwd.extensions import db

    with app.app_context():
        from plugins.email.src.models.email_template import EmailTemplate  # noqa: F401

        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()


class TestPopulateDbSeed:
    def test_seed_creates_core_templates(self, db):
        from plugins.email.src.models.email_template import EmailTemplate

        result = seed_core_templates(db.session)

        expected_count = len(load_core_templates())
        assert result["created"] == expected_count
        assert db.session.query(EmailTemplate).count() == expected_count

    def test_seed_includes_contact_form_received(self, db):
        from plugins.email.src.models.email_template import EmailTemplate

        seed_core_templates(db.session)

        template = (
            db.session.query(EmailTemplate)
            .filter_by(event_type="contact_form.received")
            .first()
        )
        assert template is not None
        # The fixed template renders the handler's data, not unprovided vars.
        assert "fields_text" in template.text_body
        assert "sender_name" not in template.html_body

    def test_seed_is_idempotent(self, db):
        from plugins.email.src.models.email_template import EmailTemplate

        first = seed_core_templates(db.session)
        second = seed_core_templates(db.session)

        expected_count = len(load_core_templates())
        # First run creates all; second run updates in place (0 new rows).
        assert first["created"] == expected_count
        assert second["created"] == 0
        assert db.session.query(EmailTemplate).count() == expected_count

        # Exactly one row per event_type — no duplicates.
        for template in load_core_templates():
            event_type = template["event_type"]
            matches = (
                db.session.query(EmailTemplate).filter_by(event_type=event_type).count()
            )
            assert matches == 1, f"duplicate rows for {event_type}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
