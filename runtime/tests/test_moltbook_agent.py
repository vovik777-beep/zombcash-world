import unittest

from moltbook_agent import (
    CONTRACT,
    build_contextual_comment,
    build_reply_to_comment,
    extract_comments,
    extract_posts,
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


if __name__ == "__main__":
    unittest.main()
