"""Website feature spotlight publisher, independent of the hourly meme schedule."""
import asyncio
import html
import io
import json
import logging
import os
import time
import urllib.request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

INTERVAL = max(900, int(os.getenv("SITE_PROMO_INTERVAL_SECONDS", "10800")))
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_promo_state.json")
HOME = "https://zombcash.github.io/"
PAGES = [
 ("home","ZOMBIECASH HQ",HOME,"The official project hub: token identity, ecosystem navigation, project updates, community links and access to the tools.","Show newcomers where the whole ZombieCash ecosystem starts."),
 ("market","TOKEN INTEL",HOME+"zombcash-market-data.html","A token information page with market-data context and links for independent verification. Never promise that live data is always available.","Explain where survivors can inspect token information and verify data independently."),
 ("buy","TOKEN FIELD GUIDE",HOME+"buy-zombcash.html","A practical guide to the official Solana token mint, trading links and basic verification. Remind readers to check the mint before any transaction.","Focus on safe verification, not price predictions or investment claims."),
 ("arcade","SURVIVOR ARCADE",HOME+"arcade.html","Community game experiences, survival challenges and progress connected to Zombie Lab.","Invite the crew to play, complete challenges and compare survivor progress."),
 ("maker","ZOMBIE MAKER",HOME+"zombie-maker.html","A creative tool for turning a photo into a playful zombie-style portrait, with style choices and image download/sharing.","Show the creative side: make a mutant portrait and share the result."),
 ("survivor","SURVIVOR HUB",HOME+"survivor-hub.html","A survivor profile hub with display-name customization, leaderboard visibility and optional online-presence controls.","Highlight profiles and progress while reminding users to review their privacy settings."),
 ("crew","CREW VOTE",HOME+"crew-vote.html","A community voting page for ideas and crew participation.","Invite members to vote on ideas and help shape community priorities."),
 ("outbreak","OUTBREAK TRANSMISSION",HOME+"outbreak-transmission.html","A news and field-report archive with explainers and source-linked reports about the Solana ecosystem.","Point readers to sourced reports, dates and practical context—not hype."),
 ("manual","SURVIVOR FIELD MANUAL",HOME+"survivor-field-manual.html","A collection of practical guides covering token research, wallet safety, liquidity checks and survival in the Solana meme-coin ecosystem.","Promote useful research and wallet-safety knowledge."),
 ("research","RESEARCH LIBRARY",HOME+"how-to-research-solana-meme-coins.html","Educational material on researching Solana meme coins and checking risk signals before interacting with tokens.","Teach the crew to research first and verify independently."),
 ("gallery","ZOMBIE GALLERY",HOME+"gallery.html","A gallery of ZombieCash-themed visual creations and community content.","Spotlight community creativity and visual content."),
 ("ai","AI AGENT HUB",HOME+"ai-agent-hub.html","A hub for discovering AI-powered community tools and experiences.","Show how AI tools support creation, discovery and participation."),
 ("radar","MEME RADAR BOT","https://t.me/ZombieCashMemeRadarBot","A Telegram community tool for exploring Solana token pairs, market signals, AI analysis and community-rated submissions. Information is not financial advice.","Introduce Meme Radar as a research tool; encourage verification rather than blind calls."),
 ("lab","ZOMBIE LAB BOT","https://t.me/MemeCoinAIAgent_bot","The Telegram Zombie Lab includes zombie and meme creation, a survival game, supply raids, stats, achievements and community content tools.","Show that the Telegram bot is an interactive toolbox, not just a news feed."),
]

def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        logging.exception("Website spotlight state could not be read")
        return {}

def _save(value):
    temp = STATE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, STATE)

