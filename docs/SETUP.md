# llamawatch setup guide

This guide takes you from nothing to a working llamawatch, one step at a time. You do not need to know how to code. Every command is meant to be copied and pasted into a terminal exactly as shown.

Work through it in levels. Level 1 gets the dashboard running. Each level after that adds something, and you can stop at any point. Every step ends with **You'll know it worked when**, so you can check before moving on. If something goes wrong, look in [Troubleshooting](#troubleshooting) at the end.

**Contents**

- [Before you start](#before-you-start)
- [Level 1: Install and open it](#level-1-install-and-open-it)
- [Level 2: Connect your models](#level-2-connect-your-models)
- [Level 3: Keep it running and open it from your phone](#level-3-keep-it-running-and-open-it-from-your-phone)
- [Level 4: The Settings panel, tab by tab](#level-4-the-settings-panel-tab-by-tab)
- [Level 5: The other pages](#level-5-the-other-pages) (Research, Jobs, Approvals, Spend, Agents)
- [Level 6: Optional extras](#level-6-optional-extras) (web search, Knowledge page, chat attachments, temperature history)
- [Editing config.local.json by hand](#editing-configlocaljson-by-hand)
- [Troubleshooting](#troubleshooting)
- [Getting help from an AI assistant](#getting-help-from-an-ai-assistant)

---

## Before you start

**What llamawatch is.** A dashboard you open in a web browser. It runs on the computer where your AI models run, and shows what those models and that computer are doing. It can also show other computers on your network.

**What you need:**

| You need | How to check | If it's missing |
|---|---|---|
| A Linux or macOS computer. On Windows, use WSL2 (see below) | | |
| Python 3.10 or newer | `python3 --version` prints 3.10 or higher | Ubuntu/Debian: `sudo apt install python3 python3-venv`. Fedora: `sudo dnf install python3`. macOS: install from [python.org](https://www.python.org/downloads/) |
| git | `git --version` prints a version | Ubuntu/Debian: `sudo apt install git`. Fedora: `sudo dnf install git`. macOS: `xcode-select --install` |
| A model server (optional to start) | see [Level 2](#level-2-connect-your-models) | llamawatch runs without one; the model panels stay empty |

**Windows users:** llamawatch's server does not run on Windows directly. Install WSL2 (open PowerShell as administrator, run `wsl --install`, restart), open the "Ubuntu" app, and follow this guide inside it. You then open the dashboard in your normal Windows browser at the same address.

**How to open a terminal:** Linux: press Ctrl+Alt+T, or search for "Terminal". macOS: press Cmd+Space, type "Terminal", press Enter.

---

## Level 1: Install and open it

### Step 1. Download llamawatch

```bash
cd ~
git clone https://github.com/Huzy85/llamawatch.git
cd llamawatch
```

**You'll know it worked when** `ls` shows files including `README.md` and `pyproject.toml`.

### Step 2. Install it into its own folder

Modern Linux systems refuse `pip install` into the system Python (you'd see "externally-managed-environment"). A virtual environment, or "venv", is a private folder for llamawatch's Python packages, so nothing else on your computer is touched.

```bash
python3 -m venv .venv
.venv/bin/pip install .
```

This takes a minute or two.

**You'll know it worked when** the last line says `Successfully installed ... llamawatch-0.6.0`.

### Step 3. Let it look around your computer

```bash
.venv/bin/llamawatch init
```

This checks for model servers on the usual ports, your graphics card, temperature sensors, and services. It saves what it finds in `~/.config/llamawatch/config.local.json`.

**You'll know it worked when** it prints a "Found:" list and `Config written to ~/.config/llamawatch/config.local.json`. Finding nothing is fine. You can add everything later in Settings.

If you'd rather answer questions than let it guess, run `.venv/bin/llamawatch init --guided` instead.

> Running `init` again later rewrites that file from scratch. Anything you added in Settings is lost. After your first run, use the Settings panel instead.

### Step 4. Start it

```bash
.venv/bin/llamawatch
```

Leave this terminal open. llamawatch runs for as long as the window stays open. To stop it, click the terminal and press Ctrl+C.

**You'll know it worked when** you see `Uvicorn running on http://127.0.0.1:8400`.

### Step 5. Open it

On the same computer, open a browser and go to:

```
http://localhost:8400/studio
```

**You'll know it worked when** you see the dashboard with gauges for your computer's CPU and memory.

### Just want a look first?

To see every panel filled with made-up data before setting anything up:

```bash
.venv/bin/llamawatch --sample tests/ui/fixtures/snapshot.json --port 8401
```

Then open `http://localhost:8401/studio`. Demo mode refuses every change, so you can click around safely. Press Ctrl+C to stop it.

---

## Level 2: Connect your models

llamawatch watches model servers. It does not run models itself. It works with three kinds:

| Server | Usual address | Type to pick in Settings |
|---|---|---|
| llama.cpp (`llama-server`) | `http://localhost:8080` | llama.cpp |
| Ollama | `http://localhost:11434` | Ollama |
| LM Studio, vLLM, or anything else "OpenAI-compatible" | LM Studio: `http://localhost:1234`, vLLM: `http://localhost:8000` | OpenAI-compatible |

Write the address **without** `/v1` on the end. llamawatch adds it.

### If `init` already found your server

Open the dashboard. The Command view shows the model name and a status ring. You're done with this level.

### If it didn't, add it by hand

1. Click the **gear icon** (top right) to open Settings.
2. Go to the **Backends** tab.
3. Under **Add Backend**, fill in a **Name** (anything, for example `my-llama`), pick the **Type**, type the **URL** and, if you know it, the **Context window** (the model's maximum tokens, for example `32768`).
4. Click **Add Backend**.
5. Click **Test** on its row. A green dot means llamawatch can reach it.

**You'll know it worked when** the Command view shows your model's name instead of "offline".

### Get the most out of llama.cpp

Start `llama-server` with `--metrics`, for example:

```bash
llama-server -m your-model.gguf --port 8080 --metrics
```

Without `--metrics`, the token usage panels stay at zero. The slot pips (which "seats" on the server are busy) come from llama.cpp's `/slots` page, which is on by default.

### Things to know

- Backends must be servers that need no API key. To use a paid online model (DeepSeek, OpenAI and so on), add it on the [Research page](#research) instead.
- **Model Display Names** in the Backends tab lets you show a friendly name instead of a long model file name.
- **Chat System Prompt** (also in Backends) is text sent at the start of every chat, for example "Answer in British English. Be brief."

---

## Level 3: Keep it running and open it from your phone

### Run it in the background (Linux)

So far llamawatch stops when you close the terminal. To run it as a background service that starts on its own, create a systemd user service.

1. Stop llamawatch if it's running (Ctrl+C).
2. Create the service file:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/llamawatch.service <<'EOF'
[Unit]
Description=llamawatch dashboard
After=network-online.target

[Service]
ExecStart=%h/llamawatch/.venv/bin/llamawatch
Restart=on-failure

[Install]
WantedBy=default.target
EOF
```

(`%h` means your home folder. If you cloned llamawatch somewhere other than `~/llamawatch`, change that line.)

3. Turn it on:

```bash
systemctl --user daemon-reload
systemctl --user enable --now llamawatch
loginctl enable-linger "$USER"
```

The last line keeps it running after you log out, and starts it when the computer boots.

**You'll know it worked when** `systemctl --user status llamawatch` says `active (running)`.

Useful commands from now on:

```bash
systemctl --user restart llamawatch      # after editing config by hand
journalctl --user -u llamawatch -n 50    # see the last 50 log lines
systemctl --user stop llamawatch         # stop it
```

On macOS, keep it running in a terminal window, or use a tool such as `launchd`. That setup isn't covered here.

### Set a password (do this before opening it to your network)

The dashboard can run commands on your computer. With no password, those controls only work from the computer itself. Before anything else can reach the dashboard, set a password:

1. On the computer running llamawatch, open `http://localhost:8400/studio`.
2. Settings (gear icon), **General** tab, **Authentication**.
3. Tick **Require password**, type a password, click **Save**.

**You'll know it worked when** you are asked to sign in after reloading the page.

**Session expiry (days)** sets how long you stay signed in on each device (default 7).

### Option A: open it from your phone on your home network

1. Set a password first (above).
2. Settings, **General**, **Server** section: change **Host** to `0.0.0.0` and save.
3. Restart llamawatch (`systemctl --user restart llamawatch`, or Ctrl+C and start it again). Host and port changes need a restart.
4. Find the computer's address: on Linux run `hostname -I` and take the first number (it looks like `192.168.1.50`). On macOS run `ipconfig getifaddr en0`.
5. On your phone, connected to the same Wi-Fi, open `http://192.168.1.50:8400/studio` (with your number).

If the phone can't connect, the computer's firewall is probably blocking it. Fedora: `sudo firewall-cmd --add-port=8400/tcp --permanent && sudo firewall-cmd --reload`. Ubuntu: `sudo ufw allow 8400/tcp`.

**Add it to your home screen:** in the phone browser's menu, choose "Add to Home Screen". It then opens like an app.

### Option B: open it from another computer without exposing it

An SSH tunnel keeps llamawatch private (Host stays `127.0.0.1`). On the other computer:

```bash
ssh -L 8400:localhost:8400 you@192.168.1.50
```

While that stays open, `http://localhost:8400/studio` on the other computer shows the dashboard. To llamawatch it counts as a local visit.

### Option C: open it from anywhere with Tailscale

Tailscale is a free app that links your own devices into a private network, so your phone can reach the dashboard on mobile data or someone else's Wi-Fi. Nothing is opened to the internet and you don't touch your router.

1. Set a password first (above).
2. Install Tailscale on the llamawatch computer and on your phone from [tailscale.com/download](https://tailscale.com/download). Sign in to the same account on both.
3. On the llamawatch computer, run:

   ```bash
   sudo tailscale serve --bg 8400
   ```

   The first time, it may give you a link to switch on HTTPS for your account. Open it, click enable, then run the command again.
4. It prints an address like `https://your-computer.tail1234.ts.net`. Open that on your phone with `/studio` on the end.

**You'll know it worked when** the dashboard opens on your phone with Wi-Fi switched off.

Host stays `127.0.0.1`, so only your own Tailscale devices can reach it. Because the address is `https://`, "Add to Home Screen" and the voice button work too. To stop sharing it, run `sudo tailscale serve reset` (this removes every Tailscale Serve share on that computer).

Use `tailscale serve`, not `tailscale funnel`. Funnel puts the dashboard on the public internet for anyone to find.

### Reverse proxies and tunnels

If you put llamawatch behind nginx, Caddy, Cloudflare Tunnel, Tailscale Serve or Tailscale Funnel, set a password first. llamawatch spots requests that come through a proxy and treats them as remote, so with no password the controls are refused.

### Change the port

If port 8400 is taken, change **Port** in Settings, General, Server and restart. For a one-off, start with `.venv/bin/llamawatch --port 8500`.

---

## Level 4: The Settings panel, tab by tab

Open it with the gear icon. Changes save straight away, except Port and Host, which need a restart.

### Studio tab

- **Views and panels.** Switch each view (Command, System, Knowledge) and each panel on or off. Hide what you don't use.
- **Voice command.** Adds a microphone button. It needs Chrome or Edge, and only works on `localhost` or an `https://` address, because browsers block the microphone on plain `http://` network addresses.
- **Quick Actions.** Up to 6 buttons shown below the terminal. Each has a **Button label**, an **Icon** and a **Shell command** that runs on the llamawatch computer when tapped. Example: label `Restart Ollama`, command `systemctl --user restart ollama`. Try the command in a normal terminal first.

### Fleet tab

**Machines.** The computers the System view shows.

- The computer llamawatch runs on: add it with **This machine (read locally, no SSH)** ticked.
- Other computers: fill in **Name**, **Host / IP** (for example `192.168.1.60`) and **SSH user**. llamawatch logs in with SSH keys. It never asks for a password.
- **Colour** sets the machine's colour across the dashboard.
- **Idle watts** and **Max watts** (optional) give an estimated power draw from CPU load. Rough figures from the maker's spec sheet are fine, for example idle `12`, max `65`.

**Set up SSH keys for a remote machine** (once per machine, from the llamawatch computer):

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519      # make a key if you don't have one; press Enter at each question
ssh-copy-id you@192.168.1.60                           # asks for that machine's password once
ssh -o BatchMode=yes you@192.168.1.60 "echo ok"        # must print: ok
```

If the last command prints `ok` without asking for anything, llamawatch can read that machine. For temperatures, install `lm_sensors` (Fedora) or `lm-sensors` (Ubuntu) on the remote machine.

**Agents.** Background programs you want shown as online or offline, for example a bot that runs in Docker. Give each a name, its **Container name(s)** and the **Machine** it runs on. They show on the Monitor page and the Agents page.

### Backends tab

Your model servers. See [Level 2](#level-2-connect-your-models).

### Services tab

Programs you want a health light for. Each entry has:

- **Name**: anything.
- **Type**:
  - **systemd (user)**: a service you run as yourself (`systemctl --user ...`).
  - **systemd (system)**: a service that runs for the whole machine (`sudo systemctl ...`). Showing its status works as-is; restarting it from the dashboard needs `sudo` that doesn't ask for a password.
  - **Docker**: a container, by its name.
  - **Process**: a running program, by its exact process name (for example `ollama`). Watch-only; it has no restart button.
- **Unit / Container**: for example `ollama.service`, or the container name.
- **Port** (optional): shown for reference.
- **Health URL** (optional): an address llamawatch opens to check the service answers, for example `http://localhost:11434/api/tags`. If the service runs but this address fails, the light turns amber ("degraded").

**Refresh Discovery** lists services and containers it can see, so you can pick from them.

### General tab

- **Dashboard**, **Name**: the title in the header and on your phone's home screen.
- **Authentication**: see [Set a password](#set-a-password-do-this-before-opening-it-to-your-network). Changing the password or turning sign-in off asks for the **Current password**.
- **Server**: **Port** and **Host**. Restart after changing.
- **Web Search**: the SearXNG address used by chat's web search toggle. See [Web search](#web-search-searxng).
- **Sensors**: which temperature sensors to show. Leave on auto unless a reading is wrong.

---

## Level 5: The other pages

The side rail on the left (a bar at the bottom on phones) moves between pages. All of them sit behind the same password.

### Research

Ask a question and get a written report with numbered sources, for and against, and what couldn't be checked. It searches the web, reads the pages, and writes the answer with the model you pick.

**Install the extra parts once:**

```bash
cd ~/llamawatch
.venv/bin/pip install ".[research]"
.venv/bin/playwright install chromium
```

Then restart llamawatch.

**Pick a search engine.** Research needs web search. You have four choices:

| Engine | Cost | What to do |
|---|---|---|
| SearXNG | free, private | Run your own (see [Web search](#web-search-searxng)) and put its address in Settings, General, Web Search |
| DuckDuckGo | free | Nothing. It is also the automatic backup when the main engine finds too little |
| Brave Search | API key | Add to `config.local.json` (below) |
| Tavily | API key | Add to `config.local.json` (below) |

To use Brave or Tavily, add this to `config.local.json` (see [Editing config.local.json by hand](#editing-configlocaljson-by-hand)):

```json
"research": {
  "search": {
    "provider": "brave",
    "brave": { "api_key": "your-brave-key" }
  }
}
```

For Tavily, use `"provider": "tavily"` and `"tavily": { "api_key": "your-tavily-key" }`.

**Pick a model.** Open the Research page from the side rail. Its **Model** list shows your backends (local, free). To add a paid online model, open that list, choose **+ Add an online API…** at the bottom and fill in:

- **Name in the model list**: for example `DeepSeek`.
- **API address**: for example `https://api.deepseek.com/v1`.
- **Model name**: for example `deepseek-chat`.
- **API key**: paste it. It's stored encrypted.
- **Price per million tokens in / out** and **Currency** (optional), so the page can show the cost.

**Run one.** Type the question, pick a depth, press the button.

- **Quick answer**: about 4 to 6 minutes, reads about 12 pages.
- **Full report**: about 15 to 30 minutes, reads 25 to 40 pages and checks each claim.

Paid models ask you to confirm the estimated cost first. One run goes at a time. You can close the page; the run carries on. Finished reports can be printed on A4, saved as PDF, or exported as Markdown or HTML.

**You'll know it worked when** a report appears with numbered sources at the bottom.

### Jobs

Every scheduled task on the computer in one list, with failed ones first: your systemd user timers, cron lines, and research runs. Click one to see its recent log lines.

**Add a job:** click the add button and fill in a **name** (for example `nightly-backup`), a **description**, the **command** and a **schedule**. Schedules can be plain English: `every 15 min`, `daily 06:30`, `Mon 02:00`, `mon,wed,fri 09:00`. The form shows when it will next run before you save.

Jobs you add can be edited, paused, run now, or deleted. Cron lines and system timers are shown read-only. Jobs only run while you're logged in unless you've run `loginctl enable-linger "$USER"` once (you did this in Level 3 if you set up the background service).

### Approvals

Anything that changes your machines (quick actions, starting and stopping services and containers, running or changing jobs, opening the terminal, requests from agents) can be made to wait for your OK.

- **Rules**: for each kind, choose **Allow**, **Ask** or **Block**. Everything starts on Allow except agent requests, which start on Ask.
- **Blocklist**: commands that can never be approved, even by you. A built-in list covers the obvious dangers; add your own patterns.
- **Allowlist**: exact agent commands that are fine to run without asking.
- **Requests expire after**: minutes a request waits before it counts as denied (default 30).
- **Notify**: a command run whenever something is waiting, so your phone can buzz. Example with the free ntfy app (pick your own long, random topic name):

  ```
  curl -d "$LW_TITLE" https://ntfy.sh/pick-a-long-random-name
  ```

  It can use `$LW_TITLE`, `$LW_KIND`, `$LW_REQUESTER`, `$LW_COMMAND` and `$LW_URL`.
- **Audit trail**: every request and decision, searchable.

**Let a script or agent ask for permission.** Click **New token** on the Approvals page and copy it (it is shown once and starts `lwa_`). The script sends it in a header. The token can ask and check, never approve.

```bash
# ask
curl -s -X POST http://localhost:8400/api/approvals \
  -H "X-Llamawatch-Token: lwa_your_token" -H "Content-Type: application/json" \
  -d '{"title": "Delete old backups", "command": "rm -r /backups/2025", "requester": "cleanup-script"}'
# the reply includes an "id"; wait up to 30 seconds for your decision
curl -s "http://localhost:8400/api/approvals/THE_ID?wait=30" -H "X-Llamawatch-Token: lwa_your_token"
```

The second reply's `"status"` is `pending` (ask again), `approved`, `denied`, `expired` or `blocked`. Your script should only go ahead on `approved`.

### Spend

A daily ledger of what you spend on paid AI services, next to the free tokens your own machines produced.

It reads, with no setup: your local backends (llama.cpp needs `--metrics`), Claude Code and Codex logs on this computer, research runs, and dashboard chats.

**Prices are never guessed.** Type each provider's price per million tokens (in, out, cached) and currency in the prices editor on the Spend page. Until a price is set, that provider shows tokens but no cost.

**Spend from your own accounts.** To include what scripts and apps spend through OpenAI, Anthropic, DeepSeek and others, paste a key under Connected providers. Which providers work, which key to make and how are in [SPEND-PROVIDERS.md](SPEND-PROVIDERS.md).

### Agents

What's running now, and a replay of past runs over the last 7 days: Claude Code sessions, Codex runs, research runs, and the agents you listed in Settings, Fleet. Each replay shows the steps, how long each took and the tokens used. It is read-only.

---

## Level 6: Optional extras

### Web search (SearXNG)

SearXNG is a free search engine you run yourself. It powers the chat's web search toggle and is the default engine for Research. You need Docker installed.

llamawatch reads SearXNG's results in JSON format, which SearXNG ships switched off. If JSON is off, every search fails with `403 Forbidden`. Step 2 below switches it on.

1. Start it, keeping its settings in a folder you can edit:

```bash
mkdir -p ~/searxng
docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v ~/searxng:/etc/searxng searxng/searxng
```

2. Wait 20 seconds, then turn on JSON output, which llamawatch needs. Open the settings file:

```bash
nano ~/searxng/settings.yml
```

Press Ctrl+W, type `formats:` and press Enter. You'll see:

```yaml
  formats:
    - html
```

Add a line so it reads:

```yaml
  formats:
    - html
    - json
```

Save with Ctrl+O, Enter, then Ctrl+X. Restart it: `docker restart searxng`.

3. Check it:

```bash
curl -s "http://localhost:8888/search?q=test&format=json" | head -c 200
```

**You'll know it worked when** you see text starting with `{"query"`. If you see `403 Forbidden`, JSON is still off: redo step 2, check `- json` lines up exactly under `- html`, and run `docker restart searxng`.

4. In llamawatch: Settings, General, **Web Search**, type `http://localhost:8888` and save.

### Knowledge page

The Knowledge view has three tabs, each switched on by its own source.

**Files** works with no setup. Upload sends files to `~/inbox` on the llamawatch computer. To hand a file to your phone, copy it into `~/inbox/llamawatch-share/` and it appears with a download button. Change the folders with `inbox_path` and `share_path` in `config.local.json`.

**Docs** shows Markdown (`.md`) files from folders you choose:

```json
"docs_roots": [
  { "path": "~/notes", "label": "Notes" },
  { "path": "~/projects/my-app/docs", "label": "My app" }
]
```

**Library** shows the collections in a ChromaDB vector database (version 1.0 or newer):

```json
"widgets": {
  "config": {
    "library": { "chromadb_url": "http://localhost:8000" }
  }
}
```

If you also run a search service for your documents, its address goes at the top level. llamawatch calls `<hub_url>/docs?q=...` and sends the key file's contents as the `X-API-Key` header:

```json
"hub_url": "http://localhost:8300",
"hub_api_key_path": "~/.config/my-hub/key.txt"
```

**Intel** shows two optional data feeds that you fill from your own scripts.

*Articles* reads a SQLite file:

```json
"press_room_db": "~/data/articles.db"
```

The file needs this table. Every column, including `last_written_at`, must exist, or the list stays empty even when the table has rows.

```sql
CREATE TABLE articles (
    id TEXT PRIMARY KEY,
    topic_key TEXT,
    title TEXT,
    hook TEXT,
    analysis TEXT,
    predictions TEXT,
    signal_card TEXT,
    topic_display TEXT,
    tier INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    created_at TEXT,
    last_written_at TEXT
);
```

*Predictions* reads PostgreSQL. Install the extra part, then add the connection:

```bash
.venv/bin/pip install ".[predictions]"
```

```json
"predictions_dsn": "postgresql://reader@localhost:5432/forecasts"
```

To keep the database password out of the file, leave it out of the address and start llamawatch with the environment variable `PREDICTIONS_DB_PASSWORD` set instead (for the background service, add a line `Environment=PREDICTIONS_DB_PASSWORD=...` under `[Service]`, or better, `EnvironmentFile=` pointing to a file only you can read). The table:

```sql
CREATE TABLE predictions (
    id UUID PRIMARY KEY,
    prediction_text TEXT,
    domain TEXT,
    geography TEXT,
    timeframe TIMESTAMPTZ,
    confidence_score NUMERIC,
    verified BOOLEAN,
    generated_at TIMESTAMPTZ,
    brief TEXT,
    reasoning TEXT
);
```

If neither Intel feed is set, the Intel tab says so and the Knowledge view opens on Library.

### Chat attachments (PDF and Word)

Text files attach to chat with no setup. For PDF and Word files:

```bash
.venv/bin/pip install ".[documents]"
```

Then restart llamawatch.

### Temperature history

On wide screens (1700 pixels or more), the System view has a History panel. It reads a log file that you fill with your own script, one line per reading:

```
{"ts": "2026-01-01T12:00:00+00:00", "temps": {"cpu": 54.3, "gpu": 48.0}}
```

The default file is `~/logs/temp-monitor.jsonl`. Change it with `"temp_log_path"` in `config.local.json`. A simple way to fill it is a Job (see [Jobs](#jobs)) that runs every minute and appends a line. Without the file, the panel stays empty.

---

## Editing config.local.json by hand

Some settings have no screen in the Settings panel yet. You add them to the file instead.

The file lives at `~/.config/llamawatch/config.local.json`. It's JSON: curly brackets, `"name": value` pairs, commas between pairs, and no comma after the last one.

1. Make a backup: `cp ~/.config/llamawatch/config.local.json ~/.config/llamawatch/config.local.json.backup`
2. Open it: `nano ~/.config/llamawatch/config.local.json`
3. Add your setting as a new line just after the first `{`, with a comma at the end. For example:

```json
{
  "docs_roots": [ { "path": "~/notes", "label": "Notes" } ],
  "port": 8400,
  ...
```

If the key already exists (for example `"research"` or `"widgets"`), add inside the existing block rather than adding a second one.

4. Save (Ctrl+O, Enter, Ctrl+X) and check the file is still valid JSON:

```bash
python3 -m json.tool ~/.config/llamawatch/config.local.json > /dev/null && echo valid
```

5. Restart llamawatch. Hand edits are read only at start-up.

**About secrets.** Passwords and keys you enter in the Settings panel or on the Research page are encrypted in this file with a key stored beside it (`secret.key`). Values you type into the file by hand stay as you typed them. Keep the file private (`chmod 600 ~/.config/llamawatch/config.local.json`) and never share it or commit it.

**Every hand-edited setting:**

| Setting | What it does | Example |
|---|---|---|
| `docs_roots` | Folders for the Docs tab | `[{"path": "~/notes", "label": "Notes"}]` |
| `inbox_path` | Where uploads go (default `~/inbox`) | `"~/inbox"` |
| `share_path` | Files offered for download (default `<inbox>/llamawatch-share`) | `"~/inbox/share"` |
| `widgets.config.library.chromadb_url` | ChromaDB for the Library tab | `"http://localhost:8000"` |
| `hub_url`, `hub_api_key_path` | Document search service for the Library tab | `"http://localhost:8300"` |
| `press_room_db` | SQLite articles file for Intel | `"~/data/articles.db"` |
| `predictions_dsn` | PostgreSQL for Intel predictions | `"postgresql://reader@localhost:5432/forecasts"` |
| `temp_log_path` | Temperature history file | `"~/logs/temp-monitor.jsonl"` |
| `research.search.provider` | Research search engine: `searxng`, `brave`, `tavily`, `duckduckgo` | `"brave"` |
| `research.search.fallback` | Backup engine (default `duckduckgo`) | `"duckduckgo"` |
| `research.search.brave.api_key`, `research.search.tavily.api_key` | Search API keys | `"your-key"` |
| `research.workers` | Pages read at once (default 3) | `3` |
| `research.reader.cache_days` | Days a fetched page is reused (default 3) | `3` |

**Environment variables** (set them before starting llamawatch, or in the service file with `Environment=`):

| Variable | What it does |
|---|---|
| `LLAMAWATCH_PORT`, `LLAMAWATCH_HOST` | Override port and host |
| `LLAMAWATCH_AUTH` | `true` or `false`, overrides the password switch |
| `LLAMAWATCH_SECRET_KEY` | Keep the encryption key out of the config folder |
| `PREDICTIONS_DB_PASSWORD` | Password for the predictions database |

**Start-up options:** `--port 8500`, `--host 0.0.0.0`, `--no-auth` (turns sign-in off for this run only), `--sample FILE` (demo data).

---

## Troubleshooting

**"externally-managed-environment" when installing.** You ran plain `pip install`, which your system blocks. Install into a venv instead, from inside the `llamawatch` folder: `cd ~/llamawatch`, then `python3 -m venv .venv`, then `.venv/bin/pip install .` (see [Step 2](#step-2-install-it-into-its-own-folder)).

**"No module named venv" or "ensurepip is not available".** Ubuntu/Debian: `sudo apt install python3-venv`, delete the half-made folder with `rm -r .venv`, and try Step 2 again.

**"llamawatch: command not found".** Use the full path `.venv/bin/llamawatch` from inside the `llamawatch` folder.

**"address already in use" when starting.** Something else is using port 8400, maybe another copy of llamawatch. Find it with `ss -ltnp | grep 8400`. Stop it, or start llamawatch on another port: `.venv/bin/llamawatch --port 8500`.

**The browser says "can't connect".** Is llamawatch still running? Check the terminal, or `systemctl --user status llamawatch`. Did you type `http://` (not `https://`) and the right port?

**The dashboard opens but the model shows offline.** Check the server is running: `curl http://localhost:8080/health` (llama.cpp) or `curl http://localhost:11434/api/tags` (Ollama). Then check the URL in Settings, Backends has no `/v1` on the end, and press Test.

**Token usage stays at zero.** Start `llama-server` with `--metrics`.

**A remote machine shows offline.** From the llamawatch computer, run `ssh -o BatchMode=yes you@192.168.1.60 "echo ok"`. If it doesn't print `ok` on its own, redo the [SSH key steps](#fleet-tab).

**Docker panel is empty.** Your user must be allowed to use Docker: `sudo usermod -aG docker "$USER"`, then log out and back in. Test with `docker ps`.

**A service shows "unknown".** Check the name: `systemctl --user status the-name.service` for a user service, `systemctl status the-name.service` for a system one. Pick the matching type in Settings, Services.

**Chat web search or Research finds nothing, or SearXNG gives `403 Forbidden`.** SearXNG's JSON output is switched off. Add `- json` under `formats:` in `~/searxng/settings.yml` and run `docker restart searxng`. Full steps in [Web search](#web-search-searxng).

**Research says Playwright or Chromium is missing.** Run both install lines in [Research](#research) and restart.

**Voice button does nothing.** Use Chrome or Edge, and open the dashboard on `localhost` or over `https://`.

**Intel or Library says "not set up".** Add the setting to `config.local.json` and restart. See [Knowledge page](#knowledge-page).

**I edited config.local.json and nothing changed.** Restart llamawatch. If it won't start, the file has a typo: run the `json.tool` check above, or restore your backup.

**Quick action buttons or the terminal say "not permitted".** With no password set, they only work from the llamawatch computer itself. Set a password to use them from other devices.

**I forgot the password.**

1. Stop llamawatch (`systemctl --user stop llamawatch`, or Ctrl+C).
2. Open `~/.config/llamawatch/config.local.json`, delete the whole `"auth_password_hash": "..."` line, and change `"auth_enabled": true` to `"auth_enabled": false`. Mind the commas.
3. Start llamawatch, open `http://localhost:8400/studio` on that computer, and set a new password in Settings, General.

**Start again from scratch.** Stop llamawatch, then `mv ~/.config/llamawatch ~/.config/llamawatch.old` and run `.venv/bin/llamawatch init`. Your old settings stay in the `.old` folder.

---

## Getting help from an AI assistant

If you use an AI coding assistant (Claude, ChatGPT, Cursor and so on), paste this prompt and fill in the brackets. It gives the assistant what it needs to help without guessing.

```
I'm setting up llamawatch (https://github.com/Huzy85/llamawatch), a self-hosted
dashboard for local LLMs. Its setup guide is docs/SETUP.md in that repo; please
follow it rather than guessing.

Facts:
- Installed in ~/llamawatch with a venv at ~/llamawatch/.venv
- Config file: ~/.config/llamawatch/config.local.json (JSON; hand edits need a restart)
- Default address: http://localhost:8400/studio
- My computer: [Linux distro / macOS / WSL2]
- My model server: [llama.cpp / Ollama / LM Studio] at [address]

What I'm trying to do: [describe]
What happened instead: [paste the exact error or what you see]

Rules: give me one step at a time with exact commands, tell me how to check each
step worked, and don't put real passwords or keys in anything you show me.
Never paste my config.local.json or secret.key anywhere public.
```
