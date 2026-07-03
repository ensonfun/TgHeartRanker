from __future__ import annotations

import sqlite3
from dataclasses import replace
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
  last_indexed_at TEXT,
  disabled_at TEXT,
  disabled_reason TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  text_preview TEXT NOT NULL,
  url TEXT NOT NULL,
  heart_count INTEGER NOT NULL DEFAULT 0,
  total_reactions INTEGER NOT NULL DEFAULT 0,
  video_duration_seconds INTEGER NOT NULL DEFAULT 0,
  media_group_id INTEGER,
  indexed_at TEXT NOT NULL,
  first_indexed_at TEXT NOT NULL,
  PRIMARY KEY (channel_id, message_id),
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_rank
ON messages(channel_id, heart_count DESC, date DESC);

CREATE INDEX IF NOT EXISTS idx_messages_total_reactions_rank
ON messages(channel_id, total_reactions DESC, date DESC);

CREATE INDEX IF NOT EXISTS idx_messages_global_total_reactions_rank
ON messages(total_reactions DESC, date DESC);

CREATE TABLE IF NOT EXISTS indexes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT,
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ranking_appearances (
  scope TEXT NOT NULL,
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (scope, channel_id, message_id),
  FOREIGN KEY (channel_id, message_id)
    REFERENCES messages(channel_id, message_id)
    ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        channel_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(channels)").fetchall()
        }
        if "disabled_at" not in channel_columns:
            conn.execute("ALTER TABLE channels ADD COLUMN disabled_at TEXT")
        if "disabled_reason" not in channel_columns:
            conn.execute("ALTER TABLE channels ADD COLUMN disabled_reason TEXT")

        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "first_indexed_at" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN first_indexed_at TEXT")
            conn.execute(
                """
                UPDATE messages
                SET first_indexed_at = indexed_at
                WHERE first_indexed_at IS NULL OR first_indexed_at = ''
                """
            )
        if "video_duration_seconds" not in columns:
            conn.execute(
                """
                ALTER TABLE messages
                ADD COLUMN video_duration_seconds INTEGER NOT NULL DEFAULT 0
                """
            )
        if "media_group_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN media_group_id INTEGER")
        conn.execute(
            """
            UPDATE messages
            SET first_indexed_at = indexed_at
            WHERE first_indexed_at IS NULL OR first_indexed_at = ''
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_first_indexed_at
            ON messages(first_indexed_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_media_group
            ON messages(channel_id, media_group_id)
            """
        )

    def upsert_channel(
        self, channel: ChannelInfo, last_indexed_at: datetime | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (
                  id,
                  username,
                  title,
                  url,
                  last_indexed_at,
                  disabled_at,
                  disabled_reason
                )
                VALUES (?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(id) DO UPDATE SET
                  username = excluded.username,
                  title = excluded.title,
                  url = excluded.url,
                  last_indexed_at = COALESCE(
                    excluded.last_indexed_at,
                    channels.last_indexed_at
                  ),
                  disabled_at = NULL,
                  disabled_reason = NULL
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

    def mark_channel_disabled(
        self,
        channel_id: int,
        *,
        when: datetime,
        reason: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE channels
                SET disabled_at = ?, disabled_reason = ?
                WHERE id = ?
                """,
                (_to_iso(when), reason, channel_id),
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
                  video_duration_seconds,
                  media_group_id,
                  indexed_at,
                  first_indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id) DO UPDATE SET
                  date = excluded.date,
                  text_preview = excluded.text_preview,
                  url = excluded.url,
                  heart_count = excluded.heart_count,
                  total_reactions = excluded.total_reactions,
                  video_duration_seconds = excluded.video_duration_seconds,
                  media_group_id = excluded.media_group_id,
                  indexed_at = excluded.indexed_at,
                  first_indexed_at = messages.first_indexed_at
                """,
                (
                    message.channel_id,
                    message.message_id,
                    _to_iso(message.date),
                    message.text_preview,
                    message.url,
                    message.heart_count,
                    message.total_reactions,
                    message.video_duration_seconds,
                    message.media_group_id,
                    _to_iso(message.indexed_at),
                    _to_iso(message.first_indexed_at or message.indexed_at),
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
        since_filter = "AND m.date >= ?" if since else ""
        params: tuple[object, ...]
        if since:
            params = (channel_id, _to_iso(since), limit, offset)
        else:
            params = (channel_id, limit, offset)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                  m.*,
                  CASE
                    WHEN m.media_group_id IS NULL THEN m.video_duration_seconds
                    ELSE COALESCE((
                      SELECT SUM(grouped.video_duration_seconds)
                      FROM messages grouped
                      WHERE grouped.channel_id = m.channel_id
                        AND grouped.media_group_id = m.media_group_id
                    ), 0)
                  END AS total_video_duration_seconds
                FROM messages m
                WHERE m.channel_id = ? AND m.total_reactions > 0
                {since_filter}
                ORDER BY m.total_reactions DESC, m.date DESC
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
                WHERE channel_id = ? AND total_reactions > 0
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

    def get_channel_by_username(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(m.message_id) AS indexed_messages
                FROM channels c
                LEFT JOIN messages m ON m.channel_id = c.id
                WHERE LOWER(c.username) = LOWER(?)
                GROUP BY c.id
                """,
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def delete_channel(self, channel_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(m.message_id) AS indexed_messages
                FROM channels c
                LEFT JOIN messages m ON m.channel_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (channel_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        return result

    def list_indexed_channels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(m.message_id) AS indexed_messages,
                  COALESCE(MAX(m.indexed_at), '') AS latest_message_indexed_at
                FROM channels c
                JOIN messages m ON m.channel_id = c.id
                WHERE c.disabled_at IS NULL
                GROUP BY c.id
                ORDER BY COALESCE(c.last_indexed_at, '') ASC, c.title ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_global_top_messages(
        self,
        *,
        limit: int,
        since: datetime | None = None,
        first_indexed_since: datetime | None = None,
    ) -> list[RankedMessage]:
        filters = ["m.total_reactions > 0"]
        params: list[object] = []
        if since:
            filters.append("m.date >= ?")
            params.append(_to_iso(since))
        if first_indexed_since:
            filters.append("m.first_indexed_at >= ?")
            params.append(_to_iso(first_indexed_since))
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                  m.*,
                  c.title AS channel_title,
                  c.username AS channel_username,
                  CASE
                    WHEN m.media_group_id IS NULL THEN m.video_duration_seconds
                    ELSE COALESCE((
                      SELECT SUM(grouped.video_duration_seconds)
                      FROM messages grouped
                      WHERE grouped.channel_id = m.channel_id
                        AND grouped.media_group_id = m.media_group_id
                    ), 0)
                  END AS total_video_duration_seconds
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                WHERE {" AND ".join(filters)}
                ORDER BY m.total_reactions DESC, m.date DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [_ranked_message_from_row(row) for row in rows]

    def mark_ranking_appearances(
        self,
        *,
        scope: str,
        rows: list[RankedMessage],
        seen_at: datetime,
    ) -> list[RankedMessage]:
        if not rows:
            return []

        annotated: list[RankedMessage] = []
        seen_at_iso = _to_iso(seen_at)
        with self._connect() as conn:
            for row in rows:
                existing = conn.execute(
                    """
                    SELECT first_seen_at
                    FROM ranking_appearances
                    WHERE scope = ? AND channel_id = ? AND message_id = ?
                    """,
                    (scope, row.channel_id, row.message_id),
                ).fetchone()
                is_new_entry = existing is None
                annotated.append(replace(row, is_new_entry=is_new_entry))
                if is_new_entry:
                    conn.execute(
                        """
                        INSERT INTO ranking_appearances (
                          scope,
                          channel_id,
                          message_id,
                          first_seen_at,
                          last_seen_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            scope,
                            row.channel_id,
                            row.message_id,
                            seen_at_iso,
                            seen_at_iso,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE ranking_appearances
                        SET last_seen_at = ?
                        WHERE scope = ? AND channel_id = ? AND message_id = ?
                        """,
                        (seen_at_iso, scope, row.channel_id, row.message_id),
                    )
        return annotated

    def get_status(self, channel_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            channel = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(m.message_id) AS indexed_messages,
                  COALESCE(SUM(CASE WHEN m.total_reactions > 0 THEN 1 ELSE 0 END), 0)
                    AS messages_with_reactions,
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
        video_duration_seconds=int(
            row["total_video_duration_seconds"]
            if "total_video_duration_seconds" in row.keys()
            else row["video_duration_seconds"]
        ),
        media_group_id=(
            int(row["media_group_id"])
            if row["media_group_id"] is not None
            else None
        ),
        indexed_at=str(row["indexed_at"]),
        first_indexed_at=str(row["first_indexed_at"]),
        channel_title=str(row["channel_title"]) if "channel_title" in row.keys() else "",
        channel_username=(
            str(row["channel_username"])
            if "channel_username" in row.keys() and row["channel_username"] is not None
            else None
        ),
    )
