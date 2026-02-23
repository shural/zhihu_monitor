# Walkthrough: Building Zhihu Monitor

This document chronicles the full development journey of Zhihu Monitor — from the initial idea of scraping Zhihu activity feeds to a production-grade, stealth headless scraping system integrated with the Model Context Protocol.

## Table of Contents

- [Phase 1: The Problem](#phase-1-the-problem)
- [Phase 2: Initial Scraper](#phase-2-initial-scraper)
- [Phase 3: MCP Integration](#phase-3-mcp-integration)
- [Phase 4: The Anti-Bot Wall](#phase-4-the-anti-bot-wall)
- [Phase 5: Discovering Pinchtab](#phase-5-discovering-pinchtab)
- [Phase 6: Integrating Pinchtab](#phase-6-integrating-pinchtab)
- [Phase 7: The Headless Challenge](#phase-7-the-headless-challenge)
- [Phase 8: Defeating Client Hints](#phase-8-defeating-client-hints)
- [Lessons Learned](#lessons-learned)

---

## Phase 1: The Problem

**Goal:** Monitor specific Zhihu users' public activity feeds (answers, articles, posts) and surface new content to an AI agent on demand.

Zhihu (知乎) is China's largest Q&A platform. Unlike Twitter or Reddit, it has no public API for fetching user activities. The only way to read a user's recent activity is to visit their profile page (`zhihu.com/people/{user}/activities`) in a browser and parse the dynamically-rendered React DOM.

**Challenges identified early:**
- Zhihu heavily uses client-side React rendering — no useful data in the raw HTML
- Aggressive anti-bot detection (Geetest CAPTCHA, fingerprinting)
- Session cookies required for full content access
- Rate limiting on profile page loads

## Phase 2: Initial Scraper

We built the core scraping stack:

- **`scraper.py`** — A `ZhihuScraper` class that takes a Playwright `Page` object, navigates to a user's activity feed, waits for React hydration, and extracts activity cards from the DOM using CSS selectors
- **`browser.py`** — A `BrowserManager` that launches Chromium with stealth scripts (spoofing `navigator.webdriver`, `navigator.plugins`, etc.)
- **`storage.py`** — SQLite persistence with deduplication via `unique_id` hashing
- **`cookies.py`** — Cookie management for maintaining authenticated sessions across restarts

The scraper worked reliably in headed mode on a developer machine, extracting titles, links, summaries, and timestamps from activity cards.

## Phase 3: MCP Integration

To make the scraper accessible to AI agents, we wrapped it in an MCP server (`mcp_server.py`) exposing:

| Tool | Purpose |
|------|---------|
| `get_activities(user, count)` | Live scrape a user's profile |
| `get_new_since(hours, user)` | Query stored activities |
| `get_db_stats()` | Database overview |
| `cookie_status()` | Check session validity |

A background `monitor.py` script polls configured users on a schedule and stores new activities automatically.

The MCP server integrates with [mcporter](https://github.com/nicholasgasior/mcporter) for CLI testing:
```bash
npx mcporter call zhihu-monitor.get_activities user=xu-ze-qiu count=10 --config config/mcporter.json
```

## Phase 4: The Anti-Bot Wall

Running in headless mode immediately triggered Zhihu's defenses:

1. **Geetest Security Verification** — A sliding CAPTCHA challenge appeared on every page load
2. **User-Agent Fingerprinting** — Zhihu checks the `User-Agent` string for signs of automation
3. **WebDriver Detection** — `navigator.webdriver === true` is a dead giveaway
4. **Empty Responses** — Some requests returned blank pages or 403 errors

Our initial stealth scripts (overriding `navigator.webdriver`, injecting fake plugins) worked partially, but Zhihu's detection was more sophisticated than basic bot checks.

## Phase 5: Discovering Pinchtab

[Pinchtab](https://github.com/pinchtab/pinchtab) is a Go-based headless browser automation daemon that:
- Runs Chrome as a persistent daemon (no cold-start penalty)
- Injects stealth scripts automatically
- Exposes a simple HTTP API (`/navigate`, `/evaluate`, `/tab`)
- Supports persistent Chrome profiles (preserving cookies across restarts)

We cloned Pinchtab's source and studied its stealth implementation. Key findings:
- It uses `page.AddScriptToEvaluateOnNewDocument` for injecting stealth JavaScript
- It supports `BRIDGE_USER_AGENT` for User-Agent spoofing
- It can run in both headed and headless modes

**Decision:** Replace our direct Playwright usage with Pinchtab as the browser backend, communicating entirely over HTTP.

## Phase 6: Integrating Pinchtab

We built `pinchtab_client.py` — a pure HTTP client that:

1. Checks the Pinchtab dashboard for running instances
2. Starts the `zhihu` profile if not already running
3. Navigates to the target URL via `POST /navigate`
4. Extracts DOM data via `POST /evaluate` (same JavaScript as our Playwright scraper)
5. Closes the tab via `POST /tab` to prevent memory leaks
6. Handles stale/crashed instances with self-healing recovery

The integration required several fixes:

- **Port isolation** — Pinchtab's dashboard was assigned port `9877` to avoid conflicts with OpenClaw's built-in Pinchtab instance
- **Profile path bug** — `cmd_dashboard.go` hardcoded `StateDir` for profile lookup, ignoring `BRIDGE_PROFILE`. We patched the source and recompiled.
- **Instance reuse bug** — `_ensure_instance()` discovered running instances but forgot to `return` early, causing duplicate start attempts. Fixed with an early return.

## Phase 7: The Headless Challenge

Running Pinchtab in headless mode on macOS inside OpenClaw's sandboxed environment caused a cascade of failures:

### Problem 1: macOS Sandbox Crashpad Block
Chrome's `Crashpad` crash reporter requires access to `~/Library/Application Support/Google/Chrome/Crashpad/settings.dat`. The macOS sandbox (`sandbox-exec`) blocks this, causing Chrome to abort on startup.

**Solution:** Switch from the native `Google Chrome.app` binary to Playwright's specialized `chrome-headless-shell` binary. This stripped-down build:
- Has no GUI components (no WindowServer dependency)
- Skips Crashpad initialization
- Bypasses `com.apple.provenance` quarantine checks

### Problem 2: Nested Sandbox Conflicts
Chrome's GPU process tries to create its own sandbox. Inside OpenClaw's existing sandbox, this fails with "Operation not permitted".

**Solution:** Added `--no-sandbox` to `CHROME_FLAGS`. Since we're already running inside OpenClaw's sandbox, Chrome's internal sandboxing is redundant.

### Problem 3: Go Build Failures
The Go compiler couldn't create temporary files in `/var/folders/` (blocked by sandbox).

**Solution:** Set `GOTMPDIR` and `GOCACHE` to local project directories before compiling:
```bash
export GOTMPDIR="./tmp"
export GOCACHE="./tmp/go-cache"
go build -o ./pinchtab ./cmd/pinchtab
```

## Phase 8: Defeating Client Hints

Even with all the above fixes, headless Chrome was still detectable. The smoking gun:

```
Sec-Ch-Ua: "HeadlessChrome";v="145"
```

This HTTP header (Client Hints) is sent by Chrome on every request and **cannot be spoofed** via:
- `--user-agent` flag (only affects `User-Agent` header and `navigator.userAgent`)
- `page.AddScriptToEvaluateOnNewDocument` (JavaScript can't modify HTTP headers)
- `--disable-features=UserAgentClientHint` (flag doesn't work on headless shell)

### The Solution: CDP `Emulation.SetUserAgentOverride`

The Chrome DevTools Protocol provides `Emulation.SetUserAgentOverride` which accepts a `UserAgentMetadata` struct. This struct directly controls the `Sec-Ch-Ua` header values at the protocol level.

We patched Pinchtab's Go source in two places:

1. **`bridge.go` (`tabSetup` hook)** — Fires on every new tab creation, before any navigation:
```go
emulation.SetUserAgentOverride(b.Config.UserAgent).
    WithUserAgentMetadata(&emulation.UserAgentMetadata{
        Brands: []*emulation.UserAgentBrandVersion{
            {Brand: "Not(A:Brand", Version: "99"},
            {Brand: "Google Chrome", Version: "133"},
            {Brand: "Chromium", Version: "133"},
        },
        // ...
    }).Do(ctx)
```

2. **`browser.go` (`startChrome`)** — Fires on the initial browser tab

### Verification

After the patch, `httpbin.org/headers` confirmed clean headers:
```json
{
  "Sec-Ch-Ua": "\"Not(A:Brand\";v=\"99\", \"Google Chrome\";v=\"133\", \"Chromium\";v=\"133\"",
  "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ... Chrome/145.0.7632.77 Safari/537.36"
}
```

No trace of `HeadlessChrome` anywhere in the request.

---

## Lessons Learned

### 1. Client Hints Are the New Fingerprint
The `User-Agent` header is no longer the primary bot detection vector. Modern anti-bot systems check `Sec-Ch-Ua`, `Sec-Ch-Ua-Platform`, and `Sec-Ch-Ua-Mobile` headers which are controlled by the browser engine, not by flags or JavaScript. The only reliable override is via CDP.

### 2. Persistent Daemons Beat Cold Starts
Anti-bot systems track behavioral patterns. A browser that launches, scrapes one page, and immediately exits looks suspicious. A persistent daemon that maintains a warm session looks like a real user who left their browser open.

### 3. macOS Sandbox Requires Binary-Level Workarounds
You cannot `chmod` or `csrutil` your way out of OpenClaw's sandbox. The only reliable approach is to use binaries specifically designed to run without GUI or crash-reporting infrastructure (like `chrome-headless-shell`).

### 4. Profile Cloning Beats Login Automation
Instead of building complex login automation (which constantly breaks with 2FA, CAPTCHAs, and security challenges), simply clone an already-authenticated Chrome profile. The `import_host_profile.sh` script does this in seconds.

### 5. Self-Healing Instance Management
The Pinchtab client checks for stale instances, flushes them, and restarts cleanly. This pattern prevents the common failure mode of "it worked once but now the port is stuck."
