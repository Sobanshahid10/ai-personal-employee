# Day 5 — HITL Approval Watcher

## Purpose

The approval watcher is ChiefMind's execution boundary. It observes Markdown
artifacts that a human deliberately moves into `Approved/`, validates their
YAML frontmatter, prevents duplicate execution, performs the approved action,
writes an execution receipt and dated JSON audit events, then routes the
artifact to `Done/` or `Failed/`.

The execution agent never calls an LLM. For email actions it sends the stored
`draft_body` value directly.

## Files

```text
AI Personal employee/
├── Approved/                       # Human-approved input artifacts
├── Done/                           # Successfully executed artifacts
├── Failed/                         # Invalid or failed artifacts
├── Logs/
│   ├── execution_receipts.json     # Durable duplicate guard (runtime)
│   ├── YYYY-MM-DD.json             # Structured daily audit events (runtime)
│   └── agent.log                   # Rotating text diagnostics (runtime)
├── credentials/
│   └── token.json                  # Gmail OAuth token (private)
└── scripts/
    ├── approval_watcher.py         # Day 5 watcher and execution router
    ├── workflow_utils.py           # Safe YAML/JSON and atomic file helpers
    ├── authenticate_gmail.py       # Shared Gmail credential loader
    ├── config.py                   # All paths and runtime constants
    ├── test_approval_watcher.py    # Offline tests; never contacts Gmail
    └── fixtures/
        └── day5_approval_email.md  # Live test template
```

Runtime artifacts are excluded from Git because they may contain private
workflow information.

## Initialization

From `scripts/`:

```bash
uv sync
uv run python approval_watcher.py --init
```

This creates `Logs/execution_receipts.json` as an empty JSON object without
overwriting an existing receipt history.

## Offline Tests

Run:

```bash
uv run python -m unittest -v test_approval_watcher.py
```

The tests use a fake Gmail sender and verify:

- Exact `draft_body` delivery to the sender interface.
- Receipt creation and daily JSON audit events.
- Duplicate suppression before a second send.
- Missing-draft rejection and `Failed/` routing.
- SHA-256 integrity rejection.
- Plan completion without an external action.

## Live Email Test

1. Copy the template:

   ```bash
   cp fixtures/day5_approval_email.md /tmp/day5_approval_email.md
   ```

2. Edit `/tmp/day5_approval_email.md`. Replace
   `REPLACE_WITH_YOUR_EMAIL` with an address you control. Review every character
   of `subject` and `draft_body`.

3. Start the watcher in `scripts/`:

   ```bash
   uv run python approval_watcher.py
   ```

4. In another terminal, perform the human approval action:

   ```bash
   mv /tmp/day5_approval_email.md ../Approved/
   ```

5. Verify:

   ```bash
   ls ../Done/
   sed -n '1,240p' ../Logs/execution_receipts.json
   sed -n '1,300p' "../Logs/$(date -u +%F).json"
   ```

6. Confirm that the message arrived at the controlled recipient.

Moving the file is the approval event. Do not place a file in `Approved/`
until its recipient, subject, and exact body have been reviewed.

## One-Shot Mode

For schedulers or controlled tests:

```bash
uv run python approval_watcher.py --once
```

This processes existing `Approved/*.md` files and exits. Normal mode uses
`watchdog` and reacts within seconds.

## Approval Schemas

### Email

```yaml
---
action_id: email_unique_source_id
type: email_send
to: recipient@example.org
subject: "Re: Original subject"
draft_body: |
  Exact approved text.
draft_sha256: optional_64_character_sha256
created_at: "2026-07-29T09:00:00Z"
---
```

`type: email` is accepted as an alias for `email_send`.

### LinkedIn

```yaml
---
action_id: linkedin_unique_id
type: linkedin_post
created_at: "2026-07-29T09:00:00Z"
---
```

The watcher calls:

```text
python linkedin_poster.py --approval-file <artifact-path>
```

Until Day 7 supplies that script, LinkedIn artifacts fail safely and move to
`Failed/`.

### Plan or Manual Completion

```yaml
---
action_id: plan_unique_id
type: plan
created_at: "2026-07-29T09:00:00Z"
---
```

`plan` and `manual` perform no external action. Approval acknowledges completion
and routes the artifact to `Done/`.

## Duplicate and Crash Safety

`execution_receipts.json` is keyed by `action_id`. The watcher persists an
`executing` reservation before external I/O and changes it to `executed` after
success.

Any existing receipt causes later copies of the same action to be skipped.
This includes `executing` and `failed` receipts. If a process crashes during an
external request, ChiefMind cannot know with certainty whether the provider
accepted the action. Failing closed prevents a duplicate email or post.

An operator must investigate ambiguous or failed receipts before deliberately
removing one for a retry. Never remove a receipt merely to clear an error.

## Gmail Execution

The watcher loads the OAuth token through `authenticate_gmail.py`, creates one
Gmail API service lazily, and reuses it while the watcher runs. It creates an
RFC-compliant MIME email, base64url-encodes it, and calls:

```text
users.messages.send(userId="me", body={"raw": encoded_message})
```

The MCP server is not involved. The only body supplied to Gmail is the stored
`draft_body`; no model, template, or rewriting step runs during execution.

## Audit Events

Each `Logs/YYYY-MM-DD.json` file is a valid JSON array. Events contain:

- UTC timestamp
- Agent name
- Action ID and type
- Source artifact
- Status (`execution_started`, `executed`, `failed`, or `duplicate_skipped`)
- Non-secret provider result metadata
- Error and traceback for failures

Draft bodies, OAuth tokens, and credential values are intentionally excluded
from the logs.

## Failure Rules

- Invalid YAML or missing required fields: move to `Failed/`, log traceback.
- Missing `draft_body`: refuse to send, move to `Failed/`.
- Changed draft with mismatched `draft_sha256`: refuse to send.
- Gmail or LinkedIn error: record failed receipt, log traceback, move to
  `Failed/`.
- Successful execution: finalize receipt, log success, move to `Done/`.
- Duplicate receipt: do not execute; log warning and route duplicate artifact
  to `Done/`.

No failure is silently ignored.

## Extending the Router

To add an action type:

1. Add its name to `SUPPORTED_TYPES`.
2. Add strict field validation in `validate_approval()`.
3. Add one branch in `_execute_action()`.
4. Return non-secret provider metadata.
5. Add success, validation-failure, execution-failure, and duplicate tests.

Keep provider code isolated from parsing and receipt logic. Every new external
action must remain behind human approval and the same duplicate guard.
