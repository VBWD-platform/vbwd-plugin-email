"""SMTP config resolution — environment overrides on top of config.json.

Production SMTP secrets (especially the password) must live in the
environment (or a secret store), never in the plaintext ``config.json``.
``resolve_smtp_config`` takes the merged DEFAULT_CONFIG + config.json dict
and layers the SMTP_* environment variables on top.

Precedence rule (the crux): an env var overrides the config value ONLY when
it is present AND non-empty. An unset or empty-string env var is treated as
"not provided" and must NOT clobber the config value — the running container
literally has ``SMTP_HOST=""`` set.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

# env var name -> config key
_STRING_ENV_KEYS = {
    "SMTP_HOST": "smtp_host",
    "SMTP_USER": "smtp_user",
    "SMTP_PASSWORD": "smtp_password",
    "SMTP_FROM_EMAIL": "smtp_from_email",
    "SMTP_FROM_NAME": "smtp_from_name",
}

_USE_TLS_TRUE_VALUES = {"true", "1", "yes", "starttls"}
_USE_TLS_FALSE_VALUES = {"false", "0", "no"}
_USE_TLS_SSL_VALUE = "ssl"


def resolve_smtp_config(
    cfg: Dict[str, Any], env: Optional[Mapping[str, str]] = None
) -> Dict[str, Any]:
    """Return effective SMTP config: ``cfg`` with non-empty env vars applied.

    ``cfg`` is not mutated. ``env`` defaults to ``os.environ`` and is injected
    in tests for purity.
    """
    if env is None:
        env = os.environ

    resolved = dict(cfg)

    for env_name, config_key in _STRING_ENV_KEYS.items():
        value = _present_non_empty(env, env_name)
        if value is not None:
            resolved[config_key] = value

    _apply_port(resolved, env)
    _apply_use_tls(resolved, env)

    return resolved


def _present_non_empty(env: Mapping[str, str], env_name: str) -> Optional[str]:
    """Return the env value only if present and non-empty, else None."""
    value = env.get(env_name)
    if value is None or value == "":
        return None
    return value


def _apply_port(resolved: Dict[str, Any], env: Mapping[str, str]) -> None:
    raw_port = _present_non_empty(env, "SMTP_PORT")
    if raw_port is None:
        return
    try:
        resolved["smtp_port"] = int(raw_port)
    except ValueError:
        # Non-numeric override is ignored — keep the config value.
        return


def _apply_use_tls(resolved: Dict[str, Any], env: Mapping[str, str]) -> None:
    raw_use_tls = _present_non_empty(env, "SMTP_USE_TLS")
    if raw_use_tls is None:
        return
    normalized = raw_use_tls.strip().lower()
    if normalized in _USE_TLS_TRUE_VALUES:
        resolved["smtp_use_tls"] = True
    elif normalized in _USE_TLS_FALSE_VALUES:
        resolved["smtp_use_tls"] = False
    elif normalized == _USE_TLS_SSL_VALUE:
        resolved["smtp_use_tls"] = "ssl"
    # Anything else is ignored — keep the config value.
