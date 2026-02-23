"""Cookie loader and Chrome cookie extractor for authentication."""

import json
import logging
import os
import sqlite3
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

# Add local lib directory to path for pycookiecheat/rookiepy
_lib_dir = str(Path(__file__).parent / "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)


class ChromeCookieExtractor:
    """Extract cookies from Chrome's encrypted store on macOS.

    Uses pycookiecheat (primary) or rookiepy (fallback) to decrypt
    Chrome cookies via the macOS Keychain.
    """

    ZHIHU_DOMAINS = [".zhihu.com", "www.zhihu.com", "zhihu.com"]
    ZHIHU_URLS = [
        "https://www.zhihu.com",
        "https://zhihu.com",
        "https://zhuanlan.zhihu.com",
    ]

    @staticmethod
    def extract_zhihu_cookies() -> list[dict]:
        """Extract Zhihu cookies from Chrome's encrypted cookie store.

        Tries pycookiecheat first (handles DB locking and Keychain well),
        then falls back to rookiepy.

        Returns:
            List of cookie dicts in Playwright-compatible format.
        """
        # Method 1: pycookiecheat (preferred — handles DB copy internally)
        cookies = ChromeCookieExtractor._try_pycookiecheat()
        if cookies:
            return cookies

        # Method 2: rookiepy (fallback)
        cookies = ChromeCookieExtractor._try_rookiepy()
        if cookies:
            return cookies

        logger.error(
            "All Chrome cookie extraction methods failed. "
            "Make sure you are logged into Zhihu in Chrome and that "
            "Keychain access is authorized."
        )
        return []

    @staticmethod
    def _try_pycookiecheat() -> list[dict]:
        """Extract cookies using pycookiecheat."""
        try:
            from pycookiecheat import chrome_cookies
        except ImportError:
            logger.debug("pycookiecheat not available")
            return []

        all_cookies = {}
        try:
            for url in ChromeCookieExtractor.ZHIHU_URLS:
                cookies = chrome_cookies(url)
                all_cookies.update(cookies)
        except Exception as e:
            logger.warning(f"pycookiecheat failed: {e}")
            return []

        if not all_cookies:
            logger.warning("pycookiecheat returned no Zhihu cookies")
            return []

        # pycookiecheat returns a simple dict {name: value}
        # We need to enrich with domain info from the SQLite DB
        pw_cookies = ChromeCookieExtractor._enrich_cookies_from_db(all_cookies)

        logger.info(
            f"pycookiecheat: extracted {len(pw_cookies)} Zhihu cookies"
        )
        return pw_cookies

    @staticmethod
    def _enrich_cookies_from_db(simple_cookies: dict) -> list[dict]:
        """Enrich simple name:value cookies with metadata from Chrome's DB.

        pycookiecheat only returns {name: value}. We query the DB copy
        to get domain, path, expires, secure, httpOnly, sameSite.
        """
        chrome_cookie_db = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies"

        if not chrome_cookie_db.exists():
            # Fall back to basic format
            return [
                {
                    "name": name,
                    "value": value,
                    "domain": ".zhihu.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
                for name, value in simple_cookies.items()
            ]

        # Copy DB to avoid locking issues
        pw_cookies = []
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
            os.close(tmp_fd)
            shutil.copy2(str(chrome_cookie_db), tmp_path)

            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()

            for name, value in simple_cookies.items():
                cursor.execute(
                    """SELECT host_key, path, expires_utc, is_secure, is_httponly, samesite
                       FROM cookies
                       WHERE name = ? AND host_key LIKE '%zhihu%'
                       LIMIT 1""",
                    (name,),
                )
                row = cursor.fetchone()

                if row:
                    host_key, path, expires_utc, is_secure, is_httponly, samesite = row
                    # Chrome stores expires as microseconds since 1601-01-01
                    # Convert to Unix epoch seconds
                    if expires_utc > 0:
                        expires = (expires_utc / 1_000_000) - 11644473600
                    else:
                        expires = -1

                    samesite_map = {-1: "None", 0: "None", 1: "Lax", 2: "Strict"}

                    pw_cookies.append({
                        "name": name,
                        "value": value,
                        "domain": host_key,
                        "path": path,
                        "expires": expires,
                        "httpOnly": bool(is_httponly),
                        "secure": bool(is_secure),
                        "sameSite": samesite_map.get(samesite, "Lax"),
                    })
                else:
                    pw_cookies.append({
                        "name": name,
                        "value": value,
                        "domain": ".zhihu.com",
                        "path": "/",
                        "expires": -1,
                        "httpOnly": False,
                        "secure": False,
                        "sameSite": "Lax",
                    })

            conn.close()
        except Exception as e:
            logger.warning(f"Failed to enrich cookies from DB: {e}")
            # Fall back to basic format
            pw_cookies = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".zhihu.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
                for name, value in simple_cookies.items()
            ]
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return pw_cookies

    @staticmethod
    def _try_rookiepy() -> list[dict]:
        """Extract cookies using rookiepy."""
        try:
            import rookiepy
        except ImportError:
            logger.debug("rookiepy not available")
            return []

        try:
            raw_cookies = rookiepy.chrome(
                domains=ChromeCookieExtractor.ZHIHU_DOMAINS
            )
        except Exception as e:
            logger.warning(f"rookiepy failed: {e}")
            return []

        if not raw_cookies:
            return []

        # Convert to Playwright format
        pw_cookies = []
        for c in raw_cookies:
            cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": c.get("expires", -1),
                "httpOnly": c.get("httpOnly", c.get("http_only", False)),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", c.get("same_site", "Lax")),
            }
            pw_cookies.append(cookie)

        logger.info(f"rookiepy: extracted {len(pw_cookies)} Zhihu cookies")
        return pw_cookies

    @staticmethod
    def extract_and_save(output_path: str | Path = "cookies.json") -> list[dict]:
        """Extract cookies from Chrome and save to JSON file."""
        cookies = ChromeCookieExtractor.extract_zhihu_cookies()
        if cookies:
            path = Path(output_path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(cookies)} cookies to {path}")
        return cookies

    @staticmethod
    async def inject_into_context(context: BrowserContext) -> bool:
        """Extract Chrome cookies and inject into Playwright context."""
        cookies = ChromeCookieExtractor.extract_zhihu_cookies()
        if not cookies:
            return False

        try:
            await context.add_cookies(cookies)
            logger.info(
                f"Injected {len(cookies)} Chrome cookies into browser context"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to inject cookies: {e}")
            return False


class CookieLoader:
    """Load cookies from file for fallback authentication."""

    @staticmethod
    async def load_from_file(context: BrowserContext, cookie_path: str | Path) -> bool:
        """Load cookies from JSON or Netscape format file."""
        path = Path(cookie_path)

        if not path.exists():
            raise FileNotFoundError(f"Cookie file not found: {path}")

        content = path.read_text(encoding="utf-8").strip()

        # Try JSON format first
        if content.startswith("["):
            return await CookieLoader._load_json(context, content)

        # Try Netscape format
        return await CookieLoader._load_netscape(context, content)

    @staticmethod
    async def _load_json(context: BrowserContext, content: str) -> bool:
        """Load cookies from JSON format."""
        try:
            cookies = json.loads(content)

            # Handle both array format and dict format
            if isinstance(cookies, dict):
                cookies = [cookies]

            await context.add_cookies(cookies)
            return True
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON cookie format: {e}")

    @staticmethod
    async def _load_netscape(context: BrowserContext, content: str) -> bool:
        """Load cookies from Netscape format."""
        cookies = []
        lines = content.split("\n")

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) < 7:
                continue

            cookie = {
                "name": parts[5],
                "value": parts[6],
                "domain": parts[0],
                "path": parts[2],
                "expires": int(parts[4]) if parts[4] != "-1" else -1,
                "httpOnly": False,
                "secure": parts[3] == "TRUE",
                "sameSite": "Lax",
            }
            cookies.append(cookie)

        if not cookies:
            raise ValueError("No valid cookies found in Netscape format")

        await context.add_cookies(cookies)
        return True

    @staticmethod
    async def load_from_env(
        context: BrowserContext, env_var: str = "ZHIHU_COOKIES"
    ) -> bool:
        """Load cookies from environment variable (JSON format)."""
        cookies_json = os.environ.get(env_var)

        if not cookies_json:
            return False

        try:
            cookies = json.loads(cookies_json)
            if isinstance(cookies, dict):
                cookies = [cookies]
            await context.add_cookies(cookies)
            return True
        except json.JSONDecodeError:
            return False
