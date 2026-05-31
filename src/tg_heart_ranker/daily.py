from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telethon.errors import FloodWaitError, RPCError

from .config import Settings
from .db import Database, utc_now
from .indexer import ChannelIndexer
from .models import IndexResult, RankedMessage


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelRefreshFailure:
    title: str
    reference: str
    error: str


@dataclass(frozen=True)
class DailyReport:
    started_at: datetime
    finished_at: datetime
    refreshed: list[IndexResult]
    failures: list[ChannelRefreshFailure]
    weekly_top: list[RankedMessage]
    newly_indexed_top: list[RankedMessage]


class DailyReportRunner:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        indexer: ChannelIndexer,
        bot_client: object,
        index_lock: asyncio.Lock,
    ) -> None:
        self.settings = settings
        self.db = db
        self.indexer = indexer
        self.bot_client = bot_client
        self.index_lock = index_lock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._schedule_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def run_once(self, *, send_report: bool = True) -> DailyReport:
        started_at = utc_now()
        channels = self.db.list_indexed_channels()
        refreshed: list[IndexResult] = []
        failures: list[ChannelRefreshFailure] = []
        logger.info(
            "Daily refresh started channel_count=%s limit=%s report_limit=%s",
            len(channels),
            self.settings.daily_refresh_limit,
            self.settings.daily_report_top_limit,
        )

        async with self.index_lock:
            for channel in channels:
                title = str(channel.get("title") or channel.get("username") or "")
                reference = str(channel.get("username") or channel.get("url") or "")
                if not reference:
                    failures.append(
                        ChannelRefreshFailure(
                            title=title or f"channel:{channel.get('id')}",
                            reference="",
                            error="missing channel username/url",
                        )
                    )
                    continue
                try:
                    result = await self.indexer.index_channel(
                        reference,
                        limit=self.settings.daily_refresh_limit,
                    )
                    refreshed.append(result)
                    logger.info(
                        "Daily channel refresh succeeded channel_id=%s title=%r scanned=%s stored=%s with_reactions=%s",
                        result.channel.id,
                        result.channel.title,
                        result.scanned,
                        result.stored,
                        result.with_reactions,
                    )
                except FloodWaitError as exc:
                    error = f"Telegram flood wait: retry after {exc.seconds} seconds"
                    failures.append(
                        ChannelRefreshFailure(title=title, reference=reference, error=error)
                    )
                    logger.warning(
                        "Daily channel refresh flood wait title=%r reference=%s retry_after_seconds=%s",
                        title,
                        reference,
                        exc.seconds,
                    )
                except (RPCError, ValueError, OSError) as exc:
                    failures.append(
                        ChannelRefreshFailure(
                            title=title,
                            reference=reference,
                            error=str(exc),
                        )
                    )
                    logger.warning(
                        "Daily channel refresh failed title=%r reference=%s error=%s",
                        title,
                        reference,
                        exc,
                        exc_info=True,
                    )
                except Exception as exc:
                    failures.append(
                        ChannelRefreshFailure(
                            title=title,
                            reference=reference,
                            error=str(exc),
                        )
                    )
                    logger.exception(
                        "Unexpected daily channel refresh failure title=%r reference=%s",
                        title,
                        reference,
                    )

        finished_at = utc_now()
        weekly_top = self.db.get_global_top_messages(
            limit=self.settings.daily_report_top_limit,
            since=finished_at - timedelta(days=7),
        )
        newly_indexed_top = self.db.get_global_top_messages(
            limit=self.settings.daily_report_top_limit,
            first_indexed_since=started_at,
        )
        report = DailyReport(
            started_at=started_at,
            finished_at=finished_at,
            refreshed=refreshed,
            failures=failures,
            weekly_top=weekly_top,
            newly_indexed_top=newly_indexed_top,
        )
        logger.info(
            "Daily refresh finished refreshed=%s failures=%s weekly_top=%s newly_indexed_top=%s duration_seconds=%.3f",
            len(refreshed),
            len(failures),
            len(weekly_top),
            len(newly_indexed_top),
            (finished_at - started_at).total_seconds(),
        )
        if send_report:
            await self.send_report(report)
        return report

    async def send_report(self, report: DailyReport) -> None:
        target_ids = (
            self.settings.daily_report_target_user_ids
            or self.settings.allowed_user_ids
        )
        if not target_ids:
            logger.warning("Daily report not sent because no target user IDs are configured")
            return

        text = format_daily_report(report)
        for user_id in target_ids:
            try:
                await _send_long_bot_message(
                    self.bot_client,
                    user_id,
                    text,
                    parse_mode="html",
                )
                logger.info("Daily report sent user_id=%s", user_id)
            except Exception:
                logger.exception("Failed to send daily report user_id=%s", user_id)

    async def _schedule_loop(self) -> None:
        try:
            target_time = _parse_daily_time(self.settings.daily_refresh_time)
            timezone = ZoneInfo(self.settings.daily_refresh_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            logger.error("Daily refresh disabled by invalid schedule config: %s", exc)
            return

        logger.info(
            "Daily refresh scheduler started time=%s timezone=%s",
            self.settings.daily_refresh_time,
            self.settings.daily_refresh_timezone,
        )
        while True:
            delay = _seconds_until_next_run(target_time, timezone)
            logger.info("Next daily refresh scheduled in %.0f seconds", delay)
            await asyncio.sleep(delay)
            try:
                await self.run_once(send_report=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Daily refresh run failed unexpectedly")
                await asyncio.sleep(60)


def format_daily_report(report: DailyReport) -> str:
    lines = [
        "<b>每日频道互动榜</b>",
        (
            f"刷新频道：{len(report.refreshed)} 成功"
            f" / {len(report.failures)} 失败"
        ),
        f"完成时间：{_html(report.finished_at.strftime('%Y-%m-%d %H:%M UTC'))}",
        "",
        "<b>一、最近 7 天跨频道互动排行</b>",
    ]
    lines.extend(_format_ranked_rows(report.weekly_top))
    lines.extend(["", "<b>二、本次新增入库互动排行</b>"])
    lines.extend(_format_ranked_rows(report.newly_indexed_top))
    if report.failures:
        lines.extend(["", "<b>刷新失败</b>"])
        for failure in report.failures[:10]:
            lines.append(
                f"- {_html(failure.title or failure.reference)}：{_html(failure.error)}"
            )
        if len(report.failures) > 10:
            lines.append(f"...另有 {len(report.failures) - 10} 个失败")
    return "\n".join(lines)


def format_global_ranked_messages(
    title: str,
    rows: list[RankedMessage],
) -> str:
    lines = [f"<b>{_html(title)}</b>"]
    lines.extend(_format_ranked_rows(rows))
    return "\n".join(lines)


def _format_ranked_rows(rows: list[RankedMessage]) -> list[str]:
    if not rows:
        return ["暂无数据"]

    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        channel = row.channel_title or row.channel_username or str(row.channel_id)
        preview = _html(row.text_preview or "(no text)")
        url = html.escape(row.url, quote=True)
        lines.append(
            (
                f'\n<a href="{url}">#{index:02d} 表情 {row.total_reactions} · '
                f"{_html(channel)} · {row.date[:10]}\n{preview}</a>"
            )
        )
    return lines


async def _send_long_bot_message(
    bot_client: object,
    user_id: int,
    text: str,
    *,
    parse_mode: str | None,
    chunk_size: int = 3900,
) -> None:
    remaining = text
    while remaining:
        chunk = remaining[:chunk_size]
        split_at = chunk.rfind("\n\n")
        if split_at > 0 and len(remaining) > chunk_size:
            chunk = remaining[:split_at]
        await bot_client.send_message(user_id, chunk, parse_mode=parse_mode)
        remaining = remaining[len(chunk) :].lstrip()


def _parse_daily_time(value: str) -> time:
    try:
        hour_raw, minute_raw = value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
    except ValueError as exc:
        raise ValueError("DAILY_REFRESH_TIME must use HH:MM format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("DAILY_REFRESH_TIME must be a valid HH:MM time")
    return time(hour=hour, minute=minute)


def _seconds_until_next_run(target_time: time, timezone: ZoneInfo) -> float:
    now = datetime.now(timezone)
    next_run = datetime.combine(now.date(), target_time, tzinfo=timezone)
    if next_run <= now:
        next_run += timedelta(days=1)
    return max(1.0, (next_run - now).total_seconds())


def _html(value: object) -> str:
    return html.escape(str(value), quote=False)
