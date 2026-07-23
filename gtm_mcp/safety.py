"""Scope and validation primitives for direct GTM CRUD."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

CRUD_CONTRACT_VERSION = "direct-crud-v1"
OPERATION_HASH_VERSION = 4


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


def _env_ids(name: str) -> frozenset[str]:
    values = {
        item.strip() for item in os.getenv(name, "").split(",") if item.strip()
    }
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
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE", f"{name} must be an integer."
        ) from exc
    if not minimum <= value <= maximum:
        raise SafetyError(
            "INVALID_ENVIRONMENT_VALUE",
            f"{name} must be between {minimum} and {maximum}.",
            {"value": value},
        )
    return value


def load_scope_config() -> ScopeConfig:
    """Load Horizon scope for every request."""

    return ScopeConfig(
        allowed_account_ids=_env_ids("GTM_ALLOWED_ACCOUNT_IDS"),
        allowed_container_ids=_env_ids("GTM_ALLOWED_CONTAINER_IDS"),
        allowed_workspace_ids=_env_ids("GTM_ALLOWED_WORKSPACE_IDS"),
        max_operations=_env_int("GTM_MAX_OPERATIONS_PER_REQUEST", 10, 1, 10),
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
    values: frozenset[str], value: str, variable_name: str
) -> None:
    if not values:
        raise SafetyError(
            "ALLOWLIST_NOT_CONFIGURED", f"{variable_name} is not configured."
        )
    if value not in values:
        raise SafetyError(
            "SCOPE_NOT_ALLOWED",
            f"Value {value} is outside {variable_name}.",
            {"environment_variable": variable_name, "value": value},
        )


def validate_scope(
    config: ScopeConfig,
    *,
    account_id: str,
    container_id: str,
    workspace_id: str | None = None,
) -> None:
    require_allowlist(
        config.allowed_account_ids, account_id, "GTM_ALLOWED_ACCOUNT_IDS"
    )
    require_allowlist(
        config.allowed_container_ids,
        container_id,
        "GTM_ALLOWED_CONTAINER_IDS",
    )
    if workspace_id is not None:
        require_allowlist(
            config.allowed_workspace_ids,
            workspace_id,
            "GTM_ALLOWED_WORKSPACE_IDS",
        )
