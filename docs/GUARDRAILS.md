# ChiefMind Guardrails

This policy defines the boundaries within which ChiefMind operates. A safety failure must stop the action, produce a visible log, and preserve evidence for diagnosis.

## Core rules

1. **Human approval is mandatory for external actions.** Only an artifact explicitly moved from `Pending_Approval/` to `Approved/` authorizes sending or publishing.
2. **The approved draft is sacred.** Execution sends the exact stored `draft_body`; it never asks an LLM to alter it.
3. **Identity and scope are explicit.** Recipient, subject, type, and action ID must be valid; missing execution fields are not inferred.
4. **Duplicate execution is forbidden.** The watcher checks and reserves each `action_id` in `Logs/execution_receipts.json`.
5. **Configuration is centralized.** Paths and constants come from `scripts/config.py`.
6. **Failure is visible.** Invalid/erroring artifacts go to `Failed/`; tracebacks and events go to `Logs/`.
7. **Data remains local.** Workflow state and credentials stay on the operator's machine.
8. **Least automation is the default.** `AUTO_LINKEDIN_POSTS=false` and external actions require approval.

## The system will not

- Execute an external action directly from intake, plans, or pending approval.
- Change approved content at execution time.
- Execute without an `action_id` or execute a reserved/completed ID twice.
- Silently ignore malformed YAML, unavailable APIs, authentication failures, or file errors.
- Put secrets in workflow files, logs, source control, or dashboard responses.
- Lower safety thresholds or remove receipts automatically.
- Poll Gmail faster than the 60-second minimum.
- Use the development MCP Gmail tool as the production sender.

## Approval matrix

| Action | May prepare without approval | Execution rule |
|---|---:|---|
| Read/classify email | Yes | No external effect |
| Create plan | Yes | No external effect |
| Draft email | Yes | Human reviews recipient, subject, and complete body |
| Send email | No | Exact approved artifact only |
| Draft LinkedIn post | Yes | Human reviews complete content |
| Publish LinkedIn post | No | Approval plus explicit feature enablement |
| Manual plan | Yes | Human performs the action |

Reject when intent, recipient, factual basis, or consequence is unclear. Escalate legal commitments, financial transfers, credentials, security incidents, health advice, harassment, regulated data, and irreversible actions.

## Enforced risk thresholds

The approval watcher applies fail-closed limits. Defaults are supplied by `config.py` and may be reduced through environment configuration.

| Limit | Variable | Default | Window |
|---|---|---:|---|
| Email sends | `MAX_EMAIL_SENDS_PER_HOUR` | 10 | Rolling hour |
| All external actions | `MAX_EXTERNAL_ACTIONS_PER_DAY` | 50 | UTC day |
| LinkedIn posts | `MAX_LINKEDIN_POSTS_PER_DAY` | 3 | UTC day |
| Gmail polling | `GMAIL_POLL_INTERVAL` | 120 seconds | Minimum 60 seconds |

Reservations, successes, and uncertain/failed attempts count toward limits. If an API response is ambiguous, ChiefMind assumes the action might have happened rather than risking a duplicate. Raising limits requires an explicit operational review and test evidence.

## Idempotency policy

Execution order is fixed:

1. Validate the artifact.
2. Reject a seen `action_id`.
3. Check volume limits.
4. Reserve the ID atomically.
5. Call the external service once.
6. Record the terminal state and route the file.

Never edit `execution_receipts.json` while the watcher runs. For recovery, stop the watcher, verify provider state, back up the ledger, document the decision, and make the smallest correction.

## Errors and alerts

API calls use bounded retries. Polling loops log per-cycle errors and continue; `validate_config()` prevents startup when configuration is unsafe.

Human investigation is required for:

- three consecutive provider failures;
- any artifact entering `Failed/`;
- a receipt left `reserved` after restart;
- a rate-limit rejection or duplicate attempt;
- OAuth refresh failure;
- repeated dashboard 403 responses;
- repeated daemon restarts or missing activity;
- any difference between sent and approved content.

For a suspected duplicate or content mismatch, stop the approval watcher first, preserve artifacts/logs, and check provider state before re-running anything.

## Credentials and access

- Git-ignore and permission-restrict `credentials/credentials.json`, `credentials/token.json`, and `scripts/.env`.
- Use only necessary OAuth scopes and rotate secrets after suspected exposure.
- Bind the dashboard to localhost by default. Public access requires TLS, strong authentication, restricted CORS, and host-level controls.
- Keep the approval token in browser `sessionStorage`, not persistent `localStorage`.
- Never log tokens, passwords, API keys, or credential objects.

## Common mistakes are policy violations

- Re-calling the LLM after approval changes what the human authorized.
- Hardcoded paths bypass centralized validation.
- Missing `action_id` checks can duplicate external actions.
- Committed credentials expose accounts.
- Missing watcher error handling creates invisible downtime.
- Gmail polling below 60 seconds wastes quota and increases throttling risk.
- Production use of the MCP sender bypasses approval receipts, routing, and auditing.

## Adding a new action type

Before enabling it: define an approval schema, validation, unique ID, atomic reservation, terminal receipt, rate limits, failure routing, tests, recovery documentation, and a disabled-by-default feature flag.

