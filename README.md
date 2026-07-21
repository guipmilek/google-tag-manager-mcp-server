# Protected Google Tag Manager MCP Server

Remote MCP server for the Google Tag Manager API, with Google OAuth and a protected mutation workflow designed for production containers.

This fork keeps the upstream read capabilities while blocking direct legacy writes by default. Workspace mutations, version creation, and publication are separated into explicit preflight and execution stages.

## Safety model

### Fail closed

All legacy tools remain available for reads, but mutation actions are blocked before a Google API call unless the emergency compatibility gate is explicitly enabled.

Examples of legacy actions blocked by default:

- `create`
- `update`
- `remove`
- `revert`
- `createVersion`
- `publish`
- `setLatest`
- `undelete`
- `sync`
- `resolveConflict`

Use the protected tools instead. Do not enable `GTM_ALLOW_UNSAFE_LEGACY_MUTATIONS` for normal operation.

### Protected workspace mutations

Supported resources in the first protected implementation:

- tag
- trigger
- variable
- folder

Supported actions:

- create
- update
- remove
- revert

Workflow:

1. `gtm_protected_mutation_preflight`
2. Review normalized operations, snapshots, hash, scope, and expiration.
3. Provide the exact human approval recorded by the operator.
4. `gtm_protected_mutation_execute` with the unchanged operations and signed confirmation.
5. Read-back verification is performed for completed operations.

Preflight performs Google API reads but does not mutate GTM. It captures workspace and resource fingerprints, validates the resource payload, enforces allowlists and gates, and checks duplicate names for creates.

GTM resource update endpoints use full-resource `PUT` semantics. Protected updates therefore read the current resource, remove output-only fields, merge the requested writable changes, validate the complete body, and bind that resulting body to the confirmation hash. Omitted writable fields are preserved.

Batches are intentionally non-atomic and use:

```text
SEQUENTIAL_STOP_ON_FIRST_ERROR
```

The maximum supported batch size is 10 operations.

### Separate version creation

Creating a container version does not publish it, but the GTM API removes the source workspace after successfully creating the version.

1. `gtm_create_version_preflight`
2. Review the workspace fingerprint, change count, conflicts, and the explicit `deletes_workspace` effect.
3. `gtm_create_version_execute`
4. Verify that the version is readable and that the source workspace is no longer readable.

The preflight reads the workspace and status, blocks unresolved merge conflicts and empty workspaces, snapshots the workspace fingerprint, and binds the signed confirmation to that exact state.

### Separate publication approval

Publication cannot be authorized by a workspace-mutation confirmation or by a create-version confirmation.

1. `gtm_publish_preflight`
2. Review the target version, current live version, and latest version snapshots.
3. Approve with:

```text
PUBLICAR VERSÃO GTM <VERSION_ID> | HASH <OPERATION_HASH>
```

4. `gtm_publish_execute`
5. The live version is read back and compared to the requested version.

By default, only the current latest container version may be published. Publishing an older version is blocked unless the dedicated `GTM_ALLOW_PUBLISH_NON_LATEST` gate is explicitly enabled for a separate rollback stage.

## Protected tools

```text
gtm_safety_status

gtm_protected_mutation_preflight
gtm_protected_mutation_execute

gtm_create_version_preflight
gtm_create_version_execute

gtm_publish_preflight
gtm_publish_execute
```

`gtm_safety_status` reports gates, allowlists, limits, and confirmation configuration without exposing secret values.

## Environment variables

### Required OAuth configuration

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
COOKIE_ENCRYPTION_KEY
WORKER_HOST
```

Optional hosted-domain restriction:

```text
HOSTED_DOMAIN
```

### Protected mutation configuration

```text
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
GTM_CONFIRMATION_SECRET=<secret>
```

All allowlists are comma-separated exact IDs. Empty allowlists fail closed.

Recommended initial production configuration:

```text
GTM_MUTATIONS_ENABLED=true
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

GTM_ALLOWED_ACCOUNT_IDS=<authorized-account-id>
GTM_ALLOWED_CONTAINER_IDS=<authorized-container-id>
GTM_ALLOWED_WORKSPACE_IDS=<authorized-workspace-id>

GTM_MAX_OPERATIONS_PER_REQUEST=10
GTM_CONFIRMATION_TTL_SECONDS=900
GTM_CONFIRMATION_SECRET=<independent-random-secret>
```

Enable only the gate required for the current validated stage. Return sensitive gates to `false` after use.

## Confirmation receipts

Confirmations use HMAC-SHA256 and are bound to:

- operation hash
- confirmation verb
- stage name
- account/container/workspace scope
- operation count
- issue time
- expiration time
- random nonce

The current replay cache is process-local and therefore reported as:

```text
BEST_EFFORT_PROCESS_LOCAL
```

It prevents reuse within the active Worker instance. For global single-use enforcement across instances, persist receipt fingerprints in a dedicated Durable Object or KV-backed transactional store before production publication automation is considered complete.

## OAuth connection

The server exposes remote MCP endpoints with Google OAuth:

```text
/mcp
/sse
```

Example remote configuration:

```json
{
  "mcpServers": {
    "gtm-mcp-server": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://your-worker.example.com/mcp"
      ]
    }
  }
}
```

## Development

```bash
npm ci
npm run build
npm test
npm run lint
```

Run locally with Wrangler:

```bash
npm run dev
```

Deploy:

```bash
npm run deploy
```

## Security notes

- Never commit OAuth client secrets, confirmation secrets, refresh tokens, or access tokens.
- Keep delete, revert, set-latest, undelete, publish, and non-latest publication gates disabled except for a dedicated validated stage.
- A successful preflight is connector validation, not a Google Tag Manager API dry run.
- Creating or editing workspace entities does not publish them.
- Creating a container version deletes the source workspace and must be treated as a distinct destructive transition.
- Publication always requires a separate target-version snapshot and confirmation.
- Publishing a non-latest version is a rollback operation and requires its own gate and validation.
- Do not use the unsafe legacy bypass as a permanent migration path.
