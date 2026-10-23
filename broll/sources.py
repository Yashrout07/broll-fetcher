"""The four free sources. Each search() returns a list of Candidate objects.

Pexels    - videos + photos, free key, no attribution required (but appreciated)
Pixabay   - videos + photos, free key, no attribution required (but appreciated)
Wikimedia - images, no key, real people/places/events, attribution + license REQUIRED
Openverse - images, no key, Creative Commons, attribution REQUIRED (except CC0 / public domain)
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse

from .http import Http

VIDEO_EXTS = {"mp4", "webm", "mov"}


@dataclass
class Candidate:
    source: str            # pexels | pixabay | wikimedia | openverse
    kind: str              # video | image
    id: str
    page_url: str          # human page to credit / check the license
    download_url: str
    width: int = 0
    height: int = 0
    duration: float | None = None  # seconds, videos only
    author: str = ""
    author_url: str = ""
    license: str = ""
    license_url: str = ""
    title: str = ""
    ext: str = "jpg"
    attribution_required: bool = False
    rank: int = 0          # position in the source's own search results (0 = best)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        d.pop("rank", None)
        return d


def _ext_from_url(url: str, default: str) -> str:
    path = urlparse(url).path.lower()
    m = re.search(r"\.([a-z0-9]{3,4})$", path)
    if m:
        ext = m.group(1)
        return "jpg" if ext == "jpeg" else ext
    return default


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _best_rendition(files: list[dict], orientation: str, max_short_side: int = 1080) -> dict | None:
    """Pick the rendition whose short side is closest to 1080 without going over (no 4K).
    If everything is smaller, take the biggest available."""
    good = [f for f in files if f.get("width") and f.get("height") and f.get("url")]
    if not good:
        return None

    def short(f):
        return min(f["width"], f["height"])

    under = [f for f in good if short(f) <= max_short_side]
    if under:
        return max(under, key=short)
    return min(good, key=short)  # everything is huge -> smallest of the huge ones


# --------------------------------------------------------------------------- Pexels
class Pexels:
    name = "pexels"
    kinds = {"video", "image"}

    def __init__(self, http: Http, api_key: str | None = None):
        self.http = http
        self.key = api_key if api_key is not None else os.getenv("PEXELS_API_KEY", "").strip()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def search(self, query: str, kind: str, orientation: str = "landscape", per_page: int = 15) -> list[Candidate]:
        if self.http.pexels_remaining is not None and self.http.pexels_remaining < 3:
            self.http.log("    Pexels hourly limit almost used up - skipping Pexels for now")
            return []
        headers = {"Authorization": self.key}
        params = {"query": query, "per_page": per_page, "orientation": orientation}
        out: list[Candidate] = []
        if kind == "video":
            params["size"] = "medium"  # medium = Full HD and up
            data = self.http.get_json("https://api.pexels.com/videos/search", params, headers)
            for i, v in enumerate(data.get("videos", [])):
                files = [
                    {"width": f.get("width") or 0, "height": f.get("height") or 0, "url": f.get("link"), "q": f.get("quality")}
                    for f in v.get("video_files", [])
                    if (f.get("file_type") or "").endswith("mp4")
                ]
                pick = _best_rendition(files, orientation)
                if not pick:
                    continue
                user = v.get("user") or {}
                out.append(Candidate(
                    source="pexels", kind="video", id=str(v.get("id")), page_url=v.get("url", ""),
                    download_url=pick["url"], width=pick["width"], height=pick["height"],
                    duration=float(v.get("duration") or 0), author=user.get("name", ""), author_url=user.get("url", ""),
                    license="Pexels License", license_url="https://www.pexels.com/license/",
                    title=_title_from_pexels_url(v.get("url", "")), ext="mp4", rank=i,
                ))
        else:
            data = self.http.get_json("https://api.pexels.com/v1/search", params, headers)
            for i, p in enumerate(data.get("photos", [])):
                src = p.get("src") or {}
                w, h = int(p.get("width") or 0), int(p.get("height") or 0)
                url = src.get("original", "")
                if not url:
                    continue
                # Ask Pexels' image CDN for a ~1920px version instead of the huge original
                if w and h:
                    if w >= h and w > 1920:
                        url, h, w = url + "?auto=compress&cs=tinysrgb&w=1920", round(h * 1920 / w), 1920
                    elif h > w and h > 1920:
                        url, w, h = url + "?auto=compress&cs=tinysrgb&h=1920", round(w * 1920 / h), 1920
                out.append(Candidate(
                    source="pexels", kind="image", id=str(p.get("id")), page_url=p.get("url", ""),
                    download_url=url, width=w, height=h, author=p.get("photographer", ""),
                    author_url=p.get("photographer_url", ""), license="Pexels License",
                    license_url="https://www.pexels.com/license/", title=p.get("alt") or _title_from_pexels_url(p.get("url", "")),
                    ext=_ext_from_url(src.get("original", ""), "jpg"), rank=i,
                ))
        return out


def _title_from_pexels_url(url: str) -> str:
    # https://www.pexels.com/video/aerial-view-of-city-at-night-1234/ -> "aerial view of city at night"
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-?\d+$", "", slug)
    return slug.replace("-", " ").strip()


# --------------------------------------------------------------------------- Pixabay
class Pixabay:
    name = "pixabay"
    kinds = {"video", "image"}

    def __init__(self, http: Http, api_key: str | None = None):
        self.http = http
        self.key = api_key if api_key is not None else os.getenv("PIXABAY_API_KEY", "").strip()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def search(self, query: str, kind: str, orientation: str = "landscape", per_page: int = 20) -> list[Candidate]:
        q = query[:100]  # Pixabay: query may not exceed 100 characters
        params = {"key": self.key, "q": q, "per_page": max(3, min(per_page, 200)), "safesearch": "true"}
        out: list[Candidate] = []
        if kind == "video":
            params["video_type"] = "film"
            data = self.http.get_json("https://pixabay.com/api/videos/", params)
            for i, v in enumerate(data.get("hits", [])):
                renditions = []
                for size_name, f in (v.get("videos") or {}).items():
                    if f and f.get("url"):
                        renditions.append({"width": f.get("width") or 0, "height": f.get("height") or 0,
                                           "url": f["url"], "q": size_name})
                pick = _best_rendition(renditions, orientation)
                if not pick:
                    continue
                out.append(Candidate(
                    source="pixabay", kind="video", id=str(v.get("id")), page_url=v.get("pageURL", ""),
                    download_url=pick["url"], width=pick["width"], height=pick["height"],
                    duration=float(v.get("duration") or 0), author=v.get("user", ""),
                    author_url=f"https://pixabay.com/users/{v.get('user','')}-{v.get('user_id','')}/",
                    license="Pixabay Content License", license_url="https://pixabay.com/service/license-summary/",
                    title=v.get("tags", ""), ext="mp4", rank=i,
                ))
        else:
            params["image_type"] = "photo"
            params["orientation"] = {"landscape": "horizontal", "portrait": "vertical"}.get(orientation, "all")
            data = self.http.get_json("https://pixabay.com/api/", params)
            for i, p in enumerate(data.get("hits", [])):
                url = p.get("largeImageURL") or p.get("webformatURL")
                if not url:
                    continue
                w, h = int(p.get("imageWidth") or 0), int(p.get("imageHeight") or 0)
                # largeImageURL is scaled to max 1280 px on the long side
                if w and h and max(w, h) > 1280:
                    s = 1280 / max(w, h)
                    w, h = round(w * s), round(h * s)
                out.append(Candidate(
                    source="pixabay", kind="image", id=str(p.get("id")), page_url=p.get("pageURL", ""),
                    download_url=url, width=w, height=h, author=p.get("user", ""),
                    author_url=f"https://pixabay.com/users/{p.get('user','')}-{p.get('user_id','')}/",
                    license="Pixabay Content License", license_url="https://pixabay.com/service/license-summary/",
                    title=p.get("tags", ""), ext=_ext_from_url(url, "jpg"), rank=i,
                ))
        return out


# --------------------------------------------------------------------------- Wikimedia Commons
class Wikimedia:
    name = "wikimedia"
    kinds = {"image"}
    API = "https://commons.wikimedia.org/w/api.php"
    OK_MIME = {"image/jpeg", "image/png", "image/webp"}

    def __init__(self, http: Http):
        self.http = http

    available = True

    def search(self, query: str, kind: str, orientation: str = "landscape", per_page: int = 20) -> list[Candidate]:
        if kind != "image":
            return []
        params = {
            "action": "query", "format": "json", "formatversion": 2,
            "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": per_page, "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,
            "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|AttributionRequired|ObjectName|ImageDescription|Restrictions",
        }
        data = self.http.get_json(self.API, params)
        pages = (data.get("query") or {}).get("pages") or []
        pages = sorted(pages, key=lambda p: p.get("index", 999))
        out: list[Candidate] = []
        for i, p in enumerate(pages):
            info = (p.get("imageinfo") or [{}])[0]
            if info.get("mime") not in self.OK_MIME:
                continue
            meta = info.get("extmetadata") or {}

            def m(k):
                return (meta.get(k) or {}).get("value", "")

            lic = _strip_html(m("LicenseShortName"))
            if not lic:
                continue  # no machine-readable license -> skip, too risky
            w, h = int(info.get("width") or 0), int(info.get("height") or 0)
            url = info.get("url", "")
            if w > 1920 and info.get("thumburl"):
                url, w, h = info["thumburl"], int(info.get("thumbwidth") or 1920), int(info.get("thumbheight") or h)
            artist_html = m("Artist")
            author_url = ""
            mm = re.search(r'href="([^"]+)"', artist_html or "")
            if mm:
                author_url = mm.group(1)
                if author_url.startswith("//"):
                    author_url = "https:" + author_url
                elif author_url.startswith("/"):
                    author_url = "https://commons.wikimedia.org" + author_url
            title = p.get("title", "").removeprefix("File:")
            out.append(Candidate(
                source="wikimedia", kind="image", id=str(p.get("pageid")), page_url=info.get("descriptionurl", ""),
                download_url=url, width=w, height=h, author=_strip_html(artist_html) or "Unknown author",
                author_url=author_url, license=lic, license_url=m("LicenseUrl"),
                title=_strip_html(m("ObjectName")) or re.sub(r"\.[a-zA-Z0-9]+$", "", title),
                ext=_ext_from_url(info.get("url", ""), "jpg"),
                attribution_required=not lic.lower().startswith(("public domain", "pd", "cc0")),
                rank=i, extra={"restrictions": _strip_html(m("Restrictions"))},
            ))
        return out


# --------------------------------------------------------------------------- Openverse
class Openverse:
    name = "openverse"
    kinds = {"image"}
    API = "https://api.openverse.org/v1/images/"

    def __init__(self, http: Http):
        self.http = http

    available = True

    def search(self, query: str, kind: str, orientation: str = "landscape", per_page: int = 20) -> list[Candidate]:
        if kind != "image":
            return []
        params = {
            "q": query, "page_size": per_page,
            # only licenses that allow commercial use AND editing (cropping, zooming) -> OK for monetized YouTube
            "license": "by,by-sa,cc0,pdm",
            "aspect_ratio": {"landscape": "wide", "portrait": "tall", "square": "square"}.get(orientation, "wide"),
            "mature": "false",
            "size": "large",
        }
        headers = {}
        token = os.getenv("OPENVERSE_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = self.http.get_json(self.API, params, headers or None, retries=2, timeout=20)
        out: list[Candidate] = []
        for i, r in enumerate(data.get("results", [])):
            url = r.get("url")
            if not url:
                continue
            ft = (r.get("filetype") or _ext_from_url(url, "jpg")).lower()
            if ft not in {"jpg", "jpeg", "png", "webp"}:
                continue
            lic = (r.get("license") or "").lower()
            ver = r.get("license_version") or ""
            lic_name = "Public Domain Mark" if lic == "pdm" else ("CC0" if lic == "cc0" else f"CC {lic.upper()} {ver}".strip())
            out.append(Candidate(
                source="openverse", kind="image", id=str(r.get("id")), page_url=r.get("foreign_landing_url", ""),
                download_url=url, width=int(r.get("width") or 0), height=int(r.get("height") or 0),
                author=r.get("creator") or "Unknown author", author_url=r.get("creator_url") or "",
                license=lic_name, license_url=r.get("license_url") or "", title=r.get("title") or "",
                ext="jpg" if ft == "jpeg" else ft, attribution_required=lic not in {"cc0", "pdm"}, rank=i,
                extra={"provider": r.get("provider") or r.get("source") or "", "attribution": r.get("attribution") or ""},
            ))
        return out


SOURCE_CLASSES = {"pexels": Pexels, "pixabay": Pixabay, "wikimedia": Wikimedia, "openverse": Openverse}
DEFAULT_ORDER = ["pexels", "pixabay", "wikimedia", "openverse"]
