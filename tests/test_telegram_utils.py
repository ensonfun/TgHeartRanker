from __future__ import annotations

import unittest

from tg_heart_ranker.telegram_utils import (
    extract_reaction_counts,
    extract_video_duration_seconds,
    format_video_duration,
    parse_channel_reference,
    text_preview,
)


class Reaction:
    def __init__(self, emoticon: str) -> None:
        self.emoticon = emoticon


class ReactionCount:
    def __init__(self, reaction: Reaction, count: int) -> None:
        self.reaction = reaction
        self.count = count


class Reactions:
    def __init__(self, results: list[ReactionCount]) -> None:
        self.results = results


class VideoAttribute:
    def __init__(self, duration: float) -> None:
        self.duration = duration


class Video:
    def __init__(self, duration: float) -> None:
        self.attributes = [VideoAttribute(duration)]


class Message:
    def __init__(self, video: object | None, file: object | None = None) -> None:
        self.video = video
        self.file = file


class TelegramUtilsTest(unittest.TestCase):
    def test_parse_channel_reference(self) -> None:
        self.assertEqual(parse_channel_reference("@some_channel"), "some_channel")
        self.assertEqual(
            parse_channel_reference("https://t.me/some_channel/123?single"),
            "some_channel",
        )
        self.assertEqual(parse_channel_reference("t.me/some_channel"), "some_channel")

    def test_parse_channel_reference_rejects_private_links(self) -> None:
        with self.assertRaises(ValueError):
            parse_channel_reference("https://t.me/c/123/456")
        with self.assertRaises(ValueError):
            parse_channel_reference("https://t.me/+abcdef")

    def test_extract_reaction_counts_normalizes_heart(self) -> None:
        reactions = Reactions(
            [
                ReactionCount(Reaction("\u2764\ufe0f"), 3),
                ReactionCount(Reaction("\u2764"), 2),
                ReactionCount(Reaction("\U0001f44d"), 10),
            ]
        )
        self.assertEqual(extract_reaction_counts(reactions), (5, 15))

    def test_text_preview_compacts_and_truncates(self) -> None:
        preview = text_preview("hello\n\nworld " * 20, max_length=20)
        self.assertLessEqual(len(preview), 20)
        self.assertTrue(preview.endswith("..."))

    def test_extracts_and_formats_video_duration(self) -> None:
        self.assertEqual(extract_video_duration_seconds(Message(Video(125.4))), 125)
        self.assertEqual(extract_video_duration_seconds(Message(None)), 0)
        self.assertEqual(format_video_duration(125), "2:05")
        self.assertEqual(format_video_duration(3661), "1:01:01")
        self.assertEqual(format_video_duration(0), "")


if __name__ == "__main__":
    unittest.main()
