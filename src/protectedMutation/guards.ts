import {
  ProtectedWorkspaceAction,
  RequestedMutationOperation,
  SafetyConfig,
} from "../safety";

function requireConfiguredAllowlist(
  configured: Set<string>,
  value: string,
  variableName: string,
): void {
  if (configured.size === 0) {
    throw new Error(`ALLOWLIST_NOT_CONFIGURED:${variableName}`);
  }
  if (!configured.has(value)) {
    throw new Error(`SCOPE_NOT_ALLOWED:${variableName}:${value}`);
  }
}

export function validateScope(
  config: SafetyConfig,
  operation: RequestedMutationOperation,
): void {
  requireConfiguredAllowlist(
    config.allowedAccountIds,
    operation.accountId,
    "GTM_ALLOWED_ACCOUNT_IDS",
  );
  requireConfiguredAllowlist(
    config.allowedContainerIds,
    operation.containerId,
    "GTM_ALLOWED_CONTAINER_IDS",
  );
  requireConfiguredAllowlist(
    config.allowedWorkspaceIds,
    operation.workspaceId,
    "GTM_ALLOWED_WORKSPACE_IDS",
  );
}

export function validateActionGate(
  config: SafetyConfig,
  action: ProtectedWorkspaceAction,
): void {
  if (!config.mutationsEnabled) {
    throw new Error("GATE_DISABLED:GTM_MUTATIONS_ENABLED");
  }

  const gates: Record<ProtectedWorkspaceAction, [boolean, string]> = {
    create: [config.allowCreate, "GTM_ALLOW_CREATE"],
    update: [config.allowUpdate, "GTM_ALLOW_UPDATE"],
    remove: [config.allowDelete, "GTM_ALLOW_DELETE"],
    revert: [config.allowRevert, "GTM_ALLOW_REVERT"],
  };

  const [enabled, gate] = gates[action];
  if (!enabled) {
    throw new Error(`GATE_DISABLED:${gate}`);
  }
}

export function validateOperationShape(
  operation: RequestedMutationOperation,
): void {
  if (operation.action === "create") {
    if (operation.resourceId) {
      throw new Error("resourceId must be omitted for create operations");
    }
    if (!operation.data) {
      throw new Error("data is required for create operations");
    }
    return;
  }

  if (!operation.resourceId) {
    throw new Error(
      `resourceId is required for ${operation.action} operations`,
    );
  }
  if (operation.action === "update" && !operation.data) {
    throw new Error("data is required for update operations");
  }
  if (
    (operation.action === "remove" || operation.action === "revert") &&
    operation.data
  ) {
    throw new Error(`data must be omitted for ${operation.action} operations`);
  }
}
