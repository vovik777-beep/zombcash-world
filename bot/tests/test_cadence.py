import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assignment_node(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
    raise AssertionError(f"{name} not found in {path.name}")


class PublishingCadenceTests(unittest.TestCase):
    def test_editorial_rotation_remains_hourly(self):
        expression = ast.unparse(
            assignment_node(ROOT / "auto_post_schedule.py", "AUTO_POST_INTERVAL_SECONDS")
        )
        self.assertEqual(expression.replace(" ", ""), "60*60")

    def test_website_promotion_remains_every_three_hours(self):
        expression = assignment_node(ROOT / "website_showcase.py", "INTERVAL")
        self.assertIsInstance(expression, ast.Call)
        self.assertEqual(expression.func.id, "max")
        fallback = expression.args[1]
        self.assertIsInstance(fallback, ast.Call)
        self.assertEqual(fallback.func.id, "int")
        getenv = fallback.args[0]
        self.assertIsInstance(getenv, ast.Call)
        self.assertEqual(getenv.func.attr, "getenv")
        self.assertEqual(ast.literal_eval(getenv.args[1]), "10800")

    def test_news_worker_polls_every_ten_minutes(self):
        source = (ROOT / "news_worker.py").read_text(encoding="utf-8")
        self.assertIn("Poll every 10 minutes", source)
        self.assertIn("await asyncio.sleep(600)", source)


if __name__ == "__main__":
    unittest.main()
