import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { McpAgentToolParamsModel } from "../models/McpAgentModel";
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
  workspaceFingerprint: string | null;
  workspaceName: string | null;
  workspaceChangeCount: number;
  mergeConflictCount: number;
  requestBody: { name?: string; notes?: string };
}

const VersionExecuteInputSchema = {
  ...VersionInputSchema,
  confirmation: z.string().min(1),
};

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

function buildPaths(
  accountId: string,
  containerId: string,
  workspaceId: string,
): { workspacePath: string } {
  return {
    workspacePath: `accounts/${accountId}/containers/${containerId}/workspaces/${workspaceId}`,
  };
}

async function versionPlan(
  client: any,
  stage: string,
  accountId: string,
  containerId: string,
  workspaceId: string,
  name?: string,
  notes?: string,
): Promise<VersionPlan> {
  const { workspacePath } = buildPaths(accountId, containerId, workspaceId);
  const [workspaceResponse, statusResponse] = await Promise.all([
    client.accounts.containers.workspaces.get({ path: workspacePath }),
    client.accounts.containers.workspaces.getStatus({ path: workspacePath }),
  ]);

  const workspace = workspaceResponse.data || {};
  const status = statusResponse.data || {};
  const mergeConflict = Array.isArray(status.mergeConflict)
    ? status.mergeConflict
    : [];
  if (mergeConflict.length > 0) {
    throw new Error(
      `WORKSPACE_HAS_UNRESOLVED_CONFLICTS:${mergeConflict.length}`,
    );
  }

  return {
    operation_hash_version: 1,
    stage,
    action: "createVersion",
    accountId,
    containerId,
    workspaceId,
    workspacePath,
    workspaceFingerprint:
      typeof workspace.fingerprint === "string" ? workspace.fingerprint : null,
    workspaceName: typeof workspace.name === "string" ? workspace.name : null,
    workspaceChangeCount: Array.isArray(status.workspaceChange)
      ? status.workspaceChange.length
      : 0,
    mergeConflictCount: mergeConflict.length,
    requestBody: {
      ...(name ? { name } : {}),
      ...(notes ? { notes } : {}),
    },
  };
}

function versionScope(
  stage: string,
  accountId: string,
  containerId: string,
  workspaceId: string,
): ConfirmationScope {
  return {
    stage,
    accountIds: [accountId],
    containerIds: [containerId],
    workspaceIds: [workspaceId],
    operationCount: 1,
  };
}

export const protectedVersionActions = (
  server: McpServer,
  { props, env }: McpAgentToolParamsModel,
): void => {
  server.tool(
    "gtm_create_version_preflight",
    "Validate and snapshot creation of a GTM container version from a workspace. This never publishes the version.",
    VersionInputSchema,
    async ({ stage, accountId, containerId, workspaceId, name, notes }: VersionInput) => {
      log(`Running GTM create-version preflight for stage '${stage}'`);
      try {
        const config = getSafetyConfig(env);
        validateVersionScope(config, accountId, containerId, workspaceId);
        const client = await getTagManagerClient(props);
        const plan = await versionPlan(
          client,
          stage,
          accountId,
          containerId,
          workspaceId,
          name,
          notes,
        );
        const operationHash = await sha256Hex(plan);
        const scope = versionScope(stage, accountId, containerId, workspaceId);
        const receipt = await createConfirmation(
          config,
          "CREATE_VERSION",
          operationHash,
          stage,
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
                  publishes_container: false,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  normalized_plan: plan,
                  required_confirmation: receipt.token,
                  confirmation_expires_at: receipt.expiresAt,
                  required_human_confirmation: `EXECUTAR ETAPA ${stage} | HASH ${operationHash}`,
                  validation_receipt: {
                    expires_at: receipt.expiresAt,
                    replay_protection: replayProtectionDescription(),
                    globally_single_use: false,
                  },
                  operation_scope: scope,
                  errors: [],
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        return structuredError("CREATE_VERSION_PREFLIGHT_FAILED", errorMessage(error), {
          stage,
          executed: false,
          mutation_dispatched: false,
        });
      }
    },
  );

  server.tool(
    "gtm_create_version_execute",
    "Create the exactly validated GTM container version. This tool never publishes the version.",
    VersionExecuteInputSchema,
    async ({
      stage,
      accountId,
      containerId,
      workspaceId,
      name,
      notes,
      confirmation,
    }: VersionExecuteInput) => {
      log(`Running GTM create-version execution for stage '${stage}'`);
      try {
        const config = getSafetyConfig(env);
        validateVersionScope(config, accountId, containerId, workspaceId);
        const client = await getTagManagerClient(props);
        const plan = await versionPlan(
          client,
          stage,
          accountId,
          containerId,
          workspaceId,
          name,
          notes,
        );
        const operationHash = await sha256Hex(plan);
        const verifiedConfirmation = await verifyConfirmation(
          config,
          confirmation,
          "CREATE_VERSION",
          operationHash,
          stage,
        );
        registerConfirmationBeforeApiCall(verifiedConfirmation.fingerprint);

        const response =
          await client.accounts.containers.workspaces.create_version({
            path: plan.workspacePath,
            requestBody: plan.requestBody,
          });
        const containerVersion = response.data?.containerVersion || null;
        const compilerError = Boolean(response.data?.compilerError);
        const versionId = containerVersion?.containerVersionId || null;
        let verification: Record<string, unknown> = {
          performed: false,
          verified: false,
        };

        if (versionId) {
          const versionPath = `accounts/${accountId}/containers/${containerId}/versions/${versionId}`;
          try {
            const readBack =
              await client.accounts.containers.versions.get({ path: versionPath });
            verification = {
              performed: true,
              verified: true,
              resource_name: versionPath,
              fingerprint: readBack.data?.fingerprint || null,
              published: false,
            };
          } catch (error) {
            verification = {
              performed: true,
              verified: false,
              resource_name: versionPath,
              warning: errorMessage(error),
              published: false,
            };
          }
        }

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
                  execution_status:
                    compilerError || verification.verified !== true
                      ? "SUCCEEDED_WITH_VERIFICATION_WARNINGS"
                      : "SUCCEEDED",
                  confirmation_verified: true,
                  confirmation_registered_before_api_call: true,
                  confirmation_token_fingerprint:
                    verifiedConfirmation.fingerprint,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  action: "createVersion",
                  container_version_id: versionId,
                  compiler_error: compilerError,
                  published: false,
                  response: response.data,
                  post_execution_verification: verification,
                  errors: [],
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        return structuredError("CREATE_VERSION_EXECUTION_BLOCKED", errorMessage(error), {
          stage,
          mode: "EXECUTE",
          execution_attempted: false,
          executed: false,
          execution_status: "BLOCKED_BEFORE_API_CALL",
        });
      }
    },
  );
};
