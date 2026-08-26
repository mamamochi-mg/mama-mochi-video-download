import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/bot.sqlite3")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
_LOCK = threading.Lock()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _LOCK, connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            consent INTEGER NOT NULL DEFAULT 0,
            downloads INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT,
            url TEXT NOT NULL,
            action TEXT NOT NULL,
            quality TEXT,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_links_user ON links(user_id, created_at DESC);
        """)


def upsert_user(user_id: int, username: str | None, display_name: str | None) -> None:
    now = int(time.time())
    with _LOCK, connect() as conn:
        conn.execute("""
            INSERT INTO users(user_id, username, display_name, first_seen, last_seen)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            display_name=excluded.display_name, last_seen=excluded.last_seen
        """, (user_id, username, display_name, now, now))


def has_consent(user_id: int) -> bool:
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT consent FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["consent"])


def set_consent(user_id: int, value: bool = True) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE users SET consent=? WHERE user_id=?", (1 if value else 0, user_id))


def log_link(user_id: int, username: str | None, display_name: str | None, url: str, action: str, quality: str = "", status: str = "received") -> int:
    with _LOCK, connect() as conn:
        cur = conn.execute("""
            INSERT INTO links(user_id, username, display_name, url, action, quality, status, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, username, display_name, url, action, quality, status, int(time.time())))
        return int(cur.lastrowid)


def update_link_status(link_id: int, status: str, quality: str | None = None) -> None:
    with _LOCK, connect() as conn:
        if quality is None:
            conn.execute("UPDATE links SET status=? WHERE id=?", (status, link_id))
        else:
            conn.execute("UPDATE links SET status=?, quality=? WHERE id=?", (status, quality, link_id))
        if status == "completed":
            conn.execute("UPDATE users SET downloads=downloads+1 WHERE user_id=(SELECT user_id FROM links WHERE id=?)", (link_id,))


def recent_links(user_id: int, limit: int = 10):
    with _LOCK, connect() as conn:
        return conn.execute("SELECT * FROM links WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()


def stats() -> dict:
    with _LOCK, connect() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        links = conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]
        completed = conn.execute("SELECT COUNT(*) AS n FROM links WHERE status='completed'").fetchone()["n"]
        return {"users": users, "links": links, "completed": completed}
