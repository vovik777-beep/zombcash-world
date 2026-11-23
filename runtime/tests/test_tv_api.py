import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from tv_api import _safe_video_path, _valid_id, parse_range_header, _is_admin, _is_mp4_header, register_tv_routes, _rate_events


class TvApiTests(unittest.TestCase):
    def test_mp4_container_signature(self):
        self.assertTrue(_is_mp4_header(bytes([0, 0, 0, 24]) + b"ftypisom" + bytes(4)))
        self.assertFalse(_is_mp4_header(b"not an mp4 file"))

    def test_uuid_validation(self):
        self.assertTrue(_valid_id("123e4567-e89b-12d3-a456-426614174000"))
        self.assertFalse(_valid_id("../secret"))
        self.assertFalse(_valid_id("not-a-uuid"))

    def test_range_normal(self):
        self.assertEqual(parse_range_header("bytes=2-5", 10), (2, 5))

    def test_range_open_ended(self):
        self.assertEqual(parse_range_header("bytes=4-", 10), (4, 9))

    def test_range_suffix(self):
        self.assertEqual(parse_range_header("bytes=-3", 10), (7, 9))

    def test_range_end_is_clamped(self):
        self.assertEqual(parse_range_header("bytes=4-500", 10), (4, 9))

    def test_range_invalid_and_unsatisfiable(self):
        for value in ("bytes=10-", "bytes=-0", "bytes=8-2", "bytes=0-1,4-5", "items=0-1"):
            self.assertIsNone(parse_range_header(value, 10))

    def test_range_empty_file(self):
        self.assertIsNone(parse_range_header("bytes=0-", 0))

    def test_path_containment(self):
        with tempfile.TemporaryDirectory() as tmp:
            episode_id = "123e4567-e89b-12d3-a456-426614174000"
            safe = _safe_video_path(tmp, episode_id, "video.mp4")
            self.assertEqual(safe, Path(tmp).resolve() / "tv" / "episodes" / episode_id / "video.mp4")
            self.assertIsNone(_safe_video_path(tmp, episode_id, "../../secret"))
            self.assertIsNone(_safe_video_path(tmp, "../escape", "video.mp4"))

    def test_arbitrary_bearer_not_admin(self):
        request = Mock()
        request.headers = {"Authorization": "Bearer arbitrary.body.signature"}
        self.assertFalse(_is_admin(request, lambda _request: None))

    def test_only_allowlisted_session_is_admin(self):
        request = Mock()
        request.headers = {"Authorization": "Bearer signed.session"}
        os.environ["ZOMBCASH_ADMIN_IDS"] = "111,222"
        os.environ["ZOMBCASH_OWNER_ID"] = "999"
        try:
            self.assertTrue(_is_admin(request, lambda _request: "222"))
            self.assertTrue(_is_admin(request, lambda _request: "999"))
            self.assertFalse(_is_admin(request, lambda _request: "333"))
        finally:
            os.environ.pop("ZOMBCASH_ADMIN_IDS", None)
            os.environ.pop("ZOMBCASH_OWNER_ID", None)

    def test_json_admin_token_is_rejected_by_route_contract(self):
        # The route explicitly rejects admin_token in the submitted JSON object.
        self.assertIn('"admin_token" in payload', Path(__file__).parents[1].joinpath("tv_api.py").read_text(encoding="utf-8"))


class TvUploadRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = web.Application(client_max_size=251 * 1024 * 1024)
        os.environ["ZOMBCASH_ADMIN_IDS"] = "123456789"
        register_tv_routes(self.app, self.tmp.name, lambda _request: "123456789")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        _rate_events.clear()

    async def asyncTearDown(self):
        await self.client.close()
        os.environ.pop("ZOMBCASH_ADMIN_IDS", None)
        self.tmp.cleanup()

    async def test_admin_upload_publishes_episode_and_streams_ranges(self):
        data = FormData()
        data.add_field("title", "Raven test broadcast")
        data.add_field("description", "Test clip")
        data.add_field("duration_seconds", "4")
        data.add_field("channel", "meme")
        data.add_field("video", bytes([0, 0, 0, 24]) + b"ftypisom" + bytes(4) + b"test-video-bytes",
                       filename="anything.bin", content_type="video/mp4")
        response = await self.client.post("/api/tv/episodes/upload", data=data,
                                          headers={"Origin": "https://zombcash.github.io"})
        self.assertEqual(response.status, 201)
        published = await response.json()
        self.assertEqual(published["status"], "ready")
        self.assertEqual(published["title"], "Raven test broadcast")
        self.assertEqual(published["channel"], "meme")

        listing = await self.client.get("/api/tv/episodes")
        self.assertEqual(listing.status, 200)
        self.assertEqual((await listing.json())["total"], 1)
        self.assertEqual((await listing.json())["episodes"][0]["channel"], "meme")

        video = await self.client.get(published["video_url"], headers={"Range": "bytes=0-3"})
        self.assertEqual(video.status, 206)
        self.assertEqual(await video.read(), bytes([0, 0, 0, 24]))
        self.assertEqual(video.headers["Content-Range"].split("/")[0], "bytes 0-3")

    async def test_upload_rejects_non_admin_session(self):
        await self.client.close()
        self.app = web.Application(client_max_size=251 * 1024 * 1024)
        register_tv_routes(self.app, self.tmp.name, lambda _request: "333333")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        data = FormData()
        data.add_field("title", "Unauthorized")
        data.add_field("video", bytes([0, 0, 0, 24]) + b"ftypisom",
                       filename="x.mp4", content_type="video/mp4")
        response = await self.client.post("/api/tv/episodes/upload", data=data,
                                          headers={"Origin": "https://zombcash.github.io"})
        self.assertEqual(response.status, 403)

    async def test_upload_rejects_non_mp4(self):
        data = FormData()
        data.add_field("title", "Bad file")
        data.add_field("video", b"not an mp4 file", filename="x.mp4", content_type="video/mp4")
        response = await self.client.post("/api/tv/episodes/upload", data=data,
                                          headers={"Origin": "https://zombcash.github.io"})
        self.assertEqual(response.status, 415)


if __name__ == "__main__":
    unittest.main()
