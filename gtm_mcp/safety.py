"""Scope and validation primitives for direct GTM CRUD."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

CRUD_CONTRACT_VERSION = "direct-crud-v1"
OPERATION_HASH_VERSION = 4
DEPLOY_CONFIG_ENV = "MCP_CONFIG"


class SafetyError(ValueError):
    """Structured validation or execution error."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        retryable: bool = False,
        execution_may_have_completed: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = retryable
        self.execution_may_have_completed = execution_may_have_completed

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "execution_may_have_completed": self.execution_may_have_completed,
        }


@dataclass(frozen=True)
class ScopeConfig:
    allowed_account_ids: frozenset[str]
    allowed_container_ids: frozenset[str]
    allowed_workspace_ids: frozenset[str]
    max_operations: int


def _deployment_config() -> Mapping[str, Any]:
    raw = os.getenv(DEPLOY_CONFIG_ENV, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV} must be a valid JSON object.",
        ) from exc
    if not isinstance(value, dict):
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV} must be a JSON object.",
        )
    supported = {"accounts", "containers", "workspaces", "max_operations"}
    unknown = sorted(set(value) - supported)
    if unknown:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV} contains unsupported keys.",
            {"unsupported_keys": unknown, "supported_keys": sorted(supported)},
        )
    return value


def _config_ids(config: Mapping[str, Any], key: str) -> frozenset[str]:
    raw = config.get(key, [])
    if not isinstance(raw, list):
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV}.{key} must be an array of numeric IDs.",
        )
    values = {str(item).strip() for item in raw}
    invalid = sorted(item for item in values if not item.isdigit())
    if invalid or any(isinstance(item, bool) for item in raw):
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV}.{key} must be an array of numeric IDs.",
            {"invalid_values": invalid},
        )
    return frozenset(values)


def _config_int(
    config: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV}.{key} must be an integer.",
        )
    if not minimum <= value <= maximum:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{DEPLOY_CONFIG_ENV}.{key} must be between {minimum} and {maximum}.",
            {"value": value},
        )
    return value


def load_scope_config() -> ScopeConfig:
    """Load Horizon scope for every request."""

    config = _deployment_config()
    return ScopeConfig(
        allowed_account_ids=_config_ids(config, "accounts"),
        allowed_container_ids=_config_ids(config, "containers"),
        allowed_workspace_ids=_config_ids(config, "workspaces"),
        max_operations=_config_int(config, "max_operations", 10, 1, 10),
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def operation_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[
        :32
    ]


def require_allowlist(
    values: frozenset[str], value: str, config_key: str
) -> None:
    if not values:
        raise SafetyError(
            "ALLOWLIST_NOT_CONFIGURED",
            f"{DEPLOY_CONFIG_ENV}.{config_key} is not configured.",
        )
    if value not in values:
        raise SafetyError(
            "SCOPE_NOT_ALLOWED",
            f"Value {value} is outside {DEPLOY_CONFIG_ENV}.{config_key}.",
            {"config_key": config_key, "value": value},
        )


def validate_scope(
    config: ScopeConfig,
    *,
    account_id: str,
    container_id: str,
    workspace_id: str | None = None,
) -> None:
    require_allowlist(config.allowed_account_ids, account_id, "accounts")
    require_allowlist(
        config.allowed_container_ids,
        container_id,
        "containers",
    )
    if workspace_id is not None:
        require_allowlist(
            config.allowed_workspace_ids,
            workspace_id,
            "workspaces",
        )
