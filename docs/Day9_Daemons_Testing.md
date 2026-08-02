# Day 9 — Daemon Scheduling and Pipeline Testing

## Outcome

Day 9 turns the independently runnable ChiefMind services into supervised
background programs and adds one fast integration-contract suite. Choose the
section for the operating system that will host ChiefMind; do not install both.

## Files

```text
launchd/
├── com.chiefmind.gmailwatcher.plist.template
├── com.chiefmind.approvalwatcher.plist.template
└── com.chiefmind.dashboard.plist.template
systemd/
├── chiefmind-gmailwatcher.service.template
├── chiefmind-approvalwatcher.service.template
└── chiefmind-dashboard.service.template
scripts/
└── test_pipeline.py
```

# Part A — Daemon setup

## What a service manager does

`launchd` is macOS's service manager; `systemd` is the common Linux equivalent.
They start ChiefMind after login, capture output, restart crashed services, and
provide one place to inspect status. This is more dependable than leaving three
terminal windows open.

The repository files are templates because service managers require absolute
paths. `__VAULT_DIR__` is replaced with the repository's actual absolute path
during installation. Never install a file that still contains that marker.

Three separate services preserve fault isolation: a dashboard crash does not
stop Gmail ingestion, and a temporary Gmail failure does not stop approvals.
Do not run `scripts/main.py` or standalone watcher commands while these services
are enabled, because that would create duplicate workers.

Before either installation, keep the Mac/Linux machine awake and connected.
A sleeping laptop cannot poll Gmail. LaunchAgents and user systemd services run
as your account, so they can read your private `scripts/.env` and OAuth files
without storing secrets in a service definition.

## Step 1 — Preflight

From the project root:

```bash
cd "/Users/muhammadsoban/Desktop/Ai Personal employee"
mkdir -p Logs
scripts/.venv/bin/python scripts/main.py --check
```

Fix every reported configuration issue before installation. Also check that
each component starts independently, then stop it with Ctrl+C:

```bash
scripts/.venv/bin/python scripts/gmail_watcher.py --once
scripts/.venv/bin/python scripts/approval_watcher.py --once
scripts/.venv/bin/python dashboard/app.py
```

The first command contacts Gmail but does not send mail. The approval watcher
processes anything already in `Approved/`, so inspect that folder first.

## macOS — launchd installation

### Step 2 — Render the templates

```bash
mkdir -p "$HOME/Library/LaunchAgents"

sed "s#__VAULT_DIR__#$PWD#g" \
  launchd/com.chiefmind.gmailwatcher.plist.template \
  > "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"

sed "s#__VAULT_DIR__#$PWD#g" \
  launchd/com.chiefmind.approvalwatcher.plist.template \
  > "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"

sed "s#__VAULT_DIR__#$PWD#g" \
  launchd/com.chiefmind.dashboard.plist.template \
  > "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
```

This repository path contains spaces; the plist uses XML string elements, so
spaces are preserved correctly.

### Step 3 — Validate rendered files

```bash
plutil -lint "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
grep -R "__VAULT_DIR__" "$HOME/Library/LaunchAgents/com.chiefmind."*.plist
```

Each `plutil` call must print `OK`. The `grep` command must print nothing.

### Step 4 — Install and start

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"

launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"

launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
```

`bootstrap` is the modern equivalent of the older `launchctl load` command.
`RunAtLoad` starts each service immediately; `KeepAlive` restarts it after an
unexpected exit; `ThrottleInterval=10` prevents a tight crash loop.

### Step 5 — Verify macOS services

```bash
launchctl print "gui/$(id -u)/com.chiefmind.gmailwatcher"
launchctl print "gui/$(id -u)/com.chiefmind.approvalwatcher"
launchctl print "gui/$(id -u)/com.chiefmind.dashboard"

tail -n 50 Logs/gmailwatcher.stderr.log
tail -n 50 Logs/approvalwatcher.stderr.log
tail -n 50 Logs/dashboard.stderr.log

curl http://127.0.0.1:5000/api/stats
```

A healthy service normally shows a PID and running state. An empty error log is
good. The curl response must be JSON.

### Restart or uninstall on macOS

Restart one service after changing configuration:

```bash
launchctl kickstart -k "gui/$(id -u)/com.chiefmind.gmailwatcher"
```

Stop and unregister all three:

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.gmailwatcher.plist"
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.approvalwatcher.plist"
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.chiefmind.dashboard.plist"
```

After `bootout`, it is safe to remove the rendered plist files. The templates
in the repository remain the source of truth.

## Linux — systemd user-service installation

Use user services so credentials and workflow files remain owned by your normal
account.

### Step 2 — Render Linux templates

```bash
mkdir -p "$HOME/.config/systemd/user"

sed "s#__VAULT_DIR__#$PWD#g" \
  systemd/chiefmind-gmailwatcher.service.template \
  > "$HOME/.config/systemd/user/chiefmind-gmailwatcher.service"

sed "s#__VAULT_DIR__#$PWD#g" \
  systemd/chiefmind-approvalwatcher.service.template \
  > "$HOME/.config/systemd/user/chiefmind-approvalwatcher.service"

sed "s#__VAULT_DIR__#$PWD#g" \
  systemd/chiefmind-dashboard.service.template \
  > "$HOME/.config/systemd/user/chiefmind-dashboard.service"
```

### Step 3 — Validate and enable

