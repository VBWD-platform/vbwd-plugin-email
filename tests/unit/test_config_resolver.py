"""Unit tests for resolve_smtp_config — env vars override config.json.

Production SMTP secrets (especially the password) live in the environment,
never in the plaintext config JSON. ``resolve_smtp_config`` layers env vars
on top of the merged DEFAULT_CONFIG + config.json dict.

Critical precedence rule: an env var overrides the config value ONLY when it
is present AND non-empty. An unset or empty-string env var must NOT clobber
the config value (the running container literally has ``SMTP_HOST=""`` set).

The resolver is pure: an explicit ``env`` mapping is injected so the tests do
not depend on the real ``os.environ``.
"""
from plugins.email.src.config import resolve_smtp_config


def _base_cfg(**overrides):
    cfg = {
        "smtp_host": "mailpit",
        "smtp_port": 1025,
        "smtp_user": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "smtp_from_email": "noreply@example.com",
        "smtp_from_name": "VBWD",
    }
    cfg.update(overrides)
    return cfg


class TestEnvOverridesEachField:
    def test_host_user_password_from_email_from_name_override(self):
        env = {
            "SMTP_HOST": "smtp.prod.example.com",
            "SMTP_USER": "prod-user",
            "SMTP_PASSWORD": "s3cr3t",
            "SMTP_FROM_EMAIL": "hello@prod.example.com",
            "SMTP_FROM_NAME": "Prod Sender",
        }
        resolved = resolve_smtp_config(_base_cfg(), env)
        assert resolved["smtp_host"] == "smtp.prod.example.com"
        assert resolved["smtp_user"] == "prod-user"
        assert resolved["smtp_password"] == "s3cr3t"
        assert resolved["smtp_from_email"] == "hello@prod.example.com"
        assert resolved["smtp_from_name"] == "Prod Sender"

    def test_port_parsed_to_int(self):
        resolved = resolve_smtp_config(_base_cfg(), {"SMTP_PORT": "465"})
        assert resolved["smtp_port"] == 465
        assert isinstance(resolved["smtp_port"], int)

    def test_bad_port_is_ignored_and_keeps_cfg_value(self):
        resolved = resolve_smtp_config(
            _base_cfg(smtp_port=1025), {"SMTP_PORT": "not-a-number"}
        )
        assert resolved["smtp_port"] == 1025


class TestNonEmptyGuard:
    def test_empty_env_does_not_override_non_empty_config(self):
        # The real container bug: SMTP_HOST="" must NOT clobber "mailpit".
        resolved = resolve_smtp_config(
            _base_cfg(smtp_host="mailpit"), {"SMTP_HOST": ""}
        )
        assert resolved["smtp_host"] == "mailpit"

    def test_unset_env_does_not_override_config(self):
        resolved = resolve_smtp_config(_base_cfg(smtp_host="mailpit"), {})
        assert resolved["smtp_host"] == "mailpit"

    def test_empty_port_does_not_override_config(self):
        resolved = resolve_smtp_config(_base_cfg(smtp_port=1025), {"SMTP_PORT": ""})
        assert resolved["smtp_port"] == 1025


class TestPasswordSecurityCase:
    def test_password_from_env_wins_over_empty_config_password(self):
        cfg = _base_cfg(smtp_password="")
        resolved = resolve_smtp_config(cfg, {"SMTP_PASSWORD": "env-supplied-secret"})
        assert resolved["smtp_password"] == "env-supplied-secret"


class TestUseTlsParsing:
    def test_true_values(self):
        for raw in ("true", "1", "yes", "starttls", "TRUE", "Yes"):
            resolved = resolve_smtp_config(
                _base_cfg(smtp_use_tls=False), {"SMTP_USE_TLS": raw}
            )
            assert resolved["smtp_use_tls"] is True, raw

    def test_false_values(self):
        for raw in ("false", "0", "no", "FALSE", "No"):
            resolved = resolve_smtp_config(
                _base_cfg(smtp_use_tls=True), {"SMTP_USE_TLS": raw}
            )
            assert resolved["smtp_use_tls"] is False, raw

    def test_ssl_value_returns_string_ssl(self):
        resolved = resolve_smtp_config(
            _base_cfg(smtp_use_tls=True), {"SMTP_USE_TLS": "ssl"}
        )
        assert resolved["smtp_use_tls"] == "ssl"

    def test_garbage_value_is_ignored_keeps_config(self):
        resolved = resolve_smtp_config(
            _base_cfg(smtp_use_tls=True), {"SMTP_USE_TLS": "banana"}
        )
        assert resolved["smtp_use_tls"] is True

    def test_empty_use_tls_does_not_override(self):
        resolved = resolve_smtp_config(
            _base_cfg(smtp_use_tls=True), {"SMTP_USE_TLS": ""}
        )
        assert resolved["smtp_use_tls"] is True


class TestPurity:
    def test_does_not_mutate_input_cfg(self):
        cfg = _base_cfg(smtp_host="mailpit")
        resolve_smtp_config(cfg, {"SMTP_HOST": "other"})
        assert cfg["smtp_host"] == "mailpit"

    def test_passes_through_unrelated_keys(self):
        cfg = _base_cfg()
        cfg["active_sender"] = "smtp"
        cfg["log_sends"] = True
        resolved = resolve_smtp_config(cfg, {})
        assert resolved["active_sender"] == "smtp"
        assert resolved["log_sends"] is True
