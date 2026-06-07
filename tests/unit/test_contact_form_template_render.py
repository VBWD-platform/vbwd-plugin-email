"""Bug 2 oracle — the shipped contact_form.received template must render the
data the handler ACTUALLY passes.

The handler (``plugins/email/src/handlers.py::on_contact_form_received``) builds
this context:

    {widget_slug, recipient_email, remote_ip, fields, fields_text}

The shipped template in ``docs/imports/email/core-email-templates.json`` used to
reference ``sender_name`` / ``sender_email`` / ``message_subject`` /
``message_body`` — none of which the handler provides — so every interpolated
field rendered empty and the actual submission (carried in ``fields_text``) was
never shown.

This test renders the JSON template with the handler's REAL context, using the
SAME Jinja2 environment EmailService uses, and asserts the submitted values are
present in the rendered bodies. RED before the template fix, GREEN after.
"""
import json
import os

import pytest

from plugins.email.src.services.email_service import EmailService
from plugins.email.src.services.sender_registry import EmailSenderRegistry

CORE_TEMPLATES_JSON = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "docs",
        "imports",
        "email",
        "core-email-templates.json",
    )
)


def _load_template(event_type: str) -> dict:
    with open(CORE_TEMPLATES_JSON, encoding="utf-8") as handle:
        templates = json.load(handle)
    for template in templates:
        if template.get("event_type") == event_type:
            return template
    raise AssertionError(f"{event_type} not found in {CORE_TEMPLATES_JSON}")


def _handler_context() -> dict:
    """Mirror exactly what on_contact_form_received passes to send_event."""
    fields = [
        {"id": "name", "label": "Your name", "value": "Jane Customer"},
        {"id": "email", "label": "Your email", "value": "jane@buyer.example"},
        {
            "id": "message",
            "label": "Message",
            "value": "Please call me back about pricing.",
        },
    ]
    fields_text = "\n".join(f"  {field['label']}: {field['value']}" for field in fields)
    return {
        "widget_slug": "support-contact",
        "recipient_email": "owner@shop.example",
        "remote_ip": "203.0.113.7",
        "fields": fields,
        "fields_text": fields_text,
    }


def _render(source: str, context: dict) -> str:
    # Reuse EmailService's exact Jinja2 environment (autoescape on).
    service = EmailService(registry=EmailSenderRegistry(), db_session=None)
    return service._render(source, context)


class TestContactFormReceivedTemplate:
    def test_html_body_contains_submitted_values(self):
        template = _load_template("contact_form.received")
        context = _handler_context()
        rendered = _render(template["html_body"], context)

        # The actual submission text must appear — not blank interpolation.
        assert "Jane Customer" in rendered
        assert "jane@buyer.example" in rendered
        assert "Please call me back about pricing." in rendered

    def test_text_body_contains_submitted_values(self):
        template = _load_template("contact_form.received")
        context = _handler_context()
        rendered = _render(template["text_body"], context)

        assert "Jane Customer" in rendered
        assert "jane@buyer.example" in rendered
        assert "Please call me back about pricing." in rendered

    def test_template_does_not_reference_unprovided_variables(self):
        """Subject/bodies must not depend on vars the handler never passes."""
        template = _load_template("contact_form.received")
        unprovided = ("sender_name", "sender_email", "message_subject", "message_body")
        blob = template["subject"] + template["html_body"] + template["text_body"]
        for variable in unprovided:
            assert (
                variable not in blob
            ), f"template references unprovided variable {variable!r}"

    def test_subject_renders_without_unprovided_vars(self):
        template = _load_template("contact_form.received")
        rendered_subject = _render(template["subject"], _handler_context())
        # Non-empty and not just whitespace.
        assert rendered_subject.strip() != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
