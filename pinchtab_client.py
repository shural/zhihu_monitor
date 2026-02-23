"""Pinchtab HTTP client for Zhihu activity scraping."""

import asyncio
import logging
from typing import Optional
import aiohttp

from scraper import ActivityCard, ZhihuScraper

logger = logging.getLogger(__name__)

class PinchtabScraper:
    """Scraper for Zhihu user activities using the Pinchtab HTTP API."""

    BASE_URL = "https://www.zhihu.com/people/{token}/activities"

    def __init__(self, pinchtab_url: str, profile: str = "zhihu", headless: bool = True):
        self.pinchtab_url = pinchtab_url.rstrip("/")
        self.profile = profile
        self.headless = headless
        self.instance_port: Optional[int] = None

    async def _ensure_instance(self) -> str:
        """Ensure the profile is running and get its base URL."""
        if self.instance_port:
            return f"http://localhost:{self.instance_port}"

        logger.info(f"Starting Pinchtab instance for profile: {self.profile}")
        
        async with aiohttp.ClientSession() as session:
            try:
                # 1. Quick check for already running instance
                try:
                    resp = await session.get(f"{self.pinchtab_url}/instances", timeout=aiohttp.ClientTimeout(total=5))
                    if resp.status == 200:
                        instances = await resp.json()
                        for inst in instances:
                            if inst.get("name") == self.profile and inst.get("status") == "running":
                                self.instance_port = inst.get("port")
                                logger.info(f"Connected to already running Pinchtab instance on port {self.instance_port}")
                                return f"http://localhost:{self.instance_port}"
                            elif inst.get("name") == self.profile and inst.get("status") in ["error", "exited", "stopped"]:
                                logger.warning(f"Found stale Pinchtab profile '{self.profile}' in state '{inst.get('status')}'. Flushing...")
                                try:
                                    await session.post(f"{self.pinchtab_url}/stop/{self.profile}", timeout=aiohttp.ClientTimeout(total=10))
                                except asyncio.TimeoutError:
                                    logger.warning(f"Timeout while flushing stale profile '{self.profile}' (ignored)")
                                except Exception as e:
                                    logger.warning(f"Error while flushing stale profile: {e}")
                                await asyncio.sleep(1) # Give dashboard a second to clean up constraints
                except asyncio.TimeoutError:
                    logger.warning("Timeout while checking for running Pinchtab instances, assuming none")

                # 2. Try starting it if not running
                try:
                    target_port = "9869" if self.headless else "9868"
                    resp = await session.post(
                        f"{self.pinchtab_url}/start/{self.profile}",
                        json={"headless": self.headless, "port": target_port},
                        timeout=aiohttp.ClientTimeout(total=15)
                    )
                except asyncio.TimeoutError:
                    raise Exception(f"Timeout waiting for Pinchtab to start profile '{self.profile}'")
                
                # Wait for port if it's still starting
                if resp.status in (200, 201):
                    # Poll instances endpoint until it shows as running with a port
                    for _ in range(30): # Wait up to 15 seconds
                        await asyncio.sleep(0.5)
                        poll_resp = await session.get(f"{self.pinchtab_url}/instances")
                        if poll_resp.status == 200:
                            instances = await poll_resp.json()
                            for inst in instances:
                                if inst.get("name") == self.profile and inst.get("status") == "running" and inst.get("port"):
                                    self.instance_port = inst.get("port")
                                    logger.info(f"Started Pinchtab instance '{self.profile}' on port {self.instance_port}")
                                    return f"http://localhost:{self.instance_port}"
                    
                    raise Exception("Pinchtab profile never finished starting (timed out waiting for port)")
                else:
                    text = await resp.text()
                    logger.error(f"Failed to start Pinchtab profile: HTTP {resp.status} - {text}")
                    raise Exception(f"Pinchtab start failed: {text}")
            except Exception as e:
                logger.error(f"Error connecting to Pinchtab dashboard: {e}")
                raise

    async def fetch_activities(
        self, token: str, scroll_count: int = 1
    ) -> list[ActivityCard]:
        """Fetch activities for a given token via Pinchtab."""
        instance_url = await self._ensure_instance()
        target_url = self.BASE_URL.format(token=token)
        
        logger.info(f"Pinchtab fetching activities from: {target_url}")

        async with aiohttp.ClientSession() as session:
            # 1. Navigate to the page
            tab_id = ""
            try:
                nav_resp = await session.post(
                    f"{instance_url}/navigate",
                    json={"url": target_url, "waitNav": True, "newTab": True},
                    timeout=aiohttp.ClientTimeout(total=45)
                )
                if nav_resp.status != 200:
                    logger.error(f"Failed to navigate: {await nav_resp.text()}")
                    return []
                nav_data = await nav_resp.json()
                tab_id = nav_data.get("tabId", "")
            except asyncio.TimeoutError:
                raise Exception(f"Timeout waiting for navigation to {target_url}")
                
            try:
                # Allow page to fully render
                await asyncio.sleep(5)

                # Check if landed on a challenge by extracting title
                try:
                    title_resp = await session.post(
                        f"{instance_url}/evaluate",
                        json={"tabId": tab_id, "expression": "document.title"},
                        timeout=aiohttp.ClientTimeout(total=10)
                    )
                
                    if title_resp.status == 200:
                        title_data = await title_resp.json()
                        title = title_data.get("result", "")
                        if isinstance(title, str) and "安全验证" in title:
                            logger.warning("Pinchtab encountered anti-bot security challenge!")
                            # If headed mode is used and host profile is cloned, this shouldn't happen,
                            # but if it does, Playwright will have to serve as fallback.
                            raise Exception("Security Verification Challenge Encounted in Pinchtab")
                except asyncio.TimeoutError:
                    logger.warning("Timeout extracting document title, skipping security challenge check")
                    
                # 2. Extract DOM values exactly like the Playwright evaluation
                # Reusing the pure Javascript logic from ZhihuScraper._parse_cards
                js_script = """
                    const results = [];
                    const selectors = [
                        '.List-item', '.ContentItem', '[data-za-detail-view-id]',
                        '.ActivityItem', '.ProfileActivity-listItem'
                    ];

                    let items = [];
                    for (const selector of selectors) {
                        items = document.querySelectorAll(selector);
                        if (items.length > 0) break;
                    }

                    items.forEach((item, index) => {
                        if (index >= 10) return;
                        const activity = {};

                        const titleSelectors = [
                            '.ContentItem-title a', 'h2 a', 'h3 a', '.ActivityItem-title a',
                            'a[data-za-detail-view-element-name]', '.RichContent-title a',
                            '.ContentItem-title', 'a[href*="/question/"]',
                            'a[href*="/p/"]', 'a[href*="/article/"]'
                        ];

                        for (const sel of titleSelectors) {
                            const elem = item.querySelector(sel);
                            if (elem && elem.textContent.trim().length > 5) {
                                activity.title = elem.textContent.trim();
                                activity.link = elem.href;
                                break;
                            }
                        }

                        const contentSelectors = ['.RichText', '.ContentItem-content', '.ActivityItem-content', '.RichContent-inner'];
                        for (const sel of contentSelectors) {
                            const elem = item.querySelector(sel);
                            if (elem) {
                                const text = elem.textContent.trim();
                                if (text.length > 20) {
                                    activity.summary = text.substring(0, 300);
                                    break;
                                }
                            }
                        }

                        const timeSelectors = ['.ContentItem-time', '.ActivityItem-time', 'time', '.ContentItem-meta', '.ActivityItem-meta'];
                        for (const sel of timeSelectors) {
                            const elem = item.querySelector(sel);
                            if (elem) {
                                const text = elem.textContent.trim();
                                if (/\\d{1,2}[:\\-]\\d{2}|今天|昨天|小时前|分钟前|\\d{4}-\\d{2}-\\d{2}/.test(text)) {
                                    activity.time = text;
                                    break;
                                }
                            }
                        }

                        if (activity.title) {
                            results.push(activity);
                        }
                    });

                    return results;
                """
            
                try:
                    parse_resp = await session.post(
                        f"{instance_url}/evaluate",
                        json={"tabId": tab_id, "expression": f"(() => {{ {js_script} }})()"},
                        timeout=aiohttp.ClientTimeout(total=20)
                    )
                    if parse_resp.status != 200:
                        logger.error(f"Failed to evaluate extraction script: {await parse_resp.text()}")
                        return []
                except asyncio.TimeoutError:
                    raise Exception("Timeout waiting for DOM extraction script to complete")
                
                parse_data = await parse_resp.json()
                activities_data = parse_data.get("result", [])
                if not isinstance(activities_data, list):
                    activities_data = []

                cards = []
            
                # Use the Playwright parser's time parsing
                dummy_scraper = ZhihuScraper(None)
            
                for data in activities_data:
                    try:
                        card = ActivityCard(
                            title=data.get("title", ""),
                            link=data.get("link", ""),
                            summary=data.get("summary", ""),
                            time=dummy_scraper._parse_time(data.get("time", "")),
                            author=token,
                            raw_time=data.get("time", ""),
                        )
                        cards.append(card)
                    except Exception as e:
                        logger.debug(f"Failed to create ActivityCard: {e}")

                logger.info(f"Pinchtab found {len(cards)} activity cards for {token}")
                return cards
            
            finally:
                if tab_id:
                    try:
                        await session.post(
                            f"{instance_url}/tab",
                            json={"action": "close", "tabId": tab_id},
                            timeout=aiohttp.ClientTimeout(total=5)
                        )
                    except Exception as e:
                        logger.warning(f"Failed to close tab {tab_id}: {e}")
