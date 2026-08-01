# Day 8 — Gmail MCP, Unified Runtime, and Workflow Utilities

## Architecture

```text
                         ChiefMind
                            │
                 scripts/main.py (supervisor)
                 validate → start → monitor → stop
                    ┌───────┼────────┐
                    │       │        │
             Gmail watcher  │   Flask dashboard
                    │       │        │
              Needs_Action  │   browser + REST API
                    │       │
              reasoning     │
                    │       │
             Pending_Approval
                    │
             approval watcher ── Gmail API / LinkedIn poster

AI coding assistant
       │ MCP over stdio
       ▼
mcp-servers/gmail-send/index.js ── Gmail API

All Python agents ── scripts/workflow_utils.py ── workflow folders
```

The Python workflow remains the production human-in-the-loop path. The MCP
server is a separate development/debugging tool and does not replace the
approval watcher.

## Complete Day 8 tree

```text
mcp-servers/gmail-send/
├── index.js              # MCP server and Gmail implementation
├── index.test.js         # MIME, validation, and safety-gate tests
├── mcp.test.js           # Real stdio protocol smoke test
├── package.json
└── package-lock.json
scripts/
├── main.py               # Unified runtime supervisor
├── workflow_utils.py     # Shared safe file primitives
├── test_main.py
├── test_workflow_utils.py
├── gmail_watcher.py      # Now uses shared atomic writes and stop event
└── approval_watcher.py   # Now uses shared move and stop event
```

## Part A — Gmail Send MCP Server

### Purpose

The server exposes one model-callable tool:

```text
send_email(to: string, subject: string, body: string)
```

It uses the same ignored `credentials/credentials.json` and
`credentials/token.json` files as Python. Gmail expects an RFC-compatible MIME
message in the API's `raw` field, encoded with URL-safe base64. The server
creates that representation, calls `users.messages.send`, and returns the Gmail
message and thread IDs.

### Install and test

```bash
cd mcp-servers/gmail-send
npm install
npm run check
npm test
```

The tests do not send email. They verify exact-body encoding, header-injection
defense, the disabled-by-default gate, and a real MCP stdio handshake.

### Configure an MCP host

Use an absolute path in your host's MCP configuration. The configuration shape
varies slightly by host, but the server entry is:

```json
{
  "mcpServers": {
    "chiefmind-gmail-send": {
      "command": "node",
      "args": [
        "/Users/muhammadsoban/Desktop/Ai Personal employee/mcp-servers/gmail-send/index.js"
      ],
      "env": {
        "CHIEFMIND_ROOT": "/Users/muhammadsoban/Desktop/Ai Personal employee",
        "MCP_GMAIL_SEND_ENABLED": "false"
      }
    }
  }
}
```

Restart the MCP host and confirm that `send_email` appears. Keep the gate false
while inspecting tools. Set it to `true` only when you explicitly want that MCP
host to send reviewed messages:

```json
"MCP_GMAIL_SEND_ENABLED": "true"
```

The server writes diagnostics only to stderr. Writing ordinary logs to stdout
would corrupt the MCP protocol stream.

### Why this pattern?

MCP separates tool discovery and validated arguments from the Gmail
implementation. Any compatible local assistant can discover the same schema
without a custom integration. Stdio is appropriate because the host launches
one local child process and owns its lifetime.

### How it connects

This is a direct debugging surface. Normal ChiefMind emails still flow through
`Pending_Approval → Approved → approval_watcher.py`, retaining immutable drafts,
receipts, and duplicate protection.

### Common mistakes

- Printing logs to stdout instead of stderr.
- Using standard base64 rather than base64url for Gmail's `raw` value.
- Loading `credentials.json` but forgetting the authorized `token.json`.
- Allowing CR/LF in email headers, which enables header injection.
- Treating MCP calls as automatically human-approved. They are not.
- Enabling the send gate before testing tool discovery.

## Part B — Unified main entry point

### Purpose

`scripts/main.py` validates configuration, then starts three background worker
threads:

1. Gmail polling watcher.
2. Approved-item execution watcher.
3. Flask dashboard server.

All workers receive one `threading.Event`. Ctrl+C or SIGTERM sets that event,
stops the filesystem observer, interrupts the Gmail polling wait, shuts down
Flask, and joins the threads. An unexpected worker failure stops the whole
runtime instead of leaving a partially functional system running silently.

### Configuration check

```bash
scripts/.venv/bin/python scripts/main.py --check
```

This must succeed before starting ChiefMind. If it reports missing values,
update the ignored `scripts/.env`. Required unattended settings include:

```dotenv
GROQ_API_KEY=your_real_key
DASHBOARD_APPROVAL_TOKEN=your_long_random_token
```

It also requires `credentials/credentials.json` and
`credentials/token.json`. Create the token first if needed:

```bash
cd scripts
.venv/bin/python authenticate_gmail.py
```

### Start everything

```bash
scripts/.venv/bin/python scripts/main.py
```

Open `http://127.0.0.1:5000/`. If port 5000 is occupied, set a free
`DASHBOARD_PORT` in `scripts/.env` before starting.

