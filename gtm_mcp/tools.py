"""Read and protected GTM API tools for the Horizon runtime."""
from __future__ import annotations

import os
from typing import Any, Mapping
from googleapiclient.errors import HttpError
from .client import execute_request, get_gtm_service, paginate
from .safety import SafetyConfig, SafetyError, canonical_json, issue_confirmation, load_safety_config, validate_action_gate, validate_scope, verify_and_register_confirmation

META: dict[str, tuple[str, str, frozenset[str]]] = {
    "tag": ("tags", "tagId", frozenset({"name","type","liveOnly","priority","notes","scheduleStartMs","scheduleEndMs","parameter","firingTriggerId","blockingTriggerId","setupTag","teardownTag","parentFolderId","tagFiringOption","paused","monitoringMetadata","monitoringMetadataTagNameKey","consentSettings"})),
    "trigger": ("triggers", "triggerId", frozenset({"name","type","customEventFilter","filter","autoEventFilter","waitForTags","checkValidation","waitForTagsTimeout","uniqueTriggerId","eventName","interval","limit","continuousFilter","horizontalScrollPercentageList","verticalScrollPercentageList","visibilitySelector","visiblePercentage","onScreenDuration","minimumDuration","filterByCssSelector","verticalScrollUnits","selector","notes","parentFolderId","parameter"})),
    "variable": ("variables", "variableId", frozenset({"name","type","notes","scheduleStartMs","scheduleEndMs","parameter","formatValue","parentFolderId"})),
    "folder": ("folders", "folderId", frozenset({"name","notes"})),
}
OP_KEYS = frozenset({"resource","action","accountId","containerId","workspaceId","resourceId","data"})


