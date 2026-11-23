import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from bot.image_runtime import (
    container_memory,
    ensure_image_generation_memory,
    normalize_generated_image,
)


class ImageRuntimeTests(unittest.TestCase):
    def test_memory_guard_allows_safe_headroom(self):
        ensure_image_generation_memory(memory_reader=lambda: (100, 2_000_000_000))

    def test_memory_guard_rejects_high_usage(self):
        with self.assertRaisesRegex(RuntimeError, "memory guard"):
            ensure_image_generation_memory(memory_reader=lambda: (1_500, 2_000))

    def test_memory_guard_rejects_insufficient_headroom(self):
        with self.assertRaisesRegex(RuntimeError, "memory guard"):
            ensure_image_generation_memory(
                memory_reader=lambda: (100, 700),
                min_headroom_bytes=601,
            )

    def test_memory_guard_rejects_missing_metrics(self):
        with self.assertRaisesRegex(RuntimeError, "metrics unavailable"):
            ensure_image_generation_memory(memory_reader=lambda: (None, None))

    def test_container_memory_reads_cgroup_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current"
            limit = Path(folder) / "limit"
            current.write_text("1234", encoding="ascii")
            limit.write_text("5678", encoding="ascii")
            self.assertEqual(
                container_memory(((str(current), str(limit)),)),
                (1234, 5678),
            )

    def test_container_memory_skips_unlimited_cgroup(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / "current"
            limit = Path(folder) / "limit"
            current.write_text("1234", encoding="ascii")
            limit.write_text("max", encoding="ascii")
            self.assertEqual(
                container_memory(((str(current), str(limit)),)),
                (None, None),
            )

    def test_normalizer_outputs_bounded_jpeg(self):
        source = io.BytesIO()
        Image.new("RGB", (1600, 900), (30, 40, 50)).save(source, format="PNG")
        result = normalize_generated_image(source.getvalue())
        with Image.open(io.BytesIO(result)) as normalized:
            self.assertEqual(normalized.format, "JPEG")
            self.assertEqual(normalized.size, (1024, 576))
        self.assertLessEqual(len(result), 2_000_000)

    def test_normalizer_rejects_oversized_dimensions(self):
        source = io.BytesIO()
        Image.new("RGB", (2000, 1300)).save(source, format="PNG")
        with self.assertRaisesRegex(ValueError, "maximum 2.5 MP"):
            normalize_generated_image(source.getvalue())


if __name__ == "__main__":
    unittest.main()
