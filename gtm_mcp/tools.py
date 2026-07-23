"""Read and direct CRUD tools for the GTM Horizon runtime."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from googleapiclient.errors import HttpError

from .client import execute_request, get_gtm_service, paginate
from .safety import (
    CRUD_CONTRACT_VERSION,
    OPERATION_HASH_VERSION,
    ScopeConfig,
    SafetyError,
    canonical_json,
    load_scope_config,
    operation_hash,
    validate_scope,
)

META: dict[str, tuple[str, str, frozenset[str]]] = {
    "tag": (
        "tags",
        "tagId",
        frozenset(
            {
                "name",
                "type",
                "liveOnly",
                "priority",
                "notes",
                "scheduleStartMs",
                "scheduleEndMs",
                "parameter",
                "firingTriggerId",
                "blockingTriggerId",
                "setupTag",
                "teardownTag",
                "parentFolderId",
                "tagFiringOption",
                "paused",
                "monitoringMetadata",
                "monitoringMetadataTagNameKey",
                "consentSettings",
            }
        ),
    ),
    "trigger": (
        "triggers",
        "triggerId",
        frozenset(
            {
                "name",
                "type",
                "customEventFilter",
                "filter",
                "autoEventFilter",
                "waitForTags",
                "checkValidation",
                "waitForTagsTimeout",
                "uniqueTriggerId",
                "eventName",
                "interval",
                "limit",
                "continuousFilter",
                "horizontalScrollPercentageList",
                "verticalScrollPercentageList",
                "visibilitySelector",
                "visiblePercentage",
                "onScreenDuration",
                "minimumDuration",
                "filterByCssSelector",
                "verticalScrollUnits",
                "selector",
                "notes",
                "parentFolderId",
                "parameter",
            }
        ),
    ),
    "variable": (
        "variables",
        "variableId",
        frozenset(
            {
                "name",
                "type",
                "notes",
                "scheduleStartMs",
                "scheduleEndMs",
                "parameter",
                "formatValue",
                "parentFolderId",
            }
        ),
    ),
    "folder": ("folders", "folderId", frozenset({"name", "notes"})),
}

ToolFunction = Callable[..., Awaitable[Any]]
ToolDefinition = tuple[
    ToolFunction, str, bool, bool, bool
]  # function, title, read_only, destructive, idempotent


def numeric(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.isdigit():
        raise SafetyError(
            "INVALID_ARGUMENT", f"{field} must be a numeric string."
        )
    return value


def workspace_path(
    account_id: str, container_id: str, workspace_id: str
) -> str:
    return (
        f"accounts/{account_id}/containers/{container_id}/"
        f"workspaces/{workspace_id}"
    )


def _container_path(account_id: str, container_id: str) -> str:
    return f"accounts/{account_id}/containers/{container_id}"


def collection(service: Any, resource: str) -> Any:
    if resource not in META:
        raise SafetyError(
            "UNSUPPORTED_RESOURCE", f"Unsupported GTM resource: {resource}."
        )
    return getattr(
        service.accounts().containers().workspaces(), META[resource][0]
    )()


def resource_path(parent: str, resource: str, resource_id: str) -> str:
    return f"{parent}/{META[resource][0]}/{resource_id}"


def clean(resource: str, data: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(data) - META[resource][2])
    if unknown:
        raise SafetyError(
            "UNKNOWN_OR_OUTPUT_ONLY_FIELDS",
            f"Unsupported fields for {resource}: {', '.join(unknown)}.",
            {"fields": unknown},
        )
    return dict(data)


def status_code(error: BaseException) -> int | None:
    return int(error.resp.status) if isinstance(error, HttpError) else None


def classify_error(error: BaseException) -> dict[str, Any]:
    status = status_code(error)
    retryable = status is None or status == 429 or status >= 500
    return {
        "code": (
            "GOOGLE_TAG_MANAGER_API_ERROR"
            if status and 400 <= status < 500
            else "TRANSPORT_OR_CONNECTOR_ERROR"
        ),
        "message": str(error),
        "details": {"http_status": status},
        "retryable": retryable,
        "execution_may_have_completed": retryable,
    }


async def read_workspace(service: Any, path: str) -> dict[str, Any]:
    return await execute_request(
        service.accounts().containers().workspaces().get(path=path)
    )


async def _read_resource(
    service: Any,
    resource: str,
    path: str,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    try:
        return await execute_request(
            collection(service, resource).get(path=path)
        )
    except Exception as exc:
        if allow_missing and status_code(exc) == 404:
            return None
        raise


async def ensure_unique_name(
    service: Any,
    resource: str,
    parent: str,
    data: Mapping[str, Any],
) -> None:
    name = str(data.get("name", "")).strip().casefold()
    if not name:
        return
    target = collection(service, resource)
    items = await paginate(
        lambda token: target.list(
            parent=parent, **({"pageToken": token} if token else {})
        ),
        item_key=META[resource][0],
    )
    if any(
        str(item.get("name", "")).strip().casefold() == name for item in items
    ):
        raise SafetyError(
            "DUPLICATE_RESOURCE_NAME",
            f"A {resource} named {data.get('name')} already exists.",
        )


async def normalize_operations(
    service: Any,
    config: ScopeConfig,
    account_id: str,
    container_id: str,
    workspace_id: str,
    requested: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(requested, list) or not requested:
        raise SafetyError(
            "INVALID_ARGUMENT", "operations must be a non-empty list."
        )
    if len(requested) > config.max_operations:
        raise SafetyError(
            "OPERATION_LIMIT_EXCEEDED",
            f"Maximum operation count is {config.max_operations}.",
        )

    validate_scope(
        config,
        account_id=account_id,
        container_id=container_id,
        workspace_id=workspace_id,
    )
    parent = workspace_path(account_id, container_id, workspace_id)
    workspace = await read_workspace(service, parent)
    workspace_fingerprint = workspace.get("fingerprint")
    if not isinstance(workspace_fingerprint, str) or not workspace_fingerprint:
        raise SafetyError("WORKSPACE_FINGERPRINT_MISSING", parent)

    output: list[dict[str, Any]] = []
    for raw in requested:
        if not isinstance(raw, dict):
            raise SafetyError(
                "INVALID_ARGUMENT", "Each operation must be an object."
            )
        unknown = sorted(
            set(raw) - {"resource", "action", "resource_id", "data"}
        )
        if unknown:
            raise SafetyError(
                "UNKNOWN_OPERATION_FIELDS",
                f"Unknown fields: {unknown}",
                {"fields": unknown},
            )

        resource = str(raw.get("resource") or "").strip().lower()
        action = str(raw.get("action") or "").strip().lower()
        if resource not in META:
            raise SafetyError(
                "UNSUPPORTED_RESOURCE",
                f"Unsupported GTM resource: {resource}.",
            )
        if action not in {"create", "update", "delete", "revert"}:
            raise SafetyError(
                "UNSUPPORTED_ACTION",
                f"Unsupported operation: {resource}/{action}.",
            )

        resource_id = raw.get("resource_id")
        data = raw.get("data")
        path: str | None = None
        current: dict[str, Any] | None = None
        normalized_data: dict[str, Any] | None = None
        no_op_reason: str | None = None

        if action == "create":
            if resource_id is not None or not isinstance(data, dict):
                raise SafetyError(
                    "INVALID_ARGUMENT",
                    "create requires data and omits resource_id.",
                )
            normalized_data = clean(resource, data)
            if (
                not isinstance(normalized_data.get("name"), str)
                or not str(normalized_data["name"]).strip()
            ):
                raise SafetyError(
                    "CREATE_REQUIRED_FIELD_MISSING",
                    f"name is required for {resource}.",
                )
            if resource != "folder" and (
                not isinstance(normalized_data.get("type"), str)
                or not str(normalized_data["type"]).strip()
            ):
                raise SafetyError(
                    "CREATE_REQUIRED_FIELD_MISSING",
                    f"type is required for {resource}.",
                )
            await ensure_unique_name(service, resource, parent, normalized_data)
        else:
            resource_id = numeric(resource_id, "resource_id")
            path = resource_path(parent, resource, resource_id)
            current = await _read_resource(
                service, resource, path, allow_missing=action == "delete"
            )
            if current is None:
                no_op_reason = "ALREADY_ABSENT"
            elif action == "update":
                if not isinstance(data, dict):
                    raise SafetyError(
                        "INVALID_ARGUMENT", "update requires data."
                    )
                requested_data = clean(resource, data)
                current_data = {
                    key: value
                    for key, value in current.items()
                    if key in META[resource][2]
                }
                normalized_data = clean(
                    resource, {**current_data, **requested_data}
                )
            elif data is not None:
                raise SafetyError(
                    "INVALID_ARGUMENT",
                    f"{action} must omit data.",
                )

        resource_fingerprint = current.get("fingerprint") if current else None
        if action in {"update", "revert"} and not isinstance(
            resource_fingerprint, str
        ):
            raise SafetyError("RESOURCE_FINGERPRINT_MISSING", str(resource_id))

        output.append(
            {
                "resource": resource,
                "action": action,
                "account_id": account_id,
                "container_id": container_id,
                "workspace_id": workspace_id,
                "resource_id": resource_id,
                "parent": parent,
                "path": path,
                "data": normalized_data,
                "workspace_fingerprint": workspace_fingerprint,
                "resource_fingerprint": resource_fingerprint,
                "current_resource_name": (
                    current.get("name") if current else None
                ),
                "no_op_reason": no_op_reason,
            }
        )
    return output


async def execute_one(service: Any, operation: Mapping[str, Any]) -> str:
    target = collection(service, str(operation["resource"]))
    action = operation["action"]
    if action == "create":
        response = await execute_request(
            target.create(parent=operation["parent"], body=operation["data"])
        )
        if isinstance(response.get("path"), str):
            return response["path"]
        resource_id = response.get(META[str(operation["resource"])][1])
        if isinstance(resource_id, str):
            return resource_path(
                str(operation["parent"]),
                str(operation["resource"]),
                resource_id,
            )
        raise SafetyError(
            "MUTATION_RESPONSE_INVALID",
            "Create response omitted resource path.",
        )

    path = str(operation["path"])
    if action == "update":
        await execute_request(
            target.update(
                path=path,
                fingerprint=operation["resource_fingerprint"],
                body=operation["data"],
            )
        )
    elif action == "delete":
        await execute_request(target.delete(path=path))
    else:
        await execute_request(
            target.revert(
                path=path,
                fingerprint=operation["resource_fingerprint"],
            )
        )
    return path


async def verify_one(
    service: Any, operation: Mapping[str, Any], path: str
) -> dict[str, Any]:
    observed = await _read_resource(
        service,
        str(operation["resource"]),
        path,
        allow_missing=operation["action"] == "delete",
    )
    if operation["action"] == "delete":
        return {
            "resource_name": path,
            "expected": "NOT_FOUND",
            "observed": "NOT_FOUND" if observed is None else "STILL_READABLE",
            "verified": observed is None,
        }
    if observed is None:
        return {
            "resource_name": path,
            "expected": "READABLE",
            "observed": "NOT_FOUND",
            "verified": False,
        }
    expected = operation.get("data") or {}
    compared = (
        sorted(expected) if operation["action"] in {"create", "update"} else []
    )
    mismatched = [
        key
        for key in compared
        if canonical_json(observed.get(key))
        != canonical_json(expected.get(key))
    ]
    return {
        "resource_name": path,
        "expected": (
            "READABLE_WITH_MATCHING_FIELDS" if compared else "READABLE"
        ),
        "observed": "READABLE",
        "verified": not mismatched,
        "fingerprint": observed.get("fingerprint"),
        "compared_fields": compared,
        "mismatched_fields": mismatched,
    }


def _public_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: operation.get(key)
        for key in (
            "resource",
            "action",
            "account_id",
            "container_id",
            "workspace_id",
            "resource_id",
            "parent",
            "path",
            "data",
            "workspace_fingerprint",
            "resource_fingerprint",
            "no_op_reason",
        )
    }


async def gtm_batch_operations(
    account_id: str,
    container_id: str,
    workspace_id: str,
    operations: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run direct GTM workspace CRUD, sequentially and in one scope."""

    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    workspace_id = numeric(workspace_id, "workspace_id")
    config, service = load_scope_config(), get_gtm_service()
    normalized = await normalize_operations(
        service,
        config,
        account_id,
        container_id,
        workspace_id,
        operations,
    )
    payload = {
        "operation_hash_version": OPERATION_HASH_VERSION,
        "scope": {
            "account_id": account_id,
            "container_id": container_id,
            "workspace_id": workspace_id,
        },
        "operations": [_public_operation(item) for item in normalized],
    }
    digest = operation_hash(payload)
    if dry_run:
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "runtime": "PYTHON_FASTMCP_HORIZON",
            "mode": "DRY_RUN",
            "execution_status": "NOT_EXECUTED",
            "execution_attempted": False,
            "atomic": False,
            "execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR",
            "operation_count": len(normalized),
            "operations": [_public_operation(item) for item in normalized],
            "operation_hash": digest,
            "operation_hash_version": OPERATION_HASH_VERSION,
            "results": [],
            "verification": {
                "scope_verified": True,
                "workspace_snapshot_captured": True,
                "resource_snapshots_captured": True,
                "google_api_mutation_sent": False,
            },
        }

    results: list[dict[str, Any]] = []
    mutation_attempts = 0
    failure: dict[str, Any] | None = None
    for index, operation in enumerate(normalized):
        if operation["no_op_reason"]:
            results.append(
                {
                    "operation_index": index,
                    "resource": operation["resource"],
                    "action": operation["action"],
                    "resource_name": operation["path"],
                    "execution_status": "SUCCEEDED",
                    "outcome": operation["no_op_reason"],
                    "verification": {"verified": True},
                }
            )
            continue
        try:
            mutation_attempts += 1
            path = await execute_one(service, operation)
            verification = await verify_one(service, operation, path)
            status = (
                "SUCCEEDED"
                if verification.get("verified") is True
                else "FAILED"
            )
            results.append(
                {
                    "operation_index": index,
                    "resource": operation["resource"],
                    "action": operation["action"],
                    "resource_name": path,
                    "execution_status": status,
                    "outcome": "MUTATED",
                    "verification": verification,
                }
            )
            if status != "SUCCEEDED":
                failure = {
                    "code": "POST_EXECUTION_VERIFICATION_FAILED",
                    "message": "GTM mutation could not be verified.",
                    "details": {"operation_index": index},
                    "retryable": False,
                    "execution_may_have_completed": True,
                }
                break
        except Exception as exc:
            failure = {
                **classify_error(exc),
                "details": {
                    **classify_error(exc)["details"],
                    "operation_index": index,
                },
            }
            results.append(
                {
                    "operation_index": index,
                    "resource": operation["resource"],
                    "action": operation["action"],
                    "resource_name": operation["path"],
                    "execution_status": (
                        "UNKNOWN"
                        if failure["execution_may_have_completed"]
                        else "FAILED"
                    ),
                    "error": failure,
                }
            )
            break

    completed = sum(item["execution_status"] == "SUCCEEDED" for item in results)
    success = failure is None and completed == len(normalized)
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "runtime": "PYTHON_FASTMCP_HORIZON",
        "mode": "EXECUTE",
        "execution_status": "SUCCEEDED" if success else "FAILED",
        "execution_attempted": mutation_attempts > 0,
        "atomic": False,
        "execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR",
        "operation_count": len(normalized),
        "operations_attempted": len(results),
        "operations_completed": completed,
        "operations_not_attempted": len(normalized) - len(results),
        "operation_hash": digest,
        "operation_hash_version": OPERATION_HASH_VERSION,
        "results": results,
        "error": failure,
        "verification": {
            "all_requested_resources_verified": success,
            "post_mutation_reads_performed": mutation_attempts > 0,
        },
    }


