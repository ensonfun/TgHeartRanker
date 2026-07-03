from __future__ import annotations

import asyncio
import html
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from telethon import Button, TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from .config import Settings, load_settings
from .daily import DailyReportRunner, format_daily_report, format_global_ranked_messages
from .db import Database, utc_now
from .indexer import ChannelIndexer
from .models import IndexResult, RankedMessage
from .telegram_utils import format_video_duration, parse_channel_reference


INDEX_CACHE_TTL = timedelta(hours=24)
logger = logging.getLogger(__name__)

HELP_TEXT = """Telegram Heart Ranker

Commands:
/whoami
/rank <channel_url> [limit]
/index <channel_url> [message_limit]
/top <channel_url> [limit]
/status <channel_url>
/daily
/global [limit] [week|month|year|all]
/new [limit] [days]
/delete <channel_url>

Examples:
/rank https://t.me/some_public_channel 10
/index https://t.me/some_public_channel 500
/top @some_public_channel 10
/global 10 week
/new 10 1
/delete @some_public_channel
"""


@dataclass(frozen=True)
class UrlLimitArgs:
    reference: str
    limit: int
    period: str = "all"


class HeartRankerBot:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        user_client: TelegramClient,
        bot_client: TelegramClient,
    ) -> None:
        self.settings = settings
        self.db = db
        self.user_client = user_client
        self.bot_client = bot_client
        self.indexer = ChannelIndexer(
            user_client,
            db,
            progress_every=settings.progress_every,
            request_sleep_seconds=settings.request_sleep_seconds,
        )
        self.index_lock = asyncio.Lock()
        self.daily_runner = DailyReportRunner(
            settings,
            db,
            self.indexer,
            bot_client,
            self.index_lock,
        )

    def register_handlers(self) -> None:
        self.bot_client.add_event_handler(
            self.handle_message, events.NewMessage(incoming=True)
        )
        self.bot_client.add_event_handler(
            self.handle_page_callback, events.CallbackQuery(pattern=b"^rank:")
        )
        self.bot_client.add_event_handler(
            self.handle_delete_callback, events.CallbackQuery(pattern=b"^delete:")
        )

    async def handle_message(self, event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return
        if text.startswith("/"):
            command, args = _parse_command(text)
        else:
            command, args = "/rank", text
        logger.info(
            "Message received sender_id=%s command=%s args=%r",
            event.sender_id,
            command,
            _compact_log_text(args),
        )

        if command == "/whoami":
            logger.info("Responding to /whoami sender_id=%s", event.sender_id)
            await event.respond(f"Your Telegram user ID: {event.sender_id}")
            return

        if not self._is_allowed(event.sender_id):
            logger.warning(
                "Rejected message from unauthorized sender_id=%s command=%s",
                event.sender_id,
                command,
            )
            await event.respond("This bot is private.")
            return

        try:
            if command in {"/start", "/help"}:
                await event.respond(HELP_TEXT, parse_mode=None)
            elif command == "/index":
                await self._handle_index(event, args)
            elif command in {"/rank", "/top"}:
                await self._handle_rank(event, args)
            elif command == "/status":
                await self._handle_status(event, args)
            elif command == "/daily":
                await self._handle_daily(event)
            elif command == "/global":
                await self._handle_global(event, args)
            elif command == "/new":
                await self._handle_new(event, args)
            elif command == "/delete":
                await self._handle_delete(event, args)
            else:
                await event.respond(HELP_TEXT, parse_mode=None)
        except ValueError as exc:
            logger.info(
                "User-facing validation error sender_id=%s command=%s error=%s",
                event.sender_id,
                command,
                exc,
            )
            await event.respond(str(exc), parse_mode=None)
        except FloodWaitError as exc:
            logger.warning(
                "Telegram flood wait sender_id=%s command=%s retry_after_seconds=%s",
                event.sender_id,
                command,
                exc.seconds,
            )
            await event.respond(
                f"Telegram asked us to slow down. Retry after {exc.seconds} seconds.",
                parse_mode=None,
            )
        except RPCError as exc:
            logger.warning(
                "Telegram RPC error sender_id=%s command=%s error=%s",
                event.sender_id,
                command,
                exc,
                exc_info=True,
            )
            await event.respond(f"Telegram API error: {exc}", parse_mode=None)
        except Exception as exc:
            logger.exception(
                "Unexpected command failure sender_id=%s command=%s",
                event.sender_id,
                command,
            )
            await event.respond(f"Unexpected error: {exc}", parse_mode=None)

    async def handle_page_callback(self, event: events.CallbackQuery.Event) -> None:
        if not self._is_allowed(event.sender_id):
            logger.warning(
                "Rejected callback from unauthorized sender_id=%s data=%r",
                event.sender_id,
                event.data,
            )
            await event.answer("This bot is private.", alert=True)
            return

        try:
            channel_id, page, limit, period = _parse_rank_callback_data(event.data)
            logger.info(
                "Rank callback sender_id=%s channel_id=%s page=%s limit=%s period=%s",
                event.sender_id,
                channel_id,
                page,
                limit,
                period,
            )
            channel = self.db.get_channel(channel_id)
            if not channel:
                logger.info(
                    "Rank callback referenced missing channel sender_id=%s channel_id=%s",
                    event.sender_id,
                    channel_id,
                )
                await event.answer("Channel is not in the local database.", alert=True)
                return

            since = _period_since(period)
            total = self.db.count_top_messages(channel_id, since=since)
            page = _clamp_page(page, limit, total)
            rows = self.db.get_top_messages(
                channel_id, limit, page * limit, since=since
            )
            await event.edit(
                _format_top_messages(
                    str(channel["title"]),
                    rows,
                    page=page,
                    limit=limit,
                    total=total,
                    period=period,
                ),
                buttons=_rank_buttons(channel_id, page, limit, total, period),
                parse_mode="html",
            )
            await event.answer()
        except ValueError as exc:
            logger.info(
                "Invalid rank callback sender_id=%s data=%r error=%s",
                event.sender_id,
                event.data,
                exc,
            )
            await event.answer(str(exc), alert=True)
        except Exception as exc:
            logger.exception(
                "Unexpected rank callback failure sender_id=%s data=%r",
                event.sender_id,
                event.data,
            )
            await event.answer(f"Unexpected error: {exc}", alert=True)

    async def handle_delete_callback(
        self, event: events.CallbackQuery.Event
    ) -> None:
        if not self._is_allowed(event.sender_id):
            await event.answer("This bot is private.", alert=True)
            return

        try:
            action, channel_id = _parse_delete_callback_data(event.data)
            if action == "cancel":
                await event.edit(
                    "Channel deletion cancelled.",
                    buttons=None,
                    parse_mode=None,
                )
                await event.answer()
                return

            if self.index_lock.locked():
                await event.answer(
                    "An index job is running. Try deleting again later.",
                    alert=True,
                )
                return

            deleted = self.db.delete_channel(channel_id)
            if not deleted:
                await event.edit(
                    "Channel was already deleted or is not in the local database.",
                    buttons=None,
                    parse_mode=None,
                )
                await event.answer()
                return

            logger.info(
                "Channel deleted sender_id=%s channel_id=%s username=%s title=%r indexed_messages=%s",
                event.sender_id,
                channel_id,
                deleted.get("username"),
                deleted.get("title"),
                deleted.get("indexed_messages"),
            )
            await event.edit(
                (
                    f"Deleted channel: {deleted.get('title')}\n"
                    f"Username: @{deleted.get('username')}\n"
                    f"Deleted messages: {deleted.get('indexed_messages')}"
                ),
                buttons=None,
                parse_mode=None,
            )
            await event.answer("Channel deleted.")
        except ValueError as exc:
            await event.answer(str(exc), alert=True)
        except Exception as exc:
            logger.exception(
                "Unexpected channel deletion failure sender_id=%s data=%r",
                event.sender_id,
                event.data,
            )
            await event.answer(f"Unexpected error: {exc}", alert=True)

    async def _handle_index(
        self, event: events.NewMessage.Event, args: str
    ) -> None:
        parsed = _parse_url_limit_args(
            args,
            default_limit=self.settings.default_index_limit,
            default_when_missing=None,
            max_limit=None,
        )
        logger.info(
            "Index command parsed sender_id=%s reference=%r limit=%s",
            event.sender_id,
            _compact_log_text(parsed.reference),
            parsed.limit,
        )

        if self.index_lock.locked():
            logger.info(
                "Index command rejected because another index is running sender_id=%s",
                event.sender_id,
            )
            await event.respond("An index job is already running. Try again later.")
            return

        progress_message = await event.respond(
            "Indexing started. This can take a while for large channels.",
            parse_mode=None,
        )

        async def progress(scanned: int, stored: int, with_reactions: int) -> None:
            await _safe_edit(
                progress_message,
                (
                    "Indexing...\n"
                    f"Scanned: {scanned}\n"
                    f"Stored: {stored}\n"
                    f"Posts with reactions: {with_reactions}"
                ),
            )

        async with self.index_lock:
            result = await self.indexer.index_channel(
                parsed.reference,
                limit=parsed.limit,
                progress_callback=progress,
            )

        logger.info(
            "Index command completed sender_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s",
            event.sender_id,
            result.channel.id,
            result.scanned,
            result.stored,
            result.with_reactions,
        )
        await _safe_edit(progress_message, _format_index_result(result))
        await _send_ranked_page(event, self.db, result.channel.id, result.channel.title, 10)

    async def _handle_rank(self, event: events.NewMessage.Event, args: str) -> None:
        parsed = _parse_url_limit_args(
            args,
            default_limit=10,
            default_when_missing=10,
            max_limit=50,
        )
        logger.info(
            "Rank command parsed sender_id=%s reference=%r limit=%s period=%s",
            event.sender_id,
            _compact_log_text(parsed.reference),
            parsed.limit,
            parsed.period,
        )
        _entity, channel = await self.indexer.resolve_channel(parsed.reference)
        self.db.upsert_channel(channel)
        status = self.db.get_status(channel.id)
        indexed_messages = 0
        if status:
            indexed_messages = int(status.get("indexed_messages") or 0)

        last_indexed_at = str(status.get("last_indexed_at") or "") if status else ""
        should_refresh = indexed_messages == 0 or _index_is_stale(last_indexed_at)
        logger.info(
            "Rank channel status sender_id=%s channel_id=%s indexed_messages=%s last_indexed_at=%r should_refresh=%s",
            event.sender_id,
            channel.id,
            indexed_messages,
            last_indexed_at,
            should_refresh,
        )

        if self.index_lock.locked():
            logger.info(
                "Rank command rejected because another index is running sender_id=%s channel_id=%s",
                event.sender_id,
                channel.id,
            )
            await event.respond("An index job is already running. Try again later.")
            return

        progress_message = None
        if should_refresh:
            index_limit = (
                self.settings.default_index_limit
                if indexed_messages == 0
                else self.settings.rank_refresh_limit
            )
            progress_message = await event.respond(
                _format_rank_refresh_started(
                    channel.title, indexed_messages, index_limit
                ),
                parse_mode=None,
            )

            async def progress(scanned: int, stored: int, with_reactions: int) -> None:
                await _safe_edit(
                    progress_message,
                    (
                        f"Refreshing {channel.title} before ranking...\n"
                        f"Scanned: {scanned}\n"
                        f"Stored: {stored}\n"
                        f"Posts with reactions: {with_reactions}"
                    ),
                )

            async with self.index_lock:
                result = await self.indexer.index_channel(
                    parsed.reference,
                    limit=index_limit,
                    progress_callback=progress,
                )
            logger.info(
                "Rank refresh completed sender_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s",
                event.sender_id,
                result.channel.id,
                result.scanned,
                result.stored,
                result.with_reactions,
            )
            await _safe_edit(
                progress_message,
                (
                    f"Refreshed {result.channel.title}\n"
                    f"Scanned: {result.scanned}\n"
                    f"Stored: {result.stored}\n"
                    f"Posts with reactions: {result.with_reactions}"
                ),
            )
        else:
            result = None

        since = _period_since(parsed.period)
        rows = self.db.get_top_messages(channel.id, parsed.limit, since=since)
        if not rows:
            logger.info(
                "Rank command found no rows sender_id=%s channel_id=%s limit=%s period=%s",
                event.sender_id,
                channel.id,
                parsed.limit,
                parsed.period,
            )
            message = (
                f"No reactions were found for {channel.title} "
                f"({_period_label(parsed.period)})."
            )
            if progress_message:
                await _safe_edit(progress_message, message)
            else:
                await event.respond(message, parse_mode=None)
            return

        await _send_ranked_page(
            event,
            self.db,
            channel.id,
            channel.title,
            parsed.limit,
            period=parsed.period,
        )
        logger.info(
            "Rank command responded sender_id=%s channel_id=%s rows=%s limit=%s period=%s",
            event.sender_id,
            channel.id,
            len(rows),
            parsed.limit,
            parsed.period,
        )

    async def _handle_status(
        self, event: events.NewMessage.Event, args: str
    ) -> None:
        if not args.strip():
            raise ValueError("Usage: /status <channel_url>")
        logger.info(
            "Status command parsed sender_id=%s reference=%r",
            event.sender_id,
            _compact_log_text(args.strip()),
        )
        _entity, channel = await self.indexer.resolve_channel(args.strip())
        self.db.upsert_channel(channel)
        status = self.db.get_status(channel.id)
        if not status:
            logger.info(
                "Status command found no local status sender_id=%s channel_id=%s",
                event.sender_id,
                channel.id,
            )
            await event.respond("No local status for this channel yet.")
            return
        await event.respond(_format_status(status), parse_mode=None)
        logger.info(
            "Status command responded sender_id=%s channel_id=%s",
            event.sender_id,
            channel.id,
        )

    async def _handle_daily(self, event: events.NewMessage.Event) -> None:
        if self.index_lock.locked():
            await event.respond("An index job is already running. Try again later.")
            return
        progress_message = await event.respond(
            "Daily refresh started. I will refresh indexed channels and send the report here.",
            parse_mode=None,
        )
        report = await self.daily_runner.run_once(send_report=False)
        await _safe_edit(
            progress_message,
            (
                "Daily refresh finished.\n"
                f"Refreshed: {len(report.refreshed)}\n"
                f"Failed: {len(report.failures)}\n"
                f"Weekly ranking rows: {len(report.weekly_top)}\n"
                f"New ranking rows: {len(report.newly_indexed_top)}"
            ),
        )
        await _send_long_message(event, format_daily_report(report), parse_mode="html")

    async def _handle_global(
        self, event: events.NewMessage.Event, args: str
    ) -> None:
        limit, period = _parse_global_args(args)
        since = _period_since(period)
        rows = self.db.get_global_top_messages(limit=limit, since=since)
        await _send_long_message(
            event,
            format_global_ranked_messages(
                f"跨频道互动排行 · {_period_label(period)}",
                rows,
            ),
            parse_mode="html",
        )

    async def _handle_new(
        self, event: events.NewMessage.Event, args: str
    ) -> None:
        limit, days = _parse_new_args(args)
        rows = self.db.get_global_top_messages(
            limit=limit,
            first_indexed_since=utc_now() - timedelta(days=days),
        )
        await _send_long_message(
            event,
            format_global_ranked_messages(
                f"新增入库互动排行 · 近 {days} 天",
                rows,
            ),
            parse_mode="html",
        )

    async def _handle_delete(
        self, event: events.NewMessage.Event, args: str
    ) -> None:
        if not args.strip():
            raise ValueError("Usage: /delete <channel_url or @username>")
        username = parse_channel_reference(args.strip())
        channel = self.db.get_channel_by_username(username)
        if not channel:
            await event.respond(
                "Channel is not in the local database.",
                parse_mode=None,
            )
            return
        await event.respond(
            (
                "Confirm channel deletion:\n"
                f"Title: {channel.get('title')}\n"
                f"Username: @{channel.get('username')}\n"
                f"Indexed messages: {channel.get('indexed_messages')}\n\n"
                "This permanently removes the channel and all of its local data."
            ),
            buttons=_delete_buttons(int(channel["id"])),
            parse_mode=None,
        )

    def _is_allowed(self, sender_id: int | None) -> bool:
        if not self.settings.allowed_user_ids:
            return True
        if sender_id is None:
            return False
        return sender_id in self.settings.allowed_user_ids


async def run() -> None:
    settings = load_settings()
    _configure_logging(settings)
    logger.info(
        "Starting Telegram Heart Ranker db_path=%s user_session=%s bot_session=%s log_file=%s log_level=%s",
        settings.db_path,
        settings.user_session,
        settings.bot_session,
        settings.log_file or "disabled",
        settings.log_level,
    )
    db = Database(settings.db_path)
    db.initialize()
    logger.info("Database initialized path=%s", settings.db_path)

    if not settings.allowed_user_ids:
        logger.warning(
            "Warning: TELEGRAM_ALLOWED_USER_IDS is empty. "
            "Anyone who can message this bot can use it."
        )
    else:
        logger.info(
            "Private mode enabled allowed_user_count=%s",
            len(settings.allowed_user_ids),
        )

    user_client = TelegramClient(
        str(settings.user_session), settings.api_id, settings.api_hash
    )
    bot_client = TelegramClient(
        str(settings.bot_session), settings.api_id, settings.api_hash
    )

    logger.info("Starting Telegram user client")
    await user_client.start()
    logger.info("Starting Telegram bot client")
    await bot_client.start(bot_token=settings.bot_token)

    app = HeartRankerBot(settings, db, user_client, bot_client)
    app.register_handlers()
    if settings.daily_refresh_enabled:
        app.daily_runner.start()

    logger.info("Telegram Heart Ranker is running")
    try:
        await bot_client.run_until_disconnected()
    finally:
        await app.daily_runner.stop()
        logger.info("Disconnecting Telegram clients")
        await bot_client.disconnect()
        await user_client.disconnect()
        logger.info("Telegram Heart Ranker stopped")


def main() -> None:
    asyncio.run(run())


def _parse_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return command, args


def _parse_url_limit_args(
    args: str,
    *,
    default_limit: int,
    default_when_missing: int | None,
    max_limit: int | None,
) -> UrlLimitArgs:
    if not args.strip():
        raise ValueError("Missing channel link or @username.")

    tokens = args.split()
    limit = default_limit
    reference = args.strip()
    period = "all"
    if tokens:
        maybe_period = _normalize_period(tokens[-1])
        if maybe_period:
            period = maybe_period
            tokens = tokens[:-1]
            reference = " ".join(tokens).strip()

    if len(tokens) > 1 and tokens[-1].isdigit():
        limit = int(tokens[-1])
        reference = " ".join(tokens[:-1]).strip()
    elif default_when_missing is not None:
        limit = default_when_missing

    if not reference:
        raise ValueError("Missing channel link or @username.")
    if limit < 0:
        raise ValueError("Limit must be >= 0.")
    if max_limit is not None:
        limit = max(1, min(limit, max_limit))
    return UrlLimitArgs(reference=reference, limit=limit, period=period)


def _parse_rank_callback_data(data: bytes) -> tuple[int, int, int, str]:
    try:
        prefix, channel_id, page, limit, period = data.decode("ascii").split(":")
    except ValueError as exc:
        raise ValueError("Invalid pagination button.") from exc
    if prefix != "rank":
        raise ValueError("Invalid pagination button.")
    normalized_period = _normalize_period(period)
    if not normalized_period:
        raise ValueError("Invalid time filter.")
    return int(channel_id), int(page), int(limit), normalized_period


def _parse_delete_callback_data(data: bytes) -> tuple[str, int]:
    try:
        prefix, action, channel_id = data.decode("ascii").split(":")
    except ValueError as exc:
        raise ValueError("Invalid channel deletion button.") from exc
    if prefix != "delete" or action not in {"confirm", "cancel"}:
        raise ValueError("Invalid channel deletion button.")
    return action, int(channel_id)


def _clamp_page(page: int, limit: int, total: int) -> int:
    if limit <= 0:
        return 0
    last_page = max(0, (total - 1) // limit)
    return max(0, min(page, last_page))


def _index_is_stale(last_indexed_at: str, ttl: timedelta = INDEX_CACHE_TTL) -> bool:
    if not last_indexed_at:
        return True
    try:
        indexed_at = datetime.fromisoformat(last_indexed_at)
    except ValueError:
        return True
    if indexed_at.tzinfo is None:
        indexed_at = indexed_at.replace(tzinfo=utc_now().tzinfo)
    return utc_now() - indexed_at >= ttl


async def _safe_edit(message: object, text: str) -> None:
    try:
        await message.edit(text, parse_mode=None)
    except Exception as exc:
        logger.warning("Failed to edit Telegram message: %s", exc, exc_info=True)


async def _send_long_message(
    event: events.NewMessage.Event,
    text: str,
    *,
    parse_mode: str | None = None,
    chunk_size: int = 3900,
) -> None:
    remaining = text
    while remaining:
        chunk = remaining[:chunk_size]
        split_at = chunk.rfind("\n\n")
        if split_at > 0 and len(remaining) > chunk_size:
            chunk = remaining[:split_at]
        await event.respond(chunk, parse_mode=parse_mode)
        remaining = remaining[len(chunk) :].lstrip()


async def _send_ranked_page(
    event: events.NewMessage.Event,
    db: Database,
    channel_id: int,
    title: str,
    limit: int,
    page: int = 0,
    period: str = "all",
) -> None:
    since = _period_since(period)
    total = db.count_top_messages(channel_id, since=since)
    page = _clamp_page(page, limit, total)
    rows = db.get_top_messages(channel_id, limit, page * limit, since=since)
    if not rows:
        return
    await event.respond(
        _format_top_messages(
            title, rows, page=page, limit=limit, total=total, period=period
        ),
        buttons=_rank_buttons(channel_id, page, limit, total, period),
        parse_mode="html",
    )
    logger.info(
        "Sent ranked page channel_id=%s page=%s limit=%s total=%s period=%s rows=%s",
        channel_id,
        page,
        limit,
        total,
        period,
        len(rows),
    )


def _configure_logging(settings: Settings) -> None:
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
        level_name = "INFO"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file:
        handlers.append(
            RotatingFileHandler(
                settings.log_file,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    logging.getLogger("telethon").setLevel(max(level, logging.WARNING))
    logger.info("Logging configured level=%s file=%s", level_name, settings.log_file)


def _compact_log_text(value: str, max_length: int = 200) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."


def _format_index_result(result: IndexResult) -> str:
    return (
        f"Indexed {result.channel.title}\n"
        f"Scanned: {result.scanned}\n"
        f"Stored: {result.stored}\n"
        f"Posts with reactions: {result.with_reactions}\n"
        f"Finished: {result.finished_at.isoformat()}"
    )


def _format_rank_refresh_started(
    title: str, indexed_messages: int, index_limit: int
) -> str:
    if indexed_messages == 0:
        limit_text = "all available history" if index_limit == 0 else f"{index_limit} messages"
        return f"No local index for {title}. Indexing {limit_text} before ranking."

    limit_text = "all available history" if index_limit == 0 else f"latest {index_limit} messages"
    return f"Refreshing {limit_text} from {title} before ranking."


def _format_top_messages(
    title: str,
    rows: list[RankedMessage],
    *,
    page: int = 0,
    limit: int = 10,
    total: int | None = None,
    period: str = "all",
) -> str:
    total_count = len(rows) if total is None else total
    last_page = _clamp_page(page, limit, total_count)
    page_count = max(1, ((total_count - 1) // limit) + 1)
    lines = [
        (
            f"<b>{_html(title)}</b>\n"
            f"{_html(_period_label(period))} · "
            f"第 {last_page + 1}/{page_count} 页 · "
            f"{total_count} 条"
        )
    ]
    for index, row in enumerate(rows, start=1):
        rank = page * limit + index
        date = row.date[:10]
        preview = _html(row.text_preview or "(no text)")
        url = html.escape(row.url, quote=True)
        duration = format_video_duration(row.video_duration_seconds)
        duration_text = f"  ·  时长 {duration}" if duration else ""
        item = (
            f"#{rank:02d}  表情 {row.total_reactions}"
            f"{duration_text}  ·  {date}\n{preview}"
        )
        lines.append(
            f'\n<a href="{url}">{item}</a>'
        )
    return "\n".join(lines)


def _rank_buttons(
    channel_id: int, page: int, limit: int, total: int, period: str
) -> list[list[Button]]:
    page = _clamp_page(page, limit, total)
    last_page = max(0, (total - 1) // limit) if limit > 0 else 0
    previous_page = max(0, page - 1)
    next_page = min(last_page, page + 1)
    return [
        [
            Button.inline(
                _period_button_label("all", period),
                _rank_callback_data(channel_id, 0, limit, "all"),
            ),
            Button.inline(
                _period_button_label("month", period),
                _rank_callback_data(channel_id, 0, limit, "month"),
            ),
            Button.inline(
                _period_button_label("year", period),
                _rank_callback_data(channel_id, 0, limit, "year"),
            ),
        ],
        [
            Button.inline("首页", _rank_callback_data(channel_id, 0, limit, period)),
            Button.inline(
                "上一页", _rank_callback_data(channel_id, previous_page, limit, period)
            ),
            Button.inline(
                "下一页", _rank_callback_data(channel_id, next_page, limit, period)
            ),
            Button.inline(
                "末页", _rank_callback_data(channel_id, last_page, limit, period)
            ),
        ],
    ]


def _delete_buttons(channel_id: int) -> list[list[Button]]:
    return [
        [
            Button.inline(
                "确认删除",
                f"delete:confirm:{channel_id}".encode("ascii"),
            ),
            Button.inline(
                "取消",
                f"delete:cancel:{channel_id}".encode("ascii"),
            ),
        ]
    ]


def _rank_callback_data(channel_id: int, page: int, limit: int, period: str) -> bytes:
    return f"rank:{channel_id}:{page}:{limit}:{period}".encode("ascii")


def _normalize_period(value: str) -> str | None:
    normalized = value.strip().lower()
    aliases = {
        "all": "all",
        "全部": "all",
        "week": "week",
        "1w": "week",
        "7d": "week",
        "周": "week",
        "近一周": "week",
        "month": "month",
        "1m": "month",
        "30d": "month",
        "月": "month",
        "近一个月": "month",
        "year": "year",
        "1y": "year",
        "365d": "year",
        "年": "year",
        "近一年": "year",
    }
    return aliases.get(normalized)


def _period_since(period: str):
    if period == "week":
        return utc_now() - timedelta(days=7)
    if period == "month":
        return utc_now() - timedelta(days=30)
    if period == "year":
        return utc_now() - timedelta(days=365)
    return None


def _period_label(period: str) -> str:
    if period == "week":
        return "近 1 周"
    if period == "month":
        return "近 1 个月"
    if period == "year":
        return "近 1 年"
    return "全部时间"


def _period_button_label(button_period: str, active_period: str) -> str:
    labels = {
        "all": "全部",
        "month": "近月",
        "year": "近年",
    }
    label = labels[button_period]
    if button_period == active_period:
        return f"{label} ✓"
    return label


def _parse_global_args(args: str) -> tuple[int, str]:
    tokens = args.split()
    limit = 10
    period = "week"
    if tokens and tokens[0].isdigit():
        limit = int(tokens.pop(0))
    if tokens:
        normalized_period = _normalize_period(tokens[0])
        if not normalized_period:
            raise ValueError("Usage: /global [limit] [week|month|year|all]")
        period = normalized_period
    limit = max(1, min(limit, 50))
    return limit, period


def _parse_new_args(args: str) -> tuple[int, int]:
    tokens = args.split()
    limit = 10
    days = 1
    if tokens and tokens[0].isdigit():
        limit = int(tokens.pop(0))
    if tokens and tokens[0].isdigit():
        days = int(tokens.pop(0))
    if tokens:
        raise ValueError("Usage: /new [limit] [days]")
    limit = max(1, min(limit, 50))
    days = max(1, min(days, 30))
    return limit, days


def _html(value: object) -> str:
    return html.escape(str(value), quote=False)


def _format_status(status: dict[str, object]) -> str:
    latest_index = status.get("latest_index") or {}
    if not isinstance(latest_index, dict):
        latest_index = {}

    return (
        f"Status for {status.get('title')}\n"
        f"URL: {status.get('url')}\n"
        f"Indexed messages: {status.get('indexed_messages')}\n"
        f"Messages with reactions: {status.get('messages_with_reactions')}\n"
        f"Disabled at: {status.get('disabled_at') or 'no'}\n"
        f"Disabled reason: {status.get('disabled_reason') or 'none'}\n"
        f"Last successful index: {status.get('last_indexed_at') or 'never'}\n"
        f"Latest job: {latest_index.get('status', 'none')}\n"
        f"Latest job error: {latest_index.get('error') or 'none'}"
    )
