# 🧠 ChiefMind — 10-Day Build Plan
### Autonomous AI Chief of Staff for Knowledge Workers

---

> **Who this plan is for:** You have received this because you are building **ChiefMind**, an autonomous multi-agent AI Chief of Staff. This plan is derived from a working reference implementation that already solves the core engineering challenges you will face. Follow it day by day. Do not skip days — each day's output is required as input for the next.

---

## What You Are Building

ChiefMind is an always-on, autonomous system that:
- **Monitors** your inboxes and digital platforms for actionable information
- **Reasons** about incoming tasks using an LLM and a knowledge base
- **Proposes** exact drafted actions (emails, posts, plans)
- **Waits** for your one-click human approval before executing anything
- **Executes** approved actions via platform APIs and browser automation
- **Logs** everything to a local audit trail
- **Displays** everything on a live web dashboard

This is **not** a chatbot. ChiefMind runs continuously in the background, operates autonomously, and only contacts you when it needs a decision.

---

## Architecture Overview

```
[Scheduler — every N minutes]
        ↓
  Watcher Agent         ← monitors Gmail / other platforms
        ↓ detects new item
  Reasoning Agent       ← LLM analyzes item + knowledge base
        ↓ produces exact draft
  Pending_Approval/     ← human reviews here (HITL gate)
        ↓ human clicks Approve
  Execution Agent       ← sends email / posts / files ticket
        ↓
  Done/ + Logs/         ← full audit trail

[Web Dashboard]         ← always running, one-click approve/reject
```

### Core File-System State Machine

ChiefMind uses the **local file system as its shared state bus**. Every agent writes and reads markdown files with YAML frontmatter. This is the key design decision — it makes the system debuggable, observable in any text editor or file manager, and completely offline-first.

