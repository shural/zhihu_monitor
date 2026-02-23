# 🕵️ Zhihu Monitor

A stealthy, automated Zhihu activity monitoring system built for the [Model Context Protocol (MCP)](https://modelcontextprotocol.io). It scrapes user activity feeds from [Zhihu](https://zhihu.com), stores them in a local SQLite database, and exposes everything through an MCP server so AI agents can query Chinese knowledge-sharing activity on demand.

## Key Features

- **Live Scraping** — Fetches real-time activities from any Zhihu user profile via headless Chrome
- **Anti-Bot Bypass** — Uses a custom-patched [Pinchtab](https://github.com/pinchtab/pinchtab) binary with CDP-level fingerprint masking to evade Zhihu's anti-bot detection
- **MCP Integration** — Exposes `get_activities`, `get_new_since`, `get_db_stats`, and `cookie_status` as MCP tools
- **Persistent Storage** — SQLite database with deduplication for long-term activity tracking
- **Scheduled Monitoring** — Background `monitor.py` polls configured users on a cron interval
- **macOS Sandbox Safe** — Engineered to run inside sandboxed environments (like OpenClaw) with full headless Chrome support

## Architecture

```
┌─────────────┐     MCP/stdio      ┌──────────────┐    HTTP API     ┌──────────────┐
│   AI Agent   │◄──────────────────►│  mcp_server  │◄──────────────►│   Pinchtab   │
│  (OpenClaw)  │                    │    .py       │                │  Dashboard   │
└─────────────┘                    └──────┬───────┘                │  (port 9877) │
                                          │                        └──────┬───────┘
                                          ▼                               │
                                   ┌──────────────┐               ┌──────▼───────┐
                                   │   SQLite DB   │               │ Headless     │
                                   │  (activities) │               │ Chrome (CDP) │
                                   └──────────────┘               └──────────────┘
```

The system supports two browser backends:
1. **Pinchtab** (recommended) — A persistent headless Chrome daemon with stealth fingerprinting via a custom Go binary
2. **Playwright** (fallback) — Direct browser automation, useful for debugging

## Quick Start

### Prerequisites

- macOS (tested on Apple Silicon)
- Python 3.10+
- Google Chrome installed at `/Applications/Google Chrome.app`
- Node.js 18+ (for `npx mcporter`)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/shural/zhihu_monitor.git
cd zhihu_monitor

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Install Playwright browsers (for fallback mode)
playwright install chromium

# 4. Copy and edit config
cp config.json.example config.json
# Edit config.json with your monitored users

# 5. Import your Chrome profile (for authenticated sessions)
# Close Google Chrome first!
./import_host_profile.sh

# 6. Start the Pinchtab dashboard
./start_pinchtab_dashboard.sh
```

### Usage

```bash
# Fetch activities via MCP
npx mcporter call zhihu-monitor.get_activities user=xu-ze-qiu count=10 \
  --config config/mcporter.json

# Query stored activities from the last 24 hours
npx mcporter call zhihu-monitor.get_new_since hours=24 \
  --config config/mcporter.json

# Run the scheduled monitor
python monitor.py
```

## Configuration

Edit `config.json`:

```json
{
  "browser": {
    "headless": true,
    "use_pinchtab": true,
    "pinchtab_url": "http://127.0.0.1:9877",
    "pinchtab_profile": "zhihu"
  },
  "monitor": {
    "users": ["xu-ze-qiu", "warfalcon"],
    "interval_minutes": 30
  }
}
```

## Pinchtab Patches

This project uses a **custom-patched** Pinchtab binary. The patches add critical stealth features:

1. **`BRIDGE_USER_AGENT` environment variable** — Allows setting the User-Agent string without Chrome flag parsing bugs
2. **CDP `Sec-Ch-Ua` override** — Injects `Emulation.SetUserAgentOverride` with full `UserAgentMetadata` on every tab creation to mask `HeadlessChrome` in Client Hints headers
3. **Dashboard profile path fix** — Corrects `cmd_dashboard.go` to respect `BRIDGE_PROFILE` instead of hardcoding `StateDir`

See [`patches/`](patches/) for the raw diffs and [`PINCHTAB_PATCHES.md`](PINCHTAB_PATCHES.md) for detailed explanations.

## Project Structure

```
zhihu_monitor/
├── mcp_server.py            # MCP server exposing tools to AI agents
├── pinchtab_client.py        # HTTP client for the Pinchtab browser API
├── scraper.py                # DOM extraction logic (shared between backends)
├── browser.py                # Playwright browser manager (fallback)
├── monitor.py                # Scheduled background monitoring loop
├── storage.py                # SQLite database layer
├── config.py                 # Configuration loading and validation
├── config.json               # Runtime configuration
├── start_pinchtab_dashboard.sh  # Launches the patched Pinchtab daemon
├── import_host_profile.sh    # Clones host Chrome profile into sandbox
├── SKILL.md                  # OpenClaw agent skill definition
├── WALKTHROUGH.md            # Full development journey documentation
├── PINCHTAB_PATCHES.md       # Upstream patch documentation
└── patches/                  # Git diffs for Pinchtab upstream PR
```

## License

MIT
