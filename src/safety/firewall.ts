import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { getSafetyConfig } from "./config";
import { structuredError } from "./errors";

const READ_ONLY_ACTIONS = new Set([
  "get",
  "list",
  "live",
  "latest",
  "lookup",
  "snippet",
  "getStatus",
  "quickPreview",
  "entities",
]);

const PROTECTED_TOOL_PREFIXES = [
  "gtm_safety_",
  "gtm_protected_",
  "gtm_create_version_",
  "gtm_publish_",
];

function isProtectedTool(toolName: string): boolean {
  return PROTECTED_TOOL_PREFIXES.some((prefix) => toolName.startsWith(prefix));
}

function actionFromInput(input: unknown): string | null {
  if (!input || typeof input !== "object") {
    return null;
  }
  const action = (input as Record<string, unknown>).action;
  return typeof action === "string" ? action : null;
}

export function installLegacyMutationFirewall(
  server: McpServer,
  env: Env,
): void {
  const originalTool = server.tool.bind(server) as (
    ...args: unknown[]
  ) => unknown;
  const serverWithMutableTool = server as unknown as {
    tool: (...args: unknown[]) => unknown;
  };

  serverWithMutableTool.tool = (...registrationArgs: unknown[]): unknown => {
    const toolName = String(registrationArgs[0] || "unknown_tool");
    const handlerIndex = [...registrationArgs]
      .map((value, index) => ({ value, index }))
      .reverse()
      .find(({ value }) => typeof value === "function")?.index;

    if (handlerIndex === undefined || isProtectedTool(toolName)) {
      return originalTool(...registrationArgs);
    }

    const originalHandler = registrationArgs[handlerIndex] as (
      ...handlerArgs: unknown[]
    ) => unknown;

    registrationArgs[handlerIndex] = async (
      ...handlerArgs: unknown[]
    ): Promise<unknown> => {
      const action = actionFromInput(handlerArgs[0]);
      if (action && !READ_ONLY_ACTIONS.has(action)) {
        const config = getSafetyConfig(env);
        if (!config.allowUnsafeLegacyMutations) {
          return structuredError(
            "LEGACY_MUTATION_BLOCKED",
            `Direct mutation action '${action}' is disabled for legacy tool '${toolName}'.`,
            {
              tool: toolName,
              action,
              blocked_before_google_api_call: true,
              required_tool_family: "gtm_protected_*",
              emergency_bypass_gate: "GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS",
            },
          );
        }
      }
      return originalHandler(...handlerArgs);
    };

    return originalTool(...registrationArgs);
  };
}
