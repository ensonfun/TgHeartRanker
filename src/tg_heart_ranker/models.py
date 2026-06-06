from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChannelInfo:
    id: int
    username: str | None
    title: str
    url: str


@dataclass(frozen=True)
class MessageRecord:
    channel_id: int
    message_id: int
    date: datetime
    text_preview: str
    url: str
    heart_count: int
    total_reactions: int
    indexed_at: datetime
    first_indexed_at: datetime | None = None


@dataclass(frozen=True)
class RankedMessage:
    channel_id: int
    message_id: int
    date: str
    text_preview: str
    url: str
    heart_count: int
    total_reactions: int
    indexed_at: str
    first_indexed_at: str = ""
    channel_title: str = ""
    channel_username: str | None = None
    is_new_entry: bool = False


@dataclass(frozen=True)
class IndexResult:
    channel: ChannelInfo
    scanned: int
    stored: int
    with_reactions: int
    started_at: datetime
    finished_at: datetime