```
Inbox/              ← raw incoming items (optional staging area)
Needs_Action/       ← watcher drops .md files here
Plans/              ← reasoning agent drops Plan.md files here
Pending_Approval/   ← exact drafted actions waiting for human
Approved/           ← human moves files here to trigger execution
Rejected/           ← human rejects here; no action taken
Done/               ← execution agent moves file here after success
Failed/             ← execution agent moves file here after failure
Logs/               ← daily JSON logs + agent.log rolling text log
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem depth for AI, APIs, automation |
| Package manager | `uv` | Fast, reproducible, replaces pip+venv |
| LLM | Groq (LLaMA 3.3 70B) | Free tier, fast inference, OpenAI-compatible API |
| Email | Gmail API (OAuth2) | Read inbox, send replies, mark as read |
| Browser automation | Playwright (Chromium) | LinkedIn / any site that has no API |
| MCP server | Node.js | Expose Gmail send as a Model Context Protocol tool |
| Web dashboard | Flask + Vanilla JS/CSS | Lightweight, no framework overhead |
| Scheduler | launchd (macOS) or cron (Linux) | Keeps watchers alive 24/7 |
| Knowledge base | Markdown file + keyword retrieval | Simple, fast, auditable RAG |
| Secrets | `.env` file | Never committed; documented in `.env.example` |

---

## Prerequisites (Set Up Before Day 1)

- [ ] Python 3.11+ installed
- [ ] Node.js v18+ installed
- [ ] `uv` installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [ ] Playwright installed: `pip install playwright && playwright install chromium`
- [ ] A Google Cloud project with Gmail API enabled and `credentials.json` downloaded
- [ ] A free Groq API key from [console.groq.com](https://console.groq.com)
- [ ] A LinkedIn account (for automation on Day 7)
- [ ] Git repo initialized

---

## Exceptions — What ChiefMind Should NOT Copy

> [!IMPORTANT]
> The reference implementation is domain-specific (university department email assistant). ChiefMind is a **general-purpose** knowledge-worker assistant. The following items should be replaced or generalized:

| Reference Item | ChiefMind Replacement |
|---|---|
| `docs/University_Handbook.md` | Replace with **your domain's knowledge base** (company wiki, product docs, personal notes, etc.) |
| University email categories (registration, attendance, exams…) | Define **your own categories** relevant to your target user's workflow |
| `GMAIL_QUERY` filtering for university inbox | Update the Gmail query to match your use case |
| `GROQ_MODEL = llama-3.3-70b-versatile` | You may swap to any Groq-supported model; this one is recommended as a starting point |
| launchd plist templates | Adapt for Linux systemd or Windows Task Scheduler if not on macOS |

---

## Day-by-Day Plan

---

### ✅ Day 1 — Project Skeleton & Configuration

**Goal:** A clean, working repository with all folders, dependencies, and the central config module.

**Deliverables:**
- Git repo with proper `.gitignore`
- All 9 workflow folders created
- `config.py` — the single source of truth for all paths and constants
- `pyproject.toml` with all Python dependencies declared
- `scripts/.env.example` with all required variables documented

**Tasks:**

1. Create the repo and initialize git.
2. Create the folder structure:
   ```
   chiefmind/
   ├── scripts/
   ├── dashboard/
   │   ├── static/
   │   └── templates/
   ├── mcp-servers/
   │   └── gmail-send/
   ├── docs/
   ├── launchd/
   ├── credentials/       ← add to .gitignore immediately
   ├── Inbox/
   ├── Needs_Action/
   ├── Plans/
   ├── Pending_Approval/
   ├── Approved/
   ├── Rejected/
   ├── Done/
   ├── Failed/
   └── Logs/
   ```
3. Write `scripts/config.py`. It must:
   - Load all paths from environment variables with sensible defaults
   - Auto-create all workflow folders if missing
   - Validate credentials and secrets at startup
   - Export `setup_logging()` and `validate_config()` functions
   - Export `load_processed_ids()` and `save_processed_ids()` helpers
4. Write `scripts/pyproject.toml` declaring dependencies: `google-auth`, `google-auth-oauthlib`, `google-api-python-client`, `groq`, `flask`, `watchdog`, `playwright`, `python-dotenv`.
5. Write `scripts/.env.example` — document every variable, never commit real values.
6. Add `.gitignore` entries for: `credentials/`, `*.env`, `token.json`, `__pycache__/`, `.venv/`, `*.pyc`.
7. Run `uv sync` and confirm zero import errors on `config.py`.

**Key principle:** Everything in the system imports from `config.py`. No hardcoded paths anywhere else, ever.

---

### ✅ Day 2 — Gmail OAuth & Watcher Agent

**Goal:** Detect new emails in Gmail and write structured markdown files to `Needs_Action/`.

**Deliverables:**
- `scripts/authenticate_gmail.py` — one-time OAuth flow
- `scripts/gmail_watcher.py` — polling agent
- Test: run `gmail_watcher.py`, send yourself a test email, confirm `.md` file appears in `Needs_Action/`

**Tasks:**

1. Write `scripts/authenticate_gmail.py`:
   - Uses `google-auth-oauthlib` to run the OAuth2 browser flow
   - Saves `token.json` to `credentials/`
   - Requires `credentials.json` to already exist in `credentials/`

2. Write `scripts/gmail_watcher.py`. It must:
   - Authenticate using `credentials.json` + `token.json`
   - Poll Gmail every `GMAIL_POLL_INTERVAL` seconds (default 120)
   - Use a configurable `GMAIL_QUERY` to filter which emails to process
   - Track processed message IDs in `processed_ids.json` to avoid re-processing
   - For each new email, write a `.md` file to `Needs_Action/` with this frontmatter:
     ```yaml
     ---
     id: <gmail_message_id>
     action_id: email_<gmail_message_id>
     type: email
     from: sender@example.com
     subject: "Email subject line"
     received_at: "2026-07-23T10:00:00Z"
     priority: medium
     status: needs_action
     ---
     ```
   - Include the full email body in the markdown body
   - Mark processed emails as read in Gmail
   - After writing files, **auto-trigger `reasoning_loop.py`** as a subprocess — no human intervention needed between watcher and reasoning
   - Log all activity to `Logs/`

3. **Filename format:** Use `email_<gmail_message_id>.md` as the filename — the Gmail message ID is globally unique and prevents collisions.

4. **Error handling:** Wrap all API calls in try/except with retry logic (`GMAIL_RETRIES`, `GMAIL_RETRY_DELAY`). Never crash the polling loop.

**Test:** Send yourself an email, wait for the poll interval, check `Needs_Action/`.

---

### ✅ Day 3 — Knowledge Base & Retrieval

**Goal:** Build the grounding knowledge base that the reasoning agent will use to draft contextual replies.

**Deliverables:**
- `docs/KnowledgeBase.md` — your domain's reference document
- `scripts/knowledge.py` — keyword-based retrieval module

**Tasks:**

1. Create `docs/KnowledgeBase.md`. This is the document your LLM will cite when drafting responses. For ChiefMind targeting knowledge workers, populate it with:
   - Common policies and procedures relevant to your target domain
   - FAQ-style Q&A pairs
   - Standard response templates for recurring inquiry types
   - Escalation criteria (when to escalate vs. handle autonomously)

   > [!TIP]
   > Keep each section clearly headed with `##` headings and assign page numbers in comments (`<!-- page 14 -->`). The retrieval module will reference these.

