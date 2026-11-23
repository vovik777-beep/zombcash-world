import io
import tempfile
import unittest

from bot.auto_post_state import AutoPostImageStore


class AutoPostImageStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AutoPostImageStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_and_reload_preserves_image_and_scene(self):
        image = io.BytesIO(b"image-bytes")
        image.name = "test.jpg"
        self.store.save(image, "CARTOON", "a survivor in a bunker")
        loaded = self.store.load("CARTOON")
        self.assertFalse(loaded["sent"])
        self.assertEqual(loaded["image"].getvalue(), b"image-bytes")
        self.assertEqual(loaded["image"].zombcash_scene, "a survivor in a bunker")
        self.assertEqual(loaded["image"].name, "test.jpg")

    def test_sending_marker_is_ambiguous_and_never_resent(self):
        self.store.save(io.BytesIO(b"image"), "HORROR", "scene")
        self.store.mark_sending("HORROR")
        loaded = self.store.load("HORROR")
        self.assertTrue(loaded["sent"])
        self.assertEqual(loaded["metadata"]["status"], "sending")

    def test_sending_marker_uses_one_atomic_replace_and_keeps_pending_on_failure(self):
        from pathlib import Path
        from unittest.mock import patch
        import os

        self.store.save(io.BytesIO(b"image"), "CARTOON", "scene")
        metadata_path, _ = self.store._paths("CARTOON")
        real_replace = os.replace
        calls = []

        def fail_final_replace(source, destination):
            calls.append((Path(source), Path(destination)))
            if Path(destination) == metadata_path:
                raise OSError("simulated atomic replace failure")
            return real_replace(source, destination)

        with patch("bot.auto_post_state.os.replace", side_effect=fail_final_replace):
            with self.assertRaisesRegex(OSError, "simulated atomic replace failure"):
                self.store.mark_sending("CARTOON")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], metadata_path)
        loaded = self.store.load("CARTOON")
        self.assertFalse(loaded["sent"])
        self.assertEqual(loaded["metadata"]["status"], "pending")

    def test_sent_marker_keeps_telegram_message_id(self):
        self.store.save(io.BytesIO(b"image"), "PHOTO_PINUP", "scene")
        self.store.mark_sending("PHOTO_PINUP")
        self.store.mark_sent("PHOTO_PINUP", 12345)
        loaded = self.store.load("PHOTO_PINUP")
        self.assertTrue(loaded["sent"])
        self.assertEqual(loaded["metadata"]["telegram_message_id"], 12345)

    def test_clear_removes_metadata_and_image(self):
        self.store.save(io.BytesIO(b"image"), "CARTOON", "scene")
        self.store.clear("CARTOON")
        self.assertIsNone(self.store.load("CARTOON"))

    def test_rejects_unknown_style(self):
        with self.assertRaises(ValueError):
            self.store.save(io.BytesIO(b"image"), "UNKNOWN", "scene")


if __name__ == "__main__":
    unittest.main()
