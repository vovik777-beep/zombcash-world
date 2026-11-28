import unittest
from unittest.mock import patch

import moltbook_agent as agent

from moltbook_agent import (
    CONTRACT,
    build_contextual_comment,
    build_reply_to_comment,
    extract_comments,
    extract_posts,
    maybe_reply_to_our_posts,
)


class MoltbookEngagementTests(unittest.TestCase):
    def test_unrelated_post_is_not_commented_on(self):
        self.assertIsNone(
            build_contextual_comment(
                "A quiet morning in the mountains",
                "A long description of a hiking route and the weather in the valley.",
            )
        )

    def test_crypto_comment_is_topic_specific_and_not_promotional(self):
        comment = build_contextual_comment(
            "Liquidity is an exit condition, not a badge",
            "Pool depth and price impact matter more than the headline liquidity figure.",
        )
        self.assertIsNotNone(comment)
        self.assertIn("executable liquidity", comment)
        self.assertNotIn("zombcash.github.io", comment.lower())
        self.assertNotIn("t.me/", comment.lower())

    def test_short_or_empty_post_is_rejected(self):
        self.assertIsNone(build_contextual_comment("Solana token", "Too short"))

    def test_mint_question_gets_verified_contract_context(self):
        reply = build_reply_to_comment("A token ticker is not an identity", "What is the mint address?")
        self.assertIn(CONTRACT, reply)
        self.assertIn("explorer", reply.lower())

    def test_price_question_does_not_promise_returns(self):
        reply = build_reply_to_comment("ZombieCash on Solana", "Will this make profit?")
        self.assertIn("don't promise", reply.lower())
        self.assertNotIn("guaranteed", reply.lower())

    def test_website_question_gets_project_hub(self):
        reply = build_reply_to_comment("Community links", "Where can I find the website?")
        self.assertIn("https://zombcash.github.io/", reply)

    def test_simple_praise_does_not_trigger_automatic_reply(self):
        self.assertIsNone(build_reply_to_comment("Community links", "Great project, love it!"))

    def test_post_title_alone_does_not_trigger_promotional_link(self):
        self.assertIsNone(build_reply_to_comment("Community links", "Nice work, thanks for sharing."))

    def test_search_results_are_extracted(self):
        posts = extract_posts({"results": [{"id": "post-1"}]})
        self.assertEqual([{"id": "post-1"}], posts)

    def test_comments_are_extracted(self):
        comments = extract_comments({"data": {"comments": [{"id": "comment-1"}]}})
        self.assertEqual([{"id": "comment-1"}], comments)


    def test_reply_handler_respects_global_comment_cooldown(self):
        state = {"last_comment_epoch": 500}
        home = {"activity_on_your_posts": [{"post_id": "post-1"}]}
        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "COMMENT_INTERVAL", 7200), \
             patch.object(agent.time, "time", return_value=1000), \
             patch.object(agent, "api") as api_call:
            maybe_reply_to_our_posts(state, home)
        api_call.assert_not_called()

    def test_reply_handler_obeys_disabled_commenting_switch(self):
        state = {}
        home = {"activity_on_your_posts": [{"post_id": "post-1"}]}
        with patch.object(agent, "COMMENTING_ENABLED", False), \
             patch.object(agent, "api") as api_call:
            maybe_reply_to_our_posts(state, home)
        api_call.assert_not_called()

    def test_deliberately_skipped_comment_is_persisted_before_reading_notifications(self):
        state = {}
        home = {"activity_on_your_posts": [{"post_id": "post-1", "post_title": "Community links"}]}
        comments_response = {
            "comments": [{
                "id": "comment-skip-1",
                "author": {"name": "real_member"},
                "content": "Great project, love it!",
            }]
        }
        def fake_api(method, path, payload=None):
            if method == "GET":
                return comments_response
            return {"success": True}
        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "MAX_COMMENTS_PER_DAY", 2), \
             patch.object(agent, "COMMENT_INTERVAL", 7200), \
             patch.object(agent, "api", side_effect=fake_api), \
             patch.object(agent, "write_state") as save_state, \
             patch.object(agent, "notify_telegram_activity"):
            maybe_reply_to_our_posts(state, home)
        self.assertIn("comment-skip-1", state["handled_comment_ids"])
        self.assertGreaterEqual(save_state.call_count, 1)

    def test_reply_handler_publishes_at_most_one_reply_per_cycle(self):
        state = {}
        home = {"activity_on_your_posts": [{"post_id": "post-1", "post_title": "Community links"}]}
        comments_response = {
            "comments": [
                {"id": "comment-1", "author": {"name": "member_one"},
                 "content": "Could you explain this exact feature and how it works for new users?"},
                {"id": "comment-2", "author": {"name": "member_two"},
                 "content": "Could you explain this other feature and how it works for new users?"},
            ]
        }
        calls = []
        def fake_api(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                return comments_response
            return {"success": True, "comment": {"id": "reply-1"}}
        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "MAX_COMMENTS_PER_DAY", 2), \
             patch.object(agent, "COMMENT_INTERVAL", 7200), \
             patch.object(agent, "api", side_effect=fake_api), \
             patch.object(agent, "write_state"), \
             patch.object(agent, "notify_telegram_activity"):
            maybe_reply_to_our_posts(state, home)
        published = [call for call in calls if call[0] == "POST" and "/comments" in call[1]]
        self.assertEqual(1, len(published))
        self.assertIn("comment-1", state["handled_comment_ids"])
        self.assertNotIn("comment-2", state["handled_comment_ids"])




    def test_reply_handler_stops_after_one_reply_across_multiple_posts(self):
        state = {}
        home = {
            "activity_on_your_posts": [
                {"post_id": "post-1", "post_title": "Community links"},
                {"post_id": "post-2", "post_title": "Zombie Lab"},
            ]
        }
        comments_response = {
            "comments": [{
                "id": "comment-1",
                "author": {"name": "member_one"},
                "content": "Could you explain this exact feature and how it works for new users?",
            }]
        }
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET":
                return comments_response
            return {"success": True, "comment": {"id": "reply-1"}}

        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "MAX_COMMENTS_PER_DAY", 2), \
             patch.object(agent, "COMMENT_INTERVAL", 7200), \
             patch.object(agent, "api", side_effect=fake_api), \
             patch.object(agent, "write_state"), \
             patch.object(agent, "notify_telegram_activity"):
            maybe_reply_to_our_posts(state, home)

        published = [call for call in calls if call[0] == "POST" and "/comments" in call[1]]
        fetched_posts = [call[1] for call in calls if call[0] == "GET"]
        self.assertEqual(1, len(published))
        self.assertEqual(1, len(fetched_posts))
        self.assertIn("/posts/post-1/comments", fetched_posts[0])

    def test_legacy_replied_ids_are_migrated_without_duplicate_reply(self):
        state = {"replied_comment_ids": ["comment-old"]}
        home = {"activity_on_your_posts": [{"post_id": "post-1", "post_title": "Community links"}]}
        def fake_api(method, path, payload=None):
            if method == "GET":
                return {"comments": [{
                    "id": "comment-old",
                    "author": {"name": "real_member"},
                    "content": "Where can I find the website?",
                }]}
            return {"success": True}
        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "MAX_COMMENTS_PER_DAY", 2), \
             patch.object(agent, "COMMENT_INTERVAL", 7200), \
             patch.object(agent, "api", side_effect=fake_api) as api_call, \
             patch.object(agent, "write_state"), \
             patch.object(agent, "notify_telegram_activity"):
            maybe_reply_to_our_posts(state, home)
        self.assertFalse(any(call.args[0] == "POST" and "/comments" in call.args[1]
                             for call in api_call.call_args_list))
        self.assertIn("comment-old", state["handled_comment_ids"])

    def test_daily_comment_cap_blocks_reply_processing(self):
        from datetime import datetime, timezone
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = {"comment_counts": {day: 2}}
        home = {"activity_on_your_posts": [{"post_id": "post-1"}]}
        with patch.object(agent, "COMMENTING_ENABLED", True), \
             patch.object(agent, "MAX_COMMENTS_PER_DAY", 2), \
             patch.object(agent, "api") as api_call:
            maybe_reply_to_our_posts(state, home)
        api_call.assert_not_called()



if __name__ == "__main__":
    unittest.main()
