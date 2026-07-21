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

const PublishInputSchema = {
  stage: z.string().min(1).max(100),
  accountId: z.string().min(1),
  containerId: z.string().min(1),
  containerVersionId: z.string().min(1),
};

type PublishInput = {
  stage: string;
  accountId: string;
  containerId: string;
  containerVersionId: string;
};

type PublishExecuteInput = PublishInput & { confirmation: string };

interface PublishPlan {
  operation_hash_version: 1;
  stage: string;
  action: "publish";
  accountId: string;
  containerId: string;
  containerVersionId: string;
  targetPath: string;
  targetFingerprint: string;
  targetName: string | null;
  targetDescription: string | null;
  liveVersionId: string | null;
  liveFingerprint: string | null;
}

const PublishExecuteInputSchema = {
  ...PublishInputSchema,
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

function validatePublishScope(
  config: SafetyConfig,
  accountId: string,
  containerId: string,
): void {
  if (!config.mutationsEnabled) {
    throw new Error("GATE_DISABLED:GTM_MUTATIONS_ENABLED");
  }
  if (!config.allowPublish) {
    throw new Error("GATE_DISABLED:GTM_ALLOW_PUBLISH");
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
}

function versionPath(
  accountId: string,
  containerId: string,
  containerVersionId: string,
): string {
  return `accounts/${accountId}/containers/${containerId}/versions/${containerVersionId}`;
}

async function publishPlan(
  client: any,
  stage: string,
  accountId: string,
  containerId: string,
  containerVersionId: string,
): Promise<PublishPlan> {
  const targetPath = versionPath(accountId, containerId, containerVersionId);
  const [targetResponse, liveResponse] = await Promise.all([
    client.accounts.containers.versions.get({ path: targetPath }),
    client.accounts.containers.versions.live({
      parent: `accounts/${accountId}/containers/${containerId}`,
    }),
  ]);
  const target = targetResponse.data || {};
  const live = liveResponse.data || {};

  if (target.containerVersionId !== containerVersionId) {
    throw new Error("TARGET_VERSION_ID_MISMATCH");
  }
  if (!target.fingerprint) {
    throw new Error("TARGET_VERSION_FINGERPRINT_MISSING");
  }
  if (live.containerVersionId === containerVersionId) {
    throw new Error("TARGET_VERSION_ALREADY_LIVE");
  }

  return {
    operation_hash_version: 1,
    stage,
    action: "publish",
    accountId,
    containerId,
    containerVersionId,
    targetPath,
    targetFingerprint: String(target.fingerprint),
    targetName: typeof target.name === "string" ? target.name : null,
    targetDescription:
      typeof target.description === "string" ? target.description : null,
    liveVersionId:
      typeof live.containerVersionId === "string"
        ? live.containerVersionId
        : null,
    liveFingerprint:
      typeof live.fingerprint === "string" ? live.fingerprint : null,
  };
}

function publishScope(
  stage: string,
  accountId: string,
  containerId: string,
): ConfirmationScope {
  return {
    stage,
    accountIds: [accountId],
    containerIds: [containerId],
    workspaceIds: [],
    operationCount: 1,
  };
}

export const protectedPublishActions = (
  server: McpServer,
  { props, env }: McpAgentToolParamsModel,
): void => {
  server.tool(
    "gtm_publish_preflight",
    "Validate a GTM container version publication and snapshot both target and current live versions. No publication is performed.",
    PublishInputSchema,
    async ({ stage, accountId, containerId, containerVersionId }: PublishInput) => {
      log(`Running GTM publish preflight for stage '${stage}'`);
      try {
        const config = getSafetyConfig(env);
        validatePublishScope(config, accountId, containerId);
        const client = await getTagManagerClient(props);
        const plan = await publishPlan(
          client,
          stage,
          accountId,
          containerId,
          containerVersionId,
        );
        const operationHash = await sha256Hex(plan);
        const scope = publishScope(stage, accountId, containerId);
        const receipt = await createConfirmation(
          config,
          "PUBLISH",
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
                  action: "publish",
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  normalized_plan: plan,
                  required_confirmation: receipt.token,
                  confirmation_expires_at: receipt.expiresAt,
                  required_human_confirmation: `PUBLICAR VERSÃO GTM ${containerVersionId} | HASH ${operationHash}`,
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
        return structuredError("PUBLISH_PREFLIGHT_FAILED", errorMessage(error), {
          stage,
          containerVersionId,
          executed: false,
          publication_dispatched: false,
        });
      }
    },
  );

  server.tool(
    "gtm_publish_execute",
    "Publish the exact GTM container version validated by gtm_publish_preflight. Requires the separate signed PUBLISH confirmation.",
    PublishExecuteInputSchema,
    async ({
      stage,
      accountId,
      containerId,
      containerVersionId,
      confirmation,
    }: PublishExecuteInput) => {
      log(`Running GTM publish execution for stage '${stage}'`);
      try {
        const config = getSafetyConfig(env);
        validatePublishScope(config, accountId, containerId);
        const client = await getTagManagerClient(props);
        const plan = await publishPlan(
          client,
          stage,
          accountId,
          containerId,
          containerVersionId,
        );
        const operationHash = await sha256Hex(plan);
        const verifiedConfirmation = await verifyConfirmation(
          config,
          confirmation,
          "PUBLISH",
          operationHash,
          stage,
        );
        registerConfirmationBeforeApiCall(verifiedConfirmation.fingerprint);

        const response = await client.accounts.containers.versions.publish({
          path: plan.targetPath,
          fingerprint: plan.targetFingerprint,
        });

        let verification: Record<string, unknown>;
        try {
          const liveResponse = await client.accounts.containers.versions.live({
            parent: `accounts/${accountId}/containers/${containerId}`,
          });
          verification = {
            performed: true,
            live_version_id: liveResponse.data?.containerVersionId || null,
            expected_live_version_id: containerVersionId,
            verified:
              liveResponse.data?.containerVersionId === containerVersionId,
            live_fingerprint: liveResponse.data?.fingerprint || null,
          };
        } catch (error) {
          verification = {
            performed: true,
            verified: false,
            warning: errorMessage(error),
          };
        }

        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  mode: "PUBLISH",
                  validation_status: "PRIOR_VALIDATION_VERIFIED",
                  execution_attempted: true,
                  executed: true,
                  execution_status:
                    verification.verified === true
                      ? "SUCCEEDED"
                      : "SUCCEEDED_WITH_VERIFICATION_WARNINGS",
                  confirmation_verified: true,
                  confirmation_registered_before_api_call: true,
                  confirmation_token_fingerprint:
                    verifiedConfirmation.fingerprint,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  published_container_version_id: containerVersionId,
                  previous_live_version_id: plan.liveVersionId,
                  response: response.data,
                  post_publication_verification: verification,
                  errors: [],
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        return structuredError("PUBLISH_EXECUTION_BLOCKED", errorMessage(error), {
          stage,
          containerVersionId,
          mode: "PUBLISH",
          execution_attempted: false,
          executed: false,
          execution_status: "BLOCKED_BEFORE_API_CALL",
        });
      }
    },
  );
};
