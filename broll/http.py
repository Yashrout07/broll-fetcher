"""Polite HTTP helper: one session, per-host pacing, retries, and a 24h JSON cache."""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

try:  # optional progress bars
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover - tqdm is optional
    tqdm = None

from . import __version__

CACHE_TTL_SECONDS = 24 * 60 * 60  # Pixabay asks API results to be cached for 24 hours
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024  # refuse anything bigger than 400 MB

# Minimum seconds between two API calls to the same host.
#  - Pexels: 200 requests/hour by default  -> we also read its X-Ratelimit-Remaining header
#  - Pixabay: 100 requests/minute          -> 0.7 s spacing keeps us under it
#  - Wikimedia / Openverse: no key, be gentle
HOST_SPACING = {
    "api.pexels.com": 0.4,
    "pixabay.com": 0.7,
    "commons.wikimedia.org": 0.5,
    "api.openverse.org": 1.5,
}


class RateLimited(Exception):
    """The API says we are out of requests for now."""


class Http:
    def __init__(self, cache_dir: str | Path = ".cache/broll", user_agent: str | None = None,
                 timeout: float = 30.0, quiet: bool = False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.quiet = quiet
        self.session = requests.Session()
        contact = os.getenv("BROLL_CONTACT", "").strip()
        ua = user_agent or f"broll-fetcher/{__version__} (https://github.com/; {contact or 'no contact set'}) python-requests"
        self.session.headers["User-Agent"] = ua
        self._last_call: dict[str, float] = {}
        self.pexels_remaining: int | None = None
        self.stats = {"api_calls": 0, "cache_hits": 0, "downloads": 0}

    # ---------- helpers ----------
    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def _pace(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        gap = HOST_SPACING.get(host, 0.2)
        last = self._last_call.get(host)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def _cache_path(self, url: str, params: dict | None) -> Path:
        # Never put API keys into the cache key / file
        safe = {k: v for k, v in (params or {}).items() if k.lower() not in {"key", "api_key"}}
        raw = url + "?" + json.dumps(safe, sort_keys=True)
        return self.cache_dir / (hashlib.sha256(raw.encode()).hexdigest()[:32] + ".json")

    # ---------- JSON GET with cache + retries ----------
    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                 use_cache: bool = True, retries: int = 4, timeout: float | None = None) -> dict:
        cpath = self._cache_path(url, params)
        if use_cache and cpath.exists():
            try:
                blob = json.loads(cpath.read_text(encoding="utf-8"))
                if time.time() - blob.get("_saved", 0) < CACHE_TTL_SECONDS:
                    self.stats["cache_hits"] += 1
                    return blob["data"]
            except Exception:
                pass  # broken cache file -> just refetch

        delay = 2.0
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            self._pace(url)
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=timeout or self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                self.log(f"    network problem ({type(e).__name__}), retry {attempt}/{retries} in {delay:.0f}s")
                time.sleep(delay + random.random())
                delay *= 2
                continue
            self.stats["api_calls"] += 1
            if "X-Ratelimit-Remaining" in resp.headers and "pexels" in url:
                try:
                    self.pexels_remaining = int(resp.headers["X-Ratelimit-Remaining"])
                except ValueError:
                    pass
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else delay
                if wait > 120:
                    raise RateLimited(f"{urlparse(url).hostname} rate limit reached (retry after {wait:.0f}s)")
                self.log(f"    rate limited by {urlparse(url).hostname}, waiting {wait:.0f}s")
                time.sleep(wait + random.random())
                delay *= 2
                continue
            if resp.status_code >= 500:
                last_err = requests.HTTPError(f"{resp.status_code} from {urlparse(url).hostname}")
                self.log(f"    server error {resp.status_code}, retry {attempt}/{retries} in {delay:.0f}s")
                time.sleep(delay + random.random())
                delay *= 2
                continue
            if resp.status_code in (401, 403):
                raise PermissionError(f"{urlparse(url).hostname} refused the request ({resp.status_code}) - check your API key")
            if resp.status_code >= 400:
                # Build our own message: requests' default one contains the full URL, and Pixabay's
                # URL contains your API key - never print that.
                body = (getattr(resp, "text", "") or "")[:160].strip()
                if "pixabay" in url and "key" in body.lower():
                    raise PermissionError(f"pixabay.com refused the request ({body}) - check your API key")
                raise requests.HTTPError(f"HTTP {resp.status_code} from {urlparse(url).hostname}: {body}")
            data = resp.json()
            try:
                cpath.write_text(json.dumps({"_saved": time.time(), "url": url, "data": data}), encoding="utf-8")
            except OSError:
                pass
            return data
        raise last_err or RuntimeError(f"giving up on {url}")

    # ---------- file download ----------
    def download(self, url: str, dest: Path, retries: int = 3) -> int:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        delay = 2.0
        for attempt in range(1, retries + 1):
            self._pace(url)
            try:
                with self.session.get(url, stream=True, timeout=self.timeout) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise requests.HTTPError(f"HTTP {resp.status_code}")
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    if total and total > MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"file is {total/1e6:.0f} MB, over the {MAX_DOWNLOAD_BYTES/1e6:.0f} MB safety cap")
                    bar = None
                    if tqdm is not None and not self.quiet and total > 2_000_000:
                        bar = tqdm(total=total, unit="B", unit_scale=True, leave=False, desc=dest.name)
                    written = 0
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            written += len(chunk)
                            if bar:
                                bar.update(len(chunk))
                            if written > MAX_DOWNLOAD_BYTES:
                                raise ValueError("download exceeded the safety cap")
                    if bar:
                        bar.close()
                if written == 0:
                    raise ValueError("empty file")
                os.replace(tmp, dest)
                self.stats["downloads"] += 1
                return written
            except (requests.RequestException, ValueError, OSError) as e:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                if attempt == retries or isinstance(e, ValueError) and "cap" in str(e):
                    raise
                self.log(f"    download problem ({e}), retry {attempt}/{retries} in {delay:.0f}s")
                time.sleep(delay + random.random())
                delay *= 2
        raise RuntimeError("download failed")
