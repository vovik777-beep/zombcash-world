import json
import os
import tempfile
import unittest
from pathlib import Path

from bot.scene_history import choose_fresh_scene


class SceneHistoryTests(unittest.TestCase):
    def test_avoids_recent_scene_for_same_style(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            Path(path).write_text(json.dumps({"HORROR": ["a"]}), encoding="utf-8")
            chosen = choose_fresh_scene(
                "HORROR", ["a", "b", "c"], history_file=path,
                chooser=lambda options: options[0],
            )
            self.assertEqual(chosen, "b")

    def test_history_is_isolated_by_style(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            Path(path).write_text(json.dumps({"HORROR": ["a"]}), encoding="utf-8")
            chosen = choose_fresh_scene(
                "CARTOON", ["a", "b"], history_file=path,
                chooser=lambda options: options[0],
            )
            self.assertEqual(chosen, "a")

    def test_persists_utf8_and_trims_history_to_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            for scene in ["один", "два", "три"]:
                choose_fresh_scene(
                    "PHOTO_PINUP", [scene], history_file=path,
                    recent_limit=2, chooser=lambda options: options[0],
                )
            state = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(state["PHOTO_PINUP"], ["два", "три"])

    def test_cycles_when_all_scenes_are_recent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            Path(path).write_text(json.dumps({"HORROR": ["a", "b"]}), encoding="utf-8")
            chosen = choose_fresh_scene(
                "HORROR", ["a", "b"], history_file=path,
                chooser=lambda options: options[-1],
            )
            self.assertEqual(chosen, "b")

    def test_recovers_from_malformed_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            Path(path).write_text("{broken", encoding="utf-8")
            chosen = choose_fresh_scene(
                "HORROR", ["scene"], history_file=path,
                chooser=lambda options: options[0],
            )
            self.assertEqual(chosen, "scene")
            self.assertEqual(json.loads(Path(path).read_text())["HORROR"], ["scene"])

    def test_rejects_empty_scene_list(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            choose_fresh_scene("HORROR", [])

    def test_does_not_leave_temporary_file_after_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.json")
            choose_fresh_scene(
                "HORROR", ["scene"], history_file=path,
                chooser=lambda options: options[0],
            )
            self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
