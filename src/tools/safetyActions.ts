import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { McpAgentToolParamsModel } from "../models/McpAgentModel";
import {
  configuredAllowlist,
  getSafetyConfig,
  replayProtectionDescription,
} from "../safety";

export const safetyActions = (
  server: McpServer,
  { env }: McpAgentToolParamsModel,
): void => {
  server.tool(
    "gtm_safety_status",
    "Return the protected mutation safety configuration without exposing any secret values.",
    async () => {
      const config = getSafetyConfig(env);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                protected_mode: true,
                legacy_mutation_firewall: {
                  enabled: !config.allowUnsafeLegacyMutations,
                  unsafe_bypass_enabled: config.allowUnsafeLegacyMutations,
                  unsafe_bypass_gate: "GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS",
                },
                protected_workspace_resources: [
                  "tag",
                  "trigger",
                  "variable",
                  "folder",
                ],
                protected_workspace_actions: [
                  "create",
                  "update",
                  "remove",
                  "revert",
                ],
                protected_version_creation: true,
                protected_publication: true,
                gates: {
                  mutations_enabled: config.mutationsEnabled,
                  allow_create: config.allowCreate,
                  allow_update: config.allowUpdate,
                  allow_delete: config.allowDelete,
                  allow_revert: config.allowRevert,
                  allow_create_version: config.allowCreateVersion,
                  allow_set_latest: config.allowSetLatest,
                  allow_publish: config.allowPublish,
                  allow_undelete: config.allowUndelete,
                },
                allowlists: {
                  account_ids: configuredAllowlist(config.allowedAccountIds),
                  container_ids: configuredAllowlist(
                    config.allowedContainerIds,
                  ),
                  workspace_ids: configuredAllowlist(
                    config.allowedWorkspaceIds,
                  ),
                },
                max_operations_per_request: config.maxOperationsPerRequest,
                confirmation: {
                  secret_configured: Boolean(config.confirmationSecret),
                  ttl_seconds: config.confirmationTtlSeconds,
                  replay_protection: replayProtectionDescription(),
                  globally_single_use: false,
                },
                publication_requires_separate_confirmation: true,
                publication_confirmation_format:
                  "PUBLICAR VERSÃO GTM <VERSION_ID> | HASH <OPERATION_HASH>",
              },
              null,
              2,
            ),
          },
        ],
      };
    },
  );
};
