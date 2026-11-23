"""Pure content composition for the ZombieCash hourly publishing rotation.

These helpers preserve the current private bot's English captions, three editorial
voices, Telegram footer, and Bluesky cross-post structure without network access.
"""
from __future__ import annotations

import html
from typing import Callable


WEBSITE_URL = "https://zombcash.github.io/"
TRACKED_WEBSITE_URL = (
    "https://zombcash.github.io/?utm_source=telegram&utm_medium=bot&utm_campaign=organic"
)

STYLE_HEADINGS = {
    "HORROR": "🩸 <b>NIGHTMARE & HORROR</b>",
    "CARTOON": "😂 <b>ZOMBIECASH MEMES</b>",
    "PHOTO_PINUP": "🌙 <b>UNDEAD AFTER DARK</b>",
}
STYLE_TAGS = {
    "HORROR": "#ZombieCashHorror",
    "CARTOON": "#ZombieCashMeme",
    "PHOTO_PINUP": "#ZombieCashAfterDark",
}
STYLE_NAMES = {
    "HORROR": "Nightmare & Horror",
    "CARTOON": "ZombieCash Memes",
    "PHOTO_PINUP": "Undead After Dark",
}


def caption_instruction(style: str) -> str:
    """Return the existing style-specific caption direction verbatim."""
    if style == "CARTOON":
        return (
            "Write a genuinely funny, sharp ZombieCash meme caption with a real punchline, not a slogan. "
            "Use the exact visual scene as the setup. Rotate joke engines aggressively: witty dialogue, petty gothic drama, "
            "absurd luxury problems during the apocalypse, deadpan confession, mock corporate language, fake royal decrees, "
            "rom-com misunderstanding, skeleton bureaucracy, visual misdirection, overconfident villain failure, cute-creepy contrast, "
            "crypto-trader pain, unexpected reversal and perfectly timed understatement. Give recurring character types distinct voices. "
            "Make the line elegant, clever, short and genuinely funny; a punchline, not a slogan. Never explain the joke or reuse a recent setup. "
            "Avoid generic 'market is dead' wording."
        )
    if style == "PHOTO_PINUP":
        return (
            "Write a witty, confident caption grounded in the exact scene and personality of this adult gothic undead heroine. "
            "Give her a distinct voice: seductive aristocrat, sarcastic vampire, chaotic kunoichi, dramatic valkyrie, spoiled zombie princess, "
            "or deadpan goth girl. Use clever dark comedy, playful menace, a tasteful cheeky double meaning, mock-romantic drama, "
            "or an unexpected apocalypse inconvenience. The joke must be about her action or the scene, not just her looks. "
            "Avoid generic beauty compliments, generic flirting, and recycled catchphrases. One memorable punchline only."
        )
    if style == "HORROR":
        return (
            "Write a short, unsettling horror caption tied to the most disturbing concrete detail in the exact scene. "
            "Rotate formats: classified outbreak incident, survivor's last transmission, forensic note, quarantine warning, "
            "ominous discovery, or a brutal dark-humor observation. Make it vivid, specific and memorable; imply a story beyond the frame. "
            "Avoid generic horror clichés and vague 'the dead are rising' language. One or two short sentences."
        )
    raise ValueError(f"Unsupported editorial style: {style}")


def build_caption_prompt(style: str, scene_description: str) -> str:
    """Compose the exact English caption-generation task used by the live publisher."""
    if style not in STYLE_HEADINGS:
        raise ValueError(f"Unsupported editorial style: {style}")
    instruction = caption_instruction(style)
    return (
        "Create ONLY the short main caption text for a ZombieCash image post. "
        "Language: English. Vary wording every time. Image style: " + style + ". "
        "The exact image scene is: " + scene_description + ". "
        + instruction + " "
        "Do NOT include the token name/ticker, slogan, contract address, links, hashtags, heading, or footer. "
        "Return only the caption, without explanation or quotation marks. "
        "Do not invent price, volume, holders, partnerships, profits, purchases, or market results."
    )


def build_telegram_caption(
    style: str,
    answer: str,
    *,
    contract_address: str = "",
    x_url: str = "",
    telegram_url: str = "",
    website_url: str = TRACKED_WEBSITE_URL,
) -> str:
    """Build the current HTML Telegram channel caption, capped at 1024 characters."""
    if style not in STYLE_HEADINGS:
        raise ValueError(f"Unsupported editorial style: {style}")
    footer_lines = [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "🧟 <b>$ZOMBCASH</b>  |  MARKET DIED. ZOMBIECASH DIDN'T.",
    ]
    if contract_address:
        footer_lines.append(f"📋 CA: <code>{html.escape(contract_address)}</code>")
    footer_lines.append(
        f'🌐 Website: <a href="{html.escape(website_url, quote=True)}">zombcash.github.io</a>'
    )
    if x_url:
        footer_lines.append(f'𝕏 <a href="{html.escape(x_url, quote=True)}">X Community</a>')
    if telegram_url:
        footer_lines.append(f'💬 <a href="{html.escape(telegram_url, quote=True)}">Telegram</a>')
    safe_caption = html.escape((answer or "").strip()) or "The dead market has a new nightmare."
    return (
        STYLE_HEADINGS[style] + "\n\n" + safe_caption + "\n\n"
        + STYLE_TAGS[style] + "\n" + "\n".join(footer_lines)
    )[:1024]


def build_bluesky_post_text(
    style: str,
    answer: str,
    *,
    contract_address: str = "",
    text_fits: Callable[[str], bool],
) -> str:
    """Build the current Bluesky text, removing tags only when needed; never truncate caption."""
    if style not in STYLE_TAGS:
        raise ValueError(f"Unsupported editorial style: {style}")
    caption = (answer or "The survivors are still broadcasting.").strip()
    tags = ["#ZombieCash", "#Solana", "#Memecoin", "#Crypto", STYLE_TAGS[style]]
    parts = [
        caption,
        "$ZOMBCASH | Solana meme community",
        " ".join(tags),
    ]
    if contract_address:
        parts.append("CA: " + contract_address)
    parts.append(WEBSITE_URL)
    result = "\n\n".join(parts)
    while tags and not text_fits(result):
        tags.pop()
        if tags:
            parts[2] = " ".join(tags)
        else:
            parts.pop(2)
        result = "\n\n".join(parts)
    if not text_fits(result):
        raise ValueError(
            "Bluesky post metadata and full caption exceed the platform limit "
            "even after removing all hashtags; full caption preserved, post skipped."
        )
    return result
