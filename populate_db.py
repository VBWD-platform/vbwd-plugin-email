#!/usr/bin/env python3
"""Idempotent seed for the email plugin's CORE transactional templates.

Loads ``docs/imports/email/core-email-templates.json`` (the same JSON the admin
"Import templates" endpoint consumes) and UPSERTS each entry into the
``email_template`` table keyed by ``event_type`` — exactly the logic of
``POST /api/v1/admin/email/templates/import``. This guarantees a cold-start /
fresh-CI database has the core templates (including ``contact_form.received``)
without a manual admin import.

Seeding goes through the EmailTemplate model/repository layer, never raw SQL —
see ``feedback_no_direct_db_for_test_data.md``.

Discovery: ``flask seed [email]`` runs this module via the standard per-plugin
seed path (``vbwd/cli/seed.py``), passing ``--force``. It is idempotent:
re-running creates 0 duplicates (upsert by ``event_type``).

Usage:
    python plugins/email/populate_db.py [--force] [--check]
"""
import argparse
import json
import os
import sys
from typing import Dict, List

sys.path.insert(0, "/app")

CORE_TEMPLATES_JSON = os.path.join(
    os.path.dirname(__file__),
    "docs",
    "imports",
    "email",
    "core-email-templates.json",
)


def load_core_templates() -> List[Dict]:
    """Read the core-email-templates.json shipped with the plugin."""
    with open(CORE_TEMPLATES_JSON, encoding="utf-8") as handle:
        return json.load(handle)


def upsert_templates(session, templates: List[Dict]) -> Dict[str, int]:
    """Upsert each template by ``event_type`` (create or update in place).

    Mirrors the import-route upsert so the seed and the admin import behave
    identically. Idempotent: a second run updates the existing rows in place,
    never duplicating. Returns counts of created/updated.
    """
    from plugins.email.src.models.email_template import EmailTemplate

    created = 0
    updated = 0
    for item in templates:
        event_type = item.get("event_type")
        if not event_type:
            continue

        existing = session.query(EmailTemplate).filter_by(event_type=event_type).first()
        if existing is not None:
            existing.subject = item.get("subject", existing.subject)
            existing.html_body = item.get("html_body", existing.html_body)
            existing.text_body = item.get("text_body", existing.text_body)
            existing.is_active = item.get("is_active", existing.is_active)
            updated += 1
        else:
            session.add(
                EmailTemplate(
                    event_type=event_type,
                    subject=item.get("subject", ""),
                    html_body=item.get("html_body", ""),
                    text_body=item.get("text_body", ""),
                    is_active=item.get("is_active", True),
                )
            )
            created += 1

    session.commit()
    return {"created": created, "updated": updated}


def seed_core_templates(session) -> Dict[str, int]:
    """Load + upsert the shipped core templates. Returns created/updated counts."""
    return upsert_templates(session, load_core_templates())


def populate(force: bool = False) -> Dict[str, int]:
    from vbwd.extensions import db

    result = seed_core_templates(db.session)
    print(
        "[email] core templates seeded — "
        f"created={result['created']} updated={result['updated']}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed email core templates")
    parser.add_argument(
        "--force", action="store_true", help="Re-upsert existing templates"
    )
    parser.add_argument(
        "--check", action="store_true", help="Exit 1 if templates already present"
    )
    arguments = parser.parse_args()

    from vbwd.app import create_app
    from vbwd.extensions import db

    app = create_app()
    with app.app_context():
        if arguments.check:
            from plugins.email.src.models.email_template import EmailTemplate

            count = db.session.query(EmailTemplate).count()
            sys.exit(1 if count > 0 else 0)
        populate(force=arguments.force)


if __name__ == "__main__":
    main()
