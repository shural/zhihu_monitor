# Pinchtab Upstream Patches

These patches were developed against Pinchtab commit [`0e9ca2f`](https://github.com/pinchtab/pinchtab/commit/0e9ca2f) (`main` branch) to resolve critical anti-bot detection issues when running Chrome in headless mode on macOS.

> **Note:** Pinchtab is evolving rapidly. These patches should be rebased against the latest `main` before submitting a PR. Use `git apply --check` to verify compatibility.

## Patch Summary

| File | Change | Purpose |
|------|--------|---------|
| `internal/config/config.go` | Add `UserAgent` field + `BRIDGE_USER_AGENT` env | Dedicated UA config without flag-parsing bugs |
| `cmd/pinchtab/browser.go` | Accept `baseUA`, inject CDP `SetUserAgentOverride` | Spoof `Sec-Ch-Ua` Client Hints at browser init |
| `cmd/pinchtab/main.go` | Pass `cfg.UserAgent` to `startChrome()` | Wire config to browser startup |
| `internal/bridge/bridge.go` | Add CDP override in `tabSetup()` hook | Per-tab Client Hints spoofing before navigation |
| `cmd/pinchtab/cmd_dashboard.go` | Fix `profilesDir` derivation | Respect `BRIDGE_PROFILE` env correctly |

## Detailed Changes

### 1. `BRIDGE_USER_AGENT` Environment Variable

**Problem:** Chrome's `--user-agent` flag correctly overrides `navigator.userAgent` in JavaScript, but does **not** affect the `Sec-Ch-Ua` HTTP headers (Client Hints). Passing complex User-Agent strings via `CHROME_FLAGS` also suffers from Go's `strings.Fields()` splitting on spaces, mangling the value.

**Solution:** Added a dedicated `UserAgent` field to `RuntimeConfig` that reads from `BRIDGE_USER_AGENT`. This string is used for both the CLI `--user-agent` flag and the CDP override.

```diff
// internal/config/config.go
+ UserAgent        string
  ...
+ UserAgent:        os.Getenv("BRIDGE_USER_AGENT"),
```

### 2. CDP `Emulation.SetUserAgentOverride` with `UserAgentMetadata`

**Problem:** The `Sec-Ch-Ua` HTTP header leaks `"HeadlessChrome"` as a brand name, instantly flagging the browser to anti-bot systems. This header is controlled by the browser engine (not JavaScript) and cannot be spoofed via `--user-agent` or `page.AddScriptToEvaluateOnNewDocument`.

**Solution:** Inject `emulation.SetUserAgentOverride` with a complete `UserAgentMetadata` struct at two lifecycle points:

1. **Browser init** (`startChrome` in `browser.go`) — Covers the initial tab
2. **Tab creation** (`tabSetup` in `bridge.go`) — Covers every subsequent tab opened via the API

This ensures the `Sec-Ch-Ua` header reads something like:
```
"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"
```
instead of:
```
"HeadlessChrome";v="145"
```

> **Future improvement:** The `Brands` and `FullVersionList` values are currently hardcoded. These should ideally be parsed dynamically from the `UserAgent` string or provided via additional environment variables.

### 3. Dashboard Profile Path Fix

**Problem:** `cmd_dashboard.go` constructs the profiles directory as `filepath.Join(cfg.StateDir, "profiles")`, completely ignoring the `BRIDGE_PROFILE` environment variable. This causes the dashboard orchestrator to look for profiles in the wrong directory.

**Solution:** Changed to `filepath.Join(filepath.Dir(cfg.ProfileDir), "profiles")` so it respects the user-configured profile path.

## Applying the Patch

```bash
cd /path/to/pinchtab
git apply --check ../zhihu_monitor/patches/0001-stealth-ua-override-and-dashboard-profile-fix.patch
git apply ../zhihu_monitor/patches/0001-stealth-ua-override-and-dashboard-profile-fix.patch
```

## Proposed PR Structure

Since Pinchtab is evolving quickly, consider splitting into two focused PRs:

1. **PR #1: `BRIDGE_USER_AGENT` + CDP Client Hints override**
   - `config.go`, `browser.go`, `main.go`, `bridge.go`
   - Title: `feat: add BRIDGE_USER_AGENT with CDP Sec-Ch-Ua override`

2. **PR #2: Dashboard profile path fix**
   - `cmd_dashboard.go`
   - Title: `fix: respect BRIDGE_PROFILE in dashboard profiles dir`