async def gtm_create_resource(
    account_id: str,
    container_id: str,
    workspace_id: str,
    resource: str,
    data: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create one GTM workspace resource directly."""

    return await gtm_batch_operations(
        account_id,
        container_id,
        workspace_id,
        [{"resource": resource, "action": "create", "data": data}],
        dry_run,
    )


async def gtm_update_resource(
    account_id: str,
    container_id: str,
    workspace_id: str,
    resource: str,
    resource_id: str,
    data: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update one GTM workspace resource directly."""

    return await gtm_batch_operations(
        account_id,
        container_id,
        workspace_id,
        [
            {
                "resource": resource,
                "action": "update",
                "resource_id": resource_id,
                "data": data,
            }
        ],
        dry_run,
    )


async def gtm_delete_resource(
    account_id: str,
    container_id: str,
    workspace_id: str,
    resource: str,
    resource_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete one GTM workspace resource directly and idempotently."""

    return await gtm_batch_operations(
        account_id,
        container_id,
        workspace_id,
        [
            {
                "resource": resource,
                "action": "delete",
                "resource_id": resource_id,
            }
        ],
        dry_run,
    )


async def gtm_revert_resource(
    account_id: str,
    container_id: str,
    workspace_id: str,
    resource: str,
    resource_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Revert one GTM workspace resource directly."""

    return await gtm_batch_operations(
        account_id,
        container_id,
        workspace_id,
        [
            {
                "resource": resource,
                "action": "revert",
                "resource_id": resource_id,
            }
        ],
        dry_run,
    )


async def gtm_crud_status() -> dict[str, Any]:
    """Return the direct CRUD contract and non-secret GTM scope."""

    config = load_scope_config()
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "runtime": "PYTHON_FASTMCP_HORIZON",
        "write_mode": "DIRECT",
        "deployment_env_keys": ["MCP_CREDENTIALS", "MCP_CONFIG"],
        "dry_run_supported": True,
        "approval_workflow": False,
        "allowlists": {
            "account_ids": sorted(config.allowed_account_ids),
            "container_ids": sorted(config.allowed_container_ids),
            "workspace_ids": sorted(config.allowed_workspace_ids),
        },
        "max_operations_per_request": config.max_operations,
        "atomic": False,
        "execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR",
    }


async def gtm_list_mutable_resources() -> dict[str, Any]:
    """List GTM resources and direct CRUD actions."""

    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "resources": [
            {
                "resource": resource,
                "actions": [
                    "create",
                    "get",
                    "list",
                    "update",
                    "delete",
                    "revert",
                ],
                "fields": sorted(fields),
            }
            for resource, (_, _, fields) in META.items()
        ],
        "special_actions": ["create_version", "publish_version"],
    }


