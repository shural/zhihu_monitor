"""Zhihu activity scraper with Playwright."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


@dataclass
class ActivityCard:
    """Represents a single activity card from Zhihu."""

    title: str
    link: str
    summary: str
    time: datetime
    author: str
    raw_time: str

    @property
    def unique_id(self) -> str:
        """Generate unique identifier for deduplication."""
        return f"{self.author}:{self.link}"


class ZhihuScraper:
    """Scraper for Zhihu user activities."""

    BASE_URL = "https://www.zhihu.com/people/{token}/activities"

    def __init__(self, page: Page):
        self.page = page

    async def fetch_activities(
        self, token: str, scroll_count: int = 1
    ) -> list[ActivityCard]:
        """Fetch activities for a given token."""
        url = self.BASE_URL.format(token=token)
        logger.info(f"Fetching activities from: {url}")

        # Retry loop — anti-bot challenges are handled by browser warmup,
        # but we may need a few attempts for the session to stabilize
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.page.goto(url, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                logger.warning(f"Timeout loading {url}, retrying with domcontentloaded...")
                try:
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeout:
                    logger.warning(f"Second timeout on attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(3)
                        continue
                    break

            # Check if we landed on the actual profile page (not challenge)
            current_url = self.page.url
            title = await self.page.title()
            if "unhuman" in current_url or "安全验证" in title:
                logger.warning(
                    f"Anti-bot challenge on attempt {attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    # Wait progressively longer — session may stabilize
                    wait_time = 5 + attempt * 5
                    logger.info(f"Waiting {wait_time}s for session to stabilize...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.warning("Challenge persisted, attempting to parse anyway")
                    break

            # Wait for actual content elements to appear
            try:
                await self.page.wait_for_selector(
                    ".List-item, .ContentItem, [data-za-detail-view-id]",
                    timeout=10000,
                )
            except PlaywrightTimeout:
                logger.warning("No activity elements found, using fallback wait")
                await asyncio.sleep(3)

            break  # Successfully loaded (no challenge), proceed to parse

        # Scroll to load more content
        for i in range(scroll_count):
            await self._scroll_down()
            await asyncio.sleep(1)

        # Parse cards
        cards = await self._parse_cards(token)
        logger.info(f"Found {len(cards)} activity cards for {token}")

        return cards

    async def _scroll_down(self) -> None:
        """Scroll down to load more content."""
        await self.page.evaluate("""
            () => {
                window.scrollBy(0, window.innerHeight * 2);
            }
        """)

    async def _parse_cards(self, author_token: str) -> list[ActivityCard]:
        """Parse activity cards from the page using JavaScript evaluation for reliability."""
        cards = []

        # Use JavaScript evaluation for more reliable extraction
        activities_data = await self.page.evaluate("""
            () => {
                const results = [];

                // Try multiple selectors for activity items
                const selectors = [
                    '.List-item',
                    '.ContentItem',
                    '[data-za-detail-view-id]',
                    '.ActivityItem',
                    '.ProfileActivity-listItem'
                ];

                let items = [];
                for (const selector of selectors) {
                    items = document.querySelectorAll(selector);
                    if (items.length > 0) break;
                }

                items.forEach((item, index) => {
                    if (index >= 10) return;

                    const activity = {};

                    // Try to find title
                    const titleSelectors = [
                        '.ContentItem-title a',
                        'h2 a',
                        'h3 a',
                        '.ActivityItem-title a',
                        'a[data-za-detail-view-element-name]',
                        '.RichContent-title a',
                        '.ContentItem-title',
                        'a[href*="/question/"]',
                        'a[href*="/p/"]',
                        'a[href*="/article/"]'
                    ];

                    for (const sel of titleSelectors) {
                        const elem = item.querySelector(sel);
                        if (elem && elem.textContent.trim().length > 5) {
                            activity.title = elem.textContent.trim();
                            activity.link = elem.href;
                            break;
                        }
                    }

                    // Try to find content/summary
                    const contentSelectors = [
                        '.RichText',
                        '.ContentItem-content',
                        '.ActivityItem-content',
                        '.RichContent-inner'
                    ];

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

                    // Try to find time
                    const timeSelectors = [
                        '.ContentItem-time',
                        '.ActivityItem-time',
                        'time',
                        '.ContentItem-meta',
                        '.ActivityItem-meta'
                    ];

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

                    // Try to find action type
                    const actionSelectors = [
                        '.ActivityItem-action',
                        '.ContentItem-action',
                        '.ActivityItem-type',
                        '.ContentItem-meta span:first-child'
                    ];

                    for (const sel of actionSelectors) {
                        const elem = item.querySelector(sel);
                        if (elem) {
                            const text = elem.textContent.trim();
                            if (/^(赞同|发布|收藏|关注|评论|回答|提问|喜欢)/.test(text)) {
                                activity.action = text;
                                break;
                            }
                        }
                    }

                    if (activity.title) {
                        results.push(activity);
                    }
                });

                return results;
            }
        """)

        # Convert to ActivityCard objects
        for data in activities_data:
            try:
                card = ActivityCard(
                    title=data.get("title", ""),
                    link=data.get("link", ""),
                    summary=data.get("summary", ""),
                    time=self._parse_time(data.get("time", "")),
                    author=author_token,
                    raw_time=data.get("time", ""),
                )
                cards.append(card)
            except Exception as e:
                logger.debug(f"Failed to create ActivityCard: {e}")

        logger.info(f"Found {len(cards)} activity cards for {author_token}")
        return cards

    async def _extract_from_text(self, author_token: str) -> list[ActivityCard]:
        """Extract activities from page text content as fallback."""
        cards = []

        # Get all text content
        text_content = await self.page.evaluate("""
            () => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                const texts = [];
                while(node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    if(text.length > 30 && text.length < 300) {
                        texts.push(text);
                    }
                }
                return texts;
            }
        """)

        # Look for patterns that look like titles
        for text in text_content:
            # Skip common UI elements
            if any(
                skip in text
                for skip in [
                    "赞同",
                    "评论",
                    "关注",
                    "收藏",
                    "分享",
                    "编辑",
                    "删除",
                    "举报",
                    "设置",
                ]
            ):
                continue

            # This is a best-effort extraction
            # In practice, zhihu's dynamic loading makes text extraction unreliable
            pass

        return cards

    async def _parse_single_card(
        self, card, author_token: str
    ) -> Optional[ActivityCard]:
        """Parse a single activity card."""
        # Extract title
        title_elem = await card.query_selector(".ContentItem-title")
        title = await title_elem.inner_text() if title_elem else ""
        title = title.strip()

        # Extract link
        link = ""
        if title_elem:
            link_elem = await title_elem.query_selector("a[href]")
            if link_elem:
                link = await link_elem.get_attribute("href")
                if link and not link.startswith("http"):
                    link = f"https://www.zhihu.com{link}"

        # Extract summary/content
        summary = ""
        content_elem = await card.query_selector(".RichText")
        if content_elem:
            summary = await content_elem.inner_text()
            summary = summary.strip()[:200]

        # Extract time
        time_elem = await card.query_selector(".ContentItem-time")
        raw_time = ""
        if time_elem:
            raw_time = await time_elem.inner_text()
            raw_time = raw_time.strip()

        parsed_time = self._parse_time(raw_time)

        if not title:
            return None

        return ActivityCard(
            title=title,
            link=link,
            summary=summary,
            time=parsed_time,
            author=author_token,
            raw_time=raw_time,
        )

    def _parse_time(self, time_str: str) -> datetime:
        """Parse time string to datetime."""
        if not time_str:
            return datetime.now()

        now = datetime.now()

        # Handle various formats
        # "今天 12:30" -> today at time
        # "昨天 15:20" -> yesterday at time
        # "MM-DD HH:mm" -> this year
        # "YYYY-MM-DD" -> specific date

        time_str = time_str.strip()

        # Simple patterns
        if "今天" in time_str:
            time_part = time_str.replace("今天", "").strip()
            return self._combine_with_today(time_part)
        elif "昨天" in time_str:
            time_part = time_str.replace("昨天", "").strip()
            from datetime import timedelta

            return self._combine_with_today(time_part) - timedelta(days=1)
        elif re.match(r"\d{1,2}-\d{1,2}", time_str):
            month, day = map(int, time_str.split("-"))
            return datetime(now.year, month, day)
        elif re.match(r"\d{4}-\d{2}-\d{2}", time_str):
            return datetime.strptime(time_str[:10], "%Y-%m-%d")

        return now

    def _combine_with_today(self, time_part: str) -> datetime:
        """Combine time part with today's date."""
        from datetime import timedelta

        now = datetime.now()

        if ":" in time_part:
            hour, minute = map(int, time_part.split(":"))
            return now.replace(hour=hour, minute=minute, second=0)

        return now


