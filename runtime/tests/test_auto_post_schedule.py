import unittest

from auto_post_schedule import (
    AUTO_POST_CATEGORIES,
    AUTO_POST_INTERVAL_SECONDS,
    normalize_rotation_index,
)


class AutoPostScheduleTests(unittest.TestCase):
    def test_editorial_rotation_is_hourly(self):
        self.assertEqual(AUTO_POST_INTERVAL_SECONDS, 60 * 60)

    def test_after_dark_gets_half_of_all_slots(self):
        names = [name for name, _style in AUTO_POST_CATEGORIES]
        self.assertEqual(names.count("Undead After Dark"), 2)
        self.assertEqual(names.count("ZombieCash Memes"), 1)
        self.assertEqual(names.count("Nightmare & Horror"), 1)

    def test_rotation_is_memes_afterdark_horror_afterdark(self):
        self.assertEqual(
            [name for name, _style in AUTO_POST_CATEGORIES],
            [
                "ZombieCash Memes",
                "Undead After Dark",
                "Nightmare & Horror",
                "Undead After Dark",
            ],
        )

    def test_persisted_index_wraps_for_new_four_slot_cycle(self):
        self.assertEqual(normalize_rotation_index(0), 0)
        self.assertEqual(normalize_rotation_index(3), 3)
        self.assertEqual(normalize_rotation_index(4), 0)
        self.assertEqual(normalize_rotation_index(7), 3)
        self.assertEqual(normalize_rotation_index("bad"), 0)


if __name__ == "__main__":
    unittest.main()
