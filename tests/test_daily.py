from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tg_heart_ranker.daily import (
    _is_permanent_channel_error,
    _random_channel_delay_seconds,
    _retry_delay_seconds,
)
from telethon.errors import ChannelPrivateError, UsernameInvalidError


class DailySchedulingTest(unittest.TestCase):
    def test_random_channel_delay_can_be_fixed_or_disabled(self) -> None:
        disabled = SimpleNamespace(
            daily_refresh_delay_min_seconds=0.0,
            daily_refresh_delay_max_seconds=0.0,
        )
        fixed = SimpleNamespace(
            daily_refresh_delay_min_seconds=60.0,
            daily_refresh_delay_max_seconds=60.0,
        )

        self.assertEqual(_random_channel_delay_seconds(disabled), 0.0)
        self.assertEqual(_random_channel_delay_seconds(fixed), 60.0)

    def test_random_channel_delay_uses_configured_bounds(self) -> None:
        settings = SimpleNamespace(
            daily_refresh_delay_min_seconds=60.0,
            daily_refresh_delay_max_seconds=180.0,
        )
        with patch("tg_heart_ranker.daily.random.uniform", return_value=120.0) as uniform:
            self.assertEqual(_random_channel_delay_seconds(settings), 120.0)
            uniform.assert_called_once_with(60.0, 180.0)

    def test_retry_delay_uses_exponential_backoff_with_cap(self) -> None:
        settings = SimpleNamespace(
            daily_refresh_retry_base_seconds=30.0,
            daily_refresh_retry_max_seconds=65.0,
        )
        with patch("tg_heart_ranker.daily.random.uniform", return_value=7.0):
            self.assertEqual(_retry_delay_seconds(settings, 1), 37.0)
            self.assertEqual(_retry_delay_seconds(settings, 3), 65.0)

    def test_retry_delay_honors_flood_wait_with_cap(self) -> None:
        settings = SimpleNamespace(
            daily_refresh_retry_base_seconds=30.0,
            daily_refresh_retry_max_seconds=300.0,
        )
        with patch("tg_heart_ranker.daily.random.uniform", return_value=5.0):
            self.assertEqual(
                _retry_delay_seconds(settings, 1, flood_wait_seconds=90),
                95.0,
            )

    def test_permanent_channel_errors_are_detected(self) -> None:
        self.assertTrue(
            _is_permanent_channel_error(
                UsernameInvalidError(request=None)
            )
        )
        self.assertTrue(
            _is_permanent_channel_error(
                ChannelPrivateError(request=None)
            )
        )
        self.assertFalse(_is_permanent_channel_error(OSError("temporary")))


if __name__ == "__main__":
    unittest.main()
