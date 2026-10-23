"""Offline tests: Pexels and Pixabay are mocked (no keys or internet needed).

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fetch  # noqa: E402
from broll import http as bhttp  # noqa: E402
from broll.keywords import extract_keywords, simplify_queries  # noqa: E402


# ----------------------------------------------------------------- fake API responses
def pexels_video(vid, duration, w=1920, h=1080, with_4k=True):
    files = [
        {"id": vid * 10 + 1, "quality": "sd", "file_type": "video/mp4", "width": w // 3, "height": h // 3,
         "link": f"https://videos.pexels.com/video-files/{vid}/sd.mp4"},
        {"id": vid * 10 + 2, "quality": "hd", "file_type": "video/mp4", "width": w, "height": h,
         "link": f"https://videos.pexels.com/video-files/{vid}/hd.mp4"},
    ]
    if with_4k:
        files.append({"id": vid * 10 + 3, "quality": "uhd", "file_type": "video/mp4", "width": w * 2,
                      "height": h * 2, "link": f"https://videos.pexels.com/video-files/{vid}/uhd.mp4"})
    return {"id": vid, "width": w * 2, "height": h * 2, "duration": duration,
            "url": f"https://www.pexels.com/video/city-at-night-{vid}/",
            "user": {"id": 1, "name": f"Author {vid}", "url": f"https://www.pexels.com/@author{vid}"},
            "video_files": files}


PEXELS_VIDEOS = {"videos": [
    pexels_video(101, duration=3),                      # too short for a 6.4 s scene
    pexels_video(102, duration=12),                     # long enough, 16:9, has 4K too
    pexels_video(103, duration=20, w=1080, h=1920),     # portrait
]}
PEXELS_PHOTOS = {"photos": [
    {"id": 201, "width": 6000, "height": 4000, "url": "https://www.pexels.com/photo/eiffel-tower-201/",
     "photographer": "Photo Person", "photographer_url": "https://www.pexels.com/@pp", "alt": "Eiffel Tower at dusk",
     "src": {"original": "https://images.pexels.com/photos/201/pexels-photo-201.jpeg"}},
]}
PIXABAY_VIDEOS = {"total": 1, "totalHits": 1, "hits": [
    {"id": 301, "pageURL": "https://pixabay.com/videos/id-301/", "tags": "moon, space", "duration": 15,
     "user": "pixuser", "user_id": 77,
     "videos": {
         "large": {"url": "https://cdn.pixabay.com/video/301_large.mp4", "width": 3840, "height": 2160, "size": 9},
         "medium": {"url": "https://cdn.pixabay.com/video/301_medium.mp4", "width": 1920, "height": 1080, "size": 5},
         "small": {"url": "https://cdn.pixabay.com/video/301_small.mp4", "width": 1280, "height": 720, "size": 2},
         "tiny": {"url": "https://cdn.pixabay.com/video/301_tiny.mp4", "width": 640, "height": 360, "size": 1}}}]}
PIXABAY_PHOTOS = {"total": 1, "totalHits": 1, "hits": [
    {"id": 401, "pageURL": "https://pixabay.com/photos/id-401/", "tags": "astronaut, moon", "user": "pixphoto",
     "user_id": 88, "imageWidth": 4000, "imageHeight": 2250,
     "largeImageURL": "https://pixabay.com/get/401_1280.jpg", "webformatURL": "https://pixabay.com/get/401_640.jpg"}]}


class FakeResp:
    def __init__(self, status=200, data=None, body=b"", headers=None):
        self.status_code = status
        self._data = data
        self._body = body
        self.headers = headers or {}
        if body:
            self.headers.setdefault("Content-Length", str(len(body)))

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """Routes requests.Session.get to canned responses and records every call."""

    def __init__(self, pexels_status=200, pexels_empty=False, pixabay_429_once=False):
        self.calls = []
        self.pexels_status = pexels_status
        self.pexels_empty = pexels_empty
        self.pixabay_429_once = pixabay_429_once

    def __call__(self, session, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if url.startswith("https://api.pexels.com"):
            if self.pexels_status != 200:
                return FakeResp(self.pexels_status, {"error": "bad key"})
            if self.pexels_empty:
                return FakeResp(200, {"videos": [], "photos": []})
            data = PEXELS_VIDEOS if "/videos/" in url else PEXELS_PHOTOS
            return FakeResp(200, json.loads(json.dumps(data)), headers={"X-Ratelimit-Remaining": "150"})
        if url.startswith("https://pixabay.com/api"):
            if self.pixabay_429_once:
                self.pixabay_429_once = False
                return FakeResp(429, {}, headers={"Retry-After": "1"})
            data = PIXABAY_VIDEOS if "/videos/" in url else PIXABAY_PHOTOS
            return FakeResp(200, json.loads(json.dumps(data)))
        if url.startswith("https://commons.wikimedia.org") or url.startswith("https://api.openverse.org"):
            return FakeResp(200, {"query": {"pages": []}, "results": []})
        # any media download
        return FakeResp(200, body=b"FAKEMEDIA" * 100)


SCENES = [
    {"id": "s01", "start": 0.0, "end": 6.4, "narration": "City lights at night.", "visual_type": "stock_video",
     "stock_query": "city skyline night", "orientation": "landscape", "min_duration": 5},
    {"id": "s02", "start": 6.4, "end": 12.0, "narration": "More city lights.", "visual_type": "stock_video",
     "stock_query": "city skyline night", "orientation": "landscape"},
    {"id": "s03", "start": 12.0, "end": 18.0, "narration": "Paris built the Eiffel Tower in 1889.",
     "visual_type": "stock_image", "orientation": "landscape"},
    {"id": "s04", "start": 18.0, "end": 24.0, "narration": "The future city.", "visual_type": "ai_video",
     "video_prompt": "futuristic city"},
]


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp)
        (self.tmp / "scenes.json").write_text(json.dumps(SCENES), encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"PEXELS_API_KEY": "test-pexels", "PIXABAY_API_KEY": "test-pixabay"})
        self.env.start()
        self.sleep = mock.patch.object(bhttp.time, "sleep", lambda s: None)
        self.sleep.start()
        fetch.FAILURES.clear()
        # each test gets its own empty cache
        self.cache = mock.patch.object(fetch, "HERE", self.tmp)
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.sleep.stop()
        self.env.stop()
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_fetch(self, net, *extra):
        with mock.patch("requests.Session.get", new=lambda s, url, **kw: net(s, url, **kw)):
            return fetch.main(["scenes.json", "--out", "media/stock", "--quiet", *extra])

    def manifest(self):
        return {e["scene_id"]: e for e in json.loads(Path("media/stock/manifest.json").read_text())["scenes"]}

    def test_pexels_pick_quality_duration_and_dedup(self):
        net = FakeNet()
        rc = self.run_fetch(net)
        self.assertEqual(rc, 0)
        m = self.manifest()
        self.assertEqual(set(m), {"s01", "s02", "s03"})  # ai scene skipped
        # s01: the 12 s 16:9 clip, 1080p rendition (not 4K, not SD)
        self.assertEqual(m["s01"]["source"], "pexels")
        self.assertEqual(m["s01"]["source_id"], "102")
        self.assertEqual((m["s01"]["width"], m["s01"]["height"]), (1920, 1080))
        self.assertTrue(m["s01"]["download_url"].endswith("/hd.mp4"))
        # s02 asked for the same thing but must NOT reuse clip 102
        self.assertNotEqual(m["s02"]["source_id"], "102")
        # s03 had no stock_query -> derived from narration, picked a Pexels photo at 1920px
        self.assertTrue(m["s03"]["query_from_narration"])
        self.assertIn("Eiffel Tower", m["s03"]["query"])
        self.assertEqual(m["s03"]["source"], "pexels")
        self.assertIn("w=1920", m["s03"]["download_url"])
        self.assertTrue(Path("media/stock/s01_pexels.mp4").exists())
        self.assertTrue(Path("media/stock/s03_pexels.jpeg").exists() or Path("media/stock/s03_pexels.jpg").exists())
        # auth header sent to Pexels, key never stored in cache files
        pex = [c for c in net.calls if "pexels.com/v" in c[0] or "pexels.com/videos" in c[0]]
        self.assertTrue(all(c[2].get("Authorization") == "test-pexels" for c in pex if "api.pexels" in c[0]))
        for f in (self.tmp / ".cache" / "broll").glob("*.json"):
            self.assertNotIn("test-pixabay", f.read_text())
            self.assertNotIn("test-pexels", f.read_text())
        # outputs for HyperFrames/Codex
        out = json.loads(Path("scenes.with_media.json").read_text())
        self.assertEqual(out[0]["media_path"], "media/stock/s01_pexels.mp4")
        self.assertNotIn("media_path", out[3])
        credits = Path("media/stock/CREDITS.md").read_text()
        self.assertIn("Pexels", credits)

    def test_pixabay_fallback_when_pexels_key_is_bad(self):
        net = FakeNet(pexels_status=401)
        rc = self.run_fetch(net, "--sources", "pexels,pixabay")
        m = self.manifest()
        self.assertEqual(m["s01"]["source"], "pixabay")
        # 1080p rendition chosen over the 4K "large" one
        self.assertTrue(m["s01"]["download_url"].endswith("301_medium.mp4"))
        # only one Pixabay video exists -> s02 must not reuse it -> falls back to a Pixabay photo
        self.assertEqual(m["s02"]["source"], "pixabay")
        self.assertEqual(m["s02"].get("fallback"), "image_instead_of_video")
        # Pexels refused once and was then skipped for the rest of the run
        self.assertEqual(sum(1 for c in net.calls if "api.pexels.com" in c[0]), 1)
        # the only Pixabay photo is now taken by s02 -> s03 is reported missing (no duplicates!)
        self.assertEqual(m["s03"]["status"], "not_found")
        # Pixabay params: key sent, orientation mapped, query <= 100 chars
        img_calls = [c for c in net.calls if c[0] == "https://pixabay.com/api/"]
        self.assertEqual(img_calls[0][1]["orientation"], "horizontal")
        self.assertEqual(img_calls[0][1]["key"], "test-pixabay")
        self.assertEqual(rc, 1)  # exit code 1 = some scenes missing

    def test_pixabay_when_pexels_empty_and_429_retry(self):
        net = FakeNet(pexels_empty=True, pixabay_429_once=True)
        self.run_fetch(net, "--sources", "pexels,pixabay")
        m = self.manifest()
        self.assertEqual(m["s01"]["source"], "pixabay")

    def test_dry_run_downloads_nothing(self):
        net = FakeNet()
        rc = self.run_fetch(net, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertFalse(Path("media/stock").exists())
        self.assertFalse(any("videos.pexels.com" in c[0] for c in net.calls))

    def test_cache_and_rerun_skip(self):
        net = FakeNet()
        self.run_fetch(net)
        n_first = len(net.calls)
        net2 = FakeNet()
        self.run_fetch(net2)
        self.assertEqual(len(net2.calls), 0, "second run should reuse files + 24h cache, no network")
        self.assertGreater(n_first, 0)

    def test_scenes_object_format(self):
        Path("scenes.json").write_text(json.dumps({"title": "Demo", "scenes": SCENES[:1]}), encoding="utf-8")
        self.run_fetch(FakeNet())
        out = json.loads(Path("scenes.with_media.json").read_text())
        self.assertEqual(out["title"], "Demo")
        self.assertEqual(out["scenes"][0]["media"]["source"], "pexels")

    def test_no_keys_and_only_keyed_sources(self):
        with mock.patch.dict(os.environ, {"PEXELS_API_KEY": "", "PIXABAY_API_KEY": ""}):
            rc = self.run_fetch(FakeNet(), "--sources", "pexels,pixabay")
        self.assertEqual(rc, 2)


class KeywordTests(unittest.TestCase):
    def test_proper_nouns_kept(self):
        q = extract_keywords("Eighty years later, Neil Armstrong stepped onto the Moon.")
        self.assertTrue(q.startswith("Neil Armstrong"))

    def test_plain_words(self):
        q = extract_keywords("the ocean is full of plastic and the plastic keeps growing")
        self.assertEqual(q.split()[0], "plastic")

    def test_simplify(self):
        self.assertEqual(simplify_queries("a b c d"), ["a b c d", "a b c", "a b"])


if __name__ == "__main__":
    unittest.main()
