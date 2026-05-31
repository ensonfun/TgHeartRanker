from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telethon.errors import FloodWaitError

from .db import Database, utc_now
from .models import ChannelInfo, IndexResult, MessageRecord
from .telegram_utils import (
    canonical_channel_url,
    extract_reaction_counts,
    message_url,
    parse_channel_reference,
    text_preview,
)

ProgressCallback = Callable[[int, int, int], Awaitable[None]]
logger = logging.getLogger(__name__)


class ChannelIndexer:
    def __init__(
        self,
        user_client: object,
        db: Database,
        *,
        progress_every: int,
        request_sleep_seconds: float,
    ) -> None:
        self.user_client = user_client
        self.db = db
        self.progress_every = progress_every
        self.request_sleep_seconds = request_sleep_seconds

    async def resolve_channel(self, raw_reference: str) -> tuple[object, ChannelInfo]:
        username = parse_channel_reference(raw_reference)
        logger.info("Resolving Telegram channel reference username=%s", username)
        entity = await self.user_client.get_entity(username)
        entity_username = getattr(entity, "username", None) or username
        if not entity_username:
            raise ValueError("Only public channels with usernames are supported.")

        channel = ChannelInfo(
            id=int(getattr(entity, "id")),
            username=entity_username,
            title=str(getattr(entity, "title", None) or entity_username),
            url=canonical_channel_url(entity_username),
        )
        logger.info(
            "Resolved Telegram channel channel_id=%s username=%s title=%r",
            channel.id,
            channel.username,
            channel.title,
        )
        return entity, channel

    async def index_channel(
        self,
        raw_reference: str,
        *,
        limit: int,
        progress_callback: ProgressCallback | None = None,
    ) -> IndexResult:
        entity, channel = await self.resolve_channel(raw_reference)
        started_at = utc_now()
        self.db.upsert_channel(channel)
        index_id = self.db.start_index(channel.id, started_at)
        logger.info(
            "Index job started index_id=%s channel_id=%s username=%s title=%r limit=%s",
            index_id,
            channel.id,
            channel.username,
            channel.title,
            limit,
        )

        scanned = 0
        stored = 0
        with_reactions = 0
        try:
            iter_limit = limit if limit > 0 else None
            async for message in self.user_client.iter_messages(
                entity, limit=iter_limit
            ):
                if not getattr(message, "id", None):
                    continue

                scanned += 1
                now = utc_now()
                heart_count, total_reactions = extract_reaction_counts(
                    getattr(message, "reactions", None)
                )
                if total_reactions > 0:
                    with_reactions += 1

                record = MessageRecord(
                    channel_id=channel.id,
                    message_id=int(message.id),
                    date=getattr(message, "date", None) or now,
                    text_preview=text_preview(
                        getattr(message, "message", None)
                        or getattr(message, "text", None)
                    ),
                    url=message_url(channel.username, int(message.id)),
                    heart_count=heart_count,
                    total_reactions=total_reactions,
                    indexed_at=now,
                )
                self.db.upsert_message(record)
                stored += 1

                if progress_callback and scanned % self.progress_every == 0:
                    await progress_callback(scanned, stored, with_reactions)
                    logger.info(
                        "Index job progress index_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s",
                        index_id,
                        channel.id,
                        scanned,
                        stored,
                        with_reactions,
                    )
                if self.request_sleep_seconds and scanned % 200 == 0:
                    await asyncio.sleep(self.request_sleep_seconds)

        except FloodWaitError as exc:
            finished_at = utc_now()
            error = f"Telegram flood wait: retry after {exc.seconds} seconds."
            self.db.finish_index(index_id, "failed", finished_at, error)
            logger.warning(
                "Index job failed on Telegram flood wait index_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s retry_after_seconds=%s",
                index_id,
                channel.id,
                scanned,
                stored,
                with_reactions,
                exc.seconds,
            )
            raise
        except Exception as exc:
            finished_at = utc_now()
            self.db.finish_index(index_id, "failed", finished_at, str(exc))
            logger.exception(
                "Index job failed index_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s",
                index_id,
                channel.id,
                scanned,
                stored,
                with_reactions,
            )
            raise

        finished_at = utc_now()
        self.db.finish_index(index_id, "success", finished_at)
        self.db.mark_channel_indexed(channel.id, finished_at)
        logger.info(
            "Index job finished index_id=%s channel_id=%s scanned=%s stored=%s with_reactions=%s duration_seconds=%.3f",
            index_id,
            channel.id,
            scanned,
            stored,
            with_reactions,
            (finished_at - started_at).total_seconds(),
        )
        return IndexResult(
            channel=channel,
            scanned=scanned,
            stored=stored,
            with_reactions=with_reactions,
            started_at=started_at,
            finished_at=finished_at,
        )
