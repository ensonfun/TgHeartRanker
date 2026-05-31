from __future__ import annotations

import unittest
from datetime import timedelta

from tg_heart_ranker.bot import (
    _format_top_messages,
    _index_is_stale,
    _parse_global_args,
    _parse_new_args,
    _parse_url_limit_args,
    _parse_rank_callback_data,
    _rank_callback_data,
)
from tg_heart_ranker.db import utc_now
from tg_heart_ranker.models import RankedMessage


class BotFormatTest(unittest.TestCase):
    def test_format_top_messages_uses_compact_html(self) -> None:
        text = _format_top_messages(
            "A <Channel>",
            [
                RankedMessage(
                    channel_id=1,
                    message_id=10,
                    date="2026-04-13T00:00:00+00:00",
                    text_preview="hello <world>",
                    url="https://t.me/example/10?x=1&y=2",
                    heart_count=19,
                    total_reactions=20,
                    indexed_at="2026-04-27T00:00:00+00:00",
                )
            ],
            page=0,
            limit=10,
            total=1,
            period="all",
        )

        self.assertIn("<b>A &lt;Channel&gt;</b>", text)
        self.assertIn(
            '<a href="https://t.me/example/10?x=1&amp;y=2">'
            "#01  表情 20  ·  2026-04-13\n"
            "hello &lt;world&gt;</a>",
            text,
        )
        self.assertNotIn("查看原帖", text)
        self.assertNotIn("19 hearts", text)

    def test_rank_callback_payload_round_trip(self) -> None:
        payload = _rank_callback_data(123, 4, 10, "month")
        self.assertEqual(_parse_rank_callback_data(payload), (123, 4, 10, "month"))

    def test_index_stale_uses_24_hour_cache_window(self) -> None:
        fresh = (utc_now() - timedelta(hours=23, minutes=59)).isoformat()
        stale = (utc_now() - timedelta(hours=24, minutes=1)).isoformat()

        self.assertFalse(_index_is_stale(fresh))
        self.assertTrue(_index_is_stale(stale))
        self.assertTrue(_index_is_stale(""))

    def test_default_limit_can_be_overridden_with_zero(self) -> None:
        defaulted = _parse_url_limit_args(
            "https://t.me/example",
            default_limit=5000,
            default_when_missing=None,
            max_limit=None,
        )
        explicit_all = _parse_url_limit_args(
            "https://t.me/example 0",
            default_limit=5000,
            default_when_missing=None,
            max_limit=None,
        )

        self.assertEqual(defaulted.limit, 5000)
        self.assertEqual(explicit_all.limit, 0)

    def test_global_and_new_args(self) -> None:
        self.assertEqual(_parse_global_args(""), (10, "week"))
        self.assertEqual(_parse_global_args("20 month"), (20, "month"))
        self.assertEqual(_parse_new_args(""), (10, 1))
        self.assertEqual(_parse_new_args("20 7"), (20, 7))


if __name__ == "__main__":
    unittest.main()