def numeric(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.isdigit():
        raise SafetyError("INVALID_ARGUMENT", f"{field} must be a numeric string.")
    return value


def workspace_path(a: str, c: str, w: str) -> str:
    return f"accounts/{a}/containers/{c}/workspaces/{w}"


def collection(service: Any, resource: str) -> Any:
    return getattr(service.accounts().containers().workspaces(), META[resource][0])()


def resource_path(op: Mapping[str, Any]) -> str:
    return f"{op['parent']}/{META[str(op['resource'])][0]}/{op['resourceId']}"


def clean(resource: str, data: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(data) - META[resource][2])
    if unknown:
        raise SafetyError("UNKNOWN_OR_OUTPUT_ONLY_FIELDS", f"Unsupported fields for {resource}: {', '.join(unknown)}.", {"fields": unknown})
    return dict(data)


def scope(stage: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"stage": stage,"accountIds": sorted({x["accountId"] for x in operations}),"containerIds": sorted({x["containerId"] for x in operations}),"workspaceIds": sorted({x["workspaceId"] for x in operations}),"operationCount": len(operations)}


def mutation_payload(stage: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"operation_hash_version": 3,"stage": stage,"atomic": False,"execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR","operations": operations}


def status_code(error: Exception) -> int | None:
    return int(error.resp.status) if isinstance(error, HttpError) else None


def classify(error: Exception) -> tuple[str, bool]:
    status = status_code(error)
    return ("GOOGLE_TAG_MANAGER_API_ERROR", False) if status and 400 <= status < 500 else ("TRANSPORT_OR_CONNECTOR_ERROR", True)


async def read_workspace(service: Any, path: str) -> dict[str, Any]:
    return await execute_request(service.accounts().containers().workspaces().get(path=path))


async def ensure_unique_name(service: Any, op: Mapping[str, Any], data: Mapping[str, Any]) -> None:
    name = str(data.get("name", "")).strip().casefold()
    if not name:
        return
    target = collection(service, str(op["resource"]))
    items = await paginate(lambda token: target.list(parent=str(op["parent"]), **({"pageToken": token} if token else {})), item_key=str(op["resource"]))
    if any(str(item.get("name", "")).strip().casefold() == name for item in items):
        raise SafetyError("DUPLICATE_RESOURCE_NAME", f"A {op['resource']} named {data.get('name')} already exists.")


async def normalize(service: Any, config: SafetyConfig, requested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(requested, list) or not requested:
        raise SafetyError("INVALID_ARGUMENT", "operations must be a non-empty list.")
    if len(requested) > config.max_operations:
        raise SafetyError("OPERATION_LIMIT_EXCEEDED", f"Maximum operation count is {config.max_operations}.")
    workspaces: dict[str, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for raw in requested:
        if not isinstance(raw, dict):
            raise SafetyError("INVALID_ARGUMENT", "Each operation must be an object.")
        if set(raw) - OP_KEYS:
            raise SafetyError("UNKNOWN_OPERATION_FIELDS", f"Unknown fields: {sorted(set(raw) - OP_KEYS)}")
        resource, action = raw.get("resource"), raw.get("action")
        if resource not in META or action not in {"create","update","remove","revert"}:
            raise SafetyError("UNSUPPORTED_OPERATION", f"Unsupported operation: {resource}/{action}.")
        a, c, w = numeric(raw.get("accountId"), "accountId"), numeric(raw.get("containerId"), "containerId"), numeric(raw.get("workspaceId"), "workspaceId")
        validate_scope(config, account_id=a, container_id=c, workspace_id=w)
        validate_action_gate(config, str(action))
        rid, data = raw.get("resourceId"), raw.get("data")
        if action == "create":
            if rid is not None or not isinstance(data, dict):
                raise SafetyError("INVALID_ARGUMENT", "create requires data and omits resourceId.")
        else:
            rid = numeric(rid, "resourceId")
            if action == "update" and not isinstance(data, dict):
                raise SafetyError("INVALID_ARGUMENT", "update requires data.")
            if action in {"remove","revert"} and data is not None:
                raise SafetyError("INVALID_ARGUMENT", f"{action} must omit data.")
        parent = workspace_path(a, c, w)
        ws = workspaces.get(parent) or await read_workspace(service, parent)
        workspaces[parent] = ws
        wfp = ws.get("fingerprint")
        if not isinstance(wfp, str) or not wfp:
            raise SafetyError("WORKSPACE_FINGERPRINT_MISSING", parent)
        base = {"resource": resource,"action": action,"accountId": a,"containerId": c,"workspaceId": w,"resourceId": rid,"parent": parent,"workspaceFingerprint": wfp}
        current: dict[str, Any] | None = None
        normalized_data: dict[str, Any] | None = None
        if action == "create":
            normalized_data = clean(str(resource), data)
            if not isinstance(normalized_data.get("name"), str) or not str(normalized_data["name"]).strip() or (resource != "folder" and (not isinstance(normalized_data.get("type"), str) or not str(normalized_data["type"]).strip())):
                raise SafetyError("CREATE_REQUIRED_FIELD_MISSING", f"Required fields missing for {resource}.")
            await ensure_unique_name(service, base, normalized_data)
        else:
            current = await execute_request(collection(service, str(resource)).get(path=resource_path(base)))
            if action == "update":
                requested_data = clean(str(resource), data)
                current_data = {k: v for k, v in current.items() if k in META[str(resource)][2]}
                normalized_data = clean(str(resource), {**current_data, **requested_data})
        rfp = current.get("fingerprint") if current else None
        if action in {"update","revert"} and not isinstance(rfp, str):
            raise SafetyError("RESOURCE_FINGERPRINT_MISSING", str(rid))
        result.append({**base,"path": resource_path(base) if rid else None,"data": normalized_data,"resourceFingerprint": rfp,"currentResourceName": current.get("name") if current else None})
    return result


async def execute_one(service: Any, op: Mapping[str, Any]) -> str:
    target, action = collection(service, str(op["resource"])), op["action"]
    if action == "create":
        response = await execute_request(target.create(parent=op["parent"], body=op["data"]))
        if isinstance(response.get("path"), str):
            return response["path"]
        rid = response.get(META[str(op["resource"])][1])
        if isinstance(rid, str):
            return f"{op['parent']}/{META[str(op['resource'])][0]}/{rid}"
        raise SafetyError("MUTATION_RESPONSE_INVALID", "Create response omitted resource path.")
    path = str(op["path"])
    if action == "update":
        await execute_request(target.update(path=path, fingerprint=op["resourceFingerprint"], body=op["data"]))
    elif action == "remove":
        await execute_request(target.delete(path=path))
    else:
        await execute_request(target.revert(path=path, fingerprint=op["resourceFingerprint"]))
    return path


async def verify_one(service: Any, op: Mapping[str, Any], path: str) -> dict[str, Any]:
    target = collection(service, str(op["resource"]))
    if op["action"] == "remove":
        try:
            await execute_request(target.get(path=path))
        except Exception as exc:
            return {"resource_name": path,"expected": "NOT_FOUND","observed": "NOT_FOUND" if status_code(exc) == 404 else "READ_FAILED","verified": status_code(exc) == 404,"warning": None if status_code(exc) == 404 else str(exc)}
        return {"resource_name": path,"expected": "NOT_FOUND","observed": "STILL_READABLE","verified": False}
    try:
        observed = await execute_request(target.get(path=path))
    except Exception as exc:
        return {"resource_name": path,"expected": "READABLE","observed": "READ_FAILED","verified": False,"warning": str(exc)}
    expected = op.get("data") or {}
    compared = sorted(expected) if op["action"] in {"create","update"} else []
    mismatched = [key for key in compared if canonical_json(observed.get(key)) != canonical_json(expected.get(key))]
    return {"resource_name": path,"expected": "READABLE_WITH_MATCHING_FIELDS" if compared else "READABLE","observed": "READABLE","verified": not mismatched,"fingerprint": observed.get("fingerprint"),"compared_fields": compared,"mismatched_fields": mismatched}


async def gtm_safety_status() -> dict[str, Any]:
    """Return GTM safety gates and allowlists without exposing secrets."""
    c = load_safety_config()
    return {"runtime": "PYTHON_FASTMCP_HORIZON","mutations_enabled": c.mutations_enabled,"gates": {"create": c.allow_create,"update": c.allow_update,"delete": c.allow_delete,"revert": c.allow_revert,"create_version": c.allow_create_version,"set_latest": c.allow_set_latest,"publish": c.allow_publish,"publish_non_latest": c.allow_publish_non_latest,"undelete": c.allow_undelete},"allowlists": {"account_ids": sorted(c.allowed_account_ids),"container_ids": sorted(c.allowed_container_ids),"workspace_ids": sorted(c.allowed_workspace_ids)},"max_operations_per_request": c.max_operations,"confirmation_ttl_seconds": c.confirmation_ttl_seconds,"confirmation_secret_configured": len(os.getenv("GTM_CONFIRMATION_SECRET", "").encode()) >= 32,"replay_protection": "BEST_EFFORT_PROCESS_LOCAL","globally_single_use": False}


async def gtm_list_accounts() -> dict[str, Any]:
    """List GTM accounts accessible to the ADC identity."""
    service = get_gtm_service()
    items = await paginate(lambda token: service.accounts().list(**({"pageToken": token} if token else {})), item_key="account")
    return {"accounts": items, "count": len(items)}


async def gtm_list_containers(account_id: str) -> dict[str, Any]:
    """List containers under one GTM account."""
    parent, service = f"accounts/{numeric(account_id, 'account_id')}", get_gtm_service()
    items = await paginate(lambda token: service.accounts().containers().list(parent=parent, **({"pageToken": token} if token else {})), item_key="container")
    return {"parent": parent, "containers": items, "count": len(items)}


async def gtm_list_workspaces(account_id: str, container_id: str) -> dict[str, Any]:
    """List workspaces under one GTM container."""
    parent = f"accounts/{numeric(account_id, 'account_id')}/containers/{numeric(container_id, 'container_id')}"
    service = get_gtm_service()
    items = await paginate(lambda token: service.accounts().containers().workspaces().list(parent=parent, **({"pageToken": token} if token else {})), item_key="workspace")
    return {"parent": parent, "workspaces": items, "count": len(items)}


async def gtm_get_workspace(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """Get one GTM workspace."""
    return await read_workspace(get_gtm_service(), workspace_path(numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(workspace_id,"workspace_id")))


async def gtm_get_workspace_status(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """Read changes and conflicts for one GTM workspace."""
    path = workspace_path(numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(workspace_id,"workspace_id"))
    return await execute_request(get_gtm_service().accounts().containers().workspaces().getStatus(path=path))


async def list_resources(resource: str, account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    parent = workspace_path(numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(workspace_id,"workspace_id"))
    target = collection(get_gtm_service(), resource)
    items = await paginate(lambda token: target.list(parent=parent, **({"pageToken": token} if token else {})), item_key=resource)
    return {"parent": parent, META[resource][0]: items, "count": len(items)}


async def gtm_list_tags(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """List tags in one GTM workspace."""
    return await list_resources("tag", account_id, container_id, workspace_id)


async def gtm_list_triggers(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """List triggers in one GTM workspace."""
    return await list_resources("trigger", account_id, container_id, workspace_id)


async def gtm_list_variables(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """List variables in one GTM workspace."""
    return await list_resources("variable", account_id, container_id, workspace_id)


async def gtm_list_folders(account_id: str, container_id: str, workspace_id: str) -> dict[str, Any]:
    """List folders in one GTM workspace."""
    return await list_resources("folder", account_id, container_id, workspace_id)


async def gtm_get_live_version(account_id: str, container_id: str) -> dict[str, Any]:
    """Get the live GTM container version."""
    parent = f"accounts/{numeric(account_id,'account_id')}/containers/{numeric(container_id,'container_id')}"
    return await execute_request(get_gtm_service().accounts().containers().versions().live(parent=parent))


async def gtm_get_latest_version_header(account_id: str, container_id: str) -> dict[str, Any]:
    """Get the latest GTM version header."""
    parent = f"accounts/{numeric(account_id,'account_id')}/containers/{numeric(container_id,'container_id')}"
    return await execute_request(get_gtm_service().accounts().containers().version_headers().latest(parent=parent))


async def gtm_get_version(account_id: str, container_id: str, container_version_id: str) -> dict[str, Any]:
    """Get one GTM container version."""
    path = f"accounts/{numeric(account_id,'account_id')}/containers/{numeric(container_id,'container_id')}/versions/{numeric(container_version_id,'container_version_id')}"
    return await execute_request(get_gtm_service().accounts().containers().versions().get(path=path))


async def gtm_protected_mutation_preflight(stage: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Snapshot protected GTM workspace mutations without changing GTM."""
    if not isinstance(stage, str) or not stage.strip() or len(stage) > 100:
        raise SafetyError("INVALID_ARGUMENT", "stage must contain 1-100 characters.")
    config, service = load_safety_config(), get_gtm_service()
    normalized = await normalize(service, config, operations)
    payload, op_scope = mutation_payload(stage, normalized), scope(stage, normalized)
    receipt = issue_confirmation(payload, verb="EXECUTE", stage=stage, scope=op_scope, ttl_seconds=config.confirmation_ttl_seconds)
    return {"mode": "VALIDATE_ONLY","validation_kind": "CONNECTOR_PREFLIGHT","gtm_api_validate_only_supported": False,"validation_status": "PASSED","executed": False,"execution_status": "NOT_EXECUTED","atomic": False,"execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR","operation_count": len(normalized),"normalized_operations": normalized,**receipt,"required_human_confirmation": f"EXECUTAR ETAPA {stage} | HASH {receipt['operation_hash']}","operation_scope": op_scope,"verification": {"google_api_reads_performed": True,"workspace_snapshots_captured": True,"resource_snapshots_captured": True,"mutation_dispatched": False},"errors": []}


async def gtm_protected_mutation_execute(stage: str, operations: list[dict[str, Any]], confirmation: str) -> dict[str, Any]:
    """Execute the exact GTM workspace plan authorized by its signed receipt."""
    config, service = load_safety_config(), get_gtm_service()
    normalized = await normalize(service, config, operations)
    payload, op_scope = mutation_payload(stage, normalized), scope(stage, normalized)
    confirmation_state = verify_and_register_confirmation(confirmation, payload, expected_verb="EXECUTE", stage=stage, scope=op_scope)
    completed: list[tuple[dict[str, Any], str]] = []
    for index, op in enumerate(normalized):
        try:
            completed.append((op, await execute_one(service, op)))
        except Exception as exc:
            error_type, may_have_completed = classify(exc)
            verification = [await verify_one(service, done, path) for done, path in completed]
            return {"error": {"type": error_type,"message": str(exc),"details": {"mode": "EXECUTE","execution_attempted": True,"execution_status": "UNKNOWN" if may_have_completed else "FAILED","execution_may_have_completed": may_have_completed,**confirmation_state,"operation_count": len(normalized),"operations_completed": len(completed),"operation_failed_index": index,"operations_not_attempted": len(normalized)-index-1,"post_execution_verification": verification}}}
    verification = [await verify_one(service, op, path) for op, path in completed]
    return {"mode": "EXECUTE","validation_status": "PRIOR_VALIDATION_VERIFIED","execution_attempted": True,"executed": True,"execution_status": "SUCCEEDED" if all(x.get("verified") is True for x in verification) else "SUCCEEDED_WITH_VERIFICATION_WARNINGS",**confirmation_state,"atomic": False,"execution_strategy": "SEQUENTIAL_STOP_ON_FIRST_ERROR","operation_count": len(normalized),"operations_completed": len(completed),"response_resource_names": [path for _, path in completed],"post_execution_verification": verification,"errors": []}


def single_scope(stage: str, a: str, c: str, w: str | None = None) -> dict[str, Any]:
    return {"stage": stage,"accountIds": [a],"containerIds": [c],"workspaceIds": [w] if w else [],"operationCount": 1}


def version_gate(config: SafetyConfig, a: str, c: str, w: str) -> None:
    if not config.mutations_enabled or not config.allow_create_version:
        gate = "GTM_MUTATIONS_ENABLED" if not config.mutations_enabled else "GTM_ALLOW_CREATE_VERSION"
        raise SafetyError("GATE_DISABLED", f"{gate} is false.", {"gate": gate})
    validate_scope(config, account_id=a, container_id=c, workspace_id=w)


async def version_plan(service: Any, stage: str, a: str, c: str, w: str, name: str | None, notes: str | None) -> dict[str, Any]:
    path = workspace_path(a, c, w)
    ws = await read_workspace(service, path)
    status = await execute_request(service.accounts().containers().workspaces().getStatus(path=path))
    fp, conflicts, changes = ws.get("fingerprint"), status.get("mergeConflict", []), status.get("workspaceChange", [])
    if not isinstance(fp, str) or not fp:
        raise SafetyError("WORKSPACE_FINGERPRINT_MISSING", path)
    if isinstance(conflicts, list) and conflicts:
        raise SafetyError("WORKSPACE_HAS_UNRESOLVED_CONFLICTS", f"Workspace has {len(conflicts)} conflicts.")
    if not isinstance(changes, list) or not changes:
        raise SafetyError("WORKSPACE_HAS_NO_CHANGES", "Workspace has no changes.")
    return {"operation_hash_version": 3,"stage": stage,"action": "createVersion","accountId": a,"containerId": c,"workspaceId": w,"workspacePath": path,"workspaceFingerprint": fp,"workspaceName": ws.get("name"),"workspaceChangeCount": len(changes),"mergeConflictCount": len(conflicts) if isinstance(conflicts, list) else 0,"deletesWorkspace": True,"requestBody": {**({"name": name} if name else {}),**({"notes": notes} if notes else {})}}


async def gtm_create_version_preflight(stage: str, account_id: str, container_id: str, workspace_id: str, name: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Validate version creation; successful creation deletes the workspace."""
    a, c, w = numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(workspace_id,"workspace_id")
    config, service = load_safety_config(), get_gtm_service()
    version_gate(config, a, c, w)
    plan, op_scope = await version_plan(service, stage, a, c, w, name, notes), single_scope(stage, a, c, w)
    receipt = issue_confirmation(plan, verb="CREATE_VERSION", stage=stage, scope=op_scope, ttl_seconds=config.confirmation_ttl_seconds)
    return {"mode": "VALIDATE_ONLY","validation_kind": "CONNECTOR_PREFLIGHT","gtm_api_validate_only_supported": False,"validation_status": "PASSED","executed": False,"action": "createVersion","deletes_workspace": True,"publishes_container": False,"normalized_plan": plan,**receipt,"required_human_confirmation": f"EXECUTAR ETAPA {stage} | HASH {receipt['operation_hash']}","operation_scope": op_scope,"errors": []}


async def gtm_create_version_execute(stage: str, account_id: str, container_id: str, workspace_id: str, confirmation: str, name: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Create the validated GTM version without publishing it."""
    a, c, w = numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(workspace_id,"workspace_id")
    config, service = load_safety_config(), get_gtm_service()
    version_gate(config, a, c, w)
    plan, op_scope = await version_plan(service, stage, a, c, w, name, notes), single_scope(stage, a, c, w)
    state = verify_and_register_confirmation(confirmation, plan, expected_verb="CREATE_VERSION", stage=stage, scope=op_scope)
    try:
        response = await execute_request(service.accounts().containers().workspaces().create_version(path=plan["workspacePath"], body=plan["requestBody"]))
    except Exception as exc:
        kind, uncertain = classify(exc)
        return {"error": {"type": kind,"message": str(exc),"details": {"execution_status": "UNKNOWN" if uncertain else "FAILED","execution_may_have_completed": uncertain,**state}}}
    version = response.get("containerVersion", {})
    vid = version.get("containerVersionId")
    version_ok = False
    if isinstance(vid, str):
        try:
            await execute_request(service.accounts().containers().versions().get(path=f"accounts/{a}/containers/{c}/versions/{vid}")); version_ok = True
        except Exception:
            pass
    workspace_deleted = False
    try:
        await read_workspace(service, plan["workspacePath"])
    except Exception as exc:
        workspace_deleted = status_code(exc) == 404
    verified = version_ok and workspace_deleted and not bool(response.get("compilerError"))
    return {"mode": "EXECUTE","validation_status": "PRIOR_VALIDATION_VERIFIED","execution_attempted": True,"executed": True,"execution_status": "SUCCEEDED" if verified else "SUCCEEDED_WITH_VERIFICATION_WARNINGS",**state,"action": "createVersion","container_version_id": vid,"compiler_error": bool(response.get("compilerError")),"deletes_workspace": True,"published": False,"post_execution_verification": {"version_readable": version_ok,"workspace_deleted": workspace_deleted},"response": response,"errors": []}


def publish_gate(config: SafetyConfig, a: str, c: str) -> None:
    if not config.mutations_enabled or not config.allow_publish:
        gate = "GTM_MUTATIONS_ENABLED" if not config.mutations_enabled else "GTM_ALLOW_PUBLISH"
        raise SafetyError("GATE_DISABLED", f"{gate} is false.", {"gate": gate})
    validate_scope(config, account_id=a, container_id=c)


async def publish_plan(service: Any, config: SafetyConfig, stage: str, a: str, c: str, vid: str) -> dict[str, Any]:
    parent, path = f"accounts/{a}/containers/{c}", f"accounts/{a}/containers/{c}/versions/{vid}"
    target = await execute_request(service.accounts().containers().versions().get(path=path))
    live = await execute_request(service.accounts().containers().versions().live(parent=parent))
    latest = await execute_request(service.accounts().containers().version_headers().latest(parent=parent))
    if target.get("containerVersionId") != vid or not isinstance(target.get("fingerprint"), str):
        raise SafetyError("TARGET_VERSION_INVALID", path)
    if live.get("containerVersionId") == vid:
        raise SafetyError("TARGET_VERSION_ALREADY_LIVE", path)
    latest_id = latest.get("containerVersionId")
    if not isinstance(latest_id, str):
        raise SafetyError("LATEST_VERSION_ID_MISSING", parent)
    is_latest = latest_id == vid
    if not is_latest and not config.allow_publish_non_latest:
        raise SafetyError("NON_LATEST_PUBLICATION_BLOCKED", "GTM_ALLOW_PUBLISH_NON_LATEST is false.", {"target": vid,"latest": latest_id})
    return {"operation_hash_version": 3,"stage": stage,"action": "publish","accountId": a,"containerId": c,"containerVersionId": vid,"targetPath": path,"targetFingerprint": target["fingerprint"],"targetName": target.get("name"),"targetDescription": target.get("description"),"liveVersionId": live.get("containerVersionId"),"liveFingerprint": live.get("fingerprint"),"latestVersionId": latest_id,"targetIsLatest": is_latest}


async def gtm_publish_preflight(stage: str, account_id: str, container_id: str, container_version_id: str) -> dict[str, Any]:
    """Validate GTM publication and snapshot target, live and latest versions."""
    a, c, vid = numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(container_version_id,"container_version_id")
    config, service = load_safety_config(), get_gtm_service()
    publish_gate(config, a, c)
    plan, op_scope = await publish_plan(service, config, stage, a, c, vid), single_scope(stage, a, c)
    receipt = issue_confirmation(plan, verb="PUBLISH", stage=stage, scope=op_scope, ttl_seconds=config.confirmation_ttl_seconds)
    return {"mode": "VALIDATE_ONLY","validation_kind": "CONNECTOR_PREFLIGHT","gtm_api_validate_only_supported": False,"validation_status": "PASSED","executed": False,"action": "publish","normalized_plan": plan,**receipt,"required_human_confirmation": f"PUBLICAR VERSÃO GTM {vid} | HASH {receipt['operation_hash']}","operation_scope": op_scope,"errors": []}


async def gtm_publish_execute(stage: str, account_id: str, container_id: str, container_version_id: str, confirmation: str) -> dict[str, Any]:
    """Publish the exact GTM version authorized by a PUBLISH receipt."""
    a, c, vid = numeric(account_id,"account_id"), numeric(container_id,"container_id"), numeric(container_version_id,"container_version_id")
    config, service = load_safety_config(), get_gtm_service()
    publish_gate(config, a, c)
    plan, op_scope = await publish_plan(service, config, stage, a, c, vid), single_scope(stage, a, c)
    state = verify_and_register_confirmation(confirmation, plan, expected_verb="PUBLISH", stage=stage, scope=op_scope)
    try:
        response = await execute_request(service.accounts().containers().versions().publish(path=plan["targetPath"], fingerprint=plan["targetFingerprint"]))
    except Exception as exc:
        kind, uncertain = classify(exc)
        return {"error": {"type": kind,"message": str(exc),"details": {"execution_status": "UNKNOWN" if uncertain else "FAILED","execution_may_have_completed": uncertain,**state}}}
    live = await execute_request(service.accounts().containers().versions().live(parent=f"accounts/{a}/containers/{c}"))
    verified = live.get("containerVersionId") == vid and not bool(response.get("compilerError"))
    return {"mode": "PUBLISH","validation_status": "PRIOR_VALIDATION_VERIFIED","execution_attempted": True,"executed": True,"execution_status": "SUCCEEDED" if verified else "SUCCEEDED_WITH_VERIFICATION_WARNINGS",**state,"compiler_error": bool(response.get("compilerError")),"published_container_version_id": vid,"previous_live_version_id": plan["liveVersionId"],"post_publication_verification": {"expected_live_version_id": vid,"live_version_id": live.get("containerVersionId"),"verified": live.get("containerVersionId") == vid},"response": response,"errors": []}


READ_TOOLS = (gtm_safety_status,gtm_list_accounts,gtm_list_containers,gtm_list_workspaces,gtm_get_workspace,gtm_get_workspace_status,gtm_list_tags,gtm_list_triggers,gtm_list_variables,gtm_list_folders,gtm_get_live_version,gtm_get_latest_version_header,gtm_get_version)
MUTATION_TOOLS = (gtm_protected_mutation_preflight,gtm_protected_mutation_execute,gtm_create_version_preflight,gtm_create_version_execute,gtm_publish_preflight,gtm_publish_execute)
