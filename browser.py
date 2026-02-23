"""Browser manager with Playwright persistent context for stealth."""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from config import BrowserConfig
from cookies import ChromeCookieExtractor, CookieLoader

logger = logging.getLogger(__name__)

# Workaround: macOS may block /var/folders temp dir
if sys.platform == "darwin" and "TMPDIR" not in os.environ:
    os.environ["TMPDIR"] = "/tmp"

_STEALTH_SCRIPT = """
    // Hide webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // Overwrite plugins
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    // Overwrite languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en-US', 'en', 'ja']
    });
    // Add chrome runtime
    Object.defineProperty(navigator, 'chrome', { get: () => ({ runtime: {} }) });
    // Override permissions query
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
    // Mock screen
    window.screen = {
        availHeight: 1080, availLeft: 0, availTop: 0, availWidth: 1920,
        colorDepth: 24, height: 1080, isExtended: true, onchange: null,
        orientation: { angle: 0, type: 'landscape-primary' },
        pixelDepth: 24, width: 1920
    };
    if (!window.navigator.permissions) {
        window.navigator.permissions = {
            query: () => Promise.resolve({ state: 'prompt' })
        };
    }
    if (!window.chrome) {
        window.chrome = { csi: () => ({}), loadTimes: () => ({}), app: {} };
    }
    window.navigator.webdriver = false;
    Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    });
"""

_ADVANCED_STEALTH_SCRIPT = """
    // Extra hardware/memory mocking
    const hardwareCore = 8;
    const deviceMem = 16;
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => hardwareCore,
        configurable: true
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => deviceMem,
        configurable: true
    });

    // Clean up web driver leakage
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

    // Advanced canvas noise
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {
        const context = this.getContext('2d');
        if (context && this.width > 0 && this.height > 0) {
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = this.width;
            tempCanvas.height = this.height;
            const tempCtx = tempCanvas.getContext('2d');
            tempCtx.drawImage(this, 0, 0);
            
            // Subtle pixel manipulation
            const imageData = tempCtx.getImageData(0, 0, this.width, this.height);
            const noisePixels = Math.min(10, Math.floor(imageData.data.length / 400));
            for (let i = 0; i < noisePixels; i++) {
                const idx = Math.floor(Math.random() * (imageData.data.length / 4)) * 4;
                if (imageData.data[idx] < 255) imageData.data[idx] += 1;
            }
            tempCtx.putImageData(imageData, 0, 0);
            return originalToDataURL.apply(tempCanvas, args);
        }
        return originalToDataURL.apply(this, args);
    };

    // WebGL Vendor spoofing
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.apply(this, arguments);
    };
"""



