# Changelog

All notable changes to llamawatch are documented here.

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
  dormant back-compat branches that only fired for a machine literally named "m5".
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
  config-failure fallback, `_collect_primary` DB aggregation + corrupt DB, Apollo
  snapshot delta (1 entry=zero, 2 entries=delta, corrupt line skipped), Claude JSONL
  timestamp filter + corrupt line skip.

## 0.5.1

### Fixed (pre-public audit)
- **Machine names are now fully dynamic** — the bottom vitals strip, fleet RAM bars,
  power estimate, process donuts, and mobile accordions all derive the local machine
  key from fleet config at runtime. Previously these were hardcoded to "M5" and would
  silently fail for any user whose local machine had a different name.
- **PWA icon changed from "M5" to "LW"** — the app icon no longer shows a private
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
