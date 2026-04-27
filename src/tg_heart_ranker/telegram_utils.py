from __future__ import annotations

import re
from urllib.parse import urlparse

HEART_BASE = "\u2764"
VARIATION_SELECTOR_16 = "\ufe0f"

PUBLIC_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
UNSUPPORTED_TME_PREFIXES = {"c", "joinchat", "addstickers", "addemoji"}


def parse_channel_reference(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Missing channel link or @username.")

    if value.startswith("@"):
        username = value[1:]
        if _is_public_username(username):
            return username
        raise ValueError("Invalid Telegram username.")

    if _is_public_username(value):
        return value

    if value.startswith("t.me/") or value.startswith("telegram.me/"):
        value = "https://" + value

    parsed = urlparse(value)
    host = (parsed.netloc or "").lower()
    if host not in TELEGRAM_HOSTS:
        raise ValueError("Expected a public t.me channel link or @username.")

    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Telegram link does not include a channel username.")

    username = parts[0]
    if username.startswith("+") or username in UNSUPPORTED_TME_PREFIXES:
        raise ValueError("Invite, private, and t.me/c links are not supported.")
    if not _is_public_username(username):
        raise ValueError("Invalid Telegram channel username in link.")
    return username


def canonical_channel_url(username: str | None) -> str:
    if not username:
        return ""
    return f"https://t.me/{username}"


def message_url(username: str | None, message_id: int) -> str:
    if not username:
        return ""
    return f"https://t.me/{username}/{message_id}"


def extract_reaction_counts(reactions: object | None) -> tuple[int, int]:
    if reactions is None:
        return 0, 0

    heart_count = 0
    total_count = 0
    results = getattr(reactions, "results", None) or []
    for result in results:
        count = int(
            getattr(result, "count", None)
            or getattr(result, "total_count", None)
            or 0
        )
        total_count += count
        if is_heart_reaction(getattr(result, "reaction", None)):
            heart_count += count
    return heart_count, total_count


def is_heart_reaction(reaction: object | None) -> bool:
    emoji = reaction_emoji(reaction)
    return normalize_emoji(emoji) == HEART_BASE


def reaction_emoji(reaction: object | None) -> str | None:
    if reaction is None:
        return None
    if isinstance(reaction, str):
        return reaction

    for attr in ("emoticon", "emoji"):
        value = getattr(reaction, attr, None)
        if isinstance(value, str):
            return value

    nested_type = getattr(reaction, "type", None)
    if nested_type is not None:
        return reaction_emoji(nested_type)

    return None


def normalize_emoji(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace(VARIATION_SELECTOR_16, "")


def text_preview(text: str | None, max_length: int = 120) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."


def _is_public_username(value: str) -> bool:
    return bool(PUBLIC_USERNAME_RE.fullmatch(value))