2. Write `scripts/knowledge.py` with a `retrieve_relevant_sections(query, top_k=3)` function:
   - Tokenize the query into keywords
   - Score each section of the knowledge base by keyword overlap
   - Return the top-K most relevant sections as a string
   - This is intentionally simple (no vector embeddings needed) — keyword overlap works well for structured domain docs

3. Write a quick test: call `retrieve_relevant_sections("refund policy deadline")` and print the output. Confirm the right sections are returned.

**Why not vector embeddings?** For a structured domain knowledge base, keyword retrieval is faster, cheaper, requires no embedding API, and is fully auditable. Add vector search only if you find keyword retrieval insufficient after testing.

---

### ✅ Day 4 — Reasoning Agent (LLM Brain)

**Goal:** Read files from `Needs_Action/`, analyze them with an LLM, and produce structured outputs in `Plans/` and `Pending_Approval/`.

**Deliverables:**
- `scripts/reasoning_loop.py` — the core AI brain
- Test: place a sample `.md` file in `Needs_Action/`, run the script, see `Plan.md` in `Plans/` and an approval artifact in `Pending_Approval/`

**Tasks:**

1. Write `scripts/reasoning_loop.py`. It must:

   **Step 1 — Load all files from `Needs_Action/`**
   - Read each `.md` file and parse YAML frontmatter + body

   **Step 2 — Classify the action type**
   - Call Groq API with a classification prompt
   - Classify into: `email_send` (needs a drafted reply) or `manual` (needs human attention only)
   - Use `GROQ_MODEL` from config

   **Step 3 — Retrieve knowledge base context**
   - Call `knowledge.retrieve_relevant_sections(email_subject + email_body)`
   - Inject retrieved sections into the LLM prompt

   **Step 4 — Draft an exact reply**
   - For `email_send`: prompt the LLM to write a complete, ready-to-send email reply
   - The prompt must include: the original email, the retrieved knowledge base sections, and a clear instruction to produce only the reply body
   - Store the exact draft in `draft_body` field — **this is the only text that will be sent; the LLM is never called again after approval**

   **Step 5 — Create a Plan.md in `Plans/`**
   ```yaml
   ---
   id: plan_<timestamp>
   source_email: email_<gmail_message_id>.md
   priority: high | medium | low
   category: <detected category>
   recommended_action: email_send | manual
   steps:
     - Step 1
     - Step 2
   ---
   ```

   **Step 6 — Create an approval artifact in `Pending_Approval/`**
   ```yaml
   ---
   action_id: email_<gmail_message_id>
   type: email_send
   to: sender@example.com
   subject: "Re: Original subject"
   draft_body: |
     Dear [Name],
     [Full email body here]
     Best regards,
     [Your Name]
   knowledge_references:
     - "Knowledge Base page 14: Refund Policy"
   created_at: "2026-07-23T10:05:00Z"
   ---
   ```

   **Step 7 — Move source file from `Needs_Action/` to `Done/` or leave it**
   - Informational items (no reply needed): move to `Done/`
   - Items needing approval: leave the approval artifact in `Pending_Approval/`

2. **Duplicate guard:** Skip any `action_id` that already exists in `Pending_Approval/`, `Approved/`, or `Done/`.

3. **Temperature:** Use `temperature=0.3` for email drafts. Lower temperature = more predictable, professional output.

**Key principle:** The `draft_body` written here is sacred. The execution agent sends exactly this text — it never re-calls the LLM after approval. What the human approves is exactly what gets sent.

---

### ✅ Day 5 — HITL Approval Watcher (Execution Agent)

**Goal:** Watch the `Approved/` folder and execute the exact approved action.

**Deliverables:**
- `scripts/approval_watcher.py` — execution agent using `watchdog`
- `Logs/execution_receipts.json` — duplicate action guard
- Test: move an approval artifact to `Approved/`, confirm the email is sent

**Tasks:**

