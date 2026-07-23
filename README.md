# Google Tag Manager MCP Server

Google Tag Manager API v2 MCP deployed on Prefect Horizon. The Horizon runtime
uses a direct, one-call CRUD contract: write tools validate their scope and
payload, capture concurrency fingerprints, execute immediately unless
`dry_run=true`, and verify the result with API reads.

## Production runtime

```text
Runtime: Python 3.12
Framework: FastMCP 3.2.0
Entrypoint: horizon_server.py:mcp
Credentials: GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64
Contract: direct-crud-v1
```

The TypeScript/Cloudflare Worker implementation remains only as upstream
reference code. Its deploy workflow is manual so a push to `main` cannot
replace the Horizon runtime.

## Horizon tools

Inventory and schema tools:

```text
gtm_crud_status
gtm_list_mutable_resources
gtm_get_mutation_schema
gtm_list_accounts
gtm_list_containers
gtm_list_workspaces
gtm_get_workspace
gtm_get_workspace_status
gtm_get_resource
gtm_list_resources
gtm_list_tags
gtm_list_triggers
gtm_list_variables
gtm_list_folders
gtm_get_live_version
gtm_get_latest_version_header
gtm_get_version
```

Direct write tools:

```text
gtm_create_resource
gtm_update_resource
gtm_delete_resource
gtm_revert_resource
gtm_batch_operations
gtm_create_version
gtm_publish_version
```

Supported workspace resources are tags, triggers, variables, and folders.
Delete is idempotent: deleting an already absent resource succeeds with
`ALREADY_ABSENT`. Batches are non-atomic and stop at the first error. Version
creation is destructive because the GTM API deletes the source workspace after
success. Publishing is explicit and never implied by a workspace write.

There are no connector-level action gates, signed confirmations, approval
codes, prepare/execute pairs, or replay-token workflows. `dry_run=true`
performs schema, allowlist, snapshot, and precondition reads without sending a
Google mutation.

## Horizon configuration

```text
Branch: main
Python: 3.12
Entrypoint: horizon_server.py:mcp
Dependencies: pyproject.toml
```

Required Google API credentials:

```env
GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64=<base64-service-account-json>
GOOGLE_PROJECT_ID=<google-cloud-project-id>
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id>
```

Required mutation scope:

```env
GTM_ALLOWED_ACCOUNT_IDS=<comma-separated-numeric-ids>
GTM_ALLOWED_CONTAINER_IDS=<comma-separated-numeric-ids>
GTM_ALLOWED_WORKSPACE_IDS=<comma-separated-numeric-ids>
GTM_MAX_OPERATIONS_PER_REQUEST=10
```

Empty allowlists fail closed. Legacy variables such as
`GTM_MUTATIONS_ENABLED`, `GTM_ALLOW_DELETE`, and
`GTM_CONFIRMATION_SECRET` are intentionally ignored by the Horizon runtime.

Optional FastMCP endpoint authentication:

```env
GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_ID=<client-id>
GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_SECRET=<secret>
GOOGLE_TAG_MANAGER_MCP_BASE_URL=<public-horizon-server-url>
```

Endpoint authentication and Google API authorization remain separate.

## ChatGPT workspace setup

After redeploying, a ChatGPT workspace owner or admin must refresh the custom
MCP app so its current tool schemas and annotations are imported. Enable the
write actions in Workspace Settings → Apps → Action control. Where the
workspace permits it, choose **Never ask** for those actions. A
`workspace_policy_block` is enforced by ChatGPT and cannot be bypassed by the
MCP server.

## Local verification

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall gtm_mcp horizon_server.py
fastmcp inspect horizon_server.py:mcp
```

Use a dedicated non-production GTM account/container/workspace for live CRUD
tests. Never commit service-account JSON, OAuth secrets, or access tokens.
