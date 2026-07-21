import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { McpAgentToolParamsModel } from "../models/McpAgentModel";
import {
  classifyExecutionError,
  executeOne,
  verifyCompletedOperation,
} from "../protectedMutation/execution";
import { OperationSchema } from "../protectedMutation/model";
import { normalizeOperations } from "../protectedMutation/normalize";
import { confirmationScope, mutationHash } from "../protectedMutation/plan";
import {
  NormalizedMutationOperation,
  RequestedMutationOperation,
  createConfirmation,
  errorMessage,
  getSafetyConfig,
  registerConfirmationBeforeApiCall,
  replayProtectionDescription,
  structuredError,
  verifyConfirmation,
} from "../safety";
import { getTagManagerClient, log } from "../utils";

const PreflightInputSchema = {
  stage: z.string().min(1).max(100),
  operations: z.array(OperationSchema).min(1).max(10),
};

const ExecuteInputSchema = {
  ...PreflightInputSchema,
  confirmation: z.string().min(1),
};

type MutationPreflightInput = {
  stage: string;
  operations: RequestedMutationOperation[];
};

type MutationExecuteInput = MutationPreflightInput & {
  confirmation: string;
};

export const protectedMutationActions = (
  server: McpServer,
  { props, env }: McpAgentToolParamsModel,
): void => {
  server.tool(
    "gtm_protected_mutation_preflight",
    "Validate and snapshot protected GTM workspace mutations without changing GTM. Supports tag, trigger, variable and folder create/update/remove/revert operations.",
    PreflightInputSchema,
    async ({ stage, operations }: MutationPreflightInput) => {
      log(`Running protected GTM mutation preflight for stage '${stage}'`);
      try {
        const config = getSafetyConfig(env);
        const client = await getTagManagerClient(props);
        const normalized = await normalizeOperations(client, config, operations);
        const operationHash = await mutationHash(stage, normalized);
        const scope = confirmationScope(stage, normalized);
        const receipt = await createConfirmation(
          config,
          "EXECUTE",
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
                  atomic: false,
                  execution_strategy: "SEQUENTIAL_STOP_ON_FIRST_ERROR",
                  operation_count: normalized.length,
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  normalized_operations: normalized,
                  required_confirmation: receipt.token,
                  confirmation_expires_at: receipt.expiresAt,
                  required_human_confirmation: `EXECUTAR ETAPA ${stage} | HASH ${operationHash}`,
                  validation_receipt: {
                    expires_at: receipt.expiresAt,
                    replay_protection: replayProtectionDescription(),
                    globally_single_use: false,
                  },
                  operation_scope: scope,
                  verification: {
                    google_api_reads_performed: true,
                    workspace_snapshots_captured: true,
                    resource_snapshots_captured: true,
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
        return structuredError("PREFLIGHT_FAILED", errorMessage(error), {
          stage,
          mode: "VALIDATE_ONLY",
          executed: false,
          mutation_dispatched: false,
        });
      }
    },
  );

  server.tool(
    "gtm_protected_mutation_execute",
    "Execute an exactly matching GTM workspace mutation plan using the signed confirmation returned by gtm_protected_mutation_preflight.",
    ExecuteInputSchema,
    async ({ stage, operations, confirmation }: MutationExecuteInput) => {
      log(`Running protected GTM mutation execution for stage '${stage}'`);
      const completed: Array<{
        index: number;
        operation: NormalizedMutationOperation;
        resource_name: string;
      }> = [];

      try {
        const config = getSafetyConfig(env);
        const client = await getTagManagerClient(props);
        const normalized = await normalizeOperations(client, config, operations);
        const operationHash = await mutationHash(stage, normalized);
        const verifiedConfirmation = await verifyConfirmation(
          config,
          confirmation,
          "EXECUTE",
          operationHash,
          stage,
        );

        registerConfirmationBeforeApiCall(verifiedConfirmation.fingerprint);

        for (let index = 0; index < normalized.length; index += 1) {
          const operation = normalized[index];
          try {
            const result = await executeOne(client, operation);
            completed.push({
              index,
              operation,
              resource_name: result.resourceName,
            });
          } catch (error) {
            const classification = classifyExecutionError(error);
            const verification = [];
            for (const item of completed) {
              verification.push(
                await verifyCompletedOperation(
                  client,
                  item.operation,
                  item.resource_name,
                ),
              );
            }

            return structuredError(classification.errorType, errorMessage(error), {
              mode: "EXECUTE",
              validation_status: "PRIOR_VALIDATION_VERIFIED",
              execution_attempted: true,
              executed: completed.length > 0,
              execution_status: classification.executionMayHaveCompleted
                ? "UNKNOWN"
                : "FAILED",
              execution_may_have_completed:
                classification.executionMayHaveCompleted,
              confirmation_verified: true,
              confirmation_registered_before_api_call: true,
              confirmation_token_fingerprint:
                verifiedConfirmation.fingerprint,
              operation_hash: operationHash,
              operation_hash_version: 1,
              operation_count: normalized.length,
              operations_completed: completed.length,
              operation_failed_index: index,
              operations_not_attempted: normalized.length - index - 1,
              completed_resource_names: completed.map(
                (item) => item.resource_name,
              ),
              post_execution_verification: verification,
            });
          }
        }

        const verification = [];
        for (const item of completed) {
          verification.push(
            await verifyCompletedOperation(
              client,
              item.operation,
              item.resource_name,
            ),
          );
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
                  execution_status: verification.every(
                    (item) => item.verified === true,
                  )
                    ? "SUCCEEDED"
                    : "SUCCEEDED_WITH_VERIFICATION_WARNINGS",
                  confirmation_verified: true,
                  confirmation_registered_before_api_call: true,
                  confirmation_token_fingerprint:
                    verifiedConfirmation.fingerprint,
                  atomic: false,
                  execution_strategy: "SEQUENTIAL_STOP_ON_FIRST_ERROR",
                  operation_hash: operationHash,
                  operation_hash_version: 1,
                  operation_count: normalized.length,
                  operations_completed: completed.length,
                  response_resource_names: completed.map(
                    (item) => item.resource_name,
                  ),
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
        return structuredError("EXECUTION_BLOCKED", errorMessage(error), {
          stage,
          mode: "EXECUTE",
          execution_attempted: false,
          executed: false,
          execution_status: "BLOCKED_BEFORE_API_CALL",
          confirmation_registered_before_api_call: false,
          operations_completed: completed.length,
        });
      }
    },
  );
};
