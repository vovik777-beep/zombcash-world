import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assignment_value(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


class PublishingCadenceTests(unittest.TestCase):
    def test_editorial_rotation_remains_hourly(self):
        self.assertEqual(
            assignment_value(ROOT / "auto_post_schedule.py", "AUTO_POST_INTERVAL_SECONDS"),
            60 * 60,
        )

    def test_website_promotion_remains_every_three_hours(self):
        source = (ROOT / "website_showcase.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        interval = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == "INTERVAL" for target in node.targets):
                    # Keep the production default explicit in the environment fallback.
                    call = node.value
                    self.assertIsInstance(call, ast.Call)
                    self.assertEqual(ast.literal_eval(call.args[1]), "10800")
                    interval = True
                    break
        self.assertTrue(interval, "website showcase interval definition not found")

    def test_news_worker_has_independent_ten_minute_polling(self):
        source = (ROOT / "news_worker.py").read_text(encoding="utf-8")
        self.assertIn("NEWS_POLL_INTERVAL_SECONDS", source)
        self.assertIn("600", source)


if __name__ == "__main__":
    unittest.main()
