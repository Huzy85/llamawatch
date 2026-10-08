# Changelog

All notable changes to llamawatch are documented here.

## 0.6.0

### Added
- **Five new pages, reached from a side rail** (a bottom bar on phones). The
  dashboard itself is now the **Monitor** page.
  - **Research**: ask a question, pick any configured model (local or an online
    API you add from the page), and get a cited report: answer, key findings
    with sources and confidence, for and against, what could not be verified,
    and follow-up questions. Two depths. Runs carry on if you leave the page.
    Reports print to A4, save as PDF, and export as Markdown or HTML. Needs the
    `[research]` extra.
  - **Jobs**: every systemd user timer, cron line and research run on the
    machine in one list, failed ones first. Add, edit and delete your own
    timers from the page.
  - **Approvals**: quick actions, services, containers, jobs, the terminal and
    agents ask here before they act. Allow / Ask / Block rules per kind, a
    searchable audit trail, a fixed built-in blocklist plus your own patterns,
    a requester token for agents on other machines (it can ask, never
    approve), an optional notify command and a request expiry time.
  - **Spend**: a daily ledger of paid API spend next to the free tokens your own
    machines produced. Prices are typed in by you, never guessed. Connect provider
    accounts (OpenAI, Anthropic, OpenRouter, DeepSeek, Kimi, xAI, SiliconFlow,
    Novita, DeepInfra) to include spend from other apps; see
    docs/SPEND-PROVIDERS.md for what each one gives and how to connect it.
  - **Agents**: what is running now, plus a replay of each past run: its steps,
    how long they took and the tokens they used. Read only.
- **Screens 1025px+ wide and 900px+ tall (Command page).** The ring is smaller
  and a chat sits open under it; labelled Chat / Open Terminal buttons; a
  3-line message box; bigger agent rows that list their containers; the
  network graph takes at most a third of the column.
- **Dashboard sized from the space it has** (phone to 32:9): dials and text
  scale with their panel, 12px text floor, a hover side rail (bottom bar on
  phones), 1-3 dashboard pages side by side depending on window shape.
- Network panel is a line chart with an automatic scale; it keeps its recent
  history when the page rebuilds.
- Lists end on a whole row and refit when rows change height.
- Browser tests (Playwright, `-m ui`) at six screen sizes, with reference
  screenshots on demo data.
- **Wide-screen layout (1700px and up).** A gentle zoom for readability, plus
  extra information instead of empty space: the container list moves onto the
  Command view, a prediction list sits under the map on Knowledge, and the
  System view gains a **History** panel (highs, lows, averages and a chart of
  past temperatures; 24h / 3d / 7d / 30d). Phones and laptops are unchanged.
- `GET /api/history?range=24h|3d|7d|30d` reads a JSON-lines temperature log
  (`temp_log_path` config key, default `~/logs/temp-monitor.jsonl`; one object
  per line: `{"ts": "<ISO UTC>", "temps": {"cpu": 54.3, "gpu": 48.0}}`). With no
  log the panel just shows "No temperature history found yet".
- The `--sample` demo data now covers the Research page (models, past runs and
  a live run log), and the README has a one-minute video tour of every page.
- Knowledge hides an Intel panel whose source is not set up (`press_room_db`,
  `predictions_dsn`). With neither, it opens on Library and Intel shows one
  line on how to turn it on. `GET /api/knowledge/sources` reports which are set.
- **Setup guide** (`docs/SETUP.md`): step by step from installing Python to
  every optional feature, written for people who don't code. Each step says how
  to check it worked, and it ends with a troubleshooting list and a prompt to
  paste into an AI assistant. The README quick start now installs into a venv.

### Security
- Every write request is checked for a cross-site origin in one place.
- The login throttle uses the real visitor address and cannot be fooled by a
  forged `X-Forwarded-For` header.
- Changing or removing the password needs the current password, and a change
  signs out every other session. The password hash cannot be set directly.
- Size caps on uploads, chat attachments and saved layouts; uploads never
  overwrite an existing file.
- The terminal no longer passes secret-looking environment variables
  (tokens, passwords, keys) to the shell.
- Research never reaches private addresses: a site that points inward is not
  retried another way, and every request the headless browser makes (redirects,
  frames, images, scripts) must go to a public address. Downloads stream and
  stop at the size cap.
- `predictions_dsn` is now encrypted at rest like the other secrets.
- Minimum versions raised to patched releases: starlette 1.7.0, anyio 4.15.1,
  python-multipart 0.0.32.