1. Write `scripts/approval_watcher.py`. It must:

   **Watch `Approved/` with `watchdog`**
   - React within seconds of a file appearing in `Approved/`

   **Parse the approval artifact**
   - Read YAML frontmatter
   - Extract `type`, `action_id`, `to`, `subject`, `draft_body`

   **Duplicate guard**
   - Load `Logs/execution_receipts.json`
   - If `action_id` already exists: log a warning and skip
   - Never execute the same `action_id` twice

   **Route by `type`**
   - `email` or `email_send` → send the exact `draft_body` via Gmail API
   - `linkedin_post` → call `linkedin_poster.py`
   - `plan` → no external action; just move to `Done/`

   **Refuse invalid files**
   - If a file lacks `draft_body`: refuse to send and move to `Failed/`
   - Log the reason

   **After execution**
   - Save `action_id` to `execution_receipts.json`
   - Move processed file to `Done/`
   - Log action to `Logs/YYYY-MM-DD.json`

2. **Email sending:** Use the Gmail API directly (not the MCP server — the MCP server is for Claude Code tool calls). Use the same authenticated service object as the watcher.

3. **Error handling:** On any API error, move file to `Failed/` and log the full traceback. Never silently swallow exceptions.

**Test:** Manually create an approval `.md` file with a valid `draft_body`, drop it in `Approved/`, and confirm the email arrives in the recipient's inbox.

---

### ✅ Day 6 — Web Dashboard (Flask Backend)

**Goal:** A REST API server that reads the live state of all workflow folders.

**Deliverables:**
- `dashboard/app.py` — Flask server with 8 API endpoints

**Tasks:**

Write `dashboard/app.py` exposing these endpoints:

| Endpoint | Method | Returns |
|---|---|---|
| `/api/stats` | GET | KPI counts (pending, done, failed, etc.) + recent activity list |
| `/api/folder/<key>` | GET | List all `.md` files in a given workflow folder |
| `/api/file/<folder>/<name>` | GET | Parsed YAML frontmatter + markdown body of a single file |
| `/api/approve/<name>` | POST | Move file from `Pending_Approval/` → `Approved/` |
| `/api/reject/<name>` | POST | Move file from `Pending_Approval/` → `Rejected/` |
| `/api/logs` | GET | All entries from all daily JSON log files in `Logs/` |
| `/api/agent-log` | GET | Last 200 lines of `Logs/agent.log` |
| `/api/all-items` | GET | All `.md` files across all workflow folders |

**Security:** When `DASHBOARD_APPROVAL_TOKEN` is set in the environment, all POST endpoints must validate the `X-Approval-Token` request header. Return `403 Forbidden` if the token is missing or wrong.

**CORS:** Enable CORS headers so the frontend JS can call the API freely in development.

**Folder key mapping:**
```python
FOLDER_KEYS = {
    "inbox": config.INBOX,
    "needs_action": config.NEEDS_ACTION,
    "plans": config.PLANS,
    "pending_approval": config.PENDING_APPROVAL,
    "approved": config.APPROVED,
    "done": config.DONE,
    "rejected": config.REJECTED,
    "failed": config.FAILED,
}
```

**Test:** Run `python app.py`, call `curl http://localhost:5000/api/stats`, confirm you get valid JSON.

---

### ✅ Day 7 — Web Dashboard (Frontend) & LinkedIn Poster

**Goal:** Build the visual dashboard UI and the LinkedIn automation agent.

**Part A — Dashboard Frontend**

**Deliverables:**
- `dashboard/static/index.html`
- `dashboard/static/style.css`
- `dashboard/static/app.js`

**Design requirements:**
- Dark glassmorphism aesthetic (dark background, frosted glass cards, subtle gradients)
- Live KPI cards: Pending Approval, Needs Action, Done, Failed, Plans, Total Items
- Sidebar navigation with views: Dashboard, Pending Approval, Needs Action, Done, Plans, Activity Log, Failed
- Pending Approval view: show each item with **✓ Approve** and **✗ Reject** buttons
- File detail modal: clicking an item shows parsed frontmatter + full markdown body
- Activity Log: chronological timeline from daily JSON logs
- Auto-refresh every 30 seconds

