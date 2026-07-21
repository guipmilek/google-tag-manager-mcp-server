import {
  JsonObject,
  NormalizedMutationOperation,
  RequestedMutationOperation,
  SafetyConfig,
} from "../safety";
import {
  validateActionGate,
  validateOperationShape,
  validateScope,
} from "./guards";
import {
  getCollection,
  mergeUpdatePayload,
  parseCreatePayload,
  resourcePath,
  workspacePath,
} from "./model";

async function readWorkspace(client: any, path: string): Promise<JsonObject> {
  const response = await client.accounts.containers.workspaces.get({ path });
  return (response.data || {}) as JsonObject;
}

async function readResource(
  client: any,
  operation: RequestedMutationOperation,
): Promise<JsonObject> {
  const path = resourcePath(operation);
  if (!path) {
    throw new Error("resource path is unavailable");
  }
  const response = await getCollection(client, operation.resource).get({
    path,
  });
  return (response.data || {}) as JsonObject;
}

async function ensureNoDuplicateName(
  client: any,
  operation: RequestedMutationOperation,
  data: JsonObject,
): Promise<void> {
  const name = typeof data.name === "string" ? data.name.trim() : "";
  if (!name) {
    return;
  }

  const response = await getCollection(client, operation.resource).list({
    parent: workspacePath(operation),
  });
  const collectionKey = operation.resource;
  const resources = Array.isArray(response.data?.[collectionKey])
    ? response.data[collectionKey]
    : [];
  const duplicate = resources.find(
    (item: JsonObject) =>
      typeof item.name === "string" &&
      item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase(),
  );
  if (duplicate) {
    throw new Error(`DUPLICATE_RESOURCE_NAME:${operation.resource}:${name}`);
  }
}

export async function normalizeOperations(
  client: any,
  config: SafetyConfig,
  requestedOperations: RequestedMutationOperation[],
): Promise<NormalizedMutationOperation[]> {
  if (requestedOperations.length > config.maxOperationsPerRequest) {
    throw new Error(
      `OPERATION_LIMIT_EXCEEDED:${requestedOperations.length}:${config.maxOperationsPerRequest}`,
    );
  }

  const workspaces = new Map<string, JsonObject>();
  const normalized: NormalizedMutationOperation[] = [];

  for (const requested of requestedOperations) {
    validateScope(config, requested);
    validateActionGate(config, requested.action);
    validateOperationShape(requested);

    const parent = workspacePath(requested);
    let workspace = workspaces.get(parent);
    if (!workspace) {
      workspace = await readWorkspace(client, parent);
      workspaces.set(parent, workspace);
    }

    const workspaceFingerprint =
      typeof workspace.fingerprint === "string" ? workspace.fingerprint : null;
    if (!workspaceFingerprint) {
      throw new Error(`WORKSPACE_FINGERPRINT_MISSING:${parent}`);
    }

    let currentResource: JsonObject | null = null;
    let data: JsonObject | undefined;

    if (requested.action === "create") {
      data = parseCreatePayload(requested.resource, requested.data);
      await ensureNoDuplicateName(client, requested, data);
    } else {
      currentResource = await readResource(client, requested);
      if (requested.action === "update") {
        data = mergeUpdatePayload(
          requested.resource,
          currentResource,
          requested.data,
        );
      }
    }

    const resourceFingerprint =
      currentResource && typeof currentResource.fingerprint === "string"
        ? currentResource.fingerprint
        : null;
    if (
      (requested.action === "update" || requested.action === "revert") &&
      !resourceFingerprint
    ) {
      throw new Error(
        `RESOURCE_FINGERPRINT_MISSING:${resourcePath(requested) || "unknown"}`,
      );
    }

    normalized.push({
      resource: requested.resource,
      action: requested.action,
      accountId: requested.accountId,
      containerId: requested.containerId,
      workspaceId: requested.workspaceId,
      resourceId: requested.resourceId,
      data,
      parent,
      path: resourcePath(requested),
      workspacePath: parent,
      workspaceFingerprint,
      resourceFingerprint,
      currentResourceName:
        currentResource && typeof currentResource.name === "string"
          ? currentResource.name
          : null,
    });
  }

  return normalized;
}