### Fixed
- The in-browser terminal never started: the page requested a session id the
  server rejects. It now asks for `new`.
- Ctrl+C stops the server within a few seconds instead of hanging on open
  live connections.
- Services added from Settings never showed their status, and their health
  URL was ignored. The "Process" type now works (watch-only, matched by exact
  name), and the type list says user or system service.
- In-app hints no longer point to Settings screens that don't exist; they name
  the `config.local.json` key and link the setup guide. SearXNG help says to
  turn on JSON output.
- README: removed a power-cost claim the dashboard doesn't do, fixed a broken
  code block, and moved the chat system prompt to the Backends tab where it is.
- Monitor page on tablets and landscape phones: the dials, network graph and
  GPU bars were squeezed to nothing. Header values no longer run together on
  narrower screens. Agent subtitles are no longer clipped on wide screens.
- Setup guide: new section on reaching the dashboard away from home with
  Tailscale Serve, without opening it to the internet.

## 0.5.2

### Security
- **CSRF defence-in-depth on cookie-authenticated writes.** The session cookie
  is already `SameSite=Lax` (the primary defence). Added a second layer:
  `security.csrf_ok()` rejects a write whose `Origin`/`Referer` clearly belongs
  to a different site. Proxy-safe — when the app sees a loopback `Host` (a reverse
  proxy rewrote it) it can't compare, so it allows and leans on SameSite; requests
  with no `Origin` (curl/API clients) are not CSRF vectors and pass.
- **Unauthenticated live-feed socket (closed).** The `/ws` dashboard socket
  accepted connections without checking auth — unlike the terminal/chat sockets,
  it never got the gate the HTTP middleware assumes each WebSocket enforces. On
  an authenticated, network- or tunnel-exposed instance, anyone who could reach
  the host could open it without logging in and watch the full live feed (machine
  names/IPs, CPU/RAM/GPU, container list, processes, token usage). It can't run
  commands — information disclosure, not takeover — but it leaks internal topology.
  Now gated with the same `security.action_allowed` check before accept.
- **Login brute-force throttle (added).** `/auth/login` accepted unlimited
  password guesses. Argon2 makes each guess slow, but there was no cap; added a
  per-client-IP sliding-window limiter (10 attempts / 60s → `429` with
  `Retry-After`). `security.rate_limit()` + regression tests.
- **Reverse-proxy / tunnel bypass of the localhost gate (closed).** With auth
  off, dangerous actions (terminal, shell, Docker, settings writes) were
  restricted to `127.0.0.1` by checking the connection's source address. Behind
  a reverse proxy or tunnel (nginx, Caddy, Cloudflare Tunnel, Tailscale Funnel)
  the proxy is the connecting client, so every request arrived from `127.0.0.1`
  and was silently trusted as local — opening the shell to anyone who could
  reach the proxy, even when bound to `127.0.0.1`. The gate now treats a request
  as local only if it comes from loopback **and** carries no proxy forwarding
  headers (`X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded`, `X-Real-IP`,
  `CF-Connecting-IP`, `True-Client-IP`). Proxied requests are refused (fail
  closed) unless a password is set. Applies to both the HTTP middleware and the
  WebSocket terminal/chat gate (which bypasses the middleware). New
  `security.is_local_request()` plus 12 regression tests.

### Changed
- **One shell-action path.** There were two routes that ran a configured shell
  command: the live `/api/quick-action/{id}` and a dead `/api/action` (from the
  retired actions widget) that ironically had the better mechanics. Removed
  `/api/action` and routed quick actions through a single shared
  `run_shell_command` helper — so quick actions now get the concurrency cap,
  kill-on-timeout (`408`), output cap, and audit logging they previously lacked.
- **`server.py` split into a `routes/` package.** The single 1,571-line module
  that held every HTTP and WebSocket route is now a 247-line hub (app instance,
  shared state, auth middleware, core routes, router wiring). Routes are grouped
  into `routes/auth.py`, `routes/dashboard.py`, `routes/settings.py`,
  `routes/chat.py`, `routes/actions.py`, and `routes/knowledge.py`. Each reaches
  shared state and dependencies via `import llamawatch.server as srv`, the same
  pattern `routes_framework.py` already used. No behaviour change — all 589 tests
  pass unmodified.

### Fixed
- **README oversold "encrypted at rest".** Secrets are Fernet-encrypted, but the
  key (`secret.key`) sits next to the config by default — so it guards against
  casual exposure (sharing/backups), not an attacker who can read your home
  directory. Wording corrected; `LLAMAWATCH_SECRET_KEY` documented for separating
  the key.