**Font:** Use [Inter](https://fonts.google.com/specimen/Inter) from Google Fonts.

**Auth flow:** On the first API call that returns `403`, prompt the user for the approval token and store it in `sessionStorage`. Send it as `X-Approval-Token` on all subsequent requests.

**Part B — LinkedIn Poster**

**Deliverables:**
- `scripts/linkedin_poster.py`

**Tasks:**

Write `linkedin_poster.py` using Playwright:
1. Launch Chromium (headless=False for debugging, headless=True for production)
2. Navigate to `linkedin.com/login`, enter `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD`
3. Navigate to the LinkedIn feed
4. Click "Start a post", type the post content, click "Post"
5. Log success or failure
6. Return True/False to the caller

**Trigger:** `approval_watcher.py` calls this when it sees `type: linkedin_post` in an approval file.

**Note:** LinkedIn automation is `AUTO_LINKEDIN_POSTS=false` by default. Set to `true` in `.env` only if you explicitly want automated LinkedIn posting.

---

### ✅ Day 8 — Gmail Send MCP Server & Main Entry Point

**Goal:** Build the Node.js MCP server that exposes Gmail send as a tool, and a unified `main.py` entry point.

**Part A — MCP Server**

**Deliverables:**
- `mcp-servers/gmail-send/index.js`
- `mcp-servers/gmail-send/package.json`

**Tasks:**

1. `package.json` — declare dependency on `@modelcontextprotocol/sdk` and `googleapis`.
2. `index.js` — implement MCP server over stdio:
   - Expose one tool: `send_email(to: string, subject: string, body: string)`
   - Authenticate using the same `credentials.json` + `token.json` from `credentials/`
   - Call `gmail.users.messages.send()` with a base64-encoded RFC 2822 message
   - Return `{ success: true, messageId }` on success

**Why MCP?** This allows AI coding assistants (like Claude Code) to call `send_email` as a native tool during development and debugging — without running the full Python pipeline.

**Part B — Main Entry Point**

**Deliverables:**
- `scripts/main.py`

Write `scripts/main.py` that starts all agents:
- `validate_config()` first — fail loudly if anything is misconfigured
- Start `approval_watcher.py` in a background thread/process
- Start `gmail_watcher.py` in a background thread/process
- Start the Flask dashboard in a background thread
- Join all processes; handle `KeyboardInterrupt` gracefully

**Part C — Workflow Utilities**

Write `scripts/workflow_utils.py` with shared helpers:
- `move_file(src, dst)` — atomic file move with logging
- `parse_frontmatter(path)` — parse YAML frontmatter from a `.md` file
- `write_frontmatter(path, data, body)` — write a `.md` file with YAML frontmatter

All agents import these helpers instead of duplicating file-handling logic.

---

### ✅ Day 9 — Scheduling, Testing & launchd Integration

**Goal:** Make ChiefMind run 24/7 without manual intervention.

**Part A — launchd / Scheduler Integration**

**Deliverables:**
- `launchd/com.chiefmind.gmailwatcher.plist.template`
- `launchd/com.chiefmind.approvalwatcher.plist.template`
- `launchd/com.chiefmind.dashboard.plist.template`

Each template uses the placeholder `__VAULT_DIR__` for the absolute path.

Render and install with:
```bash
sed "s#__VAULT_DIR__#$(pwd)#g" launchd/com.chiefmind.gmailwatcher.plist.template \
  > ~/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist
launchctl load ~/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist
```

For Linux: create equivalent systemd unit files with `[Service] Restart=always`.

**Part B — Testing**

**Deliverables:**
- `scripts/test_pipeline.py`

Write tests that cover:

| Test | What it checks |
|---|---|
| `test_config_loads` | All required constants exist and paths are valid |
| `test_gmail_auth` | OAuth credentials can be loaded without error |
| `test_knowledge_retrieval` | `retrieve_relevant_sections()` returns non-empty results |
| `test_frontmatter_parse` | `parse_frontmatter()` correctly parses a sample `.md` file |
| `test_processed_ids` | `load_processed_ids()` / `save_processed_ids()` round-trip |
| `test_dashboard_stats` | Flask `/api/stats` returns valid JSON |
| `test_duplicate_guard` | `action_id` already in receipts → execution is skipped |
| `test_approval_routing` | File with `type: email_send` routes to the email sender |

Run with: `uv run python test_pipeline.py`

---

### ✅ Day 10 — Documentation, README & End-to-End Demo

**Goal:** Fully documented, demo-ready system.

**Deliverables:**
- `README.md` — complete setup guide
- `AGENTS.md` — agent documentation
- `docs/DEPLOYMENT.md` — production configuration guide
- `docs/GUARDRAILS.md` — safety rules and risk thresholds
- `scripts/.env.example` — finalized

**README.md must cover:**
1. What ChiefMind is (one paragraph, non-technical)
2. Architecture diagram (ASCII art or Mermaid)
3. Complete feature table
4. Project structure tree
5. Setup instructions (step by step, copy-paste commands)
6. How to run locally (4-terminal setup)
7. How to run in production (launchd)
8. How the HITL flow works
9. Security considerations
10. Technology stack table

**AGENTS.md must cover:**
- One section per agent describing: trigger, input, output, what it does
- HITL flow diagram
- Scheduling table
- Shared configuration constants table

**End-to-End Demo Checklist:**
- [ ] Send a test email to the monitored inbox
- [ ] Wait for `gmail_watcher.py` to detect it (or trigger manually)
- [ ] Confirm `.md` file appears in `Needs_Action/`
- [ ] Wait for `reasoning_loop.py` to process it
- [ ] Confirm `Plan.md` appears in `Plans/`
- [ ] Confirm approval artifact appears in `Pending_Approval/`
- [ ] Open dashboard at `http://127.0.0.1:5000` — confirm item appears
- [ ] Click ✓ Approve in the dashboard
- [ ] Confirm email is sent to the original sender
- [ ] Confirm file moves to `Done/`
- [ ] Confirm entry appears in `Logs/`
- [ ] Confirm activity log updates in the dashboard

---

## Summary — Deliverable by Day

| Day | Core Deliverable | Key Test |
|---|---|---|
| 1 | Project skeleton, `config.py`, `pyproject.toml` | `uv sync` + `python config.py` prints ✅ |
| 2 | `authenticate_gmail.py`, `gmail_watcher.py` | New email → `.md` in `Needs_Action/` |
| 3 | `KnowledgeBase.md`, `knowledge.py` | Query → relevant sections returned |
| 4 | `reasoning_loop.py` | `Needs_Action/` item → `Plans/` + `Pending_Approval/` |
| 5 | `approval_watcher.py`, execution receipts | Approved file → email sent → `Done/` |
| 6 | `dashboard/app.py` (8 endpoints) | `curl /api/stats` returns valid JSON |
| 7 | Dashboard frontend + `linkedin_poster.py` | UI loads, approve/reject works |
| 8 | MCP server, `main.py`, `workflow_utils.py` | All agents start from single command |
| 9 | launchd plists, `test_pipeline.py` | All tests pass, services survive reboot |
| 10 | README, AGENTS.md, full E2E demo | Checklist above all green |

---

## Design Principles to Follow Throughout

1. **File system as shared state** — every agent communicates via `.md` files with YAML frontmatter. No databases, no message queues.

2. **Config is the single source of truth** — every path and constant lives in `config.py`. Nothing is hardcoded anywhere else.

3. **The draft is sacred** — after human approval, the execution agent sends exactly the `draft_body` that was approved. It never re-calls the LLM.

4. **Fail loudly** — at startup, `validate_config()` checks everything and exits with a clear error message if anything is wrong. Silent failures are not acceptable.

5. **Idempotency** — every agent checks `action_id` before acting. Running any agent twice on the same input must be safe.

6. **Human stays in control** — no email is sent, no post is published, no ticket is filed without a human explicitly approving the exact artifact.

7. **Local-first** — all data lives on your machine. No third-party cloud storage. Credentials are never committed to git.

---

## Common Mistakes to Avoid

> [!WARNING]
> These are the most common failure modes when building this type of system.

- **Re-calling the LLM after approval.** Never generate a new draft at execution time. The `draft_body` in the approval artifact is what gets sent.
- **Hardcoding paths.** Always use `config.py` constants. If you find yourself typing a path string anywhere else, stop and add it to config.
- **Missing duplicate guards.** Always check `action_id` against `execution_receipts.json` before executing. Sending the same email twice is very bad.
- **Committing credentials.** Add `credentials/`, `.env`, and `token.json` to `.gitignore` on Day 1. Never fix this later.
- **No error handling in watchers.** A crash in `gmail_watcher.py` means no emails are detected. Every API call must be wrapped in try/except.
- **Polling too fast.** Gmail API has rate limits. Do not set `GMAIL_POLL_INTERVAL` below 60 seconds in production.
- **Using the MCP server for production email sending.** The MCP server is for AI coding assistant tool calls during development. The Python execution agent sends production emails directly via the Gmail API.

---

*Good luck. Build it in order, test each day before moving on, and you will have a working autonomous AI Chief of Staff in 10 days.*