def _changes():
    req = urllib.request.Request(
        "https://api.github.com/repos/zombcash/zombcash.github.io/commits?per_page=10",
        headers={"Accept":"application/vnd.github+json","User-Agent":"ZombieCash-Website-Spotlight/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            rows = json.loads(response.read().decode("utf-8"))
        result = []
        for row in rows if isinstance(rows, list) else []:
            commit = row.get("commit", {})
            msg = str(commit.get("message", "")).splitlines()[0].strip()
            if msg and not msg.lower().startswith(("merge ", "merge pull request")):
                result.append((commit.get("author", {}).get("date", "")[:10], msg[:150]))
            if len(result) == 4:
                break
        return result
    except Exception:
        logging.warning("Could not read public website commit history")
        return []

def _screenshot(url):
    req = urllib.request.Request(
        "https://image.thum.io/get/width/1200/crop/900/noanimate/" + url,
        headers={"User-Agent":"Mozilla/5.0 (compatible; ZombieCashWebsiteSpotlight/1.0)"})
    with urllib.request.urlopen(req, timeout=35) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Screenshot exceeds size limit")
    if not (data.startswith(bytes.fromhex("89504e470d0a1a0a")) or data.startswith(bytes.fromhex("ffd8"))):
        raise ValueError("Screenshot provider returned a non-image response")
    return io.BytesIO(data)

def register_website_showcase(application, ask_gemini):
    async def publish(context):
        state = _load()
        previous = state.get("last_page_key")
        index = next((i for i, p in enumerate(PAGES) if p[0] == previous), -1)
        key, name, url, facts, angle = PAGES[(index + 1) % len(PAGES)]
        recent = _changes()
        recent_text = "\n".join("- " + date + ": " + msg for date, msg in recent) or "No recent commit messages available."
        try:
            text = await ask_gemini(
                "Write a concise, engaging English Telegram post for the ZombieCash website. "
                "Use a punchy hook, 2-4 short lines and 2-3 relevant hashtags. The URL is on a button. "
                "Describe the selected page accurately and vary the hook and wording between posts. "
                "Mention the broader ZombieCash ecosystem naturally when relevant, but do not cram every feature into every post.\n"
                f"Page: {name}\nKnown context: {facts}\nAngle: {angle}\n"
                f"Recent public repository commit messages (not necessarily release notes):\n{recent_text}\n"
                "Do not invent facts, metrics, users, partnerships, working features, dates or financial claims. "
                "Do not call commits user-facing releases unless clearly supported. You may add one idea only if "
                "labelled 'POSSIBLE FUTURE IDEA — NOT ANNOUNCED'; never imply it is approved or promised.")
            text = (text or "").strip()
            if not text:
                raise RuntimeError("Empty generated post")
        except Exception:
            logging.exception("Website spotlight text generation failed")
            text = facts + "\n\nExplore the page and tell the crew what you would like to see next.\n#ZombieCash #ZOMBCASH #Web3"
        page_tags = {
            "home": "#ZombieCashHQ",
            "arcade": "#ZombieCashArcade",
            "maker": "#ZombieMaker",
            "survivor": "#ZombieCashSurvivorHub",
            "crew": "#ZombieCashCrewVote",
            "outbreak": "#ZombieCashOutbreak",
            "gallery": "#ZombieCashGallery",
            "ai": "#ZombieCashAI",
        }
        unique_tag = page_tags.get(key, "#ZombieCashSite")
        if unique_tag.lower() not in text.lower():
            text = text.rstrip() + "\n" + unique_tag
        text = text[:850]  # Leave room for HTML markup and Telegram's 1024-character caption limit.
        caption = "🛰️ <b>ZOMBIECASH SITE SPOTLIGHT</b>\n\n" + html.escape(text)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 EXPLORE THIS PAGE", url=url)],
            [InlineKeyboardButton("☣️ OPEN ZOMBIECASH HQ", url=HOME)]])
        sent = False
        try:
            photo = await asyncio.to_thread(_screenshot, url)
            photo.name = "zombcash-site-" + key + ".jpg"
            await context.bot.send_photo(chat_id="@ZOMBCASH", photo=photo, caption=caption,
                                         parse_mode="HTML", reply_markup=keyboard)
            sent = True
            logging.info("Website spotlight with screenshot published: %s", key)
        except Exception:
            logging.exception("Screenshot failed; trying a text post")
            try:
                await context.bot.send_message(chat_id="@ZOMBCASH", text=caption,
                    parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False)
                sent = True
            except Exception:
                logging.exception("Website spotlight text fallback failed")
        if sent:
            state.update({"last_page_key":key, "last_successful_post":time.time()})
            try:
                _save(state)
            except OSError:
                logging.exception("Could not save website spotlight state")
        context.job_queue.run_once(publish, when=INTERVAL if sent else 900, name="zombcash_website_showcase")

    state = _load()
    last = state.get("last_successful_post")
    delay = max(60, INTERVAL - max(0, time.time() - last)) if isinstance(last, (int, float)) else INTERVAL
    application.job_queue.run_once(publish, when=delay, name="zombcash_website_showcase")
    logging.info("Separate website spotlight scheduled in %.0f seconds", delay)