- **Test warning** — a timeout test mocked `asyncio.wait_for` without closing the
  `proc.communicate()` coroutine it was handed, so it surfaced later as a
  "coroutine never awaited" `RuntimeWarning` attributed to random tests. The mock
  now closes it; the suite runs clean under `-W error::RuntimeWarning`.
- **Version drift** — `__init__.py` reported `0.5.1` while the package was `0.5.2`,
  so the About panel showed the wrong version. Synced to `0.5.2`.
- **Cosmetic residue from the genericisation work** — stale comments and docstring
  examples still named specific machines and hardware (a power-model comment quoting
  exact idle/TDP wattages, collector docstrings naming particular hosts, a
  `get_model_id` docstring quoting a private model name) and the JavaScript carried
  dormant back-compat branches that only fired for one particular hard-coded machine name.
  All genericised or removed, and three orphaned comment stubs (each trailed by a
  stray `>`) left in `studio.html` when the old hardcoded machine blocks were ripped
  out are gone. No behaviour change; the dashboard remains fully config-driven.

### Tests
- **173 new tests** across 11 new files, bringing the total to 589 passing.
- `test_auth_sessions` — session persistence: corrupt `sessions.json`, expired-on-load
  discard, save-failure silent, token pop after expiry, custom expiry days.
- `test_model_status_swap` — swap-lock parsing: empty file, non-numeric timestamp,
  future timestamp, threshold boundaries, `_get_n_decoded` (list/dict/fallback), KV
  usage multi-slot aggregation, `_check_generating_from_slots`.
- `test_network_collector` — `_pick_primary` (loopback-only, no interfaces, highest
  traffic), counter rollover clamped to zero, zero-elapsed fallback, global state reset
  between tests.
- `test_email_collector` — `_decode_header` RFC 2047, `_extract_name` display/addr
  fallback, `_get_preview` PGP skip + HTML fallback, IMAP failure + stale-cache
  fallback, STARTTLS failure continues, cache TTL.
- `test_ws_hub_diff_extra` — `compute_diff` identical/nested/key-ordering/list-order,
  non-serialisable values (both sides), NaN vs None, connection add/remove,
  `log_buffer` maxlen=500.
- `test_audit_extra` — empty file, whitespace-only, CRLF line endings, corrupt lines
  skipped, chmod failure silent, multi-entry roundtrip.
- `test_request_log_extra` — 200-char truncation at/below/above boundary, whitespace-
  only files, corrupt JSONL lines, limit across multiple day-files, write failure silent.
- `test_connections_extra` — all six connection types validate, missing required fields,
  `list_redacted` hides password/api_key, `resolve` returns shallow copy, secret-field
  coverage assertion.
- `test_docs_safe_resolve` — path inside root returned, `../..` traversal blocked,
  absolute path outside root blocked, symlink pointing outside root blocked (resolve
  follows links), symlink within root allowed, multi-root lookup.
- `test_press_room_search` — `%` and `_` LIKE wildcards in query, title containing
  `%`, limit clamping (0→1, 9999→200), missing DB returns empty.
- `test_token_usage_extra` — `_is_primary` exact/prefix/case/empty/None, `_labels`
  config-failure fallback, `_collect_primary` DB aggregation + corrupt DB, metrics
  snapshot delta (1 entry=zero, 2 entries=delta, corrupt line skipped), Claude JSONL
  timestamp filter + corrupt line skip.

## 0.5.1

### Fixed (pre-public audit)
- **Machine names are now fully dynamic** — the bottom vitals strip, fleet RAM bars,
  power estimate, process donuts, and mobile accordions all derive the local machine
  key from fleet config at runtime. Previously these were hardcoded to one machine name and would
  silently fail for any user whose local machine had a different name.
- **PWA icon now shows "LW"** — the app icon no longer shows a private
  machine name on the user's home screen.
- **Claude Code session path uses the real UID** — `collectors/claude_code.py` was
  reading from `/tmp/claude-1000` (hardcoded). Now reads `/tmp/claude-{uid}` so it
  works for any user, including root (UID 0) and non-default UIDs.
- **FastAPI startup event migrated to `lifespan`** — `@app.on_event("startup")` was
  deprecated in FastAPI 0.93 and will be removed. Replaced with an
  `@asynccontextmanager lifespan` function.
- **`asyncio.get_event_loop()` replaced with `get_running_loop()`** — 17 call sites
  across `server.py`, `ws_hub.py`, and `sse.py` used the deprecated form; corrected
  to the safe form for use inside async handlers.
