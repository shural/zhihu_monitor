"""Notification system for new activity alerts."""

import asyncio
import logging
import os
import platform
from abc import ABC, abstractmethod
from typing import Optional

from config import NotificationsConfig, TelegramConfig, NtfyConfig, DesktopConfig

logger = logging.getLogger(__name__)


class NotificationChannel(ABC):
    """Base class for notification channels."""

    @abstractmethod
    async def send(self, title: str, message: str, link: str = "") -> bool:
        """Send notification. Returns True if successful."""
        pass


class TelegramNotifier(NotificationChannel):
    """Telegram notification channel."""

    def __init__(self, config: TelegramConfig):
        self.config = config
        self.bot_token = config.bot_token
        self.chat_id = config.chat_id

    async def send(self, title: str, message: str, link: str = "") -> bool:
        """Send Telegram message."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured")
            return False

        import aiohttp

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        text = f"📝 *{title}*\n\n{message}"
        if link:
            text += f"\n\n🔗 [查看原文]({link})"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"Telegram notification sent: {title}")
                        return True
                    else:
                        logger.error(f"Telegram API error: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False


class NtfyNotifier(NotificationChannel):
    """ntfy.sh notification channel."""

    def __init__(self, config: NtfyConfig):
        self.config = config
        self.server = config.server.rstrip("/")
        self.topic = config.topic

    async def send(self, title: str, message: str, link: str = "") -> bool:
        """Send ntfy notification."""
        if not self.topic:
            logger.warning("ntfy not configured")
            return False

        import aiohttp

        url = f"{self.server}/{self.topic}"

        headers = {}
        if self.server != "https://ntfy.sh":
            headers["Authorization"] = f"Bearer {self.config.server}"

        body = f"{title}\n\n{message}"
        if link:
            body += f"\n\n{link}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=body.encode("utf-8"),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"ntfy notification sent: {title}")
                        return True
                    else:
                        logger.error(f"ntfy error: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Failed to send ntfy notification: {e}")
            return False


class DesktopNotifier(NotificationChannel):
    """Desktop notification using plyer."""

    def __init__(self, config: DesktopConfig):
        self.config = config

    async def send(self, title: str, message: str, link: str = "") -> bool:
        """Send desktop notification."""
        try:
            from plyer import notification

            notification.notify(
                title=title,
                message=message[:200],
                app_name="Zhihu Monitor",
                timeout=10,
            )
            logger.info(f"Desktop notification sent: {title}")
            return True
        except ImportError:
            logger.warning("plyer not installed")
            return False
        except Exception as e:
            logger.error(f"Failed to send desktop notification: {e}")
            return False


class NotificationManager:
    """Manages multiple notification channels."""

    def __init__(self, config: NotificationsConfig):
        self.config = config
        self.channels: list[NotificationChannel] = []
        self._setup_channels()

    def _setup_channels(self) -> None:
        """Setup enabled notification channels."""
        if not self.config.enabled:
            logger.info("Notifications disabled")
            return

        if self.config.telegram.enabled:
            self.channels.append(TelegramNotifier(self.config.telegram))

        if self.config.ntfy.enabled:
            self.channels.append(NtfyNotifier(self.config.ntfy))

        if self.config.desktop.enabled:
            self.channels.append(DesktopNotifier(self.config.desktop))

        if self.channels:
            logger.info(f"Configured {len(self.channels)} notification channels")
        else:
            logger.warning("No notification channels configured")

    async def notify(self, title: str, message: str, link: str = "") -> None:
        """Send notification to all configured channels."""
        if not self.channels:
            return

        results = await asyncio.gather(
            *[channel.send(title, message, link) for channel in self.channels],
            return_exceptions=True,
        )

        success = sum(1 for r in results if r is True)
        logger.info(f"Notification sent to {success}/{len(self.channels)} channels")
