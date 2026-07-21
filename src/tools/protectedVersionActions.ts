import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { McpAgentToolParamsModel } from "../models/McpAgentModel";
import { classifyExecutionError } from "../protectedMutation/execution";
import {
  ConfirmationScope,
  SafetyConfig,
  createConfirmation,
  errorMessage,
  getSafetyConfig,
  registerConfirmationBeforeApiCall,
  replayProtectionDescription,
  sha256Hex,
  structuredError,
  verifyConfirmation,
} from "../safety";
import { getTagManagerClient, log } from "../utils";

const VersionInputSchema = {
  stage: z.string().min(1).max(100),
  accountId: z.string().min(1),
  containerId: z.string().min(1),
  workspaceId: z.string().min(1),
  name: z.string().max(200).optional(),
  notes: z.string().max(1000).optional(),
};

const VersionExecuteInputSchema = {
  ...VersionInputSchema,
  confirmation: z.string().min(1),
};

type VersionInput = {
  stage: string;
  accountId: string;
  containerId: string;
  workspaceId: string;
  name?: string;
  notes?: string;
};

type VersionExecuteInput = VersionInput & { confirmation: string };

interface VersionPlan {
  operation_hash_version: 1;
  stage: string;
  action: "createVersion";
  accountId: string;
  containerId: string;
  workspaceId: string;
  workspacePath: string;
  workspaceFingerprint: string;
  workspaceName: string | null;
  workspaceChangeCount: number;
  mergeConflictCount: number;
  deletesWorkspace: true;
  requestBody: { name?: string; notes?: string };
}

function requireAllowlist(
  values: Set<string>,
  value: string,
  variableName: string,
): void {
  if (values.size === 0) {
    throw new Error(`ALLOWLIST_NOT_CONFIGURED:${variableName}`);
  }
  if (!values.has(value)) {
    throw new Error(`SCOPE_NOT_ALLOWED:${variableName}:${value}`);
  }
}

function validateVersionScope(
  config: SafetyConfig,
  accountId: string,
  containerId: string,
  workspaceId: string,
): void {
  if (!config.mutationsEnabled) {
    throw new Error("GATE_DISABLED:GTM_MUTATIONS_ENABLED");
  }
  if (!config.allowCreateVersion) {
    throw new Error("GATE_DISABLED:GTM_ALLOW_CREATE_VERSION");
  }
  requireAllowlist(
    config.allowedAccountIds,
    accountId,
    "GTM_ALLOWED_ACCOUNT_IDS",
  );
  requireAllowlist(
    config.allowedContainerIds,
    containerId,
    "GTM_ALLOWED_CONTAINER_IDS",
  );
  requireAllowlist(
    config.allowedWorkspaceIds,
    workspaceId,
    "GTM_ALLOWED_WORKSPACE_IDS",
  );
}

function workspacePath(
  accountId: string,
  containerId: string,
  workspaceId: string,
): string {
  return `accounts/${accountId}/containers/${containerId}/workspaces/${workspaceId}`;
}

async function buildVersionPlan(
  client: any,
  input: VersionInput,
): Promise<VersionPlan> {
  const path = workspacePath(
    input.accountId,
    input.containerId,
    input.workspaceId,
  );
  const [workspaceResponse, statusResponse] = await Promise.all([
    client.accounts.containers.workspaces.get({ path }),
    client.accounts.containers.workspaces.getStatus({ path }),
  ]);

  const workspace = workspaceResponse.data || {};
  const status = statusResponse.data || {};
  const fingerprint =
    typeof workspace.fingerprint === "string" ? workspace.fingerprint : null;
  if (!fingerprint) {
    throw new Error(`WORKSPACE_FINGERPRINT_MISSING:${path}`);
  }

  const mergeConflicts = Array.isArray(status.mergeConflict)
    ? status.mergeConflict
    : [];
  if (mergeConflicts.length > 0) {
    throw new Error(
      `WORKSPACE_HAS_UNRESOLVED_CONFLICTS:${mergeConflicts.length}`,
    );
  }

  const workspaceChanges = Array.isArray(status.workspaceChange)
    ? status.workspaceChange
    : [];
  if (workspaceChanges.length === 0) {
    throw new Error("WORKSPACE_HAS_NO_CHANGES");
  }

  return {
    operation_hash_version: 1,
    stage: input.stage,
    action: "createVersion",
    accountId: input.accountId,
    containerId: input.containerId,
    workspaceId: input.workspaceId,
    workspacePath: path,
    workspaceFingerprint: fingerprint,
    workspaceName: typeof workspace.name === "string" ? workspace.name : null,
    workspaceChangeCount: workspaceChanges.length,
    mergeConflictCount: mergeConflicts.length,
    deletesWorkspace: true,
    requestBody: {
      ...(input.name ? { name: input.name } : {}),
      ...(input.notes ? { notes: input.notes } : {}),
    },
  };
}

