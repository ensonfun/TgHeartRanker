from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ChannelInfo, MessageRecord, RankedMessage


SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY,
  username TEXT,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  text_preview TEXT NOT NULL,
  url TEXT NOT NULL,
  heart_count INTEGER NOT NULL DEFAULT 0,
  total_reactions INTEGER NOT NULL DEFAULT 0,
  indexed_at TEXT NOT NULL,
  PRIMARY KEY (channel_id, message_id),
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_rank
ON messages(channel_id, heart_count DESC, date DESC);

CREATE TABLE IF NOT EXISTS indexes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT,
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_channel(
        self, channel: ChannelInfo, last_indexed_at: datetime | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, username, title, url, last_indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  username = excluded.username,
                  title = excluded.title,
                  url = excluded.url,
                  last_indexed_at = COALESCE(
                    excluded.last_indexed_at,
                    channels.last_indexed_at
                  )
                """,
                (
                    channel.id,
                    channel.username,
                    channel.title,
                    channel.url,
                    _to_iso(last_indexed_at) if last_indexed_at else None,
                ),
            )

    def mark_channel_indexed(self, channel_id: int, when: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE channels SET last_indexed_at = ? WHERE id = ?",
                (_to_iso(when), channel_id),
            )

    def upsert_message(self, message: MessageRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                  channel_id,
                  message_id,
                  date,
                  text_preview,
                  url,
                  heart_count,
                  total_reactions,
                  indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id) DO UPDATE SET
                  date = excluded.date,
                  text_preview = excluded.text_preview,
                  url = excluded.url,
                  heart_count = excluded.heart_count,
                  total_reactions = excluded.total_reactions,
                  indexed_at = excluded.indexed_at
                """,
                (
                    message.channel_id,
                    message.message_id,
                    _to_iso(message.date),
                    message.text_preview,
                    message.url,
                    message.heart_count,
                    message.total_reactions,
                    _to_iso(message.indexed_at),
                ),
            )

    def start_index(self, channel_id: int, started_at: datetime) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO indexes (channel_id, status, started_at)
                VALUES (?, 'running', ?)
                """,
                (channel_id, _to_iso(started_at)),
            )
            return int(cursor.lastrowid)

    def finish_index(
        self,
        index_id: int,
        status: str,
        finished_at: datetime,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE indexes
                SET status = ?, finished_at = ?, error = ?
                WHERE id = ?
                """,
                (status, _to_iso(finished_at), error, index_id),
            )

    def get_top_messages(
        self,
        channel_id: int,
        limit: int,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[RankedMessage]:
        since_filter = "AND date >= ?" if since else ""
        params: tuple[object, ...]
        if since:
            params = (channel_id, _to_iso(since), limit, offset)
        else:
            params = (channel_id, limit, offset)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM messages
                WHERE channel_id = ? AND heart_count > 0
                {since_filter}
                ORDER BY heart_count DESC, date DESC
                LIMIT ?
                OFFSET ?
                """,
                params,
            ).fetchall()
        return [_ranked_message_from_row(row) for row in rows]

    def count_top_messages(
        self, channel_id: int, since: datetime | None = None
    ) -> int:
        since_filter = "AND date >= ?" if since else ""
        params: tuple[object, ...]
        if since:
            params = (channel_id, _to_iso(since))
        else:
            params = (channel_id,)

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM messages
                WHERE channel_id = ? AND heart_count > 0
                {since_filter}
                """,
                params,
            ).fetchone()
        return int(row["count"]) if row else 0

    def get_channel(self, channel_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE id = ?", (channel_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_status(self, channel_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            channel = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(m.message_id) AS indexed_messages,
                  COALESCE(SUM(CASE WHEN m.heart_count > 0 THEN 1 ELSE 0 END), 0)
                    AS messages_with_hearts,
                  COALESCE(MAX(m.indexed_at), '') AS latest_message_indexed_at
                FROM channels c
                LEFT JOIN messages m ON m.channel_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (channel_id,),
            ).fetchone()
            if not channel:
                return None

            latest_index = conn.execute(
                """
                SELECT status, started_at, finished_at, error
                FROM indexes
                WHERE channel_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (channel_id,),
            ).fetchone()

        result = dict(channel)
        result["latest_index"] = dict(latest_index) if latest_index else None
        return result

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _ranked_message_from_row(row: sqlite3.Row) -> RankedMessage:
    return RankedMessage(
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]),
        date=str(row["date"]),
        text_preview=str(row["text_preview"]),
        url=str(row["url"]),
        heart_count=int(row["heart_count"]),
        total_reactions=int(row["total_reactions"]),
        indexed_at=str(row["indexed_at"]),
    )
