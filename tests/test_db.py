from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tg_heart_ranker.db import Database
from tg_heart_ranker.models import ChannelInfo, MessageRecord


class DatabaseTest(unittest.TestCase):
    def test_upsert_and_rank_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "ranker.sqlite3")
            db.initialize()

            channel = ChannelInfo(
                id=100,
                username="some_channel",
                title="Some Channel",
                url="https://t.me/some_channel",
            )
            db.upsert_channel(channel)

            now = datetime.now(timezone.utc)
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=1,
                    date=now,
                    text_preview="first",
                    url="https://t.me/some_channel/1",
                    heart_count=1,
                    total_reactions=2,
                    indexed_at=now,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=2,
                    date=now,
                    text_preview="second",
                    url="https://t.me/some_channel/2",
                    heart_count=5,
                    total_reactions=5,
                    indexed_at=now,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=1,
                    date=now,
                    text_preview="first updated",
                    url="https://t.me/some_channel/1",
                    heart_count=8,
                    total_reactions=9,
                    indexed_at=now,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=3,
                    date=now,
                    text_preview="third",
                    url="https://t.me/some_channel/3",
                    heart_count=0,
                    total_reactions=3,
                    indexed_at=now,
                )
            )
            old = now - timedelta(days=500)
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=4,
                    date=old,
                    text_preview="old",
                    url="https://t.me/some_channel/4",
                    heart_count=100,
                    total_reactions=100,
                    indexed_at=now,
                )
            )

            top = db.get_top_messages(100, 10)
            self.assertEqual([row.message_id for row in top], [4, 1, 2, 3])
            self.assertEqual(top[0].total_reactions, 100)
            self.assertEqual(db.count_top_messages(100), 4)

            second_page = db.get_top_messages(100, 1, offset=1)
            self.assertEqual([row.message_id for row in second_page], [1])

            recent_since = now - timedelta(days=365)
            recent_top = db.get_top_messages(100, 10, since=recent_since)
            self.assertEqual([row.message_id for row in recent_top], [1, 2, 3])
            self.assertEqual(db.count_top_messages(100, since=recent_since), 3)

            status = db.get_status(100)
            self.assertIsNotNone(status)
            self.assertEqual(status["indexed_messages"], 4)
            self.assertEqual(status["messages_with_reactions"], 4)

    def test_disabled_channels_are_excluded_from_daily_refresh_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "ranker.sqlite3")
            db.initialize()
            active = ChannelInfo(
                id=100,
                username="active_channel",
                title="Active Channel",
                url="https://t.me/active_channel",
            )
            disabled = ChannelInfo(
                id=200,
                username="disabled_channel",
                title="Disabled Channel",
                url="https://t.me/disabled_channel",
            )
            now = datetime.now(timezone.utc)
            db.upsert_channel(active)
            db.upsert_channel(disabled)
            for channel_id in (100, 200):
                db.upsert_message(
                    MessageRecord(
                        channel_id=channel_id,
                        message_id=1,
                        date=now,
                        text_preview="message",
                        url=f"https://t.me/channel/{channel_id}",
                        heart_count=1,
                        total_reactions=1,
                        indexed_at=now,
                    )
                )

            db.mark_channel_disabled(
                200,
                when=now,
                reason="Nobody is using this username",
            )

            channels = db.list_indexed_channels()
            self.assertEqual([channel["id"] for channel in channels], [100])
            status = db.get_status(200)
            self.assertEqual(status["disabled_reason"], "Nobody is using this username")

            db.upsert_channel(disabled)
            self.assertIsNone(db.get_status(200)["disabled_at"])

    def test_first_indexed_at_is_preserved_on_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "ranker.sqlite3")
            db.initialize()
            channel = ChannelInfo(
                id=100,
                username="some_channel",
                title="Some Channel",
                url="https://t.me/some_channel",
            )
            db.upsert_channel(channel)

            first_seen = datetime(2026, 5, 1, tzinfo=timezone.utc)
            refreshed = datetime(2026, 5, 2, tzinfo=timezone.utc)
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=1,
                    date=first_seen,
                    text_preview="first",
                    url="https://t.me/some_channel/1",
                    heart_count=1,
                    total_reactions=2,
                    indexed_at=first_seen,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=1,
                    date=first_seen,
                    text_preview="first updated",
                    url="https://t.me/some_channel/1",
                    heart_count=2,
                    total_reactions=8,
                    indexed_at=refreshed,
                )
            )

            top = db.get_top_messages(100, 1)
            self.assertEqual(top[0].indexed_at, refreshed.isoformat())
            self.assertEqual(top[0].first_indexed_at, first_seen.isoformat())

    def test_global_top_messages_can_filter_by_date_and_first_indexed_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "ranker.sqlite3")
            db.initialize()
            first_channel = ChannelInfo(
                id=100,
                username="first_channel",
                title="First Channel",
                url="https://t.me/first_channel",
            )
            second_channel = ChannelInfo(
                id=200,
                username="second_channel",
                title="Second Channel",
                url="https://t.me/second_channel",
            )
            db.upsert_channel(first_channel)
            db.upsert_channel(second_channel)

            now = datetime(2026, 5, 31, tzinfo=timezone.utc)
            old = now - timedelta(days=10)
            db.upsert_message(
                MessageRecord(
                    channel_id=100,
                    message_id=1,
                    date=now,
                    text_preview="first",
                    url="https://t.me/first_channel/1",
                    heart_count=1,
                    total_reactions=20,
                    indexed_at=now,
                    first_indexed_at=now,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=200,
                    message_id=1,
                    date=now,
                    text_preview="second",
                    url="https://t.me/second_channel/1",
                    heart_count=1,
                    total_reactions=30,
                    indexed_at=now,
                    first_indexed_at=old,
                )
            )
            db.upsert_message(
                MessageRecord(
                    channel_id=200,
                    message_id=2,
                    date=old,
                    text_preview="old",
                    url="https://t.me/second_channel/2",
                    heart_count=1,
                    total_reactions=100,
                    indexed_at=now,
                    first_indexed_at=now,
                )
            )

            recent = db.get_global_top_messages(limit=10, since=now - timedelta(days=7))
            self.assertEqual([row.message_id for row in recent], [1, 1])
            self.assertEqual(recent[0].channel_title, "Second Channel")

            new = db.get_global_top_messages(
                limit=10,
                first_indexed_since=now - timedelta(hours=1),
            )
            self.assertEqual([(row.channel_id, row.message_id) for row in new], [(200, 2), (100, 1)])


if __name__ == "__main__":
    unittest.main()
