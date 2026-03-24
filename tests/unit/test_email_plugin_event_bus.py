"""Unit tests — EmailPlugin wires a generic event handler via EventBus.

The email plugin is AGNOSTIC: it uses bus.subscribe_all() to receive every
event and forwards it to EmailService.send_event(). If a template exists
for the event_type, an email is sent; otherwise the event is ignored.
"""
from unittest.mock import MagicMock, patch

from vbwd.events.bus import EventBus
from plugins.email import EmailPlugin


def _enabled_plugin(config=None) -> tuple:
    """Return (plugin, bus) with plugin initialized + enabled."""
    plugin = EmailPlugin()
    plugin.initialize(config or {})
    plugin.enable()
    bus = EventBus()
    plugin.register_event_handlers(bus)
    return plugin, bus


class TestGenericEventHandler:
    """The email plugin handles ANY event via subscribe_all."""

    def test_global_subscriber_registered(self):
        _plugin, bus = _enabled_plugin()
        assert len(bus._global_subscribers) > 0

    def test_any_event_with_user_email_calls_send_event(self):
        _plugin, bus = _enabled_plugin({"smtp_host": "localhost"})
        mock_svc = MagicMock()
        mock_svc.send_event.return_value = True

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish(
                "some.future.event",
                {"user_email": "test@example.com", "key": "value"},
            )

        mock_svc.send_event.assert_called_once()
        args = mock_svc.send_event.call_args
        assert args[0][0] == "some.future.event"
        assert args[0][1] == "test@example.com"

    def test_event_without_email_is_ignored(self):
        _plugin, bus = _enabled_plugin()
        mock_svc = MagicMock()

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish("some.event", {"data": "no email field"})

        mock_svc.send_event.assert_not_called()

    def test_subscription_activated_calls_send_event(self):
        _plugin, bus = _enabled_plugin({"smtp_host": "localhost"})
        mock_svc = MagicMock()
        mock_svc.send_event.return_value = True

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish(
                "subscription.activated",
                {
                    "user_email": "user@example.com",
                    "user_name": "Alice",
                    "plan_name": "Pro",
                },
            )

        mock_svc.send_event.assert_called_once()
        assert mock_svc.send_event.call_args[0][0] == "subscription.activated"
        assert mock_svc.send_event.call_args[0][1] == "user@example.com"

    def test_booking_created_calls_send_event(self):
        """Booking events are handled generically — no booking-specific code."""
        _plugin, bus = _enabled_plugin({"smtp_host": "localhost"})
        mock_svc = MagicMock()
        mock_svc.send_event.return_value = True

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish(
                "booking.created",
                {
                    "user_email": "booker@example.com",
                    "user_name": "Alice",
                    "resource_name": "Dr. Smith",
                },
            )

        mock_svc.send_event.assert_called_once()
        assert mock_svc.send_event.call_args[0][0] == "booking.created"
        assert mock_svc.send_event.call_args[0][1] == "booker@example.com"

    def test_send_failure_does_not_propagate(self):
        """A crashing EmailService doesn't raise to the caller."""
        _plugin, bus = _enabled_plugin()
        mock_svc = MagicMock()
        mock_svc.send_event.side_effect = RuntimeError("smtp down")

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish("user.registered", {"user_email": "a@b.com"})


class TestContactFormSpecialCase:
    """contact_form.received has special handling (recipient_email, fields_text)."""

    def test_contact_form_has_dedicated_subscriber(self):
        _plugin, bus = _enabled_plugin()
        assert bus.has_subscribers("contact_form.received")

    def test_contact_form_uses_recipient_email(self):
        _plugin, bus = _enabled_plugin({"smtp_host": "localhost"})
        mock_svc = MagicMock()
        mock_svc.send_event.return_value = True

        with patch(
            "plugins.email.src.handlers._make_email_service",
            return_value=mock_svc,
        ):
            bus.publish(
                "contact_form.received",
                {
                    "recipient_email": "admin@example.com",
                    "widget_slug": "contact",
                    "fields": [{"label": "Name", "value": "Bob"}],
                },
            )

        # Called twice: once by the dedicated handler, once by the global handler
        # The dedicated handler transforms fields → fields_text
        assert mock_svc.send_event.call_count >= 1
        # At least one call should have fields_text
        calls = mock_svc.send_event.call_args_list
        dedicated_call = next(
            (c for c in calls if "fields_text" in c[0][2]),
            None,
        )
        assert dedicated_call is not None
