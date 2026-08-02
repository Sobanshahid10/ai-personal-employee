# ChiefMind Deployment Playbook

This playbook describes how to operate ChiefMind as a local-first service on macOS, verify it, and recover it when something fails. Run all commands from the repository root.

## Operating model

ChiefMind uses the local filesystem as its workflow state. Agents exchange Markdown files with YAML frontmatter through `Needs_Action/`, `Pending_Approval/`, `Approved/`, and the other workflow folders. There is no database or remote queue to recover.

Every process imports paths and settings from `scripts/config.py`. Never place absolute project paths in application source. The only deployment-time path substitution is the repository root rendered into a service definition.

## 1. Production preflight

### Install dependencies

```bash
cd "/absolute/path/to/chiefmind"
uv sync --project scripts
uv run --project scripts playwright install chromium
cd mcp-servers/gmail-send && npm install && cd ../..
```

The MCP server is optional and development-only. The production approval watcher sends mail directly through the Gmail API.

### Configure secrets

```bash
cp scripts/.env.example scripts/.env
chmod 600 scripts/.env
```

Edit `scripts/.env`, set `GROQ_API_KEY`, and choose a strong `DASHBOARD_APPROVAL_TOKEN`. Keep `AUTO_LINKEDIN_POSTS=false` unless LinkedIn automation has been explicitly reviewed and approved.

Place the Google OAuth desktop client at `credentials/credentials.json`, then run:

```bash
uv run --project scripts python scripts/authenticate_gmail.py
```

This creates `credentials/token.json`. Both credential files and `scripts/.env` must remain excluded from Git.

### Validate before installing services

```bash
uv run --project scripts python scripts/main.py --check
uv run --project scripts python -m unittest discover -s scripts -p 'test_*.py'
```

Do not install daemons until both commands succeed. `validate_config()` intentionally fails with a clear message when required secrets or credentials are unavailable.

## 2. macOS launchd installation

`launchd` starts processes at login, restarts crashed watchers, and retains their output for diagnosis. The templates use `__VAULT_DIR__`; rendering substitutes the current absolute repository path without hardcoding it in application code.

```bash
mkdir -p "$HOME/Library/LaunchAgents"
sed "s#__VAULT_DIR__#$(pwd)#g" launchd/com.chiefmind.gmailwatcher.plist.template > "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"
sed "s#__VAULT_DIR__#$(pwd)#g" launchd/com.chiefmind.approvalwatcher.plist.template > "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"
sed "s#__VAULT_DIR__#$(pwd)#g" launchd/com.chiefmind.dashboard.plist.template > "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
```

If an older service is already loaded, unload it before bootstrapping its replacement.

### Verify, restart, and stop

```bash
launchctl print "gui/$(id -u)/com.chiefmind.gmailwatcher"
launchctl print "gui/$(id -u)/com.chiefmind.approvalwatcher"
launchctl print "gui/$(id -u)/com.chiefmind.dashboard"
curl --fail http://127.0.0.1:5000/api/stats
```

Restart:

```bash
launchctl kickstart -k "gui/$(id -u)/com.chiefmind.gmailwatcher"
launchctl kickstart -k "gui/$(id -u)/com.chiefmind.approvalwatcher"
launchctl kickstart -k "gui/$(id -u)/com.chiefmind.dashboard"
```

Stop and uninstall:

```bash
launchctl bootout "gui/$(id -u)/com.chiefmind.gmailwatcher"
launchctl bootout "gui/$(id -u)/com.chiefmind.approvalwatcher"
launchctl bootout "gui/$(id -u)/com.chiefmind.dashboard"
```

## 3. Linux systemd alternative

Render the templates in `systemd/`, inspect the resulting `User`, `WorkingDirectory`, and executable path, then install them:

```bash
sed "s#__VAULT_DIR__#$(pwd)#g" systemd/chiefmind-gmailwatcher.service.template > /tmp/chiefmind-gmailwatcher.service
sed "s#__VAULT_DIR__#$(pwd)#g" systemd/chiefmind-approvalwatcher.service.template > /tmp/chiefmind-approvalwatcher.service
sed "s#__VAULT_DIR__#$(pwd)#g" systemd/chiefmind-dashboard.service.template > /tmp/chiefmind-dashboard.service
sudo cp /tmp/chiefmind-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chiefmind-gmailwatcher chiefmind-approvalwatcher chiefmind-dashboard
```

```bash
systemctl status chiefmind-gmailwatcher chiefmind-approvalwatcher chiefmind-dashboard
journalctl -u chiefmind-gmailwatcher -n 100 --no-pager
sudo systemctl restart chiefmind-gmailwatcher chiefmind-approvalwatcher chiefmind-dashboard
sudo systemctl stop chiefmind-gmailwatcher chiefmind-approvalwatcher chiefmind-dashboard
```

## 4. Monitoring and logs

| Source | Purpose |
|---|---|
| `Logs/agent.log` | Human-readable events and tracebacks |
| `Logs/YYYY-MM-DD.json` | Structured audit events shown by the dashboard |
| `Logs/execution_receipts.json` | Authoritative idempotency ledger |
| `Logs/*.stdout.log` / `Logs/*.stderr.log` | launchd process output |
| `launchctl print ...` or `systemctl status ...` | Daemon state and exit information |