```bash
systemd-analyze --user verify \
  "$HOME/.config/systemd/user/chiefmind-gmailwatcher.service" \
  "$HOME/.config/systemd/user/chiefmind-approvalwatcher.service" \
  "$HOME/.config/systemd/user/chiefmind-dashboard.service"

systemctl --user daemon-reload
systemctl --user enable --now chiefmind-gmailwatcher.service
systemctl --user enable --now chiefmind-approvalwatcher.service
systemctl --user enable --now chiefmind-dashboard.service
```

Every unit has `Restart=always`, a ten-second restart delay, SIGINT for graceful
Python shutdown, and a 30-second stop timeout.

### Step 4 — Keep services running after logout

On machines that should work without an active login session:

```bash
sudo loginctl enable-linger "$USER"
```

This changes a system account setting and requires administrator approval.

### Step 5 — Verify Linux services

```bash
systemctl --user status chiefmind-gmailwatcher.service
systemctl --user status chiefmind-approvalwatcher.service
systemctl --user status chiefmind-dashboard.service

journalctl --user -u chiefmind-gmailwatcher.service -n 50 --no-pager
journalctl --user -u chiefmind-approvalwatcher.service -n 50 --no-pager
journalctl --user -u chiefmind-dashboard.service -n 50 --no-pager

curl http://127.0.0.1:5000/api/stats
```

Stop and remove automatic startup with:

```bash
systemctl --user disable --now chiefmind-gmailwatcher.service
systemctl --user disable --now chiefmind-approvalwatcher.service
systemctl --user disable --now chiefmind-dashboard.service
```

## Daemon troubleshooting

- **Repeated restart:** inspect the service stderr log first, then run
  `scripts/main.py --check` manually.
- **Executable not found:** run `uv sync` in `scripts/` and confirm
  `scripts/.venv/bin/python` exists.
- **Dashboard restart loop:** another process probably owns
  `DASHBOARD_PORT`; stop it or change the port in `scripts/.env`.
- **OAuth failure:** run `scripts/authenticate_gmail.py` interactively and then
  restart the watcher.
- **No activity while asleep:** service managers do not keep a sleeping laptop
  awake. Use a continuously powered host for true 24/7 operation.
- **Configuration change ignored:** restart the relevant service; environment
  and `.env` values are loaded at process startup.
- **Duplicate processing:** ensure `main.py` and standalone watchers are not
  running alongside the installed services.

### Part A verification checklist

- [ ] Configuration preflight succeeds
- [ ] Templates render with no remaining `__VAULT_DIR__`
- [ ] All three services show running state
- [ ] Three stderr logs contain no startup traceback
- [ ] Dashboard stats endpoint returns JSON
- [ ] Gmail watcher reports successful polling
- [ ] Only one instance of each watcher is active

# Part B — Pipeline test suite

## What the suite proves

`scripts/test_pipeline.py` is an integration-contract suite: it verifies that
components agree on paths, schemas, API responses, and routing rules without
calling external send APIs.

| Test | Reliability boundary |
| --- | --- |
| `test_config_loads` | Required constants exist; workflow paths are absolute and created |
| `test_gmail_auth` | Real OAuth JSON parses, grants configured scopes, and has a refresh token |
| `test_knowledge_retrieval` | A realistic policy query returns grounded text |
| `test_frontmatter_parse` | Safe YAML metadata and Markdown body round-trip correctly |
| `test_processed_ids` | Gmail IDs persist and reload without touching the real store |
| `test_dashboard_stats` | Flask exposes valid KPI JSON over its test client |
| `test_duplicate_guard` | An executed action ID cannot send twice |
| `test_approval_routing` | `email_send` invokes a fake sender with the exact approved body |

Temporary directories replace all mutable production folders. The fake Gmail
sender records the call but performs no network activity. OAuth loading is
offline: the real files are parsed while expiry refresh is suppressed for the
test. This makes the suite fast and safe enough to run before every deployment.

## Run Day 9 tests

```bash
cd "/Users/muhammadsoban/Desktop/Ai Personal employee/scripts"
uv run python test_pipeline.py
```

Expected ending:

```text
Ran 8 tests

OK
```

Run the complete project regression suite as a final check:

```bash
cd "/Users/muhammadsoban/Desktop/Ai Personal employee"
scripts/.venv/bin/python -m unittest -v \
  scripts.test_pipeline \
  scripts.test_workflow_utils \
  scripts.test_main \
  scripts.test_knowledge \
  scripts.test_reasoning_loop \
  scripts.test_approval_watcher \
  scripts.test_linkedin_poster \
  dashboard.test_app
```

If a test fails, read the first traceback from the bottom upward. Run only that
test while debugging, for example:

```bash
cd scripts
uv run python -m unittest -v \
  test_pipeline.PipelineTests.test_duplicate_guard
```

### Part B verification checklist

- [ ] Eight pipeline tests pass
- [ ] Test run sends no email and posts nothing externally
- [ ] Existing project regression tests still pass
- [ ] Real workflow queues and receipts are unchanged

# Final Day 9 checklist

- [ ] Three launchd templates created and validated
- [ ] Three Linux systemd alternatives created
- [ ] Templates rendered for the chosen operating system
- [ ] Three background services enabled
- [ ] Service logs and dashboard health checked
- [ ] Eight pipeline tests passing
- [ ] Full regression suite passing
- [ ] Restart and uninstall commands documented
- [ ] ChiefMind host stays powered and connected
- [ ] Ready for Day 10
