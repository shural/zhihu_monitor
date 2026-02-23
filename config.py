"""Type-safe configuration management for Zhihu Monitor."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Viewport:
    """Browser viewport configuration."""

    width: int = 1920
    height: int = 1080


@dataclass
class BrowserConfig:
    """Browser configuration."""

    headless: bool = False
    viewport: Viewport = field(default_factory=Viewport)
    user_agent: str = ""
    user_data_dir: str = "./zhihu-profile"
    login_timeout_seconds: int = 60
    chrome_cookie_extraction: bool = True
    stealth_level: str = "light"
    # Pinchtab integration
    use_pinchtab: bool = False
    pinchtab_url: str = "http://localhost:9877"
    pinchtab_profile: str = "zhihu"


@dataclass
class TelegramConfig:
    """Telegram notification configuration."""

    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class NtfyConfig:
    """ntfy.sh notification configuration."""

    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""


@dataclass
class DesktopConfig:
    """Desktop notification configuration."""

    enabled: bool = False


@dataclass
class NotificationsConfig:
    """Notification configuration."""

    enabled: bool = True
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    desktop: DesktopConfig = field(default_factory=DesktopConfig)


@dataclass
class RSSHubConfig:
    """RSSHub fallback configuration."""

    enabled: bool = False
    url: str = "http://localhost:1200"
    cookies: str = ""


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    file: str = "zhihu_monitor.log"


@dataclass
class Config:
    """Main configuration for Zhihu Monitor."""

    url_tokens: list[str] = field(default_factory=list)
    poll_interval_minutes: int = 30
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    rsshub: RSSHubConfig = field(default_factory=RSSHubConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """Load configuration from JSON or YAML file."""
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(f)
            else:
                data = json.load(f)

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        """Create Config from dictionary."""
        # Handle nested configs
        browser_data = data.get("browser", {})
        browser = BrowserConfig(
            headless=browser_data.get("headless", False),
            viewport=Viewport(
                **browser_data.get("viewport", {"width": 1920, "height": 1080})
            ),
            user_agent=browser_data.get("user_agent", ""),
            user_data_dir=browser_data.get("user_data_dir", "./zhihu-profile"),
            login_timeout_seconds=browser_data.get("login_timeout_seconds", 60),
            chrome_cookie_extraction=browser_data.get("chrome_cookie_extraction", True),
            stealth_level=browser_data.get("stealth_level", "light"),
            use_pinchtab=browser_data.get("use_pinchtab", False),
            pinchtab_url=browser_data.get("pinchtab_url", "http://localhost:9867"),
            pinchtab_profile=browser_data.get("pinchtab_profile", "zhihu"),
        )

        notif_data = data.get("notifications", {})
        telegram_data = notif_data.get("telegram", {})
        ntfy_data = notif_data.get("ntfy", {})
        desktop_data = notif_data.get("desktop", {})

        notifications = NotificationsConfig(
            enabled=notif_data.get("enabled", True),
            telegram=TelegramConfig(**telegram_data),
            ntfy=NtfyConfig(**ntfy_data),
            desktop=DesktopConfig(**desktop_data),
        )

        rsshub_data = data.get("rsshub", {})
        rsshub = RSSHubConfig(**rsshub_data)

        logging_data = data.get("logging", {})
        logging_config = LoggingConfig(**logging_data)

        return cls(
            url_tokens=data.get("url_tokens", []),
            poll_interval_minutes=data.get("poll_interval_minutes", 30),
            browser=browser,
            notifications=notifications,
            rsshub=rsshub,
            logging=logging_config,
        )

    def save(self, path: str | Path) -> None:
        """Save configuration to JSON file."""
        path = Path(path)
        data = self._to_dict()

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _to_dict(self) -> dict:
        """Convert Config to dictionary."""
        return {
            "url_tokens": self.url_tokens,
            "poll_interval_minutes": self.poll_interval_minutes,
            "browser": {
                "headless": self.browser.headless,
                "viewport": {
                    "width": self.browser.viewport.width,
                    "height": self.browser.viewport.height,
                },
                "user_data_dir": self.browser.user_data_dir,
                "login_timeout_seconds": self.browser.login_timeout_seconds,
                "chrome_cookie_extraction": self.browser.chrome_cookie_extraction,
                "stealth_level": self.browser.stealth_level,
                "use_pinchtab": self.browser.use_pinchtab,
                "pinchtab_url": self.browser.pinchtab_url,
                "pinchtab_profile": self.browser.pinchtab_profile,
            },
            "notifications": {
                "enabled": self.notifications.enabled,
                "telegram": {
                    "enabled": self.notifications.telegram.enabled,
                    "bot_token": self.notifications.telegram.bot_token,
                    "chat_id": self.notifications.telegram.chat_id,
                },
                "ntfy": {
                    "enabled": self.notifications.ntfy.enabled,
                    "server": self.notifications.ntfy.server,
                    "topic": self.notifications.ntfy.topic,
                },
                "desktop": {
                    "enabled": self.notifications.desktop.enabled,
                },
            },
            "rsshub": {
                "enabled": self.rsshub.enabled,
                "url": self.rsshub.url,
                "cookies": self.rsshub.cookies,
            },
            "logging": {
                "level": self.logging.level,
                "file": self.logging.file,
            },
        }


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from file or environment."""
    # Check for config path argument
    if config_path:
        return Config.from_file(config_path)

    # Check common config file locations
    candidates = [
        "config.json",
        "config.yaml",
        "config.yml",
        "config.json.example",
    ]

    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return Config.from_file(path)

    # Check environment variable
    env_config = os.environ.get("ZHIHU_CONFIG")
    if env_config and Path(env_config).exists():
        return Config.from_file(env_config)

    raise FileNotFoundError(
        "No config file found. Please create config.json or specify --config"
    )
