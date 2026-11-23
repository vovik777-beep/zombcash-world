# ZombieCash Telegram Survivor Registration — setup

The website UI and API endpoints are committed, but login remains disabled until the external services are configured.

## 1. Create the free Supabase project

1. Open https://supabase.com/dashboard and create a new project on the Free plan.
2. Open **SQL Editor** in that project.
3. Run the complete SQL from `supabase/schema.sql`.
4. In **Project Settings → API**, copy the Project URL and the **service_role** secret key.

Never put the service-role key in website JavaScript, GitHub, or a public message.

## 2. Confirm the Telegram bot

The website login widget is currently configured for `MemeCoinAIAgent_bot`. The API verifies login signatures using the existing `TELEGRAM_BOT_TOKEN` environment variable from the bot deployment. Before enabling login, confirm that this token belongs to that exact bot.

In Telegram, open **@BotFather**, select that bot, and run:

`/setdomain`

Set the domain to:

`zombcash.github.io`

Do not create a new bot unless the existing token and username do not match. The login bot can be the existing bot; it does not require a separate worker process.

## 3. Add environment variables in Fadehost

In the existing `zombcash-bot` service, add:

- `SUPABASE_URL` = the Supabase Project URL
- `SUPABASE_SERVICE_ROLE_KEY` = the Supabase service-role secret

Keep the existing `TELEGRAM_BOT_TOKEN` unchanged. Do not replace other variables. Save the environment and redeploy/restart the service.

The API already starts from `start.sh`, so no additional process is required.

## 4. Test

1. Open `https://zombcash.github.io/` and hard-refresh with Ctrl+F5.
2. Click **LOGIN WITH TELEGRAM** and authorize the existing bot.
3. Confirm the profile is saved in `public.survivor_profiles` and a zero-point login event appears in `public.survivor_activity`.
4. Verify that the profile shows 0 points for a new account. Points are not awarded for merely logging in.

## Current implementation boundary

- The API verifies Telegram's signed login payload and rejects stale responses.
- The API issues a signed, seven-day session token and reads the user's profile/activity only with that token.
- Supabase tables have RLS enabled and browser-role access revoked; only the API's service role can access them.
- Login activity is recorded with zero points. Reward points for games, votes, or other actions must be added later through server-validated event handlers; never trust a points value sent by the browser.
- The site login interface has translations for English, German, French, Italian, Japanese, Simplified Chinese, Russian, Turkish, and Hindi.
