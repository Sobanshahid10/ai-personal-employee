<div align="center">

# 🧠 ChiefMind — Autonomous AI Personal Employee

**A production-grade, local-first AI personal executive assistant.**  
Grounded reasoning · Immutable human-in-the-loop approval · Zero-cloud file state · Live glassmorphic dashboard · Audit receipts

[![Python](https://img.shields.io/badge/Python-3.11%20|%203.12-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Groq](https://img.shields.io/badge/Groq-Llama--3.3--70b-f05032?style=flat-square)](https://groq.com/)
[![Gmail API](https://img.shields.io/badge/Gmail%20API-OAuth%202.0-ea4335?style=flat-square&logo=gmail&logoColor=white)](https://developers.google.com/gmail/api)
[![Watchdog](https://img.shields.io/badge/Watchdog-File%20Monitoring-4b5563?style=flat-square)](https://python-watchdog.readthedocs.io/)
[![MCP Protocol](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-8a2be2?style=flat-square)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)
[![Deployment](https://img.shields.io/badge/Daemon-launchd%20%7C%20systemd-0078d4?style=flat-square)](docs/DEPLOYMENT.md)

</div>

---

**ChiefMind** is an autonomous AI Personal Employee that monitors intake channels (Gmail, LinkedIn, Job Feeds), parses unstructured communications into structured workflow state, performs grounded multi-step reasoning using Groq LLM + local knowledge retrieval, and stages executable drafts—pausing before every external action to guarantee human-in-the-loop authorization.

> 🛡 **Human-in-the-Loop Invariant:** LLM classifications and drafts **never execute external actions autonomously**. External emails or posts are dispatched **only** when a human explicitly approves an immutable artifact (`Pending_Approval/` → `Approved/`) through the local dashboard.

---

## 📸 Executive Dashboard & System Interface

<div align="center">

| Feature View | Capability |
| :--- | :--- |
| **KPI Metrics & Quick Stats** | Live tracking of Pending Approvals, Auto-Handled emails, Action Plans, Done ledger, and Failed items |
| **1-Click Human Approval** | Direct, single-click Approve/Reject buttons with SHA-256 draft integrity checks |
| **Rich Email & Plan Reader** | Full formatted reader displaying email subjects, senders, and action plan steps instead of raw IDs |
| **Autonomy & Audit Stream** | Live timeline of auto-handled routine mail, classification decisions, and execution receipts |

</div>

---

## 🔍 Core System Capabilities

### 📬 Autonomous Intake & File-System State Machine
- **Gmail Watcher Daemon**: Polls unread emails at configurable intervals, parses plain text, strips HTML bloat, and stages deterministic YAML/Markdown artifacts.
- **Zero-Cloud Storage**: Workflow state relies entirely on local file system directories (`Inbox`, `Needs_Action`, `Plans`, `Pending_Approval`, `Approved`, `Rejected`, `Done`, `Failed`). No external database required.
- **Idempotency Engine**: Uses unique Gmail Message IDs and SHA-256 hashes stored in `processed_ids.json` to prevent duplicate ingestion.

### 🧠 Grounded Reasoning & Knowledge Retrieval
- **Knowledge Retriever (`knowledge.py`)**: Tokenizes queries against `docs/KnowledgeBase.md` to extract exact, auditable reference sections before invoking LLMs.
- **Structured Reasoning Engine (`reasoning_loop.py`)**: Uses Groq (`llama-3.3-70b-versatile`) with temperature `0.0` for deterministic classification and `0.3` for email drafting.
- **Autonomous Policy Routing**: Low-impact notification mail is automatically summarized into daily digests, while actionable requests require human approval.

### 🛡 Cryptographic Approval & Execution Gate
- **SHA-256 Integrity Checks**: Approvals store a `draft_sha256` hash of the exact proposed reply text. If a draft is modified without re-authorization, execution fails closed.
- **Approval Watcher Daemon (`approval_watcher.py`)**: Monitors `Approved/`, validates schemas and rate limits, reserves action IDs in `execution_receipts.json`, and dispatches external I/O.
- **Zero LLM Re-Generation**: The executor sends the exact approved text word-for-word. The LLM is never called during execution.

### 🖥 Real-Time Glassmorphic Web Dashboard
- **Flask REST API & Modern SPA**: Real-time KPI summary, category filtering (Emails, LinkedIn, Plans, Manual), and item details.
- **1-Click Approval Interface**: Single-click Approve / Reject workflow with instant toast notifications.
- **Developer Profile Card**: Integrated developer identity overlay.

### 💼 Technical Job Discovery Agent
- **Automated Lead Scoring**: Scrapes technical job feeds, cross-references skills against `candidate_profile.json`, filters senior/unrelated/foreign roles, and stages actionable leads.

---

## 📐 System Architecture & Workflow Pipeline

```
 ┌────────────────┐
 │  Gmail Inbox   │
 └───────┬────────┘
         │ Ingestion & Deduplication
         ▼
 ┌────────────────┐
 │ Gmail Watcher  │ ──► Writes to Needs_Action/*.md
 └───────┬────────┘
         │ Triggers
         ▼
 ┌────────────────────────────────────────────────────────┐
 │                   Reasoning Agent                      │
 │  1. Knowledge Base Retrieval (KnowledgeBase.md)       │
 │  2. Groq LLM Classification & Assessment (Temp 0.0)    │
 │  3. Draft Generation & SHA-256 Hashing (Temp 0.3)      │
 └───────┬────────────────────────────────────────────────┘
         │
         ├───[Routine / Notification] ──► Summarized in Daily Digest ──► Done/*.md
         │
         └───[Action Required] ──► Creates Plans/*.md + Pending_Approval/*.md
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │  Human Reviewer (Dashboard) │
                         │  • 1-Click Approve / Reject │
                         └──────────────┬──────────────┘
                                        │ Moves File
                                        ▼
                                 Approved/*.md
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │      Approval Watcher       │
                         │  • Check SHA-256 Hash       │
                         │  • Rate Limit Check         │
                         │  • Reserve Action ID        │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │      Execution Agent        │
                         │  • Gmail API Send           │
                         │  • LinkedIn Poster          │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
                                   Done/*.md
                         + execution_receipts.json
```

---

## 📑 Human-in-the-Loop State Machine Contract

| State Directory | Purpose | Immutable? | Triggered Action |
| :--- | :--- | :--- | :--- |
| `Inbox/` | Raw intake landing directory | No | Ingested by Gmail Watcher |
| `Needs_Action/` | Staged un-processed items requiring reasoning | No | Processed by Reasoning Agent |
| `Plans/` | Generated step-by-step action plans | Yes | Displayed on Dashboard |
| `Pending_Approval/` | Staged proposals awaiting human approval | Yes | Human review in Dashboard |
| `Approved/` | Human-approved items queued for execution | Yes | Picked up by Approval Watcher |
| `Rejected/` | Human-rejected proposals | Yes | Archived locally, no external I/O |
| `Done/` | Completed work with resolution metadata | Yes | Archived for audit ledger |
| `Failed/` | Items failing validation or rate limits | Yes | Requires manual inspection |

---

## 🚀 Quick Start Guide

### Prerequisites
- **Python 3.11+**
- **Node.js 18+** (for dev MCP server & frontend assets)
- **`uv` Package Manager**

```bash
# Verify runtimes
python3 --version
node --version
uv --version
```

If `uv` is not installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

### Step 1 — Clone & Install Dependencies

```bash
git clone https://github.com/Sobanshahid10/ai-personal-employee.git
cd ai-personal-employee

# Install Python dependencies using uv
uv pip install flask pyyaml python-dotenv groq google-api-python-client google-auth-httplib2 google-auth-oauthlib watchdog
```

---

### Step 2 — Configure Environment Variables

Create `.env` from the template:
```bash
cp scripts/.env.example scripts/.env
```

Edit `scripts/.env` with your real keys:
```dotenv
GROQ_API_KEY=gsk_your_real_groq_api_key_here
DASHBOARD_APPROVAL_TOKEN=your_secure_approval_token
GMAIL_POLL_INTERVAL=120
GMAIL_QUERY=is:unread
LINKEDIN_MODE=mock
AUTO_LINKEDIN_POSTS=false
```

---

### Step 3 — Authenticate Gmail OAuth

Place your Google Cloud OAuth client file as `credentials/credentials.json`, then run:

```bash
uv run python scripts/authenticate_gmail.py
```
*This opens Google OAuth in your browser and saves `credentials/token.json`.*

---

### Step 4 — Run ChiefMind Supervisor

Launch all services (Gmail intake, approval watcher, reasoning loop, and web dashboard) in a single command:

```bash
uv run python scripts/main.py
```

Open your browser to: **[http://127.0.0.1:5055](http://127.0.0.1:5055)**

---

## ⚙ Environment Variables Reference

| Variable | Description | Default | Required |
| :--- | :--- | :--- | :--- |
| `GROQ_API_KEY` | Groq API Key for LLM classification & drafting | `""` | **Yes** |
| `GROQ_MODEL` | Groq LLM model name | `llama-3.3-70b-versatile` | No |
| `GMAIL_QUERY` | Gmail search query filter | `is:unread` | No |
| `GMAIL_POLL_INTERVAL` | Interval in seconds between Gmail syncs (min 60s) | `120` | No |
| `DASHBOARD_HOST` | Host address for web dashboard | `127.0.0.1` | No |
| `DASHBOARD_PORT` | HTTP port for web dashboard | `5055` | No |
| `DASHBOARD_APPROVAL_TOKEN` | Optional token secret for API approvals | `""` | No |
| `LINKEDIN_MODE` | Execution mode for LinkedIn (`mock`, `live`, `browser`) | `mock` | No |
| `MAX_EMAIL_SENDS_PER_HOUR` | Fail-closed hourly rate limit for emails | `10` | No |
| `MAX_EXTERNAL_ACTIONS_PER_DAY` | Fail-closed daily external action limit | `50` | No |

---

## 📋 REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the single-page dashboard UI |
| `GET` | `/api/stats` | Fetches real-time counts, KPIs, and recent timeline |
| `GET` | `/api/folder/<key>` | Lists all Markdown items in a folder (`pending_approval`, `plans`, `done`, etc.) |
| `GET` | `/api/file/<folder>/<name>` | Reads file frontmatter metadata and parsed body |
| `POST` | `/api/approve/<name>` | Atomically moves item from `Pending_Approval/` to `Approved/` |
| `POST` | `/api/reject/<name>` | Atomically moves item from `Pending_Approval/` to `Rejected/` |
| `GET` | `/api/logs` | Fetches daily audit log events |
| `GET` | `/api/digest` | Returns auto-handled daily digest summaries |
| `GET` | `/api/done-summary` | Returns completed work statistics and ledger |

---

## 🧪 Automated Testing Suite

Run the full automated unit and pipeline test suite:

```bash
# Run dashboard API tests
uv run python -m unittest dashboard/test_app.py

# Run core script & workflow tests
uv run python -m unittest discover -s scripts
```

| Test File | Description / Coverage |
| :--- | :--- |
| `dashboard/test_app.py` | Tests all REST API endpoints, CORS preflight, approval moves, and file safety |
| `scripts/test_pipeline.py` | End-to-end simulation of email intake → plan creation → approval → execution |
| `scripts/test_reasoning_loop.py` | Tests Groq LLM prompt parsing, decision mapping, and plan formatting |
| `scripts/test_approval_watcher.py` | Tests SHA-256 draft integrity verification, rate limiting, and receipt ledger |
| `scripts/test_knowledge.py` | Tests TF-IDF keyword overlap retriever over `KnowledgeBase.md` |
| `scripts/test_linkedin_poster.py` | Tests LinkedIn mock execution and Posts API payload formatting |

---

## 🗂 Project Structure

```
ai-personal-employee/
├── AGENTS.md                    # Core agent specifications & contracts
├── README.md                    # Master documentation
├── dashboard/
│   ├── app.py                   # Flask REST API server
│   ├── test_app.py              # Dashboard API test suite
│   ├── static/
│   │   ├── index.html           # Single-page app layout
│   │   ├── app.js               # Dynamic SPA logic & 1-click approvals
│   │   └── style.css            # Dark glassmorphic design system
│   └── templates/
├── docs/
│   ├── DEPLOYMENT.md            # Production launchd/systemd deployment guide
│   ├── GUARDRAILS.md             # Security policies & rate limit rules
│   └── KnowledgeBase.md         # Reference knowledge base for grounding
├── scripts/
│   ├── .env.example             # Environment template
│   ├── config.py                # Centralized paths and settings loader
│   ├── authenticate_gmail.py    # Interactive OAuth setup utility
│   ├── gmail_watcher.py         # Gmail intake daemon
│   ├── reasoning_loop.py        # LLM reasoning & plan generator
│   ├── knowledge.py             # Knowledge retrieval engine
│   ├── approval_watcher.py      # Execution agent & approval daemon
│   ├── linkedin_poster.py       # LinkedIn posting adapter
│   ├── job_search_agent.py      # Technical job lead discovery
│   ├── workflow_utils.py        # File-system utilities & frontmatter parser
│   ├── main.py                  # Unified multi-thread supervisor
│   └── test_*.py                # Component test files
├── mcp-servers/gmail-send/      # Development-only MCP server
├── launchd/                     # macOS LaunchAgent templates (.plist)
├── systemd/                     # Linux systemd service templates (.service)
├── Inbox/                       # Intake landing folder
├── Needs_Action/                # Staged un-processed items
├── Plans/                       # Generated step-by-step action plans
├── Pending_Approval/            # Proposals awaiting human approval
├── Approved/                    # Items approved for execution
├── Rejected/                    # Human-rejected items
├── Done/                        # Completed items archive
├── Failed/                      # Quarantined failed items
└── Logs/                        # Rotating system logs & execution receipts
```

---

## 🛡 Invariants & Operational Security

1. **Immutable Draft Execution**: The execution agent sends **only** the exact text authorized by the human. It never rewrites, summarizes, or calls an LLM during execution.
2. **SHA-256 Cryptographic Hash Check**: Every `Pending_Approval` artifact includes a hash of the `draft_body`. Any post-approval modification invalidates the hash and aborts execution.
3. **Atomic File Operations**: All state transitions use temporary file writes followed by atomic renames to prevent partial reads by background daemons.
4. **Local-First State**: All logs, credentials, and state items remain strictly on local storage and are excluded from Git version control.
5. **Fail-Closed Thresholds**: Outbound rate limits (hourly email send cap, daily total external action limit) enforce automated execution circuit breakers.

---

## 🛠 Technology Stack Matrix

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Language & Runtime** | Python 3.11+ | Core runtime across all services |
| **Intake / Email API** | Gmail API · Google OAuth 2.0 | Message fetch, thread parsing & email dispatch |
| **LLM Reasoning** | Groq (`llama-3.3-70b-versatile`) | Event assessment, policy evaluation & reply drafting |
| **Knowledge Engine** | Custom TF-IDF Keyword Matcher | Auditable grounding over `KnowledgeBase.md` |
| **Web Dashboard** | Flask 3.1 · HTML5 · Vanilla CSS · JS | Live monitoring UI & 1-click human approval gate |
| **File Monitoring** | Python `watchdog` | File-system event watching for `Approved/` state |
| **Package Manager** | `uv` | Fast dependency resolution & virtualenv management |
| **Process Daemon** | launchd (macOS) / systemd (Linux) | Fault-isolated background daemon execution |

---

## 👤 Author & Maintainer

**Muhammad Soban**  
AI Engineer  
Department of Artificial Intelligence  
University of Management and Technology, Lahore, Pakistan  

[![GitHub](https://img.shields.io/badge/GitHub-Sobanshahid10-181717?style=flat-square&logo=github)](https://github.com/Sobanshahid10/ai-personal-employee)

---

<div align="center">
<sub>ChiefMind is designed for total human agency. AI generates recommendations — humans retain absolute authority.</sub>
</div>
