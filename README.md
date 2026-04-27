# Telegram Heart Ranker

Telegram Heart Ranker 是一个本地运行的 Telegram 频道爱心反应排行榜机器人。它可以读取公开频道的历史消息，统计每条消息的爱心反应数量，并通过 bot 命令返回排行榜。

这个项目把 bot 当作命令入口；历史消息读取使用你的 Telegram 用户 session 通过 MTProto 完成，因为 Telegram Bot API 不能直接完成「输入任意公开频道链接并回溯历史消息」这类操作。

## 功能

- 按爱心反应数排行公开 Telegram 频道消息。
- 支持频道链接和 `@username`。
- 支持本地 SQLite 缓存，避免每次翻页都重新读取 Telegram。
- 支持分页按钮：`First`、`Prev`、`Next`、`Last`。
- 支持时间筛选：全部、最近 30 天、最近 365 天。
- 支持限制允许使用 bot 的 Telegram 用户 ID。

## 安装

先准备 Telegram API 凭据和 bot token：

1. 到 https://my.telegram.org/apps 创建应用，获取 `api_id` 和 `api_hash`。
2. 在 Telegram 中通过 `@BotFather` 创建 bot，获取 bot token。
3. 安装项目依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

然后编辑 `.env`，至少填入：

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token
```

`.env`、`sessions/`、`data/` 和 `logs/` 都是本地文件，默认不会提交到 Git。请不要把真实 token、session 文件或本地数据库上传到公开仓库。

## 运行

```bash
python -m tg_heart_ranker
```

也可以使用安装后的命令：

```bash
tg-heart-ranker
```

第一次运行时，Telethon 会在终端中要求输入手机号和登录验证码。登录成功后，用户 session 会保存到 `sessions/`。

## 推荐的私有使用限制

第一次启动 bot 后，在 Telegram 里发送：

```text
/whoami
```

复制返回的数字用户 ID，然后写入 `.env`：

```env
TELEGRAM_ALLOWED_USER_IDS=123456789
```

重启服务后，只有这些用户 ID 可以使用这个 bot。

## Bot 命令

```text
/start
/whoami
/rank <channel_url> [limit] [all|month|year]
/index <channel_url> [message_limit]
/top <channel_url> [limit] [all|month|year]
/status <channel_url>
```

常用示例：

```text
/rank https://t.me/some_public_channel 10
/rank https://t.me/some_public_channel 10 month
/rank https://t.me/some_public_channel 10 year
/index https://t.me/some_public_channel 500
/top @some_public_channel 10
/status @some_public_channel
```

`/rank` 是主要命令。它会先刷新本地索引，再返回排行榜。`/top` 是同样行为的别名。你也可以直接发送公开频道链接或 `@username`，bot 会按 `/rank <channel> 10` 处理。

## 索引策略

如果 `/index` 没有传 `message_limit`，会使用 `.env` 中的 `INDEX_MESSAGE_LIMIT`。默认值是 `5000`，表示读取最新 5000 条消息。

传入 `0` 表示读取所有可访问历史消息：

```text
/index https://t.me/some_public_channel 0
```

当 `/rank` 或 `/top` 遇到一个还没有本地数据的频道时，也会使用 `INDEX_MESSAGE_LIMIT` 做第一次索引。频道已经有本地数据且缓存超过 24 小时时，会使用 `RANK_REFRESH_MESSAGE_LIMIT` 刷新最新消息后再排行。

## 配置项

主要配置都在 `.env.example` 中：

- `TELEGRAM_API_ID`：Telegram 应用 API ID。
- `TELEGRAM_API_HASH`：Telegram 应用 API hash。
- `TELEGRAM_BOT_TOKEN`：BotFather 提供的 bot token。
- `TELEGRAM_ALLOWED_USER_IDS`：允许使用 bot 的 Telegram 用户 ID，多个 ID 用逗号分隔。
- `HEART_RANKER_DB`：SQLite 数据库路径。
- `TELEGRAM_USER_SESSION`：用户 session 保存路径。
- `TELEGRAM_BOT_SESSION`：bot session 保存路径。
- `INDEX_MESSAGE_LIMIT`：默认索引消息数量。
- `RANK_REFRESH_MESSAGE_LIMIT`：排行榜刷新时读取的最新消息数量。
- `LOG_LEVEL`、`LOG_FILE`：日志级别和日志文件路径。

## 注意事项

- 当前版本面向本地 MVP 使用。
- 只支持你的 Telegram 用户账号可以访问的公开频道。
- 不会绕过 Telegram 私有频道权限。
- 本地数据库只保存排行需要的消息元数据，包括消息 ID、日期、预览文本、链接、爱心数、总反应数和索引时间。
- 大频道可能触发 Telegram 速率限制，遇到限制时请等待后重试，或先使用较小的索引数量。

## 本地检查

```bash
PYTHONPATH=src python -m unittest discover -s tests
python -m compileall src tests
```
