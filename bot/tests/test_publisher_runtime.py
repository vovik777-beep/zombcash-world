import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.publisher_runtime import register_publishing_schedules


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return []

    def run_once(self, callback, *, when, data=None, name):
        self.jobs.append({"callback": callback, "when": when, "data": data, "name": name})


class PublishingRuntimeTests(unittest.TestCase):
    def test_registers_hourly_and_independent_website_schedule(self):
        queue = FakeJobQueue()
        application = SimpleNamespace(bot_data={}, job_queue=queue, add_handler=lambda handler: None)
        gemini_client = object()
        is_admin = lambda update: True
        with patch("bot.publisher_runtime.register_hourly_publisher") as hourly, \
             patch("bot.publisher_runtime.register_website_showcase") as website:
            register_publishing_schedules(application, gemini_client, is_admin)
        self.assertIs(application.bot_data["gemini_client"], gemini_client)
        hourly.assert_called_once_with(application)
        self.assertIs(application.bot_data["is_admin"], is_admin)
        website.assert_called_once()
        self.assertEqual(website.call_args.args[0], application)
        self.assertTrue(callable(website.call_args.args[1]))

    def test_host_can_keep_its_existing_admin_command_without_duplicate_registration(self):
        queue = FakeJobQueue()
        handlers = []
        application = SimpleNamespace(bot_data={}, job_queue=queue, add_handler=handlers.append)
        with patch("bot.publisher_runtime.register_hourly_publisher"), patch("bot.publisher_runtime.register_website_showcase"):
            register_publishing_schedules(
                application, object(), register_admin_command=False
            )
        self.assertEqual(handlers, [])
        self.assertNotIn("is_admin", application.bot_data)

    def test_missing_gemini_client_fails_closed(self):
        application = SimpleNamespace(bot_data={}, job_queue=FakeJobQueue(), add_handler=lambda handler: None)
        with self.assertRaisesRegex(ValueError, "gemini_client"):
            register_publishing_schedules(application, None, lambda update: True)
        with self.assertRaisesRegex(ValueError, "is_admin_fn"):
            register_publishing_schedules(application, object(), None)


if __name__ == "__main__":
    unittest.main()
