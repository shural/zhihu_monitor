"""Main monitoring loop with APScheduler."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from browser import BrowserManager
from config import Config, load_config
from cookies import CookieLoader, ChromeCookieExtractor
from notifications import NotificationManager
from scraper import ZhihuScraper, RSSHubScraper
from storage import Database

logger = logging.getLogger(__name__)


class ZhihuMonitor:
    """Main monitor class that orchestrates all components."""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database()
        self.browser_manager: BrowserManager | None = None
        self.notifier = NotificationManager(config.notifications)
        self.scheduler = AsyncIOScheduler()
        self._running = False

    async def start(self) -> None:
        """Start the monitoring loop."""
        logger.info("Starting Zhihu Monitor...")

        # Determine which scraper to use
        use_rsshub = self.config.rsshub.enabled and await self._check_rsshub()

        if use_rsshub:
            logger.info("Using RSSHub fallback")
            self.scraper = RSSHubScraper(
                self.config.rsshub.url, self.config.rsshub.cookies
            )
        elif self.config.browser.use_pinchtab:
            logger.info("Using Pinchtab HTTP browser backend")
            from pinchtab_client import PinchtabScraper
            self.scraper = PinchtabScraper(
                self.config.browser.pinchtab_url,
                self.config.browser.pinchtab_profile,
                headless=self.config.browser.headless,
            )
        else:
            logger.info("Using Playwright browser")
            self.browser_manager = BrowserManager(self.config.browser)
            await self.browser_manager.start()

            # If Chrome cookie extraction is disabled, try loading from file
            if not self.config.browser.chrome_cookie_extraction:
                cookie_path = Path("cookies.json")
                if cookie_path.exists():
                    logger.info("Loading fallback cookies from cookies.json")
                    await CookieLoader.load_from_file(
                        self.browser_manager.context, cookie_path
                    )

            self.scraper = ZhihuScraper(self.browser_manager.page)

        # Schedule polling job
        interval = self.config.poll_interval_minutes
        self.scheduler.add_job(
            self._poll_all,
            trigger=IntervalTrigger(minutes=interval),
            id="poll_activities",
            name="Poll Zhihu activities",
        )

        # Setup graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            if sys.platform != "win32":
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        self._running = True
        self.scheduler.start()

        logger.info(f"Monitor started. Polling every {interval} minutes.")

        # Run forever
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _check_rsshub(self) -> bool:
        """Check if RSSHub is available."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.config.rsshub.url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _poll_all(self) -> None:
        """Poll all configured tokens."""
        logger.info("Polling for new activities...")

        for token in self.config.url_tokens:
            await self._poll_token(token)

        # Save refreshed cookies back to file after each poll cycle
        # This captures server-issued cookie updates, keeping the session alive
        if self.browser_manager:
            await self.browser_manager.save_cookies()

    async def _poll_token(self, token: str, retry_count: int = 3) -> None:
        """Poll a single token with retry logic."""
        for attempt in range(retry_count):
            try:
                activities = await self.scraper.fetch_activities(token)

                new_count = 0
                for activity in activities:
                    if not self.db.is_seen(activity.unique_id):
                        if self.db.add_activity(
                            activity.unique_id,
                            activity.title,
                            activity.link,
                            activity.summary,
                            activity.author,
                            activity.time.isoformat(),
                        ):
                            new_count += 1
                            await self._notify_new_activity(activity)

                logger.info(
                    f"Token {token}: {len(activities)} fetched, {new_count} new"
                )
                return

            except Exception as e:
                logger.warning(
                    f"Attempt {attempt + 1}/{retry_count} failed for {token}: {e}"
                )
                if attempt < retry_count - 1:
                    await asyncio.sleep(2**attempt)

        logger.error(f"All retries exhausted for token {token}")

    async def _notify_new_activity(self, activity) -> None:
        """Notify about new activity."""
        message = f"{activity.summary[:100]}..." if activity.summary else ""

        # Console output
        print("\n" + "=" * 60)
        print(f"🆕 NEW POST from {activity.author}")
        print(f"   {activity.title}")
        print(f"   {activity.raw_time}")
        if activity.link:
            print(f"   {activity.link}")
        print("=" * 60 + "\n")

        # Send notifications
        await self.notifier.notify(
            title=f"新文章: {activity.title}", message=message, link=activity.link
        )

    async def stop(self) -> None:
        """Stop the monitor gracefully."""
        logger.info("Stopping Zhihu Monitor...")
        self._running = False

        self.scheduler.shutdown()

        if self.browser_manager:
            await self.browser_manager.close()

        self.db.close()
        logger.info("Monitor stopped")


async def run_monitor(config_path: str | None = None) -> None:
    """Run the monitor with given config."""
    # Load config
    if config_path:
        config = Config.from_file(config_path)
    else:
        config = load_config()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(config.logging.file), logging.StreamHandler()],
    )

    # Create and start monitor
    monitor = ZhihuMonitor(config)
    await monitor.start()
