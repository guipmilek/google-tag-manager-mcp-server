# Protected Google Tag Manager MCP Server

Google Tag Manager API v2 MCP with a fail-closed mutation workflow. The production deployment target is **Prefect Horizon**, matching the Google Ads and Google Analytics MCPs in this account.

## Production runtime

```text
Runtime: Python 3.12
Framework: FastMCP 3.2.0
Entrypoint: horizon_server.py:mcp
Credentials: GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64 -> ADC file in /tmp
API client: google-api-python-client, Tag Manager API v2
```

The earlier TypeScript/Cloudflare Worker implementation remains in the repository as an upstream/reference runtime. It is not the production Horizon entrypoint.

## Horizon tools

Read-only inventory:

```text
gtm_safety_status
gtm_list_accounts
gtm_list_containers
gtm_list_workspaces
gtm_get_workspace
gtm_get_workspace_status
gtm_list_tags
gtm_list_triggers
gtm_list_variables
gtm_list_folders
gtm_get_live_version
gtm_get_latest_version_header
gtm_get_version
```

Protected mutation workflow:

```text
gtm_protected_mutation_preflight
gtm_protected_mutation_execute
gtm_create_version_preflight
gtm_create_version_execute
gtm_publish_preflight
gtm_publish_execute
```

The Tag Manager API does not expose a generic `validate_only` mode for these Admin mutations. Preflight is therefore reported as `CONNECTOR_PREFLIGHT` and performs only API reads, schema checks, scope checks, gate checks, snapshotting, hashing and confirmation issuance.

## Safety model

- exact allowlists for GTM account, container and workspace IDs;
- all mutation gates default to `false`;
- maximum 10 operations per request;
- deterministic 32-character SHA-256 operation hash, version 3;
- HMAC-SHA256 confirmation receipts with nonce and expiry;
- receipt registration before the first Google API mutation call;
- process-local replay protection;
- optimistic concurrency through workspace/resource fingerprints;
- non-atomic `SEQUENTIAL_STOP_ON_FIRST_ERROR` batches;
- post-execution read-back verification;
- version creation separated from publication;
- publication requires a distinct `PUBLISH` receipt;
- non-latest publication requires a separate rollback gate.

A successful version creation deletes its source workspace, as defined by the GTM API. Publication is never implied by workspace mutation or version creation.

## Horizon configuration

Create the server from this repository with:

```text
Branch: main
Python: 3.12
Entrypoint: horizon_server.py:mcp
Dependencies: pyproject.toml
```

### Credentials

Use a service account dedicated to this MCP. Add its email as a user in the authorized GTM account/container and grant only the permissions required by the intended workflow.

Store its JSON key in Horizon as base64:

```env
GOOGLE_APPLICATION_CREDENTIALS_JSON_BASE64=<secret>
GOOGLE_PROJECT_ID=<google-cloud-project-id>
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id>
```

The entrypoint validates that the value is base64 JSON, writes it atomically with mode `0600` to:

```text
/tmp/google-tag-manager-adc.json
```

and sets `GOOGLE_APPLICATION_CREDENTIALS` before the API client is created.

### Optional FastMCP Google authentication

These variables protect access to the MCP endpoint itself. They are separate from ADC, which authorizes calls to the GTM API.

```env
GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_ID=<client-id>
GOOGLE_TAG_MANAGER_MCP_OAUTH_CLIENT_SECRET=<secret>
GOOGLE_TAG_MANAGER_MCP_BASE_URL=<public-horizon-server-url>
```

Configure both client variables together or omit both and rely on the authentication policy provided by Horizon/ChatGPT.

### Initial read-only state

```env
GTM_MUTATIONS_ENABLED=false
GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS=false

GTM_ALLOW_CREATE=false
GTM_ALLOW_UPDATE=false
GTM_ALLOW_DELETE=false
GTM_ALLOW_REVERT=false
GTM_ALLOW_CREATE_VERSION=false
GTM_ALLOW_SET_LATEST=false
GTM_ALLOW_PUBLISH=false
GTM_ALLOW_PUBLISH_NON_LATEST=false
GTM_ALLOW_UNDELETE=false

GTM_ALLOWED_ACCOUNT_IDS=
GTM_ALLOWED_CONTAINER_IDS=
GTM_ALLOWED_WORKSPACE_IDS=

GTM_MAX_OPERATIONS_PER_REQUEST=10
GTM_CONFIRMATION_TTL_SECONDS=900
GTM_CONFIRMATION_SECRET=<independent-secret-at-least-32-bytes>
```

Start with mutations disabled. Use the read tools to discover the numeric IDs, then populate the three allowlists. Enable only the single action gate required for a validated stage and return it to `false` after execution.

## Human approval commands

Workspace changes and version creation:

```text
EXECUTAR ETAPA <NOME> | HASH <OPERATION_HASH>
```

Publication:

```text
PUBLICAR VERSÃO GTM <VERSION_ID> | HASH <OPERATION_HASH>
```

The human-readable command is not the signed receipt. The signed receipt remains in the MCP conversation context and must match the re-normalized operation exactly.

## Local validation

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall gtm_mcp horizon_server.py
fastmcp inspect horizon_server.py:mcp
```

The existing TypeScript runtime can still be checked independently:

```bash
npm ci --ignore-scripts
npm test
npx eslint src/index.ts src/safety src/protectedMutation \
  src/tools/protectedMutationActions.ts \
  src/tools/protectedVersionActions.ts \
  src/tools/protectedPublishActions.ts \
  src/tools/safetyActions.ts
```

## Security notes

- Never commit service-account JSON, OAuth secrets, confirmation secrets or access tokens.
- `BEST_EFFORT_PROCESS_LOCAL` replay protection does not provide global single-use across replicas or redeploys.
- Keep publication, non-latest publication, delete, revert, set-latest and undelete gates disabled except during their own validated stages.
- Test mutation payloads in a non-production GTM account before using them in a production container.
