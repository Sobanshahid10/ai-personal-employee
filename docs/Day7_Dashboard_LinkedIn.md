# Day 7 — Dashboard Frontend and LinkedIn Poster

## Architecture

```text
Browser
  └── dashboard/static/{index.html,style.css,app.js}
        └── Flask API in dashboard/app.py
              ├── reads workflow folders from scripts/config.py
              └── moves approved decisions into Approved/
                    └── scripts/approval_watcher.py
                          └── scripts/linkedin_poster.py
                                ├── mock mode (development)
                                └── official LinkedIn Posts API (live)
```

The dashboard and API share one Flask origin. This removes deployment-time CORS
complexity while retaining the Day 6 CORS headers for a separately hosted
frontend. Every filesystem path still comes from `config.py`.

## Files

```text
dashboard/
├── app.py                 # API plus the `/` frontend route
├── test_app.py            # API and frontend-serving tests
└── static/
    ├── index.html         # Semantic dashboard structure
    ├── style.css          # Responsive dark glass design
    └── app.js             # API state, views, modal, approvals, polling
scripts/
├── approval_watcher.py    # Routes approved linkedin_post artifacts
├── linkedin_poster.py     # Mock and official API poster
├── test_linkedin_poster.py
├── config.py
├── .env.example
└── fixtures/
    └── day7_linkedin_approval.md
```

## Part A — Run and validate the dashboard

From the project folder:

```bash
cd scripts
uv sync
cd ../dashboard
../scripts/.venv/bin/python app.py
```

Then open `http://127.0.0.1:5000/`. If macOS already uses port 5000, set
`DASHBOARD_PORT=5055` in `scripts/.env`, restart Flask, and open
`http://127.0.0.1:5055/`.

Check the API independently:

```bash
curl http://127.0.0.1:5000/api/stats
```

### Dashboard test checklist

1. Confirm all six KPI cards contain live counts.
2. Open each sidebar view and confirm its files appear.
3. Click an item and confirm its frontmatter and complete Markdown body appear
   in the modal. Close it with the button, backdrop, or Escape key.
4. Put a test artifact in `Pending_Approval/`, open Pending Approval, and select
   Approve or Reject.
5. When prompted after a `403`, enter the value of
   `DASHBOARD_APPROVAL_TOKEN`. It remains only for the current browser tab.
6. To retest authentication, open browser developer tools and run
   `sessionStorage.clear()`, then perform another approval decision.
7. Confirm Activity Log shows the dashboard decision and that the affected KPI
   changes immediately.
8. Leave the page open for 30 seconds and confirm the “Updated” time changes.

The page deliberately renders API content with `textContent`, not `innerHTML`.
An email or log entry therefore cannot inject executable markup into the
dashboard.

## Part B — LinkedIn setup

### Development mode

Keep this in `scripts/.env`:

```dotenv
LINKEDIN_MODE=mock
LINKEDIN_API_VERSION=202607
LINKEDIN_REQUEST_TIMEOUT=30
```

Test the poster without contacting LinkedIn:

```bash
cd scripts
.venv/bin/python linkedin_poster.py \
  --approval-file fixtures/day7_linkedin_approval.md \
  --mode mock
```

The command prints a JSON mock receipt and adds a `linkedin_post_mocked` event
to `Logs/YYYY-MM-DD.json`.

### End-to-end approval test

1. Copy `scripts/fixtures/day7_linkedin_approval.md` into
   `Pending_Approval/`.
2. Start `scripts/approval_watcher.py` in one terminal.
3. Start the dashboard and approve the artifact in the browser.
4. The API moves it to `Approved/`; the watcher sees it within seconds.
5. The watcher calls `linkedin_poster.py`, writes the duplicate guard receipt,
   logs the result, and moves the artifact to `Done/`.
6. Confirm the item appears under Done and the mock event appears under
   Activity Log.

### Live mode

Use a LinkedIn Developer application and the official three-legged OAuth flow.
For a member post, request `w_member_social`; organization posts require
`w_organization_social` and an eligible page role. Place only the resulting
token and author URN in the ignored `scripts/.env`:

```dotenv
LINKEDIN_MODE=live
LINKEDIN_ACCESS_TOKEN=replace_with_oauth_access_token
LINKEDIN_AUTHOR_URN=urn:li:person:replace_with_member_id
LINKEDIN_API_VERSION=202607
```

Do not store a LinkedIn password or automate the login page. OAuth tokens are
revocable, scoped, and compatible with the official Posts API. Test mock mode
first. A live approval publishes publicly and cannot be undone by ChiefMind.

LinkedIn versions its APIs monthly, so review the supported version before
enabling live mode. The current default is July 2026 (`202607`).

## Approval artifact schema

```yaml
---
action_id: linkedin_unique_001
type: linkedin_post
post_body: |
  The exact approved content to publish.
created_at: "2026-07-31T10:00:00Z"
---
```

`post_body` is immutable in the same way as an approved email `draft_body`.
The execution agent does not ask the LLM to rewrite it.

## Why the frontend is designed this way

### Glassmorphism

Semi-transparent panels preserve hierarchy without making a dense operations
dashboard feel heavy. `backdrop-filter: blur(...)` blurs content behind a
panel—not the panel's own text—while translucent borders retain separation.
The CSS includes `-webkit-backdrop-filter` and remains usable if blur is not
available.

### sessionStorage instead of localStorage

The approval token disappears when the tab session ends, reducing the time a
token remains on a shared computer. `localStorage` persists across restarts and
is inappropriate here. Both stores remain readable by JavaScript, so strong
Content Security Policy and XSS prevention still matter. For an internet-facing
multi-user system, replace this simple local token with server-managed login
and secure, HttpOnly, SameSite cookies.

### Polling instead of WebSockets

Workflow files change occasionally, so one small stats request every 30 seconds
is simple, resilient, and inexpensive. WebSockets become worthwhile when users
need sub-second updates, event volume is high, or polling creates meaningful
load. They also introduce connection lifecycle and fan-out complexity.

### Modal state

The app has one modal controlled by `openDetail()` and `closeDetail()`. It
fetches detail only when required, prevents stale data from being copied into
every list item, supports Escape/backdrop closing, and avoids scattered modal
state.

### Timeline processing

The API combines dated JSON logs. The browser sorts a copy by timestamp rather
than mutating server data, then renders normalized status, source, and agent
fields. Unknown fields do not break the UI.

### Approval-token flow

Only approve/reject POST requests require the token. After a `403`, the app asks
once, stores the value for the tab, adds `X-Approval-Token` on retries, and
clears a rejected value. The server performs constant-time comparison. Never
put the token in a URL, committed file, or log.

### Fetch headers and CORS

The custom approval header is attached centrally in `apiFetch()`, preventing
individual actions from forgetting it. Day 6 already permits
`X-Approval-Token` in CORS preflight responses. For production, replace `*`
with the exact frontend origin and serve everything over HTTPS.

## Automated validation

```bash
node --check dashboard/static/app.js
scripts/.venv/bin/python -m unittest -v \
  dashboard.test_app \
  scripts.test_linkedin_poster \
  scripts.test_approval_watcher
```

These tests do not contact Gmail or LinkedIn and do not modify real workflow
folders.
