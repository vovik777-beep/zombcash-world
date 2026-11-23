import asyncio
import unittest
from unittest.mock import patch

from bot.image_prompt import SUPPORTED_STYLES, build_image_prompt
from bot.image_service import generate_zombiecash_image


class ImagePromptTests(unittest.TestCase):
    def test_all_original_styles_build_distinct_prompts(self):
        self.assertEqual(SUPPORTED_STYLES, {"HORROR", "CARTOON", "PHOTO_PINUP"})
        for style in SUPPORTED_STYLES:
            with self.subTest(style=style):
                prompt, scene = build_image_prompt(style)
                self.assertTrue(prompt.startswith("Create a completely new original ZombieCash"))
                self.assertTrue(scene)
                self.assertIn("Scene: " + scene, prompt)
                self.assertIn("ZombieCash branding", prompt)

    def test_photo_pinup_keeps_adult_and_profile_constraints(self):
        prompt, scene = build_image_prompt("PHOTO_PINUP")
        self.assertTrue(scene)
        self.assertIn("heroine is 25+", prompt)
        self.assertIn("natural LEFT OR RIGHT SIDE PROFILE", prompt)
        self.assertIn("opaque intimate panels", prompt)

    def test_horror_keeps_live_action_and_no_children_constraint(self):
        prompt, scene = build_image_prompt("HORROR")
        self.assertTrue(scene)
        self.assertIn("maximum photorealism", prompt)
        self.assertIn("No nudity, sexual violence, children", prompt)

    def test_cartoon_keeps_comedy_visual_direction(self):
        prompt, scene = build_image_prompt("CARTOON")
        self.assertTrue(scene)
        self.assertIn("beautiful premium animated comedy art", prompt)
        self.assertIn("one instantly readable visual gag", prompt)

    def test_unknown_style_rejected(self):
        with self.assertRaises(ValueError):
            build_image_prompt("UNKNOWN")


class ImageServiceTests(unittest.TestCase):
    def test_returns_bytesio_with_scene_and_filename(self):
        async def fake_client(prompt, key):
            self.assertIn("Create a completely new original ZombieCash", prompt)
            self.assertEqual(key, "test-key")
            return b"jpeg-bytes", 123

        image = asyncio.run(generate_zombiecash_image(
            "HORROR", api_key="test-key",
            memory_guard=lambda: None,
            image_client=fake_client,
        ))
        self.assertEqual(image.getvalue(), b"jpeg-bytes")
        self.assertEqual(image.name, "zombcash_meme.jpg")
        self.assertTrue(image.zombcash_scene)
        image.close()

    def test_memory_guard_runs_before_provider(self):
        called = []
        async def fake_client(prompt, key):
            called.append(True)
            return b"image", 1

        def blocked():
            raise RuntimeError("memory guard blocked")

        with self.assertRaisesRegex(RuntimeError, "memory guard blocked"):
            asyncio.run(generate_zombiecash_image(
                "HORROR", api_key="test-key",
                memory_guard=blocked, image_client=fake_client,
            ))
        self.assertEqual(called, [])

    def test_missing_api_key_rejected(self):
        with patch.dict("os.environ", {"POLLINATIONS_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "POLLINATIONS_API_KEY"):
                asyncio.run(generate_zombiecash_image("HORROR", api_key=""))


if __name__ == "__main__":
    unittest.main()
