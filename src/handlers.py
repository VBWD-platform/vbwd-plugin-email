"""Email event handlers — subscribe to EventBus and fire emails.

Architecture: The email plugin is AGNOSTIC. It does not know about booking,
taro, ghrm, or any other plugin. It subscribes to ALL events via
``bus.subscribe_all()`` and forwards every event to ``EmailService.send_event()``.
If a template exists in the DB for that event_type, the email is sent.
If not, the event is silently ignored.

Special handling exists only for core events that need payload transformation
(e.g. contact_form.received uses ``recipient_email`` instead of ``user_email``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vbwd.events.bus import EventBus

logger = logging.getLogger(__name__)


def _make_email_service(cfg: dict):
    """Factory: create EmailService with active registry + db.session."""
    from vbwd.extensions import db
    from plugins.email.src.services.sender_registry import EmailSenderRegistry
    from plugins.email.src.services.smtp_sender import SmtpEmailSender
    from plugins.email.src.services.email_service import EmailService

    registry = EmailSenderRegistry()
    smtp = SmtpEmailSender(
        host=cfg.get("smtp_host", "localhost"),
        port=int(cfg.get("smtp_port", 587)),
        username=cfg.get("smtp_user") or None,
        password=cfg.get("smtp_password") or None,
        use_tls=cfg.get("smtp_use_tls", True),
        from_address=cfg.get("smtp_from_email", "noreply@example.com"),
        from_name=cfg.get("smtp_from_name", "VBWD"),
    )
    registry.register(smtp)
    registry.set_active("smtp")
    return EmailService(registry=registry, db_session=db.session)


def register_handlers(bus: "EventBus", cfg: dict) -> None:
    """Subscribe email handlers to EventBus.

    Uses ``bus.subscribe_all()`` for a single generic handler that forwards
    every event to EmailService. The service checks the DB for a matching
    template — if one exists, the email is sent; otherwise the event is
    silently ignored.

    Special-case handlers are registered for events that need payload
    transformation (e.g. contact_form.received).
    """

    def _safe_send(event_type: str, to: str, context: dict) -> None:
        try:
            svc = _make_email_service(cfg)
            svc.send_event(event_type, to, context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[email] Failed to send %s to %s: %s", event_type, to, exc)

    # ── Generic handler (catches ALL events) ──────────────────────────────

    # Events with dedicated handlers — skip in generic to avoid duplicates
    _events_with_dedicated_handler = {"contact_form.received"}

    def _on_any_event(event_name: str, payload: dict) -> None:
        """Generic handler: forward any event to EmailService.

        Skips events that have dedicated handlers (to avoid duplicate emails).
        The recipient is resolved from ``user_email`` in the payload.
        The entire payload dict is passed as the template context.
        """
        if event_name in _events_with_dedicated_handler:
            return

        to = payload.get("user_email", "")
        if not to:
            return

        _safe_send(event_name, to, payload)

    bus.subscribe_all(_on_any_event)

    # ── Special-case: contact_form.received ───────────────────────────────
    # Needs payload transformation (fields → fields_text) that plugins
    # cannot do themselves because the template expects a specific format.

    def on_contact_form_received(_name: str, payload: dict) -> None:
        recipient = payload.get("recipient_email", "")
        if not recipient:
            logger.warning(
                "[email] contact_form.received: no recipient_email in payload"
            )
            return
        fields: list = payload.get("fields", [])
        rows = "\n".join(
            f"  {f.get('label', f.get('id', '?'))}: {f.get('value', '')}"
            for f in fields
        )
        _safe_send(
            "contact_form.received",
            recipient,
            {
                "widget_slug": payload.get("widget_slug", ""),
                "recipient_email": recipient,
                "remote_ip": payload.get("remote_ip", ""),
                "fields": fields,
                "fields_text": rows,
            },
        )

    bus.subscribe("contact_form.received", on_contact_form_received)

    logger.info("[email] Event handlers registered (generic + contact_form)")
