"""Zhihu Writer Monitor - Package initialization."""

from .config import Config, load_config
from .browser import BrowserManager
from .cookies import CookieLoader
from .scraper import ZhihuScraper, RSSHubScraper, ActivityCard
from .storage import Database, StoredActivity
from .notifications import NotificationManager
from .monitor import ZhihuMonitor, run_monitor

__version__ = "1.0.0"
__all__ = [
    "Config",
    "load_config",
    "BrowserManager",
    "CookieLoader",
    "ZhihuScraper",
    "RSSHubScraper",
    "ActivityCard",
    "Database",
    "StoredActivity",
    "NotificationManager",
    "ZhihuMonitor",
    "run_monitor",
]