function versionScope(input: VersionInput): ConfirmationScope {
  return {
    stage: input.stage,
    accountIds: [input.accountId],
    containerIds: [input.containerId],
    workspaceIds: [input.workspaceId],
    operationCount: 1,
  };
}

async function verifyCreatedVersion(
  client: any,
  accountId: string,
  containerId: string,
  versionId: string | null,
): Promise<Record<string, unknown>> {
  if (!versionId) {
    return {
      performed: false,
      verified: false,
      warning: "CREATE_VERSION_RESPONSE_MISSING_VERSION_ID",
    };
  }

  const path = `accounts/${accountId}/containers/${containerId}/versions/${versionId}`;
  try {
    const response = await client.accounts.containers.versions.get({ path });
    return {
      performed: true,
      verified: true,
      resource_name: path,
      fingerprint: response.data?.fingerprint || null,
      published: false,
    };
  } catch (error) {
    return {
      performed: true,
      verified: false,
      resource_name: path,
      warning: errorMessage(error),
      published: false,
    };
  }
}

async function verifyWorkspaceDeleted(
  client: any,
  path: string,
): Promise<Record<string, unknown>> {
  try {
    await client.accounts.containers.workspaces.get({ path });
    return {
      performed: true,
      expected: "NOT_FOUND_AFTER_CREATE_VERSION",
      observed: "STILL_READABLE",
      verified: false,
    };
  } catch (error) {
    const message = errorMessage(error);
    const notFound = /404|not found/i.test(message);
    return {
      performed: true,
      expected: "NOT_FOUND_AFTER_CREATE_VERSION",
      observed: notFound ? "NOT_FOUND" : "READ_FAILED",
      verified: notFound,
      warning: notFound ? null : message,
    };
  }
}

