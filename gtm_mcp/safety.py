"""Fail-closed security primitives for Google Tag Manager mutations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

_HASH_VERSION = 3
_REPLAY_LOCK = threading.Lock()
_USED_CONFIRMATIONS: dict[str, int] = {}


class SafetyError(ValueError):
    """Structured validation or safety error."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class SafetyConfig:
    mutations_enabled: bool
    allow_unsafe_legacy_mutations: bool
    allow_create: bool
    allow_update: bool
    allow_delete: bool
    allow_revert: bool
    allow_create_version: bool
    allow_set_latest: bool
    allow_publish: bool
    allow_publish_non_latest: bool
    allow_undelete: bool
    allowed_account_ids: frozenset[str]
    allowed_container_ids: frozenset[str]
    allowed_workspace_ids: frozenset[str]
    max_operations: int
    confirmation_ttl_seconds: int


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SafetyError("INVALID_ENVIRONMENT_VALUE", f"{name} must be true or false.", {"environment_variable": name})


def _env_ids(name: str) -> frozenset[str]:
    values = {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}
    invalid = sorted(item for item in values if not item.isdigit())
    if invalid:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{name} must contain comma-separated numeric IDs.",
            {"invalid_values": invalid},
        )
    return frozenset(values)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SafetyError("INVALID_ENVIRONMENT_VALUE", f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{name} must be between {minimum} and {maximum}.",
            {"value": value},
        )
    return value


def load_safety_config() -> SafetyConfig:
    return SafetyConfig(
        mutations_enabled=_env_bool("GTM_MUTATIONS_ENABLED"),
        allow_unsafe_legacy_mutations=_env_bool("GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS"),
        allow_create=_env_bool("GTM_ALLOW_CREATE"),
        allow_update=_env_bool("GTM_ALLOW_UPDATE"),
        allow_delete=_env_bool("GTM_ALLOW_DELETE"),
        allow_revert=_env_bool("GTM_ALLOW_REVERT"),
        allow_create_version=_env_bool("GTM_ALLOW_CREATE_VERSION"),
        allow_set_latest=_env_bool("GTM_ALLOW_SET_LATEST"),
        allow_publish=_env_bool("GTM_ALLOW_PUBLISH"),
        allow_publish_non_latest=_env_bool("GTM_ALLOW_PUBLISH_NON_LATEST"),
        allow_undelete=_env_bool("GTM_ALLOW_UNDELETE"),
        allowed_account_ids=_env_ids("GTM_ALLOWED_ACCOUNT_IDS"),
        allowed_container_ids=_env_ids("GTM_ALLOWED_CONTAINER_IDS"),
        allowed_workspace_ids=_env_ids("GTM_ALLOWED_WORKSPACE_IDS"),
        max_operations=_env_int("GTM_MAX_OPERATIONS_PER_REQUEST", 10, 1, 10),
        confirmation_ttl_seconds=_env_int("GTM_CONFIRMATION_TTL_SECONDS", 900, 60, 3600),
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def operation_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:32]


