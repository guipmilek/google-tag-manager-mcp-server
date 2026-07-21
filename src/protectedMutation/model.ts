import { z } from "zod";
import { FolderSchema } from "../schemas/FolderSchema";
import { TagSchema } from "../schemas/TagSchema";
import { TriggerSchema } from "../schemas/TriggerSchema";
import { VariableSchema } from "../schemas/VariableSchema";
import {
  JsonObject,
  ProtectedWorkspaceResource,
  RequestedMutationOperation,
} from "../safety";

export const ResourceSchema = z.enum(["tag", "trigger", "variable", "folder"]);
export const ActionSchema = z.enum(["create", "update", "remove", "revert"]);

export const OperationSchema = z
  .object({
    resource: ResourceSchema,
    action: ActionSchema,
    accountId: z.string().min(1),
    containerId: z.string().min(1),
    workspaceId: z.string().min(1),
    resourceId: z.string().min(1).optional(),
    data: z.record(z.unknown()).optional(),
  })
  .strict();

export const RESOURCE_METADATA: Record<
  ProtectedWorkspaceResource,
  { plural: string; idField: string }
> = {
  tag: { plural: "tags", idField: "tagId" },
  trigger: { plural: "triggers", idField: "triggerId" },
  variable: { plural: "variables", idField: "variableId" },
  folder: { plural: "folders", idField: "folderId" },
};

const writablePayloadSchemas = {
  tag: TagSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    tagId: true,
    fingerprint: true,
    tagManagerUrl: true,
  }),
  trigger: TriggerSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    triggerId: true,
    fingerprint: true,
    tagManagerUrl: true,
  }),
  variable: VariableSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    variableId: true,
    fingerprint: true,
    tagManagerUrl: true,
  }),
  folder: FolderSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    folderId: true,
    fingerprint: true,
    tagManagerUrl: true,
  }),
} as const;

const strictPayloadSchemas = {
  tag: writablePayloadSchemas.tag.strict(),
  trigger: writablePayloadSchemas.trigger.strict(),
  variable: writablePayloadSchemas.variable.strict(),
  folder: writablePayloadSchemas.folder.strict(),
} as const;

function requireCreateFields(
  resource: ProtectedWorkspaceResource,
  data: JsonObject,
): void {
  if (typeof data.name !== "string" || !data.name.trim()) {
    throw new Error(`CREATE_REQUIRED_FIELD_MISSING:${resource}:name`);
  }
  if (
    resource !== "folder" &&
    (typeof data.type !== "string" || !data.type.trim())
  ) {
    throw new Error(`CREATE_REQUIRED_FIELD_MISSING:${resource}:type`);
  }
}

export function parseCreatePayload(
  resource: ProtectedWorkspaceResource,
  data: JsonObject | undefined,
): JsonObject {
  if (!data) {
    throw new Error(`data is required for ${resource} create operations`);
  }
  const parsed = strictPayloadSchemas[resource].parse(data) as JsonObject;
  requireCreateFields(resource, parsed);
  return parsed;
}

export function mergeUpdatePayload(
  resource: ProtectedWorkspaceResource,
  currentResource: JsonObject,
  requestedData: JsonObject | undefined,
): JsonObject {
  if (!requestedData) {
    throw new Error(`data is required for ${resource} update operations`);
  }
  const requested = strictPayloadSchemas[resource].parse(
    requestedData,
  ) as JsonObject;
  const currentWritable = writablePayloadSchemas[resource].parse(
    currentResource,
  ) as JsonObject;
  return strictPayloadSchemas[resource].parse({
    ...currentWritable,
    ...requested,
  }) as JsonObject;
}

export function workspacePath(operation: RequestedMutationOperation): string {
  return `accounts/${operation.accountId}/containers/${operation.containerId}/workspaces/${operation.workspaceId}`;
}

export function resourcePath(
  operation: RequestedMutationOperation,
): string | undefined {
  if (!operation.resourceId) {
    return undefined;
  }
  const metadata = RESOURCE_METADATA[operation.resource];
  return `${workspacePath(operation)}/${metadata.plural}/${operation.resourceId}`;
}

export function getCollection(
  client: any,
  resource: ProtectedWorkspaceResource,
): any {
  return client.accounts.containers.workspaces[
    RESOURCE_METADATA[resource].plural
  ];
}