Stop the complete system with Ctrl+C once. Do not start the individual watchers
at the same time as `main.py`; duplicate watcher instances compete for the same
files even though execution receipts reduce the damage.

### Why this pattern?

Threads are appropriate here because each worker spends most of its time
waiting for network, filesystem, or HTTP activity. They also allow one shared
shutdown event. Separate OS processes would provide stronger fault isolation,
but require more complex process-group signaling and cross-process health
reporting.

### How it connects

The orchestrator does not reproduce agent logic. It calls the existing public
watcher functions and serves the existing Flask app, keeping each component
independently testable.

### Common mistakes

- Starting workers before validating secrets and OAuth files.
- Using daemon threads and exiting before buffered work finishes.
- Calling `time.sleep()` during shutdown instead of an interruptible event wait.
- Swallowing worker exceptions and leaving the dashboard looking healthy.
- Running `main.py` and standalone watcher commands simultaneously.

## Part C — Workflow utilities

### Purpose and API

`scripts/workflow_utils.py` is the one shared implementation for workflow
artifact operations:

```python
metadata, body = parse_frontmatter(Path("Pending_Approval/item.md"))
write_frontmatter(Path("Plans/item.md"), metadata, body)
final_path = move_file(source, destination_directory)
```

It also retains the earlier helpers used by Days 3–7:

- `atomic_write_text()` and `atomic_write_json()`
- `load_frontmatter_file()`
- `load_json_object()` and `load_json_array()`
- `append_json_array()` with thread/process locking

`parse_frontmatter()` accepts either Markdown text or a `Path`, preserving
backward compatibility. YAML uses `safe_load`, preventing construction of
arbitrary Python objects. Writes use a temporary file followed by replacement,
so readers see the old complete file or the new complete file—not a partial
artifact. Moves refuse accidental overwrite by default.

### Test

```bash
scripts/.venv/bin/python -m unittest -v scripts.test_workflow_utils
```

### Why this pattern?

Central primitives enforce the same encoding, YAML safety, atomicity, and
collision behavior everywhere. Fixing a file-handling defect once then protects
every importing agent.

### Common mistakes

- Using `yaml.load()` on email-controlled content.
- Writing directly to the final filename while another agent watches it.
- Silently overwriting an existing approval or receipt.
- Copying and deleting across filesystems while claiming the move is atomic.
- Hardcoding workflow paths rather than importing `config.py`.

## LinkedIn compatibility

Day 7's official LinkedIn Posts API remains the preferred live path. Day 8 also
supports the requested Playwright fallback:

```dotenv
LINKEDIN_MODE=browser
AUTO_LINKEDIN_POSTS=true
LINKEDIN_EMAIL=your_login
LINKEDIN_PASSWORD=your_password
LINKEDIN_HEADLESS=false
```

Run headed first so a changed selector or security challenge is visible. The
automation stops on checkpoints rather than attempting to bypass them. Browser
mode is less reliable and may be inconsistent with LinkedIn's platform rules;
use official OAuth/API mode for production. Both live modes require the
`AUTO_LINKEDIN_POSTS=true` gate, while mock mode does not publish.

The Day 7 Inter font, 30-second dashboard polling, `sessionStorage` approval
token, and `X-Approval-Token` behavior are unchanged.

## End-to-end validation

### 1. Run all offline tests

```bash
cd mcp-servers/gmail-send
npm test
cd ../..
scripts/.venv/bin/python -m unittest -v \
  scripts.test_workflow_utils \
  scripts.test_main \
  scripts.test_knowledge \
  scripts.test_reasoning_loop \
  scripts.test_approval_watcher \
  scripts.test_linkedin_poster \
  dashboard.test_app
```

### 2. Validate and start the runtime

```bash
scripts/.venv/bin/python scripts/main.py --check
scripts/.venv/bin/python scripts/main.py
```

### 3. Confirm the dashboard

```bash
curl http://127.0.0.1:5000/api/stats
```

Open the dashboard, verify KPI data, inspect an item, and test one mock approval.

### 4. Confirm Gmail ingestion

Send a test email matching `GMAIL_QUERY`. After the polling interval, confirm:

- the email was staged as Markdown;
- reasoning created a plan or approval;
- the dashboard count changed;
- the source email was marked read.

### 5. Confirm MCP without sending

Keep `MCP_GMAIL_SEND_ENABLED=false`, restart the MCP host, and list tools. The
`send_email` tool must appear, while any attempted call returns a disabled
error. Only enable and perform a real send when you have reviewed the exact
recipient, subject, and body.

## Completion checklist

- [ ] `npm install` succeeds in `mcp-servers/gmail-send/`
- [ ] Four Node/MCP tests pass
- [ ] Python workflow and supervisor tests pass
- [ ] `main.py --check` reports valid configuration
- [ ] One `main.py` process starts all three services
- [ ] Dashboard returns valid stats JSON
- [ ] Ctrl+C stops all services cleanly
- [ ] MCP host discovers exactly `send_email`
- [ ] MCP sending remains disabled until explicitly authorized
- [ ] LinkedIn remains mock or official API mode unless browser fallback is intentional