class BrowserManager:
    """Manages Playwright browser with persistent context for stealth."""

    def __init__(self, config: BrowserConfig):
        self.config = config
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._is_persistent = False

    async def start(self) -> Page:
        """Start browser and return page for scraping."""
        self.playwright = await async_playwright().start()

        # Try persistent context first, fall back to non-persistent
        try:
            await self._launch_persistent()
        except Exception as e:
            logger.warning(
                f"Persistent context failed ({e}), falling back to non-persistent"
            )
            await self._launch_non_persistent()

        # Configure stealth
        await self.context.add_init_script(_STEALTH_SCRIPT)
        
        if getattr(self.config, "stealth_level", "light") == "advanced":
            logger.info("Injecting advanced stealth script (Pinchtab-inspired)")
            await self.context.add_init_script(_ADVANCED_STEALTH_SCRIPT)

        # Get or create page
        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = await self.context.new_page()

        # Inject cookies: try Chrome extraction, then cookies.json fallback
        cookies_injected = False
        if self.config.chrome_cookie_extraction:
            logger.info("Extracting cookies from host Chrome browser...")
            cookies_injected = await ChromeCookieExtractor.inject_into_context(
                self.context
            )
            if cookies_injected:
                ChromeCookieExtractor.extract_and_save()

        if not cookies_injected:
            # Try cookies.json file
            cookie_path = Path("cookies.json")
            if cookie_path.exists():
                logger.info("Loading cookies from cookies.json (fallback)")
                try:
                    await CookieLoader.load_from_file(self.context, cookie_path)
                    cookies_injected = True
                except Exception as e:
                    logger.warning(f"Failed to load cookies.json: {e}")

        # If still no cookies and no existing session, prompt for login
        if not cookies_injected and not self._is_persistent:
            await self._handle_login()

        # Warm up the session: visit homepage to establish browsing fingerprint
        # This prevents Zhihu's anti-bot "安全验证" challenge on first real visit
        if cookies_injected:
            await self._warmup_session()

        return self.page

    async def _warmup_session(self) -> None:
        """Visit zhihu.com homepage to warm up session and handle anti-bot challenges.

        Even if the security challenge cannot be fully "solved", the interaction
        warms up the browser context (JS execution, cookie exchanges) enough that
        subsequent page navigations in the same context succeed.
        """
        try:
            logger.info("Warming up session via homepage visit...")
            await self.page.goto(
                "https://www.zhihu.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(2)

            current_url = self.page.url
            title = await self.page.title()

            if "unhuman" in current_url or "安全验证" in title:
                logger.warning("Security challenge detected during warmup")
                # Interact with the challenge to warm up the session
                await self._solve_security_challenge()
                # Even if not fully solved, the session is now warmer
                logger.info("Warmup interaction complete (challenge may persist, but session is warmer)")
            else:
                logged_in = "私信" in title or "消息" in title
                logger.info(f"Warmup complete: title='{title[:50]}', logged_in={logged_in}")

        except Exception as e:
            logger.warning(f"Warmup failed (non-fatal): {e}")

    async def _solve_security_challenge(self) -> bool:
        """Try to solve Zhihu's anti-bot verification challenge.

        The challenge page has a '开始验证' (Start verification) button.
        Clicking it triggers a JS-based proof-of-work / behavior check
        that typically auto-completes without a visual CAPTCHA.
        """
        try:
            # Look for the verify button
            verify_btn = await self.page.query_selector(
                'button:has-text("开始验证"), '
                'a:has-text("开始验证"), '
                'div:has-text("开始验证"):not(:has(div)), '
                '[class*="verify"], [class*="Verify"]'
            )

            if verify_btn:
                logger.info("Found verify button, clicking...")
                await verify_btn.click()
                # Wait for the challenge to process
                await asyncio.sleep(5)

                # Check if we got redirected away from the challenge
                current_url = self.page.url
                title = await self.page.title()
                if "unhuman" not in current_url and "安全验证" not in title:
                    logger.info("Security challenge passed after clicking verify button")
                    return True

                # Try waiting longer — some challenges take time
                logger.info("Still on challenge page, waiting more...")
                await asyncio.sleep(5)

                current_url = self.page.url
                title = await self.page.title()
                if "unhuman" not in current_url and "安全验证" not in title:
                    logger.info("Security challenge passed after extended wait")
                    return True

            # Alternative: look for "进入知乎" link
            enter_btn = await self.page.query_selector(
                'a:has-text("进入知乎"), button:has-text("进入知乎")'
            )
            if enter_btn:
                logger.info("Found '进入知乎' button, clicking...")
                await enter_btn.click()
                await asyncio.sleep(3)
                current_url = self.page.url
                if "unhuman" not in current_url:
                    logger.info("Redirected via '进入知乎' button")
                    return True

            logger.warning("Could not find or solve verification challenge")
            return False

        except Exception as e:
            logger.warning(f"Error solving security challenge: {e}")
            return False

    async def _launch_persistent(self) -> None:
        """Launch persistent browser context."""
        user_data_dir = Path(self.config.user_data_dir)

        launch_options = {
            "headless": self.config.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--window-size=1920,1080",
            ],
            "user_data_dir": str(user_data_dir),
            "viewport": {
                "width": self.config.viewport.width,
                "height": self.config.viewport.height,
            },
            "ignore_https_errors": True,
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "ignore_default_args": ["--enable-automation"],
        }

        # Note: Do NOT set channel="chromium" — it uses Chrome for Testing binary
        # which has macOS Crashpad/ProcessSingleton permission issues.
        # Playwright's bundled Chromium works correctly for persistent contexts IN HEADLESS MODE.

        context = await self.playwright.chromium.launch_persistent_context(
            **launch_options
        )
        self.context = context
        self._is_persistent = True

    async def _launch_non_persistent(self) -> None:
        """Launch non-persistent browser context (fallback)."""
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--window-size=1920,1080",
            ],
            ignore_default_args=["--enable-automation"],
        )
        self.context = await self.browser.new_context(
            viewport={
                "width": self.config.viewport.width,
                "height": self.config.viewport.height,
            },
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            ignore_https_errors=True,
        )
        self._is_persistent = False

    async def _handle_login(self) -> None:
        """Handle first-time login with user assistance."""
        print("\n" + "=" * 60)
        print("NO COOKIES FOUND - Manual login required")
        print("=" * 60)
        print("1. Browser will navigate to Zhihu")
        print("2. Please log in to your Zhihu account manually")
        print(f"3. You have {self.config.login_timeout_seconds} seconds")
        print("4. After logging in, the session will be saved")
        print("=" * 60 + "\n")

        await self.page.goto("https://www.zhihu.com", wait_until="networkidle")

        if not self.config.headless:
            await self.page.pause()
        else:
            await asyncio.sleep(self.config.login_timeout_seconds)

        try:
            await self.page.wait_for_selector(".Avatar", timeout=10000)
            print("\n✓ Login detected! Session saved.")
        except Exception:
            print("\n⚠ Warning: Login not detected. You may need to run again.")

    async def check_login_status(self) -> bool:
        """Check if browser is logged in to Zhihu."""
        if not self.page:
            return False
        try:
            await self.page.goto(
                "https://www.zhihu.com", wait_until="networkidle", timeout=10000
            )
            login_btn = await self.page.query_selector(".SignFlowHeader + *")
            avatar = await self.page.query_selector(".Avatar")
            return bool(avatar) or not bool(login_btn)
        except Exception:
            return False

    async def close(self) -> None:
        """Close browser and cleanup."""
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        if self.playwright:
            await self.playwright.stop()

    async def save_cookies(self, path: str | Path = "cookies.json") -> None:
        """Save current context cookies to file for persistence.

        This captures server-refreshed cookies from the Playwright context,
        making the session self-sustaining without needing Chrome.
        """
        if not self.context:
            return

        try:
            cookies = await self.context.cookies()
            # Filter to zhihu-related cookies
            zhihu_cookies = [
                c for c in cookies
                if "zhihu" in c.get("domain", "")
            ]

            if not zhihu_cookies:
                logger.debug("No Zhihu cookies to save")
                return

            # Convert Playwright cookie format for serialization
            save_cookies = []
            for c in zhihu_cookies:
                save_cookies.append({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                })

            with open(path, "w", encoding="utf-8") as f:
                json.dump(save_cookies, f, indent=2, ensure_ascii=False)

            logger.info(f"Saved {len(save_cookies)} refreshed cookies to {path}")

        except Exception as e:
            logger.warning(f"Failed to save cookies: {e}")

    async def new_page(self) -> Page:
        """Create a new page in the existing context."""
        if not self.context:
            raise RuntimeError("Browser not started. Call start() first.")
        return await self.context.new_page()
