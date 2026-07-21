/* eslint-disable */

declare namespace Cloudflare {
  interface Env {
    OAUTH_KV: KVNamespace;
    GOOGLE_CLIENT_ID: string;
    GOOGLE_CLIENT_SECRET: string;
    COOKIE_ENCRYPTION_KEY: string;
    HOSTED_DOMAIN: string;
    WORKER_HOST: string;
    GTM_MUTATIONS_ENABLED?: string;
    GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS?: string;
    GTM_ALLOW_CREATE?: string;
    GTM_ALLOW_UPDATE?: string;
    GTM_ALLOW_DELETE?: string;
    GTM_ALLOW_REVERT?: string;
    GTM_ALLOW_CREATE_VERSION?: string;
    GTM_ALLOW_SET_LATEST?: string;
    GTM_ALLOW_PUBLISH?: string;
    GTM_ALLOW_UNDELETE?: string;
    GTM_ALLOWED_ACCOUNT_IDS?: string;
    GTM_ALLOWED_CONTAINER_IDS?: string;
    GTM_ALLOWED_WORKSPACE_IDS?: string;
    GTM_MAX_OPERATIONS_PER_REQUEST?: string;
    GTM_CONFIRMATION_TTL_SECONDS?: string;
    GTM_CONFIRMATION_SECRET?: string;
    MCP_OBJECT: DurableObjectNamespace<import("./src/index").GoogleTagManagerMCPServer>;
  }
}
interface Env extends Cloudflare.Env {}
