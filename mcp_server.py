#!/usr/bin/env python3
"""MCP server for Zhihu activity monitoring.

Exposes tools to query Zhihu user activities via the Model Context Protocol.
Runs over stdio transport for local AI assistant integration.

Usage:
    python3 mcp_server.py          # Run as MCP server (stdio)
    python3 mcp_server.py --test   # Run built-in self-tests
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Ensure we run from the project directory so relative paths resolve
_project_dir = Path(__file__).parent.resolve()
os.chdir(_project_dir)

# Add local lib directory
_lib_dir = str(_project_dir / "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)
if str(_project_dir) not in sys.path:
    sys.path.insert(0, str(_project_dir))

# Ensure TMPDIR is writable on macOS
if sys.platform == "darwin":
    os.environ.setdefault("TMPDIR", "/tmp")

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server definition
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "zhihu-monitor",
    instructions=(
        "Monitor Zhihu (知乎) user activities. Use get_activities to fetch "
        "recent posts from any Zhihu user by their URL token. Use get_new_since "
        "to check the database for recently captured activities. Use get_db_stats "
        "for an overview."
    ),
)


# ---------------------------------------------------------------------------
# Shared state — lazy-initialized on first use
# ---------------------------------------------------------------------------

class _State:
    """Lazy-initialized shared state for browser and DB."""

    def __init__(self):
        self._browser = None
        self._scraper = None
        self._db = None
        self._initialized = False

    @property
    def db(self):
        if self._db is None:
            from storage import Database
            self._db = Database()
        return self._db

    async def ensure_browser(self):
        """Initialize browser + scraper if not yet started."""
        if self._initialized:
            return self._scraper

        from browser import BrowserManager
        from config import BrowserConfig, load_config
        from scraper import ZhihuScraper

        app_config = load_config()
        if app_config.browser.use_pinchtab:
            from pinchtab_client import PinchtabScraper
            self._scraper = PinchtabScraper(
                app_config.browser.pinchtab_url,
                app_config.browser.pinchtab_profile,
                headless=app_config.browser.headless,
            )
            self._initialized = True
            logger.info("Pinchtab Scraper initialized for MCP server")
            return self._scraper

        is_headless = os.environ.get("HEADLESS", "true").lower() == "true"
        import uuid
        profile_dir = f"/tmp/zhihu-monitor-profile-{uuid.uuid4()}"
        config = BrowserConfig(
            headless=is_headless,
            chrome_cookie_extraction=False,
            user_data_dir=profile_dir,
        )
        self._browser = BrowserManager(config)
        page = await self._browser.start()
        self._scraper = ZhihuScraper(page)
        self._initialized = True
        logger.info("Playwright browser initialized for MCP server")
        return self._scraper

    async def save_cookies(self):
        """Save refreshed cookies from browser context."""
        if self._browser:
            await self._browser.save_cookies()

    async def shutdown(self):
        """Clean up browser resources."""
        if self._browser:
            await self._browser.close()
            self._initialized = False
            self._browser = None
            self._scraper = None


state = _State()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_activities(user: str, count: int = 10) -> str:
    """Fetch recent activities from a Zhihu user's profile (live scrape).

    Args:
        user: Zhihu user URL token (e.g. 'xu-ze-qiu')
        count: Number of activities to return (default 10, max 20)
    """
    count = min(max(count, 1), 20)

    try:
        scraper = await state.ensure_browser()
        activities = await scraper.fetch_activities(user, scroll_count=max(1, count // 5))

        # Save refreshed cookies after visiting Zhihu
        await state.save_cookies()

        # Store new activities in DB
        db = state.db
        new_count = 0
        for a in activities:
            if not db.is_seen(a.unique_id):
                db.add_activity(
                    a.unique_id, a.title, a.link,
                    a.summary, a.author, a.time.isoformat(),
                )
                new_count += 1

        results = []
        for a in activities[:count]:
            results.append({
                "title": a.title,
                "link": a.link,
                "summary": a.summary[:200] if a.summary else "",
                "author": a.author,
                "time": a.raw_time,
            })

        return json.dumps({
            "user": user,
            "fetched": len(results),
            "new_to_db": new_count,
            "activities": results,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Failed to fetch activities for {user}: {e}")
        import traceback
        return json.dumps({"error": repr(e), "traceback": traceback.format_exc(), "user": user})


@mcp.tool()
async def get_new_since(hours: int = 24, user: Optional[str] = None) -> str:
    """Query the database for activities captured in the last N hours.

    This does NOT scrape Zhihu — it returns previously stored results.

    Args:
        hours: Look back this many hours (default 24)
        user: Optional Zhihu user token to filter by
    """
    db = state.db
    all_activities = db.get_recent_activities(author=user, limit=100)

    cutoff = datetime.now() - timedelta(hours=hours)
    recent = [a for a in all_activities if a.created_at >= cutoff]

    results = []
    for a in recent:
        results.append({
            "title": a.title,
            "link": a.link,
            "summary": a.summary[:200] if a.summary else "",
            "author": a.author,
            "time": a.time.isoformat() if a.time else "",
            "captured_at": a.created_at.isoformat(),
        })

    return json.dumps({
        "hours": hours,
        "user_filter": user,
        "count": len(results),
        "activities": results,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_db_stats() -> str:
    """Get database statistics: total activities, tracked users, etc."""
    db = state.db
    stats = db.get_stats()

    # Get per-author counts
    conn = db._get_connection()
    cursor = conn.execute(
        "SELECT author, COUNT(*) as cnt FROM activities GROUP BY author ORDER BY cnt DESC LIMIT 20"
    )
    authors = [{"author": row[0], "count": row[1]} for row in cursor.fetchall()]

    # Most recent entry
    cursor = conn.execute(
        "SELECT created_at FROM activities ORDER BY created_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    last_capture = row[0] if row else "never"

    return json.dumps({
        **stats,
        "last_capture": last_capture,
        "authors": authors,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def cookie_status() -> str:
    """Check if cookies are valid by visiting Zhihu and checking login status.

    Returns cookie count, auth cookie presence, and login status.
    """
    cookie_path = Path("cookies.json")

    result = {
        "cookie_file_exists": cookie_path.exists(),
        "cookie_count": 0,
        "auth_cookies": [],
        "expired_count": 0,
        "login_verified": False,
    }

    if cookie_path.exists():
        with open(cookie_path) as f:
            cookies = json.load(f)
        result["cookie_count"] = len(cookies)

        now = time.time()
        auth_names = {"z_c0", "d_c0", "_xsrf"}
        result["auth_cookies"] = [
            c["name"] for c in cookies if c["name"] in auth_names
        ]
        result["expired_count"] = sum(
            1 for c in cookies
            if c.get("expires", -1) > 0 and c["expires"] < now
        )

    # Try to verify login by visiting Zhihu
    try:
        scraper = await state.ensure_browser()
        page = scraper.page
        await page.goto(
            "https://www.zhihu.com", wait_until="networkidle", timeout=30000
        )
        title = await page.title()
        result["login_verified"] = "私信" in title or "消息" in title
        result["page_title"] = title
        await state.save_cookies()
    except Exception as e:
        result["login_error"] = str(e)

    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("zhihu://config")
async def get_config() -> str:
    """Current monitor configuration."""
    config_path = Path("config.json")
    if config_path.exists():
        return config_path.read_text(encoding="utf-8")
    return json.dumps({"error": "config.json not found"})


@mcp.resource("zhihu://cookies")
async def get_cookie_summary() -> str:
    """Summary of cookie status."""
    cookie_path = Path("cookies.json")
    if not cookie_path.exists():
        return json.dumps({"error": "cookies.json not found"})

    with open(cookie_path) as f:
        cookies = json.load(f)

    now = time.time()
    summary = []
    for c in cookies:
        expires = c.get("expires", -1)
        if expires > 0:
            remaining_h = (expires - now) / 3600
            status = f"{remaining_h:.0f}h remaining" if remaining_h > 0 else "EXPIRED"
        else:
            status = "session"
        summary.append({"name": c["name"], "domain": c["domain"], "status": status})

    return json.dumps(summary, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_self_tests():
    """Run built-in self-tests to validate tools work correctly."""
    print("=" * 60)
    print("MCP Server Self-Tests")
    print("=" * 60)

    tests_passed = 0
    tests_failed = 0

    async def run_test(name, coro):
        nonlocal tests_passed, tests_failed
        print(f"\n--- Test: {name} ---")
        try:
            result = await coro
            parsed = json.loads(result)
            print(f"  Result: {json.dumps(parsed, ensure_ascii=False, indent=2)[:500]}")
            tests_passed += 1
            print(f"  ✓ PASSED")
            return parsed
        except Exception as e:
            tests_failed += 1
            print(f"  ✗ FAILED: {e}")
            return None

    # Test 1: DB stats (no browser needed)
    await run_test("get_db_stats", get_db_stats())

    # Test 2: Cookie status (may initialize browser)
    await run_test("cookie_status", cookie_status())

    # Test 3: Get activities (live scrape)
    result = await run_test(
        "get_activities(xu-ze-qiu, 3)",
        get_activities("xu-ze-qiu", 3),
    )

    if result and result.get("activities"):
        # Verify structure
        a = result["activities"][0]
        required = {"title", "link", "summary", "author", "time"}
        missing = required - set(a.keys())
        if missing:
            print(f"  ✗ Missing keys: {missing}")
            tests_failed += 1
        else:
            print(f"  ✓ Activity structure valid")
            tests_passed += 1

    # Test 4: Get new since (DB query)
    await run_test("get_new_since(hours=24)", get_new_since(24))

    # Test 5: Resources
    print(f"\n--- Test: resource zhihu://config ---")
    try:
        config_text = await get_config()
        parsed = json.loads(config_text)
        print(f"  Config keys: {list(parsed.keys())[:5]}")
        tests_passed += 1
        print(f"  ✓ PASSED")
    except Exception as e:
        tests_failed += 1
        print(f"  ✗ FAILED: {e}")

    print(f"\n--- Test: resource zhihu://cookies ---")
    try:
        cookie_text = await get_cookie_summary()
        parsed = json.loads(cookie_text)
        print(f"  Cookie entries: {len(parsed)}")
        tests_passed += 1
        print(f"  ✓ PASSED")
    except Exception as e:
        tests_failed += 1
        print(f"  ✗ FAILED: {e}")

    # Cleanup
    await state.shutdown()

    print(f"\n{'=' * 60}")
    print(f"Results: {tests_passed} passed, {tests_failed} failed")
    print(f"{'=' * 60}")

    return tests_failed == 0


def main():
    if "--test" in sys.argv:
        # Run self-tests
        logging.basicConfig(level=logging.WARNING)
        success = asyncio.run(_run_self_tests())
        sys.exit(0 if success else 1)
    else:
        # Run as MCP server over stdio
        log_path = Path(__file__).parent / "mcp_server.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_path)],
        )
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
