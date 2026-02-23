"""SQLite storage for activity deduplication."""

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class StoredActivity:
    """Activity stored in database."""

    id: int
    unique_id: str
    title: str
    link: str
    summary: str
    author: str
    time: datetime
    created_at: datetime


class Database:
    """SQLite database for storing seen activities."""

    def __init__(self, db_path: str = "zhihu_activities.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    @contextmanager
    def _transaction(self):
        """Context manager for transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unique_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT,
                    summary TEXT,
                    author TEXT NOT NULL,
                    time TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_unique_id ON activities(unique_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_author ON activities(author)
            """)

    def is_seen(self, unique_id: str) -> bool:
        """Check if activity has been seen."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT 1 FROM activities WHERE unique_id = ? LIMIT 1", (unique_id,)
        )
        return cursor.fetchone() is not None

    def add_activity(
        self,
        unique_id: str,
        title: str,
        link: str,
        summary: str,
        author: str,
        time_str: str,
    ) -> bool:
        """Add new activity. Returns True if added, False if already exists."""
        try:
            with self._transaction() as conn:
                conn.execute(
                    """INSERT INTO activities (unique_id, title, link, summary, author, time)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (unique_id, title, link, summary, author, time_str),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_recent_activities(
        self, author: Optional[str] = None, limit: int = 50
    ) -> list[StoredActivity]:
        """Get recent activities, optionally filtered by author."""
        conn = self._get_connection()

        if author:
            cursor = conn.execute(
                """SELECT * FROM activities WHERE author = ? 
                   ORDER BY created_at DESC LIMIT ?""",
                (author, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM activities ORDER BY created_at DESC LIMIT ?", (limit,)
            )

        rows = cursor.fetchall()
        return [
            StoredActivity(
                id=row["id"],
                unique_id=row["unique_id"],
                title=row["title"],
                link=row["link"],
                summary=row["summary"],
                author=row["author"],
                time=datetime.fromisoformat(row["time"])
                if row["time"]
                else datetime.now(),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def get_stats(self) -> dict:
        """Get database statistics."""
        conn = self._get_connection()

        total = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        authors = conn.execute(
            "SELECT COUNT(DISTINCT author) FROM activities"
        ).fetchone()[0]

        return {
            "total_activities": total,
            "unique_authors": authors,
        }

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