async def gtm_get_mutation_schema(resource: str) -> dict[str, Any]:
    """Return the direct CRUD schema for one GTM resource."""

    resource = resource.strip().lower()
    if resource not in META:
        raise SafetyError(
            "UNSUPPORTED_RESOURCE", f"Unsupported GTM resource: {resource}."
        )
    fields = META[resource][2]
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "resource": resource,
        "actions": [
            "create",
            "get",
            "list",
            "update",
            "delete",
            "revert",
        ],
        "fields": sorted(fields),
        "create_required": (
            ["name"] if resource == "folder" else ["name", "type"]
        ),
        "update_behavior": "MERGE_WITH_CURRENT_RESOURCE",
    }


async def gtm_list_accounts() -> dict[str, Any]:
    """List GTM accounts accessible to the ADC identity."""

    service = get_gtm_service()
    items = await paginate(
        lambda token: service.accounts().list(
            **({"pageToken": token} if token else {})
        ),
        item_key="account",
    )
    return {"accounts": items, "count": len(items)}


async def gtm_list_containers(account_id: str) -> dict[str, Any]:
    """List containers under one GTM account."""

    account_id = numeric(account_id, "account_id")
    parent = f"accounts/{account_id}"
    service = get_gtm_service()
    items = await paginate(
        lambda token: service.accounts()
        .containers()
        .list(parent=parent, **({"pageToken": token} if token else {})),
        item_key="container",
    )
    return {"parent": parent, "containers": items, "count": len(items)}