class RSSHubScraper:
    """Scraper using RSSHub fallback."""

    def __init__(self, base_url: str, cookies: str = ""):
        self.base_url = base_url.rstrip("/")
        self.cookies = cookies

    async def fetch_activities(self, token: str) -> list[ActivityCard]:
        """Fetch activities via RSSHub."""
        import aiohttp

        url = f"{self.base_url}/zhihu/people/activities/{token}"
        headers = {}

        if self.cookies:
            headers["Cookie"] = self.cookies

        logger.info(f"Fetching via RSSHub: {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"RSSHub returned {resp.status}")
                    return []

                content = await resp.text()

        # Parse RSS/Atom feed
        return self._parse_feed(content, token)

    def _parse_feed(self, content: str, author_token: str) -> list[ActivityCard]:
        """Parse RSS/Atom feed content."""
        from xml.etree import ElementTree as ET

        cards = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            logger.error("Failed to parse RSS feed XML")
            return []

        # Determine feed type
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        # Try Atom format
        entries = root.findall(".//atom:entry", ns) or root.findall(".//entry")

        for entry in entries:
            title_elem = entry.find("atom:title", ns) or entry.find("title")
            link_elem = entry.find("atom:link", ns) or entry.find("link")
            summary_elem = entry.find("atom:summary", ns) or entry.find("summary")
            updated_elem = entry.find("atom:updated", ns) or entry.find("updated")

            title = title_elem.text if title_elem is not None else ""
            link = ""
            if link_elem is not None:
                link = link_elem.get("href") or ""

            summary = summary_elem.text if summary_elem is not None else ""

            time_str = ""
            if updated_elem is not None:
                time_str = updated_elem.text

            if title:
                cards.append(
                    ActivityCard(
                        title=title.strip(),
                        link=link.strip(),
                        summary=summary.strip()[:200] if summary else "",
                        time=self._parse_feed_time(time_str),
                        author=author_token,
                        raw_time=time_str,
                    )
                )

        logger.info(f"Parsed {len(cards)} items from RSS feed")
        return cards

    def _parse_feed_time(self, time_str: str) -> datetime:
        """Parse ISO format time from feed."""
        if not time_str:
            return datetime.now()

        # Handle various ISO formats
        formats = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str[:19], fmt[:19])
            except ValueError:
                continue

        return datetime.now()
