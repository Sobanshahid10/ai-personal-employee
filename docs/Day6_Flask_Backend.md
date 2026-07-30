# Day 6 — Flask Workflow Dashboard API

## What Day 6 adds

`dashboard/app.py` exposes the live ChiefMind workflow state as JSON. It reads
all paths from `scripts/config.py`; no workflow path is duplicated in the
dashboard.

The API provides eight endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/stats` | Folder counts and recent activity |
| GET | `/api/folder/<key>` | Files in one workflow folder |
| GET | `/api/file/<folder>/<name>` | Parsed metadata and Markdown body |
| POST | `/api/approve/<name>` | Atomically move a pending item to Approved |
| POST | `/api/reject/<name>` | Atomically move a pending item to Rejected |
| GET | `/api/logs` | Entries from dated JSON audit logs |
| GET | `/api/agent-log` | Last 200 lines from `agent.log` |
| GET | `/api/all-items` | Files from every workflow folder |

## Required structure

```text
chiefmind/
├── dashboard/
│   ├── app.py
│   └── test_app.py
├── scripts/
│   ├── config.py
│   ├── workflow_utils.py
│   ├── pyproject.toml
│   └── .env
├── Inbox/
├── Needs_Action/
├── Plans/
├── Pending_Approval/
├── Approved/
├── Rejected/
├── Done/
├── Failed/
└── Logs/
    ├── agent.log
    └── YYYY-MM-DD.json
```

Missing workflow and log directories are created by `config.py`.

## Configuration

Copy `scripts/.env.example` to `scripts/.env`, then adjust these optional
dashboard settings:

```dotenv
# Leave blank for local development without approval authentication.
DASHBOARD_APPROVAL_TOKEN=

# "*" is convenient locally. Use comma-separated frontend origins when exposed.
DASHBOARD_CORS_ORIGINS=*

DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=5000
DASHBOARD_DEBUG=false
DASHBOARD_MAX_FILE_BYTES=2000000
DASHBOARD_RECENT_ACTIVITY=20
```

When `DASHBOARD_APPROVAL_TOKEN` has a value, both POST endpoints require the
same value in the `X-Approval-Token` header. Keep the real token only in
`scripts/.env`, which is ignored by Git.

## Install and run

From the repository:

```bash
cd scripts
uv sync
cd ../dashboard
../scripts/.venv/bin/python app.py
```

The API should be available at `http://127.0.0.1:5000`. If macOS reports that
port 5000 is occupied, set `DASHBOARD_PORT=5055` in `scripts/.env` and use that
port in the commands below.

## Validate every endpoint

Use a simple filename without spaces for the examples, such as
`email_test123.md`.

```bash
# 1. KPI counts and recent activity
curl -i http://127.0.0.1:5000/api/stats

# 2. List a workflow folder
curl -i http://127.0.0.1:5000/api/folder/pending_approval

# 3. Read and parse one artifact
curl -i http://127.0.0.1:5000/api/file/pending_approval/email_test123.md

# 4. Approve an artifact (this moves the file)
curl -i -X POST \
  -H "X-Approval-Token: replace-with-your-token" \
  http://127.0.0.1:5000/api/approve/email_test123.md

# 5. Reject an artifact (use a different pending file)
curl -i -X POST \
  -H "X-Approval-Token: replace-with-your-token" \
  http://127.0.0.1:5000/api/reject/email_test456.md

# 6. Read dated JSON audit logs
curl -i http://127.0.0.1:5000/api/logs

# 7. Read the last 200 agent log lines
curl -i http://127.0.0.1:5000/api/agent-log

# 8. Read items from every workflow folder
curl -i http://127.0.0.1:5000/api/all-items
```

If no approval token is configured, omit the header. A missing or wrong header
returns `403`; an unknown folder returns `404`; an invalid filename returns
`400`; malformed frontmatter returns `422`; and a destination collision returns
`409`.

## Automated test

Run the isolated dashboard tests:

```bash
scripts/.venv/bin/python -m unittest -v dashboard.test_app
```

The tests use temporary folders and do not move or modify real workflow files.

## Design decisions

- `config.py` remains the single source of truth for paths and runtime options.
- Approval and rejection use an atomic filesystem rename and refuse to
  overwrite an existing destination.
- Filenames must be direct `.md` children. Traversal and symlink access are
  rejected.
- YAML dates and other non-JSON values are normalized before serialization.
- Dated audit logs use a lock-safe JSON append helper, preventing concurrent
  writers from corrupting the log.
- The API limits artifact size and returns structured errors instead of leaking
  tracebacks to clients.
- CORS defaults to open local development access, but supports an origin
  allowlist for deployment.
- Flask's app object is WSGI-ready. The built-in server is intended for local
  validation; production deployment should use a supervised WSGI server behind
  HTTPS.