def snapshot_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or "=" in value:
        raise SafetyError("INVALID_CONFIRMATION", "Invalid base64url encoding.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise SafetyError("INVALID_CONFIRMATION", "Invalid base64url encoding.") from exc
    if _b64url_encode(decoded) != value:
        raise SafetyError("INVALID_CONFIRMATION", "Non-canonical base64url encoding.")
    return decoded


def _confirmation_secret() -> bytes:
    secret = os.getenv("GTM_CONFIRMATION_SECRET", "").encode("utf-8")
    if len(secret) < 32:
        raise SafetyError("CONFIRMATION_SECRET_MISSING", "GTM_CONFIRMATION_SECRET must be at least 32 bytes.")
    return secret


def _purge_replay_cache(now: int) -> None:
    for key in [key for key, expiry in _USED_CONFIRMATIONS.items() if expiry < now]:
        _USED_CONFIRMATIONS.pop(key, None)


def issue_confirmation(
    normalized_payload: Mapping[str, Any],
    *,
    verb: str,
    stage: str,
    scope: Mapping[str, Any],
    ttl_seconds: int,
) -> dict[str, Any]:
    digest = operation_hash(normalized_payload)
    issued_at = int(time.time())
    expires_at = issued_at + ttl_seconds
    token_payload = {
        "exp": expires_at,
        "hash": digest,
        "iat": issued_at,
        "nonce": _b64url_encode(secrets.token_bytes(12)),
        "scope": dict(scope),
        "stage": stage,
        "v": 1,
        "verb": verb,
    }
    encoded = _b64url_encode(canonical_json(token_payload).encode("utf-8"))
    signature = _b64url_encode(hmac.new(_confirmation_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return {
        "operation_hash": digest,
        "operation_hash_version": _HASH_VERSION,
        "required_confirmation": f"{verb} {digest}.{encoded}.{signature}",
        "confirmation_expires_at_epoch": expires_at,
        "validation_receipt": {
            "expires_at_epoch": expires_at,
            "cross_instance_valid": True,
            "replay_protection": "BEST_EFFORT_PROCESS_LOCAL",
            "globally_single_use": False,
        },
    }


def verify_and_register_confirmation(
    confirmation: str,
    normalized_payload: Mapping[str, Any],
    *,
    expected_verb: str,
    stage: str,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        verb, token = confirmation.split(" ", 1)
        digest, encoded, signature = token.split(".", 2)
    except ValueError as exc:
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation format is invalid.") from exc
    if verb != expected_verb or len(digest) != 32:
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation prefix or hash is invalid.")
    expected_signature = _b64url_encode(
        hmac.new(_confirmation_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected_signature):
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation signature is invalid.")
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation payload is invalid.") from exc
    expected_hash = operation_hash(normalized_payload)
    expected = {"hash": expected_hash, "scope": dict(scope), "stage": stage, "verb": expected_verb}
    observed = {key: payload.get(key) for key in expected}
    if digest != expected_hash or observed != expected:
        raise SafetyError(
            "CONFIRMATION_MISMATCH",
            "Confirmation does not match the normalized operation payload.",
            {"expected": expected, "observed": observed},
        )
    now = int(time.time())
    if not isinstance(payload.get("exp"), int) or payload["exp"] < now:
        raise SafetyError("CONFIRMATION_EXPIRED", "Confirmation has expired and must be revalidated.")
    if not isinstance(payload.get("iat"), int) or payload["iat"] > now + 60:
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation issue time is invalid.")
    if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
        raise SafetyError("INVALID_CONFIRMATION", "Confirmation nonce is missing.")
    fingerprint = hashlib.sha256(confirmation.encode("utf-8")).hexdigest()[:16]
    with _REPLAY_LOCK:
        _purge_replay_cache(now)
        if fingerprint in _USED_CONFIRMATIONS:
            raise SafetyError("CONFIRMATION_REPLAYED", "Confirmation was already registered by this process.")
        _USED_CONFIRMATIONS[fingerprint] = payload["exp"]
    return {
        "confirmation_verified": True,
        "confirmation_registered_before_api_call": True,
        "confirmation_token_fingerprint": fingerprint,
        "operation_hash": expected_hash,
        "operation_hash_version": _HASH_VERSION,
    }


def require_allowlist(values: frozenset[str], value: str, variable_name: str) -> None:
    if not values:
        raise SafetyError("ALLOWLIST_NOT_CONFIGURED", f"{variable_name} is not configured.")
    if value not in values:
        raise SafetyError(
            "SCOPE_NOT_ALLOWED",
            f"Value {value} is outside {variable_name}.",
            {"environment_variable": variable_name, "value": value},
        )


def validate_scope(
    config: SafetyConfig,
    *,
    account_id: str,
    container_id: str,
    workspace_id: str | None = None,
) -> None:
    require_allowlist(config.allowed_account_ids, account_id, "GTM_ALLOWED_ACCOUNT_IDS")
    require_allowlist(config.allowed_container_ids, container_id, "GTM_ALLOWED_CONTAINER_IDS")
    if workspace_id is not None:
        require_allowlist(config.allowed_workspace_ids, workspace_id, "GTM_ALLOWED_WORKSPACE_IDS")


def validate_action_gate(config: SafetyConfig, action: str) -> None:
    if not config.mutations_enabled:
        raise SafetyError("GATE_DISABLED", "GTM_MUTATIONS_ENABLED is false.", {"gate": "GTM_MUTATIONS_ENABLED"})
    gates = {
        "create": (config.allow_create, "GTM_ALLOW_CREATE"),
        "update": (config.allow_update, "GTM_ALLOW_UPDATE"),
        "remove": (config.allow_delete, "GTM_ALLOW_DELETE"),
        "revert": (config.allow_revert, "GTM_ALLOW_REVERT"),
    }
    if action not in gates:
        raise SafetyError("UNSUPPORTED_ACTION", f"Unsupported action: {action}.")
    enabled, gate = gates[action]
    if not enabled:
        raise SafetyError("GATE_DISABLED", f"{gate} is false.", {"gate": gate})
