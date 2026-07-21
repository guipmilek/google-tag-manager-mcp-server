import { SafetyConfig } from "./types";

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

function envValue(env: Env, key: string): string | undefined {
  const value = (env as unknown as Record<string, unknown>)[key];
  return typeof value === "string" ? value.trim() : undefined;
}

function envBoolean(env: Env, key: string, fallback = false): boolean {
  const value = envValue(env, key);
  if (value === undefined || value === "") {
    return fallback;
  }
  return TRUE_VALUES.has(value.toLowerCase());
}

function envInteger(
  env: Env,
  key: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const value = envValue(env, key);
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(maximum, Math.max(minimum, parsed));
}

function envSet(env: Env, key: string): Set<string> {
  const value = envValue(env, key);
  if (!value) {
    return new Set();
  }
  return new Set(
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export function getSafetyConfig(env: Env): SafetyConfig {
  return {
    mutationsEnabled: envBoolean(env, "GTM_MUTATIONS_ENABLED", false),
    allowUnsafeLegacyMutations: envBoolean(
      env,
      "GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS",
      false,
    ),
    allowCreate: envBoolean(env, "GTM_ALLOW_CREATE", false),
    allowUpdate: envBoolean(env, "GTM_ALLOW_UPDATE", false),
    allowDelete: envBoolean(env, "GTM_ALLOW_DELETE", false),
    allowRevert: envBoolean(env, "GTM_ALLOW_REVERT", false),
    allowCreateVersion: envBoolean(env, "GTM_ALLOW_CREATE_VERSION", false),
    allowSetLatest: envBoolean(env, "GTM_ALLOW_SET_LATEST", false),
    allowPublish: envBoolean(env, "GTM_ALLOW_PUBLISH", false),
    allowUndelete: envBoolean(env, "GTM_ALLOW_UNDELETE", false),
    allowedAccountIds: envSet(env, "GTM_ALLOWED_ACCOUNT_IDS"),
    allowedContainerIds: envSet(env, "GTM_ALLOWED_CONTAINER_IDS"),
    allowedWorkspaceIds: envSet(env, "GTM_ALLOWED_WORKSPACE_IDS"),
    maxOperationsPerRequest: envInteger(
      env,
      "GTM_MAX_OPERATIONS_PER_REQUEST",
      10,
      1,
      10,
    ),
    confirmationTtlSeconds: envInteger(
      env,
      "GTM_CONFIRMATION_TTL_SECONDS",
      900,
      60,
      3600,
    ),
    confirmationSecret: envValue(env, "GTM_CONFIRMATION_SECRET") || null,
  };
}

export function configuredAllowlist(set: Set<string>): string[] {
  return [...set].sort();
}
