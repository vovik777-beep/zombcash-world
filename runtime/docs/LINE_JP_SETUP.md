# LINE Japan publisher setup

## Scope and current architecture

PR #62 already adds `line_integration.py`: signed webhook verification, follow/unfollow and reply handling, Japanese assistant replies, and an in-memory event deduplicator. It does **not** publish news on a schedule. `line_publisher.py` adds an independent, opt-in broadcast task to the API server.

The publisher reads the shared `published_news.json` queue. Enriched news arrives from `news_worker.py`; scheduled image posts from the existing three-rubric Telegram rotation (ZombieCash Memes, Undead After Dark, Nightmare & Horror) are copied into the same queue by `bot.py` after Telegram confirms delivery. Meme posts reuse the exact generated image and caption already published to Telegram; no second image is generated. The LINE publisher translates the caption into Japanese and sends the stored image plus a short Japanese meme post. It does not change Telegram/X cadence or text. Existing queue items are marked as a first-run baseline; only newly arriving enriched items are eligible. A durable append-only `line_publish_log.jsonl` provides deduplication and recovery. A pending record is treated as attempted after a restart to favor at-most-once delivery.

Japanese copy is translated from those stored fields using the project's existing `GEMINI_API_KEY` integration and configurable `GEMINI_LINE_JP_MODEL`. No OpenAI API is used and no new AI provider is added. Translation is cached in the LINE journal. If translation fails, that article is not sent. Source images are reused as-is; no new news images are generated.

User-requested, private meme-generation replies are not rebroadcast. Only images that the project itself has already published to the public `@ZOMBCASH` channel enter the LINE queue.

## Connect the existing Official Account

1. In LINE Official Account Manager, open **Settings → Messaging API** and enable/link the Messaging API channel for the existing ZombieCash Japan account. Do not create a second Official Account.
2. In LINE Developers Console, use that linked Messaging API channel. Copy its Channel Secret and issue a Channel Access Token. Never put either value in Git, a committed config file, or command output.
3. Set the webhook URL to `https://<FadeHost API host>/webhooks/line`, enable webhook use, then use LINE's **Verify** action. `line_integration.py` verifies the raw-body HMAC signature.
4. Keep LINE Official Account Manager greeting/auto-response settings consistent with the existing account; avoid duplicate automated replies.
5. Add the environment values below to the API app's FadeHost **secret/environment settings**, then restart only that API app after review. Do not paste tokens into chat or logs.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LINE_ENABLED` | `false` | Enables LINE webhook and one-recipient test push. |
| `LINE_CHANNEL_SECRET` | empty | Webhook signature verification. |
| `LINE_CHANNEL_ACCESS_TOKEN` | empty | Messaging API auth. |
| `LINE_BROADCAST_ENABLED` | `false` | Separate second gate for scheduled broadcasts; leave false during setup. |
| `LINE_PUBLISH_INTERVAL_HOURS` | `6` | Poll interval for newly published shared news. |
| `LINE_DAILY_LIMIT` | `10` | Local maximum successful broadcast posts per JST day. The LINE account's provider quota also applies. |
| `LINE_TEST_USER_ID` | empty | Exact allowlisted LINE user ID for one explicit test push. |
| `LINE_PUBLIC_BASE_URL` | `https://zombcash-bot.fadehost.app` | HTTPS host serving the stored news-image route. |
| `GEMINI_API_KEY` | existing project secret | Reuses the current Japanese copy translation provider; no new key. |
| `GEMINI_LINE_JP_MODEL` | `gemini-3.5-flash-lite` | Optional override for the existing Gemini model. |

Add these in FadeHost's per-app environment/secrets UI. Paste values directly into the secret fields, save, and restart the API app. Never echo environment values into logs or shell history. Keep `LINE_BROADCAST_ENABLED=false` until the single-recipient checks below are complete and the owner deliberately enables broadcasts.

## Safe test sequence

1. Keep `LINE_BROADCAST_ENABLED=false`. Add the two LINE credentials and the one permitted `LINE_TEST_USER_ID` in FadeHost.
2. Verify `GET /api/line/health` and LINE's webhook **Verify** action. Existing webhook tests cover valid/invalid signatures, follows/unfollows, and duplicate event IDs.
3. Run the explicit test push only for the allowlisted user: `python line_publisher.py`. This sends one Japanese test text through LINE Push API; it never invokes broadcast.
4. Exercise publisher automated tests with mocks: an original saved image, text-only fallback, duplicate item, transient API error/retry, HTTP 429 pause, daily limit, corrupt journal, and disabled mode. The tests make no external requests.
5. To verify a Japanese news card end-to-end, first use the mocked test in CI. A real broadcast news test is intentionally not automatic because the Broadcast API targets all followers. Get owner approval for one real channel-wide post before enabling it.
6. When the owner approves ongoing broadcasts, set `LINE_BROADCAST_ENABLED=true` manually. New content arrives from the existing Telegram worker's shared news store; the publisher sends at most one new item per configured interval. Do not enable this as part of deployment automation.

On a LINE API 429/quota response, publishing is paused for the rest of the JST day and the reason is written to `line_publish_log.jsonl`. Transient network/5xx errors retry up to three times. HTTP credentials, headers, and response bodies are not logged. No production deployment, LINE broadcast, or FadeHost setting change is performed by this code change.


## LINE broadcast insights

Scheduled posts use the Messaging API **Broadcast** endpoint. LINE's message-level Insights API supports broadcast/narrowcast messages and requires the `X-Line-Request-Id` response header from each send. The publisher now stores that request ID with the post, then refreshes its message insights on the hourly publisher cycle for up to 14 days. Metrics include delivered count, unique impressions (opens), and unique clicks. News and meme rows retain their type/category so they can be compared separately.

LINE may return `null` for interaction metrics when the relevant audience/event count is below 20; this is LINE's privacy threshold, not a publishing failure. Insights may take time to become available after a broadcast.

The API exposes an authenticated, read-only JSON endpoint at `GET /api/line/insights`. Authenticate with `Authorization: Bearer <NEWS_PUBLISH_TOKEN>`; never expose that token in a public website bundle or browser-side JavaScript. The endpoint returns aggregate totals and the latest 100 broadcasts. The same `NEWS_PUBLISH_TOKEN` already used by the trusted news worker protects this endpoint; no additional secret is required.
