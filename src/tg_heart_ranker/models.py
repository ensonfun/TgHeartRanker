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


@dataclass(frozen=True)
class IndexResult:
    channel: ChannelInfo
    scanned: int
    stored: int
    with_hearts: int
    started_at: datetime
    finished_at: datetime
