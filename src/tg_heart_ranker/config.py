from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    db_path: Path
    user_session: Path
    bot_session: Path
    allowed_user_ids: frozenset[int]
    default_index_limit: int
    rank_refresh_limit: int
    progress_every: int
    request_sleep_seconds: float
    log_level: str
    log_file: Path | None
    daily_refresh_enabled: bool
    daily_refresh_time: str
    daily_refresh_timezone: str
    daily_refresh_limit: int
    daily_report_target_user_ids: frozenset[int]
    daily_report_top_limit: int
    daily_refresh_delay_min_seconds: float
    daily_refresh_delay_max_seconds: float
    daily_refresh_max_attempts: int
    daily_refresh_retry_base_seconds: float
    daily_refresh_retry_max_seconds: float

    def prepare_filesystem(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_session.parent.mkdir(parents=True, exist_ok=True)
        self.bot_session.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = _require_env("TELEGRAM_API_ID")
    api_hash = _require_env("TELEGRAM_API_HASH")
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer.") from exc

    delay_min = _float_env("DAILY_REFRESH_DELAY_MIN_SECONDS", 60.0, minimum=0.0)
    delay_max = _float_env("DAILY_REFRESH_DELAY_MAX_SECONDS", 180.0, minimum=0.0)
    if delay_max < delay_min:
        raise RuntimeError(
            "DAILY_REFRESH_DELAY_MAX_SECONDS must be >= DAILY_REFRESH_DELAY_MIN_SECONDS."
        )

    retry_base = _float_env("DAILY_REFRESH_RETRY_BASE_SECONDS", 30.0, minimum=0.0)
    retry_max = _float_env("DAILY_REFRESH_RETRY_MAX_SECONDS", 300.0, minimum=0.0)
    if retry_max < retry_base:
        raise RuntimeError(
            "DAILY_REFRESH_RETRY_MAX_SECONDS must be >= DAILY_REFRESH_RETRY_BASE_SECONDS."
        )

    settings = Settings(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        db_path=_path_env("HEART_RANKER_DB", "data/heart_ranker.sqlite3"),
        user_session=_path_env("TELEGRAM_USER_SESSION", "sessions/user"),
        bot_session=_path_env("TELEGRAM_BOT_SESSION", "sessions/bot"),
        allowed_user_ids=_parse_allowed_user_ids(
            os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
        ),
        default_index_limit=_int_env("INDEX_MESSAGE_LIMIT", 0, minimum=0),
        rank_refresh_limit=_int_env("RANK_REFRESH_MESSAGE_LIMIT", 500, minimum=0),
        progress_every=_int_env("INDEX_PROGRESS_EVERY", 500, minimum=1),
        request_sleep_seconds=_float_env(
            "INDEX_REQUEST_SLEEP_SECONDS", 0.2, minimum=0.0
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip() or "INFO",
        log_file=_optional_path_env("LOG_FILE", "logs/tg_heart_ranker.log"),
        daily_refresh_enabled=_bool_env("DAILY_REFRESH_ENABLED", True),
        daily_refresh_time=os.environ.get("DAILY_REFRESH_TIME", "09:00").strip()
        or "09:00",
        daily_refresh_timezone=os.environ.get(
            "DAILY_REFRESH_TIMEZONE", "Australia/Sydney"
        ).strip()
        or "Australia/Sydney",
        daily_refresh_limit=_int_env("DAILY_REFRESH_MESSAGE_LIMIT", 500, minimum=0),
        daily_report_target_user_ids=_parse_allowed_user_ids(
            os.environ.get("DAILY_REPORT_TARGET_USER_IDS", "")
        ),
        daily_report_top_limit=_int_env("DAILY_REPORT_TOP_LIMIT", 10, minimum=1),
        daily_refresh_delay_min_seconds=delay_min,
        daily_refresh_delay_max_seconds=delay_max,
        daily_refresh_max_attempts=_int_env("DAILY_REFRESH_MAX_ATTEMPTS", 3, minimum=1),
        daily_refresh_retry_base_seconds=retry_base,
        daily_refresh_retry_max_seconds=retry_max,
    )
    settings.prepare_filesystem()
    return settings


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required. Copy .env.example to .env first.")
    return value


def _path_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _optional_path_env(name: str, default: str) -> Path | None:
    raw = os.environ.get(name, default).strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    return value


def _float_env(name: str, default: float, minimum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _parse_allowed_user_ids(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as exc:
            raise RuntimeError(
                "TELEGRAM_ALLOWED_USER_IDS must contain comma-separated integers."
            ) from exc
    return frozenset(values)
