import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from experimental.backend.api.strm import pick_stream
from experimental.backend.models.user_profile import ContentRating
from experimental.backend.services import access_control
from experimental.backend.services.strm_service import MARKER_FILE, StrmService


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    async def get(self, key, default=None):
        return self.values.get(key, default)

    async def set(self, key, value):
        self.values[key] = value


class FakeTMDB:
    movie = {"title": "Heat: Redux", "release_date": "1995-12-15", "external_ids": {"imdb_id": "tt0113277"}}
    show = {
        "name": "Show",
        "first_air_date": "2020-01-01",
        "external_ids": {"imdb_id": "tt1"},
        "seasons": [{"season_number": 0}, {"season_number": 1}],
    }

    async def get_details(self, tmdb_id, media_type):
        return self.movie if media_type == "movie" else self.show

    async def get_season_details(self, tmdb_id, season):
        return {"episodes": [{"episode_number": 1, "name": "Pilot"}, {"episode_number": 2, "name": "Two/Three"}]}


class PickStreamTests(unittest.TestCase):
    def test_keeps_provider_order_instead_of_sorting_by_quality(self):
        streams = {
            "AIOStreams": [
                {"url": "https://a/1080p-atmos", "quality": "1080p"},
                {"url": "https://a/2160p", "quality": "2160p"},
            ]
        }
        self.assertEqual(pick_stream(streams), "https://a/1080p-atmos")
        self.assertEqual(pick_stream(streams, index=1), "https://a/2160p")

    def test_skips_streams_jellyfin_cannot_follow(self):
        streams = {
            "addon": [
                {"url": "magnet:?xt=urn:btih:abc"},
                {"url": "https://needs-headers", "headers": {"Referer": "x"}},
                {"url": "https://ok"},
            ]
        }
        self.assertEqual(pick_stream(streams), "https://ok")

    def test_index_past_the_end_uses_last_stream(self):
        self.assertEqual(pick_stream({"a": [{"url": "https://1"}, {"url": "https://2"}]}, 9), "https://2")
        self.assertIsNone(pick_stream({"a": []}))


class StrmServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = FakeSettings(
            jellyfin_movie_path=str(root / "movies"),
            jellyfin_tv_path=str(root / "tv"),
            jfresolve_public_url="http://jfr:8765/",
        )
        self.service = StrmService(self.settings, FakeTMDB())
        self.root = root

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_movie_gets_one_tokenised_strm(self):
        result = await self.service.generate(949, "movie")
        folder = self.root / "movies" / "Heat Redux (1995)"
        self.assertEqual(result["created"], 1)
        url = (folder / "Heat Redux (1995).strm").read_text()
        parsed = urlparse(url)
        self.assertEqual(parsed.path, "/api/strm/play/movie/949")
        self.assertEqual(parse_qs(parsed.query)["token"], [self.settings.values["strm_access_token"]])
        self.assertTrue((folder / MARKER_FILE).exists())

    async def test_tv_writes_episodes_skips_specials_and_is_idempotent(self):
        first = await self.service.generate(1, "tv")
        folder = self.root / "tv" / "Show (2020)"
        self.assertEqual(first["created"], 2)
        self.assertFalse((folder / "Season 00").exists())
        episode = folder / "Season 01" / "Show (2020) - S01E02 - TwoThree.strm"
        self.assertIn("season=1&episode=2", episode.read_text())
        second = await self.service.generate(1, "tv")
        self.assertEqual(second["created"], 0)

    async def test_remove_only_deletes_marked_folders(self):
        unmanaged = self.root / "movies" / "Mine (2001)"
        unmanaged.mkdir(parents=True)
        self.assertFalse(await self.service.remove("movie", "Mine", 2001))
        self.assertTrue(unmanaged.exists())

        await self.service.generate(949, "movie")
        self.assertTrue(await self.service.remove("movie", "Heat: Redux", 1995))
        self.assertFalse((self.root / "movies" / "Heat Redux (1995)").exists())

    async def test_movie_versions_become_jellyfin_options(self):
        self.settings.values["strm_movie_versions"] = 3
        result = await self.service.generate(949, "movie")
        folder = self.root / "movies" / "Heat Redux (1995)"
        self.assertEqual(result["created"], 3)
        names = sorted(p.name for p in folder.glob("*.strm"))
        self.assertEqual(names, [f"Heat Redux (1995) - Option {n}.strm" for n in (1, 2, 3)])
        query = parse_qs(urlparse((folder / "Heat Redux (1995) - Option 2.strm").read_text()).query)
        self.assertEqual(query["index"], ["1"])

        self.settings.values["strm_movie_versions"] = 1
        await self.service.generate(949, "movie")
        self.assertEqual([p.name for p in folder.glob("*.strm")], ["Heat Redux (1995).strm"])
        self.assertNotIn("index", parse_qs(urlparse((folder / "Heat Redux (1995).strm").read_text()).query))

    async def test_rewrite_applies_versions_to_existing_movies(self):
        await self.service.generate(949, "movie")
        (self.root / "movies" / "Mine (2001)").mkdir()
        self.settings.values["strm_movie_versions"] = 2
        result = await self.service.rewrite_all()
        self.assertEqual(result, {"rewritten": 1, "failed": []})
        self.assertEqual(len(list((self.root / "movies" / "Heat Redux (1995)").glob("*.strm"))), 2)
        self.assertEqual(list((self.root / "movies" / "Mine (2001)").iterdir()), [])

    async def test_rewrite_updates_episode_links_after_url_change(self):
        await self.service.generate(1, "tv")
        episode = self.root / "tv" / "Show (2020)" / "Season 01" / "Show (2020) - S01E01 - Pilot.strm"
        self.assertTrue(episode.read_text().startswith("http://jfr:8765/"))
        self.settings.values["jfresolve_public_url"] = "http://192.168.1.10:8765"
        result = await self.service.rewrite_all()
        self.assertEqual(result, {"rewritten": 1, "failed": []})
        self.assertTrue(episode.read_text().startswith("http://192.168.1.10:8765/api/strm/play/tv/1?"))

    async def test_missing_library_path_is_a_clear_error(self):
        self.settings.values.pop("jellyfin_movie_path")
        with self.assertRaisesRegex(ValueError, "jellyfin_movie_path"):
            await self.service.generate(949, "movie")


class AgeGateTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, limit, rating, media_type="movie"):
        with patch.object(access_control, "get_rating_limit", AsyncMock(return_value=limit)):
            await access_control.ensure_item_allowed(None, SimpleNamespace(), {"media_type": media_type, "content_rating": rating})

    async def test_restricted_user_is_blocked_above_their_rating(self):
        await self.check(ContentRating.TEEN, "PG-13")
        await self.check(ContentRating.TEEN, "TV-14", "tv")
        for rating in ("R", None):
            with self.assertRaises(HTTPException) as ctx:
                await self.check(ContentRating.TEEN, rating)
            self.assertEqual(ctx.exception.status_code, 403)

    async def test_unrestricted_user_sees_everything(self):
        await self.check(None, "NC-17")
        await self.check(None, None)


if __name__ == "__main__":
    unittest.main()
