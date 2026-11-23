import unittest

from bot.auto_post_content import (
    STYLE_TAGS,
    build_bluesky_post_text,
    build_caption_prompt,
    build_telegram_caption,
    caption_instruction,
)


class AutoPostContentTests(unittest.TestCase):
    def test_each_style_has_distinct_caption_voice(self):
        self.assertIn("real punchline", caption_instruction("CARTOON"))
        self.assertIn("adult gothic undead heroine", caption_instruction("PHOTO_PINUP"))
        self.assertIn("classified outbreak incident", caption_instruction("HORROR"))

    def test_caption_prompt_preserves_scene_and_safety_constraints(self):
        prompt = build_caption_prompt("HORROR", "a radio still transmitting underground")
        self.assertIn("exact image scene is: a radio still transmitting underground", prompt)
        self.assertIn("Do not invent price, volume, holders", prompt)
        self.assertIn("Language: English", prompt)

    def test_telegram_caption_keeps_links_and_escapes_user_data(self):
        caption = build_telegram_caption(
            "CARTOON", '<dead & funny>',
            contract_address="CA<&>",
            x_url='https://x.com/a?x="1"',
            telegram_url="https://t.me/ZOMBCASH",
        )
        self.assertIn("&lt;dead &amp; funny&gt;", caption)
        self.assertIn("CA: <code>CA&lt;&amp;&gt;</code>", caption)
        self.assertIn("X Community", caption)
        self.assertIn("Telegram", caption)
        self.assertIn("#ZombieCashMeme", caption)
        self.assertLessEqual(len(caption), 1024)

    def test_telegram_caption_uses_fallback_for_empty_ai_text(self):
        caption = build_telegram_caption("HORROR", " ")
        self.assertIn("The dead market has a new nightmare.", caption)

    def test_bluesky_preserves_full_caption_and_removes_tags_first(self):
        caption = "A complete generated caption that must never be truncated."
        result = build_bluesky_post_text(
            "PHOTO_PINUP", caption, text_fits=lambda text: len(text) <= 130
        )
        self.assertIn(caption, result)
        self.assertIn("https://zombcash.github.io/", result)
        self.assertNotIn(STYLE_TAGS["PHOTO_PINUP"], result)

    def test_bluesky_skips_if_caption_itself_cannot_fit(self):
        with self.assertRaisesRegex(ValueError, "full caption preserved"):
            build_bluesky_post_text(
                "HORROR", "x" * 500, text_fits=lambda text: len(text) <= 100
            )

    def test_rejects_unknown_style(self):
        with self.assertRaises(ValueError):
            build_caption_prompt("UNKNOWN", "scene")


if __name__ == "__main__":
    unittest.main()
