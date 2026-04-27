# Telegram Heart Ranker

Telegram Heart Ranker is a local Telegram bot that ranks public channel posts by
their heart reactions. It reads channel history through your Telegram user
session, stores message metadata in SQLite, and exposes rankings through bot
commands.

The bot is the command interface, while historical indexing uses MTProto via
Telethon. This is necessary because the Telegram Bot API cannot take an
arbitrary public channel link and backfill historical messages on its own.

## Features

- Rank public Telegram channel posts by heart reaction count.
- Accept public channel links and `@username` references.
- Cache indexed messages in a local SQLite database.
- Page through ranking results with `First`, `Prev`, `Next`, and `Last` buttons.
- Filter rankings by all time, the last 30 days, or the last 365 days.
- Restrict bot access to specific Telegram user IDs.

## Requirements

- Python 3.9 or newer.
- A Telegram API ID and API hash from <https://my.telegram.org/apps>.
- A Telegram bot token from `@BotFather`.
- Access to the public channels you want to index from your Telegram account.

## Installation

Create a Telegram application first, then create a bot with `@BotFather`.
After that, install the project locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` and set at least these values:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token
```

Keep `.env`, `sessions/`, `data/`, and `logs/` local. They may contain secrets,
Telegram session files, local databases, or runtime logs and should not be
committed to a public repository.

## Running

Start the bot with:

```bash
python -m tg_heart_ranker
```

Or use the installed console script:

```bash
tg-heart-ranker
```

On the first run, Telethon will ask for your phone number and login code in the
terminal. After a successful login, the user session is saved under `sessions/`.

## Recommended Private Mode

After the bot starts, send this command to it in Telegram:

```text
/whoami
```

Copy the returned numeric user ID into `.env`:

```env
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Restart the bot. When this value is set, only the listed Telegram user IDs can
use the bot. Multiple IDs can be separated with commas.

## Bot Commands

```text
/start
/whoami
/rank <channel_url> [limit] [all|month|year]
/index <channel_url> [message_limit]
/top <channel_url> [limit] [all|month|year]
/status <channel_url>
```

Examples:

```text
/rank https://t.me/some_public_channel 10
/rank https://t.me/some_public_channel 10 month
/rank https://t.me/some_public_channel 10 year
/index https://t.me/some_public_channel 500
/top @some_public_channel 10
/status @some_public_channel
```

`/rank` is the main command. It refreshes the local index when needed and then
returns the ranking. `/top` is an alias for the same behavior. You can also send
a public channel link or `@username` directly, and the bot treats it as:

```text
/rank <channel> 10
```

## Indexing Strategy

When `/index` is called without `message_limit`, the bot uses
`INDEX_MESSAGE_LIMIT` from `.env`. The example configuration sets this to
`5000`, which indexes the latest 5,000 messages.

Pass `0` to index all accessible history:

```text
/index https://t.me/some_public_channel 0
```

When `/rank` or `/top` is used for a channel with no local data, the bot performs
an initial index using `INDEX_MESSAGE_LIMIT`. If the channel already has local
data and the cache is older than 24 hours, the bot refreshes only the newest
`RANK_REFRESH_MESSAGE_LIMIT` messages before ranking.

## Configuration

The main settings live in `.env.example`:

- `TELEGRAM_API_ID`: Telegram application API ID.
- `TELEGRAM_API_HASH`: Telegram application API hash.
- `TELEGRAM_BOT_TOKEN`: Bot token from `@BotFather`.
- `TELEGRAM_ALLOWED_USER_IDS`: Optional comma-separated list of Telegram user IDs allowed to use the bot.
- `HEART_RANKER_DB`: SQLite database path.
- `TELEGRAM_USER_SESSION`: Telethon user session path.
- `TELEGRAM_BOT_SESSION`: Telethon bot session path.
- `INDEX_MESSAGE_LIMIT`: Default number of messages to index.
- `RANK_REFRESH_MESSAGE_LIMIT`: Number of newest messages to refresh before ranking an already indexed channel.
- `INDEX_PROGRESS_EVERY`: How often indexing progress updates are sent.
- `INDEX_REQUEST_SLEEP_SECONDS`: Small pause between batches to reduce pressure on Telegram.
- `LOG_LEVEL`: Python logging level.
- `LOG_FILE`: Optional log file path. Leave it empty to log only to stdout or a service journal.

## Local Data

The SQLite database stores only the metadata needed for ranking:

- channel ID, username, title, and URL
- message ID and date
- text preview
- message URL
- heart reaction count
- total reaction count
- index timestamps and index job status

The project does not bypass Telegram permissions. It can only read public
channels that your Telegram user account can access.

## Development Checks

Run the test suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

Compile the source and tests:

```bash
python -m compileall src tests
```

## Notes

- This project is designed for local MVP usage.
- Very large channels can trigger Telegram rate limits. If Telegram asks you to
  wait, retry later or index a smaller number of messages first.
- Do not publish real bot tokens, API hashes, Telethon session files, or local
  databases.
