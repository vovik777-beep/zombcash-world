import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.publisher_runtime import register_publishing_schedules


class FakeJob:
    def __init__(self, callback, when, data, name):
        self.callback = callback
        self.when = when
        self.data = data
        self.name = name

    def schedule_removal(self):
        pass


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [job for job in self.jobs if job.name == name]

    def run_once(self, callback, *, when, data=None, name):
        job = FakeJob(callback, when, data, name)
        self.jobs.append(job)
        return job


class FakeApplication:
    def __init__(self):
        self.bot_data = {}
        self.job_queue = FakeJobQueue()
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


class RestartRecoveryTests(unittest.TestCase):
    def test_restart_restores_remaining_hourly_and_site_delays(self):
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hourly_state = root / "auto_post_state.json"
            website_state = root / "site_promo_state.json"

            hourly_state.write_text(json.dumps({
                "last_successful_rotation_post": now - 20 * 60,
                "next_rotation_index": 2,
                "unrelated_existing_key": "preserve-me",
            }), encoding="utf-8")
            website_state.write_text(json.dumps({
                "last_successful_post": now - 60 * 60,
                "last_page_key": "arcade",
            }), encoding="utf-8")

            app = FakeApplication()
            with patch("bot.hourly_publisher.STATE_FILE", str(hourly_state)), \
                 patch("bot.hourly_publisher.time.time", return_value=now), \
                 patch("bot.website_showcase.STATE", str(website_state)), \
                 patch("bot.website_showcase.time.time", return_value=now):
                register_publishing_schedules(app, object(), lambda update: True)

            jobs = {job.name: job for job in app.job_queue.jobs}
            self.assertEqual(
                jobs["zombcash_auto_post_rotation"].when,
                40 * 60,
            )
            self.assertEqual(
                jobs["zombcash_website_showcase"].when,
                2 * 60 * 60,
            )
            self.assertEqual(len(app.handlers), 1)
            self.assertEqual(app.bot_data["auto_post_retry_attempt"] if "auto_post_retry_attempt" in app.bot_data else 0, 0)

            persisted = json.loads(hourly_state.read_text(encoding="utf-8"))
            self.assertEqual(persisted["next_rotation_index"], 2)
            self.assertEqual(persisted["unrelated_existing_key"], "preserve-me")

    def test_missing_state_initializes_without_immediate_publication(self):
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as directory:
            hourly_state = Path(directory) / "missing_hourly.json"
            website_state = Path(directory) / "missing_site.json"
            app = FakeApplication()
            with patch("bot.hourly_publisher.STATE_FILE", str(hourly_state)), \
                 patch("bot.hourly_publisher.time.time", return_value=now), \
                 patch("bot.website_showcase.STATE", str(website_state)), \
                 patch("bot.website_showcase.time.time", return_value=now):
                register_publishing_schedules(app, object(), lambda update: True)

            jobs = {job.name: job for job in app.job_queue.jobs}
            self.assertEqual(jobs["zombcash_auto_post_rotation"].when, 3600)
            self.assertEqual(jobs["zombcash_website_showcase"].when, 10800)
            self.assertFalse(app.bot_data.get("publisher_started", False))


if __name__ == "__main__":
    unittest.main()
