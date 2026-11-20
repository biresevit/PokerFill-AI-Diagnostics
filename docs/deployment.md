# Azure deployment

The live service uses Azure App Service for Linux, one Python process and Azure SQL history. The public `/demo` route is a synthetic interface; private investigation endpoints require the server broker's bearer token before request-body validation. HTTPS is required in production.

## Provision

The `infra` templates describe the App Service plan, Python app, runtime settings, system-assigned identity and a container-scoped Blob Data Reader assignment. Supply existing database and diagnostic-storage resources. The SQL runtime account must be created separately by a database administrator; no administrator credential is used by the service.

Apply [the history schema](../sql/001_history.sql) once. Grant the runtime account CRUD on `diagnostics.Investigation` and SELECT only on the operational tables/views required by the integration. The adapter enforces fixed SELECTs independently of those database permissions. Never grant operational writes or DDL to the runtime account.

Configure these values in Azure application settings, using protected deployment parameters or Azure's settings interface:

| Setting | Purpose |
|---|---|
| `OPENAI_API_KEY` | Model credential, server-side only |
| `POKERFILL_DIAGNOSTICS_TOKEN` | Random server-broker token, at least 32 characters |
| `POKERFILL_DIAGNOSTICS_MODE=live` | Require real evidence and the configured model |
| `POKERFILL_DIAGNOSTICS_MODEL` | Configurable model identifier |
| `POKERFILL_HISTORY_BACKEND=azure-sql` | Durable cloud history |
| `BACKOFFICE_SQL_SERVER`, `BACKOFFICE_SQL_DATABASE` | Existing Azure SQL endpoint/database |
| `BACKOFFICE_SQL_USER`, `BACKOFFICE_SQL_PASSWORD` | Restricted runtime account |
| `POKERFILL_LOG_STORAGE_URL` | Blob account URL, with no key or SAS query |
| `POKERFILL_DIAGNOSTICS_COLLECT_LOGS=1` | Wait for broker-requested log collections |
| `SCM_DO_BUILD_DURING_DEPLOYMENT=true` | Install Python dependencies using Oryx |

The startup command is `bash startup.sh`. It sets source paths and starts exactly one Uvicorn worker. Grant the app's managed identity **Storage Blob Data Reader** on the `machine-diagnostics` container, not the whole subscription. Cloud log reads then require no storage account key.

## Package and release

```bash
python scripts/vendor_backoffice.py
python scripts/deploy_azure.py --group YOUR_GROUP --app YOUR_APP
```

The helper packages reviewed source, submits an asynchronous deployment and waits for `/health` to serve the expected Git revision. This avoids mistaking a deployment-request gateway timeout for a failed background build. It requires a logged-in Azure CLI; on Windows, `AZURE_CLI_PYTHON` can point to a Python environment containing `azure.cli` if `az` is not on PATH.

The package uses an explicit source allowlist. It excludes `.env` files, local databases, credentials, operator artifacts and screenshots. Oryx installs `requirements.txt`; the web assets and verified integration helpers are included. Do not deploy from a dirty checkout containing unreviewed source changes.

The manually triggered **Deploy Azure** workflow uses GitHub OIDC. Configure these repository/environment variables, with a federated identity restricted to this repository's `production` environment:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_APP_NAME`

Assign the deployment identity Website Contributor on the target web app. The workflow runs only from `main`. It does not set or print application secrets, and Azure credentials are not stored in GitHub.

## Connect Backoffice

Set `POKERFILL_DIAGNOSTICS_URL` in the Backoffice API to the app's HTTPS origin and set its `POKERFILL_DIAGNOSTICS_TOKEN` to the matching server token. The browser continues to use authenticated same-origin Backoffice routes. Do not point browser JavaScript directly at the private service API or embed its token.

The API contract retains the original investigation IDs, scope fields, private history listing and follow-up behavior. The original Backoffice deployment supplies Microsoft authentication, membership checks and the audited optional log-collection preflight.

## Migrate local history

Back up the original SQLite history and wait until local investigations are idle. Import rows into `diagnostics.Investigation` with bound parameters, preserving IDs, parent/root links, turn indices, results and timestamps. Map a local preview identity to a verified active operator membership only when its owner is known. Preserve customer and site scope. Unknown owners must not be guessed or assigned to another user.

Keep the backup private. Do not include it in a deployment ZIP, image, Git commit or screenshot. Validate thread counts and owner isolation after import before switching the broker and stopping the local worker.

## Verify

1. `/health` reports readiness without returning identities or configuration.
2. Unauthenticated investigation/history requests return 401.
3. The public demo works without a model key in the browser and makes no live API calls.
4. An authorized live question returns a report with valid evidence IDs and recorded model usage.
5. A follow-up reloads its thread and preserves ownership; another user/site cannot read it.
6. Log tools can read scoped archives through the app's managed identity and report coverage gaps.
7. Restart the app while idle and verify that completed history remains in Azure SQL.

History is durable, but in-flight work is not resumed after a restart. Do not scale to multiple replicas without adding distributed job coordination. If a release fails, redeploy the previous reviewed source ZIP; no operational database migration or poker-worker restart is required for an agent-only release.
