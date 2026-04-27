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
            self.assertEqual([row.message_id for row in top], [4, 1, 2])
            self.assertEqual(top[0].heart_count, 100)
            self.assertEqual(db.count_top_messages(100), 3)

            second_page = db.get_top_messages(100, 1, offset=1)
            self.assertEqual([row.message_id for row in second_page], [1])

            recent_since = now - timedelta(days=365)
            recent_top = db.get_top_messages(100, 10, since=recent_since)
            self.assertEqual([row.message_id for row in recent_top], [1, 2])
            self.assertEqual(db.count_top_messages(100, since=recent_since), 2)

            status = db.get_status(100)
            self.assertIsNotNone(status)
            self.assertEqual(status["indexed_messages"], 4)
            self.assertEqual(status["messages_with_hearts"], 3)


if __name__ == "__main__":
    unittest.main()
