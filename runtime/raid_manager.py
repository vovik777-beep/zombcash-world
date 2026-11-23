"""ZombieCash community raid manager with optional X API metrics and Telegram raid panels."""
import os, re, html, json, asyncio, logging, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("ZOMBCASH_OWNER_ID", "0") or 0)
CHAT_ID = int(os.getenv("ZOMBCASH_RAID_CHAT_ID", "0") or 0)
DB_PATH = os.getenv("ZOMBCASH_RAID_DB", "/data/raid_manager.sqlite3")
X_API_BEARER_TOKEN = os.getenv("X_API_BEARER_TOKEN", "").strip()
# Live X metrics are opt-in. Admins can also toggle them at runtime with /raidmetrics.
X_API_METRICS_DEFAULT = os.getenv("ZOMBCASH_RAID_METRICS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
# A 402 is a billing/credit gate, not a transient error. Avoid hammering X every minute.
X_API_COOLDOWN_UNTIL = None
X_API_402_COOLDOWN = timedelta(hours=6)
DEFAULT_TARGETS = {
    "likes": int(os.getenv("ZOMBCASH_RAID_TARGET_LIKES", "10")),
    "retweets": int(os.getenv("ZOMBCASH_RAID_TARGET_RETWEETS", "5")),
    "replies": int(os.getenv("ZOMBCASH_RAID_TARGET_REPLIES", "3")),
}
if not Path(DB_PATH).parent.exists():
    DB_PATH = str(Path(__file__).resolve().parent / "raid_manager.sqlite3")


def _db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS campaigns(
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, x_url TEXT NOT NULL,
        created_by INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS participants(
        campaign_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL,
        display_name TEXT NOT NULL, joined_at TEXT NOT NULL, PRIMARY KEY(campaign_id,user_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS proofs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        username TEXT NOT NULL, display_name TEXT NOT NULL, url TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', submitted_at TEXT NOT NULL, reviewer INTEGER,
        UNIQUE(campaign_id,user_id))""")
    db.execute("CREATE TABLE IF NOT EXISTS raid_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    columns = {r["name"] for r in db.execute("PRAGMA table_info(campaigns)").fetchall()}
    migrations = {
        "targets_json": """TEXT NOT NULL DEFAULT '{"likes":10,"retweets":5,"replies":3}'""",
        "metrics_json": "TEXT NOT NULL DEFAULT '{}'",
        "ended_at": "TEXT",
        "end_reason": "TEXT NOT NULL DEFAULT ''",
        "media_url": "TEXT NOT NULL DEFAULT ''",
    }
    for name, declaration in migrations.items():
        if name not in columns:
            db.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {declaration}")
    db.commit()
    return db


def _metrics_enabled():
    """Return the persisted runtime switch, falling back to the opt-in environment default."""
    db = _db()
    row = db.execute("SELECT value FROM raid_settings WHERE key='x_metrics_enabled'").fetchone()
    db.close()
    if row is None:
        return X_API_METRICS_DEFAULT
    return row["value"].lower() in {"1", "true", "yes", "on"}


def _set_metrics_enabled(enabled):
    db = _db()
    db.execute("INSERT INTO raid_settings(key,value) VALUES('x_metrics_enabled',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               ("true" if enabled else "false",))
    db.commit()
    db.close()


def _raid_module_enabled():
    """The raid module follows the live-metrics master switch unless explicitly persisted."""
    db = _db()
    row = db.execute("SELECT value FROM raid_settings WHERE key='raid_module_enabled'").fetchone()
    db.close()
    if row is None:
        return _metrics_enabled()
    return row["value"].lower() in {"1", "true", "yes", "on"}


def _set_raid_module_enabled(enabled):
    db = _db()
    db.execute("INSERT INTO raid_settings(key,value) VALUES('raid_module_enabled',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               ("true" if enabled else "false",))
    db.commit()
    db.close()


def _sync_metrics_job(application):
    """Start or remove the raid polling job together with the module state."""
    if not application or not application.job_queue:
        return
    jobs = application.job_queue.get_jobs_by_name("zombcash-raid-metrics")
    if _raid_module_enabled() and X_API_BEARER_TOKEN:
        if not jobs:
            application.job_queue.run_repeating(
                _poll_active_raids, interval=60, first=15,
                name="zombcash-raid-metrics", job_kwargs={"max_instances": 1})
    else:
        for job in jobs:
            job.schedule_removal()


def _raid_module_guard(handler):
    """Keep command registration stable while making every raid feature inert when OFF."""
    async def guarded(update, context):
        if not _raid_module_enabled():
            # The wizard listener sees ordinary text messages too; never reply to unrelated chat text.
            if handler is _wizard_text:
                if context.user_data is not None:
                    context.user_data.pop("raid_wizard", None)
                return
            message = update.effective_message
            if message:
                await message.reply_text("☣️ Raid module is OFF. Admin can enable it with /raidmetrics on.")
            elif update.callback_query:
                await update.callback_query.answer("Raid module is OFF.", show_alert=True)
            return
        return await handler(update, context)
    return guarded


async def raidmetrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        return await update.effective_message.reply_text("Admin only.")
    if not context.args:
        module_state = "ON" if _raid_module_enabled() else "OFF"
        metrics_state = "ON" if _metrics_enabled() else "OFF"
        return await update.effective_message.reply_text(
            f"Raid module: {module_state}. Live X metrics: {metrics_state}. "
            "Use /raidmetrics on or /raidmetrics off.")
    action = context.args[0].strip().lower()
    if action in {"status", "state", "статус"}:
        module_state = "ON" if _raid_module_enabled() else "OFF"
        metrics_state = "ON" if _metrics_enabled() else "OFF"
        return await update.effective_message.reply_text(
            f"Raid module: {module_state}. Live X metrics: {metrics_state}. "
            "Use /raidmetrics on or /raidmetrics off.")
    if action in {"on", "enable", "вкл"}:
        if not X_API_BEARER_TOKEN:
            return await update.effective_message.reply_text(
                "Cannot enable live X metrics: X_API_BEARER_TOKEN is not configured.")
        _set_metrics_enabled(True)
        _set_raid_module_enabled(True)
        _sync_metrics_job(context.application)
        return await update.effective_message.reply_text(
            "Raid module and live X metrics enabled. X API usage may incur charges.")
    if action in {"off", "disable", "выкл"}:
        _set_metrics_enabled(False)
        _set_raid_module_enabled(False)
        _sync_metrics_job(context.application)
        return await update.effective_message.reply_text(
            "Raid module and live X metrics are OFF. Raid commands, callbacks, wizard and background polling are paused.")
    return await update.effective_message.reply_text("Usage: /raidmetrics on | off | status")


def _admin(update):
    ids = {OWNER_ID}
    ids.update(int(x.strip()) for x in os.getenv("ZOMBCASH_ADMIN_IDS", "").split(",") if x.strip().isdigit())
    return bool(update.effective_user and update.effective_user.id in ids)


def _x_url(value):
    try:
        p = urlparse(value)
        return p.scheme == "https" and p.netloc.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} and bool(re.search(r"/status/(\d+)", p.path))
    except Exception:
        return False


def _tweet_id(url):
    match = re.search(r"/status/(\d+)", urlparse(url).path)
    return match.group(1) if match else None


def _parse_targets(raw):
    targets = dict(DEFAULT_TARGETS)
    for key, value in re.findall(r"(likes|retweets|replies)\s*=\s*(\d+)", raw.lower()):
        targets[key] = max(1, min(int(value), 1000000))
    return targets


def _targets_text(targets):
    return f"❤️ Likes: {targets.get('likes', 10)}\n🔁 Reposts: {targets.get('retweets', 5)}\n💬 Replies: {targets.get('replies', 3)}"


def _panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 CREATE RAID", callback_data="raidpanel:start"),
         InlineKeyboardButton("🔗 CREATE WITH LINK", callback_data="raidpanel:link")],
        [InlineKeyboardButton("🎯 TARGETS", callback_data="raidpanel:targets"),
         InlineKeyboardButton("📊 RAID STATS", callback_data="raidpanel:stats")],
        [InlineKeyboardButton("❌ CLOSE", callback_data="raidpanel:close")],
    ])


def _campaign_keyboard(cid, url):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 OPEN RAID POST ON X", url=url)],
        [InlineKeyboardButton("☣️ JOIN THE RAID", callback_data=f"raidjoin:{cid}"),
         InlineKeyboardButton("📊 RAID STATS", callback_data=f"raidstats:{cid}")],
    ])


async def raidoptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "☣️ <b>ZombieCash Raid Options</b>\n\nChoose an action. Live X metrics require an X API bearer token; no metrics are invented.",
        parse_mode="HTML", reply_markup=_panel_keyboard())


async def raidhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "☣️ ZOMBIE LAB — COMMUNITY RAID\n\n/raidoptions — open Raid Options\n"
        "/raidmission — current mission\n/raidjoin — join active raid\n"
        "/raidproof https://x.com/name/status/123 — submit proof\n/raidleaderboard — top contributors\n"
        "/raidstatus — mission status\n/raidrefresh — fetch X metrics (admin)\n"
        "/raidmetrics status|on|off — check or toggle live X metrics (admin; X API usage may incur charges)\n"
        "/raidfinish — end raid (admin)\n\n"
        "Admins: /raidstart https://x.com/name/status/123 | Mission title | likes=10 retweets=5 replies=3\n"
        "/raidstop — close raid\n/raidapprove ID or /raidreject ID — review proof")


async def raidstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        return await update.effective_message.reply_text("Admin only.")
    parts = [p.strip() for p in update.effective_message.text.partition(" ")[2].strip().split("|")]
    url = parts[0] if parts else ""
    title = parts[1] if len(parts) > 1 and parts[1] else "ZombieCash Community Raid"
    targets = _parse_targets(" ".join(parts[2:]))
    if not _x_url(url):
        return await update.effective_message.reply_text(
            "Usage: /raidstart https://x.com/name/status/123 | Mission title | likes=10 retweets=5 replies=3")
    db = _db()
    cur = db.execute("""INSERT INTO campaigns(title,x_url,created_by,active,created_at,targets_json,metrics_json)
        VALUES(?,?,?,?,?,?,?)""",
        (title[:180], url, update.effective_user.id, 1, datetime.now(timezone.utc).isoformat(),
         json.dumps(targets), "{}"))
    cid = cur.lastrowid
    db.commit(); db.close()
    text = (f"☣️ <b>ZOMBIECASH RAID #{cid}</b>\n\n🧟 <b>{html.escape(title[:180])}</b>\n\n"
            "1. Open the mission post on X\n2. Participate genuinely\n3. Submit proof with /raidproof\n\n"
            f"🎯 <b>Targets</b>\n{_targets_text(targets)}\n\n🏆 Approved proofs count toward the leaderboard. $ZOMBCASH")
    await context.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML",
        reply_markup=_campaign_keyboard(cid, url), disable_web_page_preview=False)
    await update.effective_message.reply_text(
        f"Raid #{cid} published in the ZombieCash CTO group. " +
        ("X API metrics enabled (usage may incur charges)." if _metrics_enabled() and X_API_BEARER_TOKEN else
         "Live X metrics are OFF. Admin can enable with /raidmetrics on."))


async def _active(campaign_id=None):
    db = _db()
    if campaign_id is not None:
        row = db.execute("SELECT * FROM campaigns WHERE active=1 AND id=?", (int(campaign_id),)).fetchone()
    else:
        row = db.execute("SELECT * FROM campaigns WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    return row


def _active_campaigns():
    db = _db()
    rows = db.execute("SELECT * FROM campaigns WHERE active=1 ORDER BY id DESC").fetchall()
    db.close()
    return rows


async def raidmission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = _active_campaigns()
    if not rows:
        return await update.effective_message.reply_text("No active raids right now, survivor.")
    for row in rows:
        await update.effective_message.reply_text(
            f"☣️ ZOMBIECASH RAID #{row['id']}\n{row['title']}\n\n{_targets_text(json.loads(row['targets_json'] or '{}'))}\n\n"
            f"Join: /raidjoin {row['id']}\nSubmit proof: /raidproof {row['id']} https://x.com/name/status/123\nMission post: {row['x_url']}",
            reply_markup=_campaign_keyboard(row["id"], row["x_url"]))


async def raidjoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    campaign_id = context.args[0] if context.args and context.args[0].isdigit() else None
    row = await _active(campaign_id)
    if not row:
        return await update.effective_message.reply_text("No active raid right now.")
    u = update.effective_user; db = _db()
    db.execute("INSERT OR IGNORE INTO participants VALUES(?,?,?,?,?)",
        (row["id"], u.id, u.username or "", u.full_name or "Survivor", datetime.now(timezone.utc).isoformat()))
    db.commit(); db.close()
    await update.effective_message.reply_text("🧟 You're in! Complete the mission and submit proof with /raidproof plus your X link.")


async def raidproof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    campaign_id = context.args[0] if context.args and context.args[0].isdigit() else None
    row = await _active(campaign_id)
    if not row:
        return await update.effective_message.reply_text("No matching active raid. Use /raidmission to see active raids.")
    args = list(context.args)
    if campaign_id is not None:
        args = args[1:]
    url = args[0].strip() if args else ""
    if not _x_url(url):
        return await update.effective_message.reply_text("Send a valid X post link: /raidproof https://x.com/name/status/123")
    u = update.effective_user; db = _db()
    try:
        db.execute("""INSERT INTO proofs(campaign_id,user_id,username,display_name,url,status,submitted_at)
            VALUES(?,?,?,?,?,'pending',?)""",
            (row["id"], u.id, u.username or "", u.full_name or "Survivor", url, datetime.now(timezone.utc).isoformat()))
        db.execute("INSERT OR IGNORE INTO participants VALUES(?,?,?,?,?)",
            (row["id"], u.id, u.username or "", u.full_name or "Survivor", datetime.now(timezone.utc).isoformat()))
        db.commit()
        pid = db.execute("SELECT id FROM proofs WHERE campaign_id=? AND user_id=?", (row["id"], u.id)).fetchone()["id"]
    except sqlite3.IntegrityError:
        db.close()
        return await update.effective_message.reply_text("You've already submitted proof for this mission. Ask an admin if it needs review.")
    db.close()
    await update.effective_message.reply_text(f"🧪 Proof #{pid} submitted for review. Thanks, survivor!")


async def raidleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    rows = db.execute("""SELECT p.user_id,p.username,p.display_name,COUNT(s.id) points
        FROM participants p LEFT JOIN proofs s ON s.campaign_id=p.campaign_id AND s.user_id=p.user_id AND s.status='approved'
        GROUP BY p.user_id ORDER BY points DESC,p.display_name LIMIT 10""").fetchall()
    db.close()
    if not rows:
        return await update.effective_message.reply_text("No survivors on the board yet. Join the raid!")
    lines = ["🏆 ZOMBIECASH RAID LEADERBOARD"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. " + ("@" + r["username"] if r["username"] else r["display_name"]) + f" — {r['points']} pts")
    await update.effective_message.reply_text("\n".join(lines))


async def _post_metrics(url):
    global X_API_COOLDOWN_UNTIL
    if not _metrics_enabled() or not X_API_BEARER_TOKEN:
        return None
    now = datetime.now(timezone.utc)
    if X_API_COOLDOWN_UNTIL and now < X_API_COOLDOWN_UNTIL:
        return None
    tid = _tweet_id(url)
    if not tid:
        return None
    def _request():
        response = requests.get(f"https://api.x.com/2/tweets/{tid}",
            headers={"Authorization": f"Bearer {X_API_BEARER_TOKEN}"},
            params={"tweet.fields": "public_metrics,attachments,created_at",
                    "expansions": "attachments.media_keys", "media.fields": "preview_image_url,url"},
            timeout=12)
        response.raise_for_status()
        return response.json()
    try:
        return await asyncio.to_thread(_request)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 402:
            X_API_COOLDOWN_UNTIL = datetime.now(timezone.utc) + X_API_402_COOLDOWN
            log.error("X API returned HTTP 402 Payment Required; pausing live raid metrics for 6 hours. Check X Developer Console credits/billing.")
        else:
            log.warning("X API metrics request failed: %s", exc)
        return None


async def _publish_raid_end(context, row, metrics, reached=False):
    targets = json.loads(row["targets_json"] or "{}")
    started = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    duration = max(1, int((datetime.now(timezone.utc) - started).total_seconds() // 60))
    def line(label, metric_key, target_key):
        value = int(metrics.get(metric_key, 0))
        target = max(1, int(targets.get(target_key, 1)))
        pct = min(100, int(value * 100 / target))
        return f"🟩 {label}: {value} / {target} [{pct}%]"
    heading = "🎉 Raid Ended — Targets Reached!" if reached else "☣️ Raid Ended"
    caption = (f"<b>{heading}</b>\n\n"
        f"{line('Likes', 'like_count', 'likes')}\n{line('Reposts', 'retweet_count', 'retweets')}\n"
        f"{line('Replies', 'reply_count', 'replies')}\n\n"
        f'<a href="{html.escape(row["x_url"], quote=True)}">Open mission on X</a>\n\n'
        f"⏱ Duration: {duration} minutes" + ("\n\n🔥 Trending" if reached else ""))
    try:
        if row["media_url"]:
            await context.bot.send_photo(chat_id=CHAT_ID, photo=row["media_url"], caption=caption, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="HTML", disable_web_page_preview=False)
    except Exception:
        log.exception("Could not publish raid result card; sending text fallback")
        await context.bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="HTML", disable_web_page_preview=False)


async def _refresh_campaign(context, row):
    response = await _post_metrics(row["x_url"])
    if not response or not response.get("data"):
        return None, "X API metrics unavailable. Check bearer token, API credits and endpoint access."
    data = response["data"]
    raw = data.get("public_metrics") or {}
    metrics = {k: int(raw.get(k, 0)) for k in ("like_count", "retweet_count", "reply_count")}
    media_url = ""
    media = response.get("includes", {}).get("media", [])
    if media:
        media_url = media[0].get("preview_image_url") or media[0].get("url") or ""
    db = _db()
    db.execute("UPDATE campaigns SET metrics_json=?, media_url=? WHERE id=?",
               (json.dumps(metrics), media_url, row["id"]))
    db.commit()
    fresh = db.execute("SELECT * FROM campaigns WHERE id=?", (row["id"],)).fetchone()
    db.close()
    targets = json.loads(fresh["targets_json"] or "{}")
    mapping = {"likes": "like_count", "retweets": "retweet_count", "replies": "reply_count"}
    reached = all(metrics.get(mapping[k], 0) >= int(targets.get(k, 1)) for k in mapping)
    if reached and fresh["active"]:
        db = _db()
        db.execute("UPDATE campaigns SET active=0, ended_at=?, end_reason='targets_reached' WHERE id=? AND active=1",
                   (datetime.now(timezone.utc).isoformat(), row["id"]))
        changed = db.execute("SELECT changes()").fetchone()[0]
        db.commit(); db.close()
        if changed:
            await _publish_raid_end(context, fresh, metrics, reached=True)
    return metrics, None


async def _poll_active_raids(context: ContextTypes.DEFAULT_TYPE):
    if not _raid_module_enabled() or not _metrics_enabled() or not X_API_BEARER_TOKEN:
        return
    for row in _active_campaigns():
        await _refresh_campaign(context, row)


async def raidrefresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        return await update.effective_message.reply_text("Admin only.")
    campaign_id = context.args[0] if context.args and context.args[0].isdigit() else None
    row = await _active(campaign_id)
    if not row:
        return await update.effective_message.reply_text("No matching active raid.")
    metrics, error = await _refresh_campaign(context, row)
    if error:
        return await update.effective_message.reply_text(error)
    db = _db(); fresh = db.execute("SELECT * FROM campaigns WHERE id=?", (row["id"],)).fetchone(); db.close()
    targets = json.loads(fresh["targets_json"] or "{}")
    await update.effective_message.reply_text(
        f"📊 RAID #{row['id']} LIVE METRICS\n❤️ Likes: {metrics['like_count']} / {targets.get('likes', 10)}"
        f"\n🔁 Reposts: {metrics['retweet_count']} / {targets.get('retweets', 5)}"
        f"\n💬 Replies: {metrics['reply_count']} / {targets.get('replies', 3)}")


async def raidstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    campaign_id = context.args[0] if context.args and context.args[0].isdigit() else None
    if campaign_id is None:
        active_rows = _active_campaigns()
        if len(active_rows) > 1:
            lines = ["☣️ ACTIVE ZOMBIECASH RAIDS"]
            lines.extend(f"#{item['id']} — {item['title']} | /raidstatus {item['id']}" for item in active_rows)
            return await update.effective_message.reply_text("\n".join(lines))
    row = await _active(campaign_id)
    if not row:
        db = _db()
        last = db.execute("SELECT * FROM campaigns WHERE id=?", (int(campaign_id),)).fetchone() if campaign_id else db.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        return await update.effective_message.reply_text("No active raid." if not last else f"Raid #{last['id']} is closed.")
    db = _db()
    joined = db.execute("SELECT COUNT(*) n FROM participants WHERE campaign_id=?", (row["id"],)).fetchone()["n"]
    pending = db.execute("SELECT COUNT(*) n FROM proofs WHERE campaign_id=? AND status='pending'", (row["id"],)).fetchone()["n"]
    approved = db.execute("SELECT COUNT(*) n FROM proofs WHERE campaign_id=? AND status='approved'", (row["id"],)).fetchone()["n"]
    db.close()
    metrics = json.loads(row["metrics_json"] or "{}"); targets = json.loads(row["targets_json"] or "{}")
    await update.effective_message.reply_text(
        f"☣️ RAID #{row['id']}: {row['title']}\n👥 Joined: {joined}\n🧪 Pending proofs: {pending}\n✅ Approved: {approved}"
        f"\n❤️ Likes: {metrics.get('like_count', '—')} / {targets.get('likes', 10)}"
        f"\n🔁 Reposts: {metrics.get('retweet_count', '—')} / {targets.get('retweets', 5)}"
        f"\n💬 Replies: {metrics.get('reply_count', '—')} / {targets.get('replies', 3)}")


async def raidstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        return await update.effective_message.reply_text("Admin only.")
    campaign_id = context.args[0] if context.args and context.args[0].isdigit() else None
    db = _db()
    row = db.execute("SELECT * FROM campaigns WHERE active=1 AND id=?", (int(campaign_id),)).fetchone() if campaign_id else db.execute("SELECT * FROM campaigns WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        db.execute("UPDATE campaigns SET active=0, ended_at=?, end_reason='admin_stopped' WHERE id=?",
                   (datetime.now(timezone.utc).isoformat(), row["id"]))
    db.commit(); db.close()
    if not row:
        return await update.effective_message.reply_text("No active raid to close.")
    metrics = json.loads(row["metrics_json"] or "{}")
    if _metrics_enabled() and X_API_BEARER_TOKEN:
        response = await _post_metrics(row["x_url"])
        if response and response.get("data"):
            metrics = response["data"].get("public_metrics") or metrics
    await _publish_raid_end(context, row, metrics, reached=False)
    await update.effective_message.reply_text("Raid closed and result card published.")


async def _review(update, status):
    if not _admin(update):
        return await update.effective_message.reply_text("Admin only.")
    arg = update.effective_message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        return await update.effective_message.reply_text("Provide a proof ID, e.g. /raidapprove 12")
    db = _db(); cur = db.execute("UPDATE proofs SET status=?,reviewer=? WHERE id=? AND status='pending'",
        (status, update.effective_user.id, int(arg)))
    db.commit(); db.close()
    await update.effective_message.reply_text(f"Proof #{arg} marked {status}." if cur.rowcount else "Pending proof not found.")


async def raidapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _review(update, "approved")


async def raidreject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _review(update, "rejected")


async def _button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, _, cid = q.data.partition(":")
    if action == "raidjoin":
        db = _db(); u = q.from_user
        db.execute("INSERT OR IGNORE INTO participants VALUES(?,?,?,?,?)",
            (int(cid), u.id, u.username or "", u.full_name or "Survivor", datetime.now(timezone.utc).isoformat()))
        db.commit(); db.close()
        await q.message.reply_text("🧟 You're in! Complete the mission and submit proof with /raidproof plus your X link.")
    elif action == "raidstats":
        db = _db(); row = db.execute("SELECT * FROM campaigns WHERE id=?", (int(cid),)).fetchone(); db.close()
        if not row:
            return await q.message.reply_text("Raid not found.")
        m = json.loads(row["metrics_json"] or "{}"); t = json.loads(row["targets_json"] or "{}")
        await q.message.reply_text(f"📊 RAID #{cid}\n❤️ Likes: {m.get('like_count', '—')} / {t.get('likes', 10)}"
            f"\n🔁 Reposts: {m.get('retweet_count', '—')} / {t.get('retweets', 5)}"
            f"\n💬 Replies: {m.get('reply_count', '—')} / {t.get('replies', 3)}")
    elif action == "raidpanel":
        if q.data == "raidpanel:close":
            return await q.edit_message_text("Raid Options closed.")
        if q.data == "raidpanel:targets":
            return await q.message.reply_text("🎯 Default targets:\n" + _targets_text(DEFAULT_TARGETS) +
                "\nOverride them in /raidstart with likes=... retweets=... replies=...")
        if q.data == "raidpanel:stats":
            return await raidstatus(update, context)
        if q.data in ("raidpanel:link", "raidpanel:start"):
            if not _admin(update):
                return await q.message.reply_text("Admin only.")
            context.user_data["raid_wizard"] = {"step": "likes"}
            return await q.message.reply_text("🚨 RAID CREATOR — Step 1/4\nHow many likes are we targeting? Send a whole number (1–1,000,000).")


async def _wizard_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Channel posts and anonymous sender-chat updates may have no effective user.
    # Never access user_data until a real user-backed update is confirmed.
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or chat.type == "channel" or user is None:
        return
    user_data = context.user_data
    if user_data is None:
        return
    state = user_data.get("raid_wizard")
    if not state:
        return
    if not _admin(update):
        context.user_data.pop("raid_wizard", None)
        return
    message = update.effective_message
    value = (message.text or "").strip()
    step = state.get("step")
    if step in ("likes", "retweets", "replies"):
        if not value.isdigit() or not 1 <= int(value) <= 1000000:
            return await message.reply_text("Please send a whole number from 1 to 1,000,000.")
        state[step] = int(value)
        if step == "likes":
            state["step"] = "retweets"
            return await message.reply_text("Step 2/4 — Target number of reposts?")
        if step == "retweets":
            state["step"] = "replies"
            return await message.reply_text("Step 3/4 — Target number of replies/comments?")
        state["step"] = "url"
        return await message.reply_text("Step 4/4 — Send the exact X post URL (https://x.com/name/status/123).")
    if step == "url":
        if not _x_url(value):
            return await message.reply_text("That doesn't look like an X post URL. Send https://x.com/name/status/123")
        targets = {"likes": state["likes"], "retweets": state["retweets"], "replies": state["replies"]}
        db = _db()
        cur = db.execute("""INSERT INTO campaigns(title,x_url,created_by,active,created_at,targets_json,metrics_json)
            VALUES(?,?,?,?,?,?,?)""",
            ("ZombieCash Community Raid", value, update.effective_user.id, 1,
             datetime.now(timezone.utc).isoformat(), json.dumps(targets), "{}"))
        cid = cur.lastrowid
        db.commit()
        db.close()
        context.user_data.pop("raid_wizard", None)
        text = (f"☣️ <b>ZOMBIECASH RAID #{cid}</b>\n\n🧟 <b>Community mission</b>\n\n"
                "1. Open the mission post on X\n2. Participate genuinely\n3. Submit proof using /raidproof "
                f"{cid} https://x.com/name/status/123\n\n🎯 <b>Targets</b>\n{_targets_text(targets)}\n\n"
                "🏆 Approved proofs count toward the leaderboard. $ZOMBCASH")
        await context.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML",
            reply_markup=_campaign_keyboard(cid, value), disable_web_page_preview=False)
        return await message.reply_text(f"✅ Raid #{cid} published. Other active raids remain open.")


def register_raid_handlers(application):
    """Register community raid tools; /raidmetrics remains available as the master switch."""
    guarded = _raid_module_guard
    application.add_handler(CommandHandler("raidoptions", guarded(raidoptions)))
    application.add_handler(CommandHandler("raidhelp", guarded(raidhelp)))
    application.add_handler(CommandHandler("raidstart", guarded(raidstart)))
    application.add_handler(CommandHandler("raidmission", guarded(raidmission)))
    application.add_handler(CommandHandler("raidjoin", guarded(raidjoin)))
    application.add_handler(CommandHandler("raidproof", guarded(raidproof)))
    application.add_handler(CommandHandler("raidleaderboard", guarded(raidleaderboard)))
    application.add_handler(CommandHandler("raidstatus", guarded(raidstatus)))
    application.add_handler(CommandHandler("raidrefresh", guarded(raidrefresh)))
    application.add_handler(CommandHandler("raidmetrics", raidmetrics))
    application.add_handler(CommandHandler("raidstop", guarded(raidstop)))
    application.add_handler(CommandHandler("raidfinish", guarded(raidstop)))
    application.add_handler(CommandHandler("raidapprove", guarded(raidapprove)))
    application.add_handler(CommandHandler("raidreject", guarded(raidreject)))
    application.add_handler(CallbackQueryHandler(guarded(_button), pattern=r"^raid(join|stats|panel):"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guarded(_wizard_text)), group=1)
    _sync_metrics_job(application)