- **Chat model picker no longer shows "(offline)" for healthy models** — the llama.cpp
  adapter returns `"healthy"` but the model picker only recognised `"ok"` and
  `"online"`. All three are now accepted.
- **`llamawatch init` sets a backend name** — auto-detected backends now include a
  `name` field (model ID, trimmed to 30 chars) so the slot occupancy panel has
  something to display on first run. Ports 8080–8084 probed (was 8080–8081 only).
- **Press room test schema** — test helpers were missing the `last_written_at` column
  that the collector queries; fixed so the test suite runs clean with no failures.

### Security (pre-public audit)
- **Secure by default**: binds to `127.0.0.1` out of the box; with no password,
  only localhost can reach the dashboard. Network access (`0.0.0.0`) now
  requires a password, with a loud startup warning otherwise.
- Terminal & chat WebSockets enforce an auth/localhost gate before connecting
  (closes an unauthenticated-remote-shell hole on networked installs).
- Quick-action, settings, and backend-test endpoints require auth/localhost.
- Credentials embedded in DSN/URL strings are redacted from `/api/settings`.
- SSH-backed docker/service actions validate container/unit names (no injection).
- XSS hardening in settings and quick-action rendering.

### Added
- **Chat panel** on the Command view — a floating, persistent window (like the
  terminal) to talk to your models. Model picker, streaming replies, an
  optional web-search toggle (SearXNG), file attach (`/api/chat/extract` —
  text natively, PDF/docx with `pypdf`/`python-docx`), and a context-usage
  meter that warns as you approach the model's limit.
- Optional per-backend **`context_window`** (Settings → Backends) — sets the
  chat meter's limit reliably for any backend; auto-detected as a fallback.
- **Full-screen mode** — a button in the header toggles the browser Fullscreen
  API. The hero ring and CPU donuts scale up with the viewport height to fill
  the extra space (circular charts stay round; layout width is unchanged).
- **About panel** — Settings → General shows the dashboard name, version,
  links to the source and README, and the AGPL licence.
- **Live network graph** — a right-column panel charts real download/upload
  throughput (from `/proc/net/dev`). It fills whatever space the column has:
  compact in normal view, a large graph in full screen.
- **Predictions world map** — interactive pan/zoom world map in the Knowledge
  view, fed from a configured PostgreSQL source. Dots are polygon-constrained
  to country borders; click a dot to see the full prediction detail.

### Fixed
- Full-screen no longer leaves blank space — the layout fills the screen and
  charts grow into the extra height instead of pooling gaps.
- Top Processes lists processes even at ~0% CPU, so an idle machine no longer
  renders as an empty donut.
- Docker container buttons now show a success/error toast (were silent), so
  actions on one-shot containers no longer look like they did nothing.

## 0.5.0

First open-source-ready release. The dashboard runs on any machine with zero
personal data in the source — all site-specific values live in a gitignored
`config.local.json`.

### Added
- **Studio dashboard** — a three-view carousel (Command / System / Knowledge)
  replacing the old drag-and-drop widget grid. Fixed, dense layout that looks
  good without manual arranging.
- **Fleet** — monitor any number of machines (local + remote over SSH). Add,
  name, colour and set power figures per machine in Settings → Fleet. Layout
  adapts from 1 to many (sideways scroll past ~5).
- **Agents panel** — track background services/containers with up/down status
  and restart controls, configured in Settings → Fleet → Agents.
- **Settings panel** — five tabs (Studio, Fleet, Backends, Services, General)
  with per-section help text and a first-run "start here" guide.
- **Panel visibility** — show/hide any view or panel from Settings → Studio.
- **Quick actions** — define toolbar buttons that run shell commands.
- **Auth** — optional password protection (Argon2id, cookie sessions persisted
  to disk).
- **PWA** — installable, with the dashboard name injected into the manifest.
- Optional integrations, all off by default and configured per-install:
  predictions (PostgreSQL), articles feed, web search (SearXNG), knowledge-hub
  RAG, docs browser, file transfer.

### Changed
- Every collector reads from config instead of hardcoded hosts/models/services.
- `model_names` / `container_descriptions` / `topology_edges` are replaced
  wholesale on save so removed entries disappear.

### Security
- No credentials or personal data in tracked source (scan-verified).
- SSH usernames, DSNs and paths come from `config.local.json` only.

### Notes
- The Studio frontend has no automated tests yet; 402 Python tests cover the
  backend and collectors.
