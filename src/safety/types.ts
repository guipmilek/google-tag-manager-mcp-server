export type ProtectedWorkspaceResource =
  | "tag"
  | "trigger"
  | "variable"
  | "folder";

export type ProtectedWorkspaceAction =
  | "create"
  | "update"
  | "remove"
  | "revert";

export type ConfirmationVerb = "EXECUTE" | "CREATE_VERSION" | "PUBLISH";

export type JsonObject = Record<string, unknown>;

export interface RequestedMutationOperation {
  resource: ProtectedWorkspaceResource;
  action: ProtectedWorkspaceAction;
  accountId: string;
  containerId: string;
  workspaceId: string;
  resourceId?: string;
  data?: JsonObject;
}

export interface NormalizedMutationOperation
  extends RequestedMutationOperation {
  path?: string;
  parent: string;
  workspacePath: string;
  workspaceFingerprint: string | null;
  resourceFingerprint: string | null;
  currentResourceName: string | null;
}

export interface ConfirmationScope {
  stage: string;
  accountIds: string[];
  containerIds: string[];
  workspaceIds: string[];
  operationCount: number;
}

export interface ConfirmationClaims {
  v: 1;
  verb: ConfirmationVerb;
  hash: string;
  stage: string;
  iat: number;
  exp: number;
  nonce: string;
  scope: ConfirmationScope;
}

export interface SafetyConfig {
  mutationsEnabled: boolean;
  allowUnsafeLegacyMutations: boolean;
  allowCreate: boolean;
  allowUpdate: boolean;
  allowDelete: boolean;
  allowRevert: boolean;
  allowCreateVersion: boolean;
  allowSetLatest: boolean;
  allowPublish: boolean;
  allowUndelete: boolean;
  allowedAccountIds: Set<string>;
  allowedContainerIds: Set<string>;
  allowedWorkspaceIds: Set<string>;
  maxOperationsPerRequest: number;
  confirmationTtlSeconds: number;
  confirmationSecret: string | null;
}
