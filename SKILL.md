---
name: zhihu_monitor
description: >
  Monitor and scrape Zhihu user activities via a connected MCP server. 
  Allows agents to check for new posts, query the captured activity database, 
  and fetch live activities from any Zhihu user profile.
homepage: https://github.com/your-username/zhihu_monitor
user-invocable: true
metadata: {"openclaw":{"emoji":"🕵️","requires":{"mcp":[{"name":"zhihu-monitor","command":"{baseDir}/venv/bin/python","args":["{baseDir}/mcp_server.py"],"env":{"PYTHONPATH":"{baseDir}/lib:{baseDir}"}}],"env":[{"name":"HEADLESS","optional":true,"description":"Run underlying browser scraping headless (true/false) if not using Pinchtab HTTP backend."}]}}}
---

# Zhihu Monitor SKILL

This skill connects the agent to the `zhihu_monitor` Python backend. It provides an `mcp` server (`zhihu-monitor`) exposing tools to query a local SQLite database of Zhihu activities and to execute live scrapes of Zhihu user profiles.

## Architecture Context
Zhihu Monitor can be configured to scrape using either a raw Playwright browser, RSSHub, or seamlessly through an isolated **Pinchtab** HTTP backend (recommended for bypassing strict anti-bot detection walls like Google's).

> [!IMPORTANT]
> To use the Pinchtab backend successfully, the script `./start_pinchtab_dashboard.sh` must be running in the background. This script correctly anchors the host Chrome profile, bypasses macOS sandboxes, and injects native User-Agents to prevent headless fingerprinting. 
> 
> **MacOS Headless Sandbox Bypass**: Pure headless mode triggers a fatal macOS `sandbox-exec` Crashpad exception. To fix this, `start_pinchtab_dashboard.sh` seamlessly switches the binary backend to Playwright's `chrome-headless-shell` which cleanly ignores GUI quarantine checks, allowing stable execution.
>
> **Sec-Ch-Ua Patch**: To perfectly neutralize the notorious `HeadlessChrome` injection in modern Client Hints headers, the internal `pinchtab` Go binary was patched. A CDP hook (`Emulation.SetUserAgentOverride`) fires silently on every single DOM injection, cleanly mocking the `UserAgentMetadata` array on the wire before Zhihu anti-bot servers can detect it.
>
> **Profile Importer**: If the configured Pinchtab profile (`zhihu`) starts empty, execute `./import_host_profile.sh` with Chrome fully quit. It securely clones the native host `Default` macOS profile straight into the `pinchtab-state` isolated sandbox, porting all established Google/Zhihu authentication cookies perfectly.
> 
> **OpenClaw Conflict Resolution**: OpenClaw natively supports the official Pinchtab project and may spin it up in the background. To guarantee our anti-bot patches and profile anchors are not overridden, you **MUST NOT** use a globally installed Pinchtab binary. `start_pinchtab_dashboard.sh` executes a custom-compiled `pinchtab` binary located firmly within the `zhihu_monitor` project root, explicitly bound to port `9877`. The `config.json` is natively configured to target this isolated deployment.

## Tools Provided
When this SKILL is enabled, the agent gains access to the following MCP tools. 

> [!NOTE]
> OpenClaw natively orchestrates these tools over the `stdio` MCP protocol (passing JSON-RPC back and forth to the `mcp_server.py` process). The LLM calls the tools as standard functions, meaning it **does not** need to construct `npx mcporter call ...` terminal commands.

### `get_activities`
Fetches recent activities directly from a Zhihu user's profile on demand (live scrape).
- **Arguments:**
  - `user`: The Zhihu user URL token (e.g. `xu-ze-qiu`). You can find this in their profile URL `https://www.zhihu.com/people/{token}`.
  - `count`: Number of activities to return (default 10, max 20).
- **Usage:** Ideal for checking a user's *current* status on the website right now. This tool will also save the scraped data to the central database if it's new.

### `get_new_since`
Queries the *local SQLite database* for activities captured in the last `N` hours.
- **Arguments:**
  - `hours`: Look back this many hours (default 24).
  - `user`: Optional Zhihu user token to filter by.
- **Usage:** This tool does **not** perform a live scrape. It is incredibly fast and cheap, strictly returning what the scheduled background `monitor.py` task has already collected.

### `get_db_stats`
Gets an overview of the local database.
- **Usage:** Returns total captured activities, tracked unique users, and the timestamp of the last successful capture.

### `cookie_status`
Checks if the current session cookies are valid by visiting Zhihu and checking the login status mechanically.
- **Usage:** Automatically tests and reports if the system has valid authenticated cookies (`z_c0`, `d_c0`).

## Resources Provided
The server also provides these read-only MCP resources:
- `zhihu://config`: Returns the JSON configuration (`config.json`).
- `zhihu://cookies`: Returns a summary of the active cookie pool status.

## Example Agent Workflow
**User Request:** "Are there any new Zhihu posts from warfalcon today?"
**Agent thought process:**
1. I should query the local database first because it is faster and cheaper.
   -> Call `get_new_since(hours=24, user="warfalcon")`.
2. If the user expects a *live* response or the DB hasn't been updated recently (checking `get_db_stats`), I should perform a live scrape.
   -> Call `get_activities(user="warfalcon", count=5)`.
3. Inform the user of the new titles and provide the Zhihu links.
