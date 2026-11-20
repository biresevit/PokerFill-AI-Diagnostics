# Design decisions and limits

## Separate the diagnosis from operational control

The server-authenticated API accepts a scope derived by the Backoffice after Microsoft sign-in and membership checks. The browser does not receive the service token. Each evidence tool independently checks that the site belongs to the supplied customer. History ownership hashes the provider, user, customer and site together.

The model can call only fixed diagnostic tools. Database object names and SQL templates live in code; values are bound parameters. The operational evidence adapter rejects writes and procedure execution. Its SQL identity can read the required operational objects and write only the dedicated history table. It has no operational update grants.

An operator's Backoffice broker can queue bounded log collection through the existing audited machine-log protocol. That deterministic preflight is separate from the LLM. It cannot log accounts in or change poker play.

## Investigation flow

1. Authenticate the server broker and validate the request shape.
2. Load the private thread, enforce latest-turn ownership and reserve an idempotent request ID.
3. Wait briefly for pending diagnostic archives when collection is enabled.
4. Establish a schedule/health/lobby baseline, or load bounded context for a follow-up.
5. Let the model choose typed evidence tools within its budget.
6. Validate the structured report and the evidence IDs used by every finding.
7. Persist the answer, usage and evidence; show a visible error if no diagnosis can be produced.

The LangGraph state carries messages and round count. Prior diagnoses are treated as historical dialogue rather than current operational evidence. Context keeps the original question and answer plus recent exchanges, bounded to 16,000 characters. The full thread remains readable, with at most twenty questions.

## Evidence semantics matter

| Evidence | Interpretation |
|---|---|
| Ring count | Confirmed seated entries, rather than every pending command |
| SNG/MTT count | Registration commitments and live participation have different meanings |
| Cached wallet | An observation with an age and state, not a guarantee of spendable funds |
| Missing wallet | Unknown, not zero and not proof of insufficient funds |
| Native balance units | Not comparable with fiat buy-ins without a verified conversion |
| `worker_restarted` | Explains a command cancellation; does not identify the restart trigger |
| No log match | Does not establish absence when archives or results are bounded |
| Reconstructed schedule history | Uses current plans; does not reconstruct every historical configuration edit |

Log parsing emits known operational codes, timestamps, coverage and numeric exit codes. Raw log messages, account identities, credentials, hand data and arbitrary exception text are not sent to the model. Explicit site tags and lifecycle site IDs are filtered on shared PCs. Untagged machine-wide events retain unconfirmed site attribution.

New collectors record the PC IANA timezone. Worker timestamps can then be converted to UTC; ambiguous DST times stay unverified. Older archives without timezone metadata keep an explicit clock uncertainty. An incident filter narrows UTC events to one hour either side; unknown local clocks use adjacent dates. File metadata can skip archives that precede the requested window, with that omission reported.

## Bounds and failure behavior

- One service process, ten queued jobs, two pending requests and ten requests per hour per owner/site.
- At most three model/tool rounds and twelve evidence calls per investigation.
- A four-minute investigation budget checked before new steps; in-flight network work can finish later.
- Aggregate estimated token reservation of 60,000 plus actual provider-usage checks. Estimates are not a billing hard limit.
- At most 24 model-visible list items and bounded text fields; oversized evidence becomes an explicit gap.
- Ninety-day thread retention based on last activity. Service restarts mark interrupted jobs failed.
- Provider quota, throttling and authentication failures have distinct safe messages. Provider bodies and credentials are not returned.

The Azure deployment uses one App Service instance and one Uvicorn worker. Its queue is in memory; Azure SQL makes history durable but does not turn the queue into a distributed job system. Deploy when investigations are idle. Scaling to multiple replicas would require distributed job ownership, coordination of limits and a restart-safe queue.

The live adapter uses pinned Backoffice domain helpers, checked against a file-hash manifest. Updating that integration is an explicit source change, not an automatic pull of mutable upstream code.

## Public demo boundary

The public interface uses synthetic, prepared examples stored in the visitor's browser. It makes no investigation API requests and carries no server token or model key. Production operations remain behind the authenticated Backoffice broker. Screenshots are captured from this real interface with Playwright, using no customer data.