async def gtm_list_workspaces(
    account_id: str, container_id: str
) -> dict[str, Any]:
    """List workspaces under one GTM container."""

    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    parent = _container_path(account_id, container_id)
    service = get_gtm_service()
    items = await paginate(
        lambda token: service.accounts()
        .containers()
        .workspaces()
        .list(parent=parent, **({"pageToken": token} if token else {})),
        item_key="workspace",
    )
    return {"parent": parent, "workspaces": items, "count": len(items)}


async def gtm_get_workspace(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """Get one GTM workspace."""

    return await read_workspace(
        get_gtm_service(),
        workspace_path(
            numeric(account_id, "account_id"),
            numeric(container_id, "container_id"),
            numeric(workspace_id, "workspace_id"),
        ),
    )


async def gtm_get_workspace_status(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """Read changes and conflicts for one GTM workspace."""

    path = workspace_path(
        numeric(account_id, "account_id"),
        numeric(container_id, "container_id"),
        numeric(workspace_id, "workspace_id"),
    )
    return await execute_request(
        get_gtm_service()
        .accounts()
        .containers()
        .workspaces()
        .getStatus(path=path)
    )


async def gtm_list_resources(
    resource: str,
    account_id: str,
    container_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """List one supported resource type in an allowlisted workspace."""

    resource = resource.strip().lower()
    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    workspace_id = numeric(workspace_id, "workspace_id")
    config = load_scope_config()
    validate_scope(
        config,
        account_id=account_id,
        container_id=container_id,
        workspace_id=workspace_id,
    )
    parent = workspace_path(account_id, container_id, workspace_id)
    target = collection(get_gtm_service(), resource)
    items = await paginate(
        lambda token: target.list(
            parent=parent, **({"pageToken": token} if token else {})
        ),
        item_key=META[resource][0],
    )
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "parent": parent,
        "resource": resource,
        "values": items,
        "count": len(items),
    }


async def gtm_get_resource(
    resource: str,
    account_id: str,
    container_id: str,
    workspace_id: str,
    resource_id: str,
) -> dict[str, Any]:
    """Get one supported resource in an allowlisted GTM workspace."""

    resource = resource.strip().lower()
    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    workspace_id = numeric(workspace_id, "workspace_id")
    resource_id = numeric(resource_id, "resource_id")
    config = load_scope_config()
    validate_scope(
        config,
        account_id=account_id,
        container_id=container_id,
        workspace_id=workspace_id,
    )
    parent = workspace_path(account_id, container_id, workspace_id)
    value = await _read_resource(
        get_gtm_service(),
        resource,
        resource_path(parent, resource, resource_id),
    )
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "resource": resource,
        "value": value,
    }


async def gtm_list_tags(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """List tags in one GTM workspace."""

    return await gtm_list_resources(
        "tag", account_id, container_id, workspace_id
    )


async def gtm_list_triggers(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """List triggers in one GTM workspace."""

    return await gtm_list_resources(
        "trigger", account_id, container_id, workspace_id
    )


async def gtm_list_variables(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """List variables in one GTM workspace."""

    return await gtm_list_resources(
        "variable", account_id, container_id, workspace_id
    )


async def gtm_list_folders(
    account_id: str, container_id: str, workspace_id: str
) -> dict[str, Any]:
    """List folders in one GTM workspace."""

    return await gtm_list_resources(
        "folder", account_id, container_id, workspace_id
    )


async def gtm_get_live_version(
    account_id: str, container_id: str
) -> dict[str, Any]:
    """Get the live GTM container version."""

    parent = _container_path(
        numeric(account_id, "account_id"),
        numeric(container_id, "container_id"),
    )
    return await execute_request(
        get_gtm_service().accounts().containers().versions().live(parent=parent)
    )


async def gtm_get_latest_version_header(
    account_id: str, container_id: str
) -> dict[str, Any]:
    """Get the latest GTM container version header."""

    parent = _container_path(
        numeric(account_id, "account_id"),
        numeric(container_id, "container_id"),
    )
    return await execute_request(
        get_gtm_service()
        .accounts()
        .containers()
        .version_headers()
        .latest(parent=parent)
    )


async def gtm_get_version(
    account_id: str, container_id: str, container_version_id: str
) -> dict[str, Any]:
    """Get one GTM container version."""

    path = (
        f"{_container_path(numeric(account_id, 'account_id'), numeric(container_id, 'container_id'))}"
        f"/versions/{numeric(container_version_id, 'container_version_id')}"
    )
    return await execute_request(
        get_gtm_service().accounts().containers().versions().get(path=path)
    )


async def _version_plan(
    service: Any,
    stage: str,
    account_id: str,
    container_id: str,
    workspace_id: str,
    name: str | None,
    notes: str | None,
) -> dict[str, Any]:
    path = workspace_path(account_id, container_id, workspace_id)
    workspace = await read_workspace(service, path)
    status = await execute_request(
        service.accounts().containers().workspaces().getStatus(path=path)
    )
    fingerprint = workspace.get("fingerprint")
    conflicts = status.get("mergeConflict", [])
    changes = status.get("workspaceChange", [])
    if not isinstance(fingerprint, str) or not fingerprint:
        raise SafetyError("WORKSPACE_FINGERPRINT_MISSING", path)
    if isinstance(conflicts, list) and conflicts:
        raise SafetyError(
            "WORKSPACE_HAS_UNRESOLVED_CONFLICTS",
            f"Workspace has {len(conflicts)} conflicts.",
        )
    if not isinstance(changes, list) or not changes:
        raise SafetyError(
            "WORKSPACE_HAS_NO_CHANGES", "Workspace has no changes."
        )
    return {
        "operation_hash_version": OPERATION_HASH_VERSION,
        "stage": stage,
        "action": "create_version",
        "account_id": account_id,
        "container_id": container_id,
        "workspace_id": workspace_id,
        "workspace_path": path,
        "workspace_fingerprint": fingerprint,
        "workspace_name": workspace.get("name"),
        "workspace_change_count": len(changes),
        "merge_conflict_count": (
            len(conflicts) if isinstance(conflicts, list) else 0
        ),
        "deletes_workspace": True,
        "request_body": {
            **({"name": name} if name else {}),
            **({"notes": notes} if notes else {}),
        },
    }


async def gtm_create_version(
    stage: str,
    account_id: str,
    container_id: str,
    workspace_id: str,
    name: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a GTM version directly; success deletes its workspace."""

    if not isinstance(stage, str) or not stage.strip() or len(stage) > 100:
        raise SafetyError(
            "INVALID_ARGUMENT", "stage must contain 1-100 characters."
        )
    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    workspace_id = numeric(workspace_id, "workspace_id")
    config, service = load_scope_config(), get_gtm_service()
    validate_scope(
        config,
        account_id=account_id,
        container_id=container_id,
        workspace_id=workspace_id,
    )
    plan = await _version_plan(
        service,
        stage,
        account_id,
        container_id,
        workspace_id,
        name,
        notes,
    )
    digest = operation_hash(plan)
    if dry_run:
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "mode": "DRY_RUN",
            "execution_status": "NOT_EXECUTED",
            "execution_attempted": False,
            "action": "create_version",
            "plan": plan,
            "operation_hash": digest,
            "results": [],
            "verification": {"workspace_snapshot_captured": True},
        }
    try:
        response = await execute_request(
            service.accounts()
            .containers()
            .workspaces()
            .create_version(
                path=plan["workspace_path"], body=plan["request_body"]
            )
        )
    except Exception as exc:
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "mode": "EXECUTE",
            "execution_status": (
                "UNKNOWN"
                if classify_error(exc)["execution_may_have_completed"]
                else "FAILED"
            ),
            "execution_attempted": True,
            "action": "create_version",
            "operation_hash": digest,
            "results": [],
            "error": classify_error(exc),
        }
    version = response.get("containerVersion", {})
    version_id = version.get("containerVersionId")
    version_readable = False
    if isinstance(version_id, str):
        try:
            await execute_request(
                service.accounts()
                .containers()
                .versions()
                .get(
                    path=(
                        f"{_container_path(account_id, container_id)}"
                        f"/versions/{version_id}"
                    )
                )
            )
            version_readable = True
        except Exception:
            pass
    workspace_deleted = False
    try:
        await read_workspace(service, plan["workspace_path"])
    except Exception as exc:
        workspace_deleted = status_code(exc) == 404
    verified = (
        version_readable
        and workspace_deleted
        and not bool(response.get("compilerError"))
    )
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "mode": "EXECUTE",
        "execution_status": (
            "SUCCEEDED" if verified else "SUCCEEDED_WITH_VERIFICATION_WARNINGS"
        ),
        "execution_attempted": True,
        "action": "create_version",
        "operation_hash": digest,
        "results": [
            {
                "container_version_id": version_id,
                "response": response,
            }
        ],
        "verification": {
            "version_readable": version_readable,
            "workspace_deleted": workspace_deleted,
            "compiler_error": bool(response.get("compilerError")),
            "verified": verified,
        },
    }


async def _publish_plan(
    service: Any,
    stage: str,
    account_id: str,
    container_id: str,
    version_id: str,
) -> dict[str, Any]:
    parent = _container_path(account_id, container_id)
    target_path = f"{parent}/versions/{version_id}"
    target = await execute_request(
        service.accounts().containers().versions().get(path=target_path)
    )
    live = await execute_request(
        service.accounts().containers().versions().live(parent=parent)
    )
    latest = await execute_request(
        service.accounts().containers().version_headers().latest(parent=parent)
    )
    if target.get("containerVersionId") != version_id or not isinstance(
        target.get("fingerprint"), str
    ):
        raise SafetyError("TARGET_VERSION_INVALID", target_path)
    latest_id = latest.get("containerVersionId")
    if not isinstance(latest_id, str):
        raise SafetyError("LATEST_VERSION_ID_MISSING", parent)
    return {
        "operation_hash_version": OPERATION_HASH_VERSION,
        "stage": stage,
        "action": "publish_version",
        "account_id": account_id,
        "container_id": container_id,
        "container_version_id": version_id,
        "target_path": target_path,
        "target_fingerprint": target["fingerprint"],
        "target_name": target.get("name"),
        "live_version_id": live.get("containerVersionId"),
        "latest_version_id": latest_id,
        "target_is_latest": latest_id == version_id,
        "no_op_reason": (
            "ALREADY_LIVE"
            if live.get("containerVersionId") == version_id
            else None
        ),
    }


async def gtm_publish_version(
    stage: str,
    account_id: str,
    container_id: str,
    container_version_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Publish any allowlisted container version directly and idempotently."""

    if not isinstance(stage, str) or not stage.strip() or len(stage) > 100:
        raise SafetyError(
            "INVALID_ARGUMENT", "stage must contain 1-100 characters."
        )
    account_id = numeric(account_id, "account_id")
    container_id = numeric(container_id, "container_id")
    container_version_id = numeric(container_version_id, "container_version_id")
    config, service = load_scope_config(), get_gtm_service()
    validate_scope(config, account_id=account_id, container_id=container_id)
    plan = await _publish_plan(
        service, stage, account_id, container_id, container_version_id
    )
    digest = operation_hash(plan)
    if dry_run:
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "mode": "DRY_RUN",
            "execution_status": "NOT_EXECUTED",
            "execution_attempted": False,
            "action": "publish_version",
            "plan": plan,
            "operation_hash": digest,
            "results": [],
            "verification": {"version_snapshots_captured": True},
        }
    if plan["no_op_reason"]:
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "mode": "EXECUTE",
            "execution_status": "SUCCEEDED",
            "execution_attempted": False,
            "action": "publish_version",
            "operation_hash": digest,
            "results": [{"outcome": plan["no_op_reason"]}],
            "verification": {"verified": True},
        }
    try:
        response = await execute_request(
            service.accounts()
            .containers()
            .versions()
            .publish(
                path=plan["target_path"],
                fingerprint=plan["target_fingerprint"],
            )
        )
    except Exception as exc:
        error = classify_error(exc)
        return {
            "contract_version": CRUD_CONTRACT_VERSION,
            "mode": "EXECUTE",
            "execution_status": (
                "UNKNOWN" if error["execution_may_have_completed"] else "FAILED"
            ),
            "execution_attempted": True,
            "action": "publish_version",
            "operation_hash": digest,
            "results": [],
            "error": error,
        }
    live = await execute_request(
        service.accounts()
        .containers()
        .versions()
        .live(parent=_container_path(account_id, container_id))
    )
    verified = live.get(
        "containerVersionId"
    ) == container_version_id and not bool(response.get("compilerError"))
    return {
        "contract_version": CRUD_CONTRACT_VERSION,
        "mode": "EXECUTE",
        "execution_status": (
            "SUCCEEDED" if verified else "SUCCEEDED_WITH_VERIFICATION_WARNINGS"
        ),
        "execution_attempted": True,
        "action": "publish_version",
        "operation_hash": digest,
        "results": [{"response": response}],
        "verification": {
            "expected_live_version_id": container_version_id,
            "live_version_id": live.get("containerVersionId"),
            "compiler_error": bool(response.get("compilerError")),
            "verified": verified,
        },
    }


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    (gtm_crud_status, "Get GTM Direct CRUD Status", True, False, True),
    (
        gtm_list_mutable_resources,
        "List GTM Mutable Resources",
        True,
        False,
        True,
    ),
    (
        gtm_get_mutation_schema,
        "Get GTM Mutation Schema",
        True,
        False,
        True,
    ),
    (gtm_list_accounts, "List GTM Accounts", True, False, True),
    (gtm_list_containers, "List GTM Containers", True, False, True),
    (gtm_list_workspaces, "List GTM Workspaces", True, False, True),
    (gtm_get_workspace, "Get GTM Workspace", True, False, True),
    (
        gtm_get_workspace_status,
        "Get GTM Workspace Status",
        True,
        False,
        True,
    ),
    (gtm_get_resource, "Get GTM Resource", True, False, True),
    (gtm_list_resources, "List GTM Resources", True, False, True),
    (gtm_list_tags, "List GTM Tags", True, False, True),
    (gtm_list_triggers, "List GTM Triggers", True, False, True),
    (gtm_list_variables, "List GTM Variables", True, False, True),
    (gtm_list_folders, "List GTM Folders", True, False, True),
    (gtm_get_live_version, "Get GTM Live Version", True, False, True),
    (
        gtm_get_latest_version_header,
        "Get GTM Latest Version Header",
        True,
        False,
        True,
    ),
    (gtm_get_version, "Get GTM Version", True, False, True),
    (gtm_create_resource, "Create GTM Resource", False, False, False),
    (gtm_update_resource, "Update GTM Resource", False, True, True),
    (gtm_delete_resource, "Delete GTM Resource", False, True, True),
    (gtm_revert_resource, "Revert GTM Resource", False, True, True),
    (gtm_batch_operations, "Run GTM CRUD Batch", False, True, False),
    (gtm_create_version, "Create GTM Version", False, True, False),
    (gtm_publish_version, "Publish GTM Version", False, True, True),
)
