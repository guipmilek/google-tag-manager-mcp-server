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

export const OperationSchema = z.object({
  resource: ResourceSchema,
  action: ActionSchema,
  accountId: z.string().min(1),
  containerId: z.string().min(1),
  workspaceId: z.string().min(1),
  resourceId: z.string().min(1).optional(),
  data: z.record(z.unknown()).optional(),
});

export const RESOURCE_METADATA: Record<
  ProtectedWorkspaceResource,
  { plural: string; idField: string }
> = {
  tag: { plural: "tags", idField: "tagId" },
  trigger: { plural: "triggers", idField: "triggerId" },
  variable: { plural: "variables", idField: "variableId" },
  folder: { plural: "folders", idField: "folderId" },
};

const payloadSchemas = {
  tag: TagSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    tagId: true,
    fingerprint: true,
  }),
  trigger: TriggerSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    triggerId: true,
    fingerprint: true,
  }),
  variable: VariableSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    variableId: true,
    fingerprint: true,
  }),
  folder: FolderSchema.omit({
    accountId: true,
    containerId: true,
    workspaceId: true,
    folderId: true,
    fingerprint: true,
  }),
} as const;

export function parsePayload(
  resource: ProtectedWorkspaceResource,
  data: JsonObject | undefined,
): JsonObject {
  if (!data) {
    throw new Error(`data is required for ${resource} create/update operations`);
  }
  return payloadSchemas[resource].parse(data) as JsonObject;
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