```bash
tail -n 200 Logs/agent.log
tail -n 100 Logs/gmailwatcher.stderr.log
tail -n 100 Logs/approvalwatcher.stderr.log
curl --fail http://127.0.0.1:5000/api/stats
```

Repeated authentication failures, files accumulating in `Failed/`, rate-limit refusals, or unexpected receipt changes require human review.

## 5. End-to-end demo

Use a harmless message from an address that can receive the reply. Include a unique marker such as `CHIEFMIND-DEMO-2026-08-03` in the subject.

### 1 — Send a test email

1. **Do:** Send the monitored account a simple question covered by `docs/KnowledgeBase.md`.
2. **See:** It appears unread and matches `GMAIL_QUERY`.
3. **If not:** Confirm the recipient and query. `is:unread` intentionally ignores read mail.

### 2 — Detect it

1. **Do:** Wait one `GMAIL_POLL_INTERVAL`, or, with the daemon stopped, run `uv run --project scripts python scripts/gmail_watcher.py --once`.
2. **See:** `Logs/agent.log` records the Gmail message ID.
3. **If not:** Check OAuth files/scopes, Gmail API enablement, query filters, and watcher stderr. Keep polling at 60 seconds or more.

### 3 — Confirm intake

1. **Do:** Look for `Needs_Action/email_<gmail_message_id>.md`.
2. **See:** Frontmatter contains IDs, sender, subject, received time, priority, and status; the body contains the message.
3. **If not:** Reasoning may already have consumed this transient file. Search `Plans/`, `Pending_Approval/`, and logs for its `action_id`.

### 4 — Run reasoning

1. **Do:** Let the watcher trigger it, or run `uv run --project scripts python scripts/reasoning_loop.py`.
2. **See:** Successful Groq, plan, and approval-artifact events.
3. **If not:** Verify `GROQ_API_KEY`, model, network, frontmatter, and the knowledge base.

### 5 — Confirm the plan

1. **Do:** Find the corresponding file in `Plans/`.
2. **See:** Source, priority, category, recommendation, and steps.
3. **If not:** Search `Failed/` and `Logs/agent.log` for parsing or LLM errors.

### 6 — Confirm pending approval

1. **Do:** Open the matching file in `Pending_Approval/`.
2. **See:** Exact `to`, `subject`, immutable `draft_body`, references, and `action_id`.
3. **If not:** It may be informational/manual. Review its plan; never invent approval to force execution.

### 7 — Open the dashboard

1. **Do:** Visit [http://127.0.0.1:5000](http://127.0.0.1:5000) and choose Pending Approval.
2. **See:** The item and complete detail modal.
3. **If not:** Call `/api/stats`, check dashboard stderr, and confirm the configured CORS origin.

### 8 — Approve the exact draft

1. **Do:** Review recipient, subject, and complete body, then click **Approve**. Enter the approval token if prompted.
2. **See:** The file moves atomically from `Pending_Approval/` to `Approved/`.
3. **If not:** A 403 means a missing/wrong token; clear `sessionStorage` and retry. A 404 usually means it was already moved.

### 9 — Confirm email delivery

1. **Do:** Check the recipient inbox and the monitored account's Sent folder.
2. **See:** The body exactly matches the approved `draft_body`; execution makes no LLM call.
3. **If not:** Check watcher stderr, Gmail errors, scopes, rate-limit events, and the receipt. Do not retry until external state is known.

### 10 — Confirm completion

1. **Do:** Find the artifact in `Done/`.
2. **See:** It is absent from `Approved/` and present in `Done/`.
3. **If not:** Look in `Failed/`; execution errors route there and log a traceback.

### 11 — Confirm logs and receipt

1. **Do:** Inspect `Logs/execution_receipts.json` and today's `Logs/YYYY-MM-DD.json`.
2. **See:** One terminal receipt for the `action_id` and structured audit events.
3. **If not:** Stop the approval watcher before any retry. Never casually delete receipts.

### 12 — Confirm dashboard activity

1. **Do:** Open Activity Log or wait for the 30-second refresh.
2. **See:** The execution event and updated KPI counts.
3. **If not:** Call `/api/logs`, validate today's JSON, and refresh. The receipt—not the UI—is authoritative.

## 6. Troubleshooting and prohibited shortcuts

- OAuth blocked: add the Gmail address as a test user and use a Desktop application client.
- Dashboard unreachable: verify port 5000 and localhost binding. Public exposure requires TLS, authentication, restricted CORS, and a production WSGI/reverse proxy.
- Duplicate message: inspect processed IDs and receipts; never remove the guard.
- Refused approval: read validation/rate-limit logs; correct the artifact or wait for the window.
- Watcher exit: inspect stderr and `Logs/agent.log`; silent watcher failure is a defect.

Never re-call the LLM after approval, hardcode paths, remove `action_id` checks, commit credentials, omit watcher error handling, poll Gmail below 60 seconds, or use the MCP server for production sends.