export const protectedVersionActions = (
  server: McpServer,
  { props, env }: McpAgentToolParamsModel,
): void => {
  server.tool(
    "gtm_create_version_preflight",
    "Validate and snapshot creation of a GTM container version. Successful creation deletes the source workspace but never publishes the version.",
    VersionInputSchema,
    async (input: VersionInput) => {
      log(`Running GTM create-version preflight for stage '${input.stage}'`);
      try {
        const config = getSafetyConfig(env);
        validateVersionScope(
          config,
          input.accountId,
          input.containerId,
          input.workspaceId,
        );
        const client = await getTagManagerClient(props);
        const plan = await buildVersionPlan(client, input);
        const operationHash = await sha256Hex(plan);
        const scope = versionScope(input);
        const receipt = await createConfirmation(
          config,
          "CREATE_VERSION",
          operationHash,
          input.stage,
          scope,
        );

        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  mode: "VALIDATE_ONLY",
                  validation_kind: "CONNECTOR_PREFLIGHT",
                  validation_status: "PASSED",
                  executed: false,
                  execution_status: "NOT_EXECUTED",
                  action: "createVersion",
                  deletes_workspace: true,
                  publishes_container: false,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  normalized_plan: plan,
                  required_confirmation: receipt.token,
                  confirmation_expires_at: receipt.expiresAt,
                  required_human_confirmation: `EXECUTAR ETAPA ${input.stage} | HASH ${operationHash}`,
                  validation_receipt: {
                    expires_at: receipt.expiresAt,
                    replay_protection: replayProtectionDescription(),
                    globally_single_use: false,
                  },
                  operation_scope: scope,
                  verification: {
                    workspace_read_performed: true,
                    workspace_status_read_performed: true,
                    mutation_dispatched: false,
                  },
                  errors: [],
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        return structuredError(
          "CREATE_VERSION_PREFLIGHT_FAILED",
          errorMessage(error),
          {
            stage: input.stage,
            executed: false,
            mutation_dispatched: false,
          },
        );
      }
    },
  );

  server.tool(
    "gtm_create_version_execute",
    "Create the exactly validated GTM container version, verify the new version, and verify deletion of the source workspace. This tool never publishes.",
    VersionExecuteInputSchema,
    async ({ confirmation, ...input }: VersionExecuteInput) => {
      log(`Running GTM create-version execution for stage '${input.stage}'`);
      let dispatchStarted = false;
      let apiResponseReceived = false;
      let operationHash: string | null = null;
      let confirmationFingerprint: string | null = null;

      try {
        const config = getSafetyConfig(env);
        validateVersionScope(
          config,
          input.accountId,
          input.containerId,
          input.workspaceId,
        );
        const client = await getTagManagerClient(props);
        const plan = await buildVersionPlan(client, input);
        operationHash = await sha256Hex(plan);
        const verifiedConfirmation = await verifyConfirmation(
          config,
          confirmation,
          "CREATE_VERSION",
          operationHash,
          input.stage,
        );
        confirmationFingerprint = verifiedConfirmation.fingerprint;
        registerConfirmationBeforeApiCall(confirmationFingerprint);

        dispatchStarted = true;
        let response: any;
        try {
          response =
            await client.accounts.containers.workspaces.create_version({
              path: plan.workspacePath,
              requestBody: plan.requestBody,
            });
          apiResponseReceived = true;
        } catch (error) {
          const classification = classifyExecutionError(error);
          return structuredError(
            classification.errorType,
            errorMessage(error),
            {
              stage: input.stage,
              mode: "EXECUTE",
              validation_status: "PRIOR_VALIDATION_VERIFIED",
              execution_attempted: true,
              executed: false,
              execution_status: classification.executionMayHaveCompleted
                ? "UNKNOWN"
                : "FAILED",
              execution_may_have_completed:
                classification.executionMayHaveCompleted,
              mutation_dispatched: true,
              api_response_received: false,
              confirmation_verified: true,
              confirmation_registered_before_api_call: true,
              confirmation_token_fingerprint: confirmationFingerprint,
              operation_hash: operationHash,
            },
          );
        }

        const containerVersion = response.data?.containerVersion || null;
        const versionId = containerVersion?.containerVersionId || null;
        const compilerError = Boolean(response.data?.compilerError);
        const [versionVerification, workspaceDeletionVerification] =
          await Promise.all([
            verifyCreatedVersion(
              client,
              input.accountId,
              input.containerId,
              versionId,
            ),
            verifyWorkspaceDeleted(client, plan.workspacePath),
          ]);

        const verified =
          !compilerError &&
          versionVerification.verified === true &&
          workspaceDeletionVerification.verified === true;

        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  mode: "EXECUTE",
                  validation_status: "PRIOR_VALIDATION_VERIFIED",
                  execution_attempted: true,
                  executed: true,
                  execution_status: verified
                    ? "SUCCEEDED"
                    : "SUCCEEDED_WITH_VERIFICATION_WARNINGS",
                  confirmation_verified: true,
                  confirmation_registered_before_api_call: true,
                  confirmation_token_fingerprint: confirmationFingerprint,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  action: "createVersion",
                  mutation_dispatched: true,
                  api_response_received: true,
                  container_version_id: versionId,
                  compiler_error: compilerError,
                  deletes_workspace: true,
                  published: false,
                  response: response.data,
                  post_execution_verification: {
                    version: versionVerification,
                    workspace_deletion: workspaceDeletionVerification,
                  },
                  errors: [],
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        if (dispatchStarted) {
          return structuredError(
            apiResponseReceived
              ? "CONNECTOR_POST_RESPONSE_ERROR"
              : "TRANSPORT_OR_CONNECTOR_ERROR",
            errorMessage(error),
            {
              stage: input.stage,
              mode: "EXECUTE",
              execution_attempted: true,
              executed: apiResponseReceived,
              execution_status: apiResponseReceived
                ? "SUCCEEDED_WITH_VERIFICATION_WARNINGS"
                : "UNKNOWN",
              execution_may_have_completed: !apiResponseReceived,
              mutation_dispatched: true,
              api_response_received: apiResponseReceived,
              confirmation_verified: Boolean(confirmationFingerprint),
              confirmation_registered_before_api_call: Boolean(
                confirmationFingerprint,
              ),
              confirmation_token_fingerprint: confirmationFingerprint,
              operation_hash: operationHash,
            },
          );
        }

        return structuredError(
          "CREATE_VERSION_EXECUTION_BLOCKED",
          errorMessage(error),
          {
            stage: input.stage,
            mode: "EXECUTE",
            execution_attempted: false,
            executed: false,
            execution_status: "BLOCKED_BEFORE_API_CALL",
            mutation_dispatched: false,
            api_response_received: false,
            confirmation_registered_before_api_call: false,
            operation_hash: operationHash,
          },
        );
      }
    },
  );
};
