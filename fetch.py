#!/usr/bin/env python3
"""broll-fetcher - download free stock videos/images that match your scenes.json timeline.

Usage:
    python fetch.py scenes.json --out media/stock
    python fetch.py scenes.json --out media/stock --sources pexels,pixabay
    python fetch.py scenes.json --dry-run          # show picks, download nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from broll import __version__
from broll.http import Http, RateLimited
from broll.keywords import extract_keywords, simplify_queries
from broll.picker import GOOD_ENOUGH, best, key
from broll.sources import DEFAULT_ORDER, SOURCE_CLASSES, Candidate

STOCK_TYPES = {"stock_video": "video", "stock_image": "image"}
FAILURES: dict[str, int] = {}  # consecutive-ish failures per source during this run
HERE = Path(__file__).resolve().parent


def load_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(HERE / ".env")


def read_scenes(path: Path) -> tuple[object, list[dict]]:
    """Accepts either a plain list of scenes or {"scenes": [...]} - returns (whole document, scene list)."""
    with open(path, "r", encoding="utf-8-sig") as fh:  # utf-8-sig: tolerate Windows Notepad's BOM
        doc = json.load(fh)
    if isinstance(doc, list):
        return doc, doc
    if isinstance(doc, dict) and isinstance(doc.get("scenes"), list):
        return doc, doc["scenes"]
    raise SystemExit(f"{path}: expected a JSON list of scenes, or an object with a \"scenes\" list")


def scene_seconds(scene: dict) -> float:
    try:
        length = float(scene.get("end", 0)) - float(scene.get("start", 0))
    except (TypeError, ValueError):
        length = 0.0
    try:
        min_d = float(scene.get("min_duration") or 0)
    except (TypeError, ValueError):
        min_d = 0.0
    return max(length, min_d, 0.0)


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def search_scene(scene: dict, kind: str, query: str, orientation: str, needed: float,
                 sources: list, used: set[str], log) -> tuple[Candidate | None, float, list[str]]:
    """Try each source in order; stop at the first 'good enough' match, else keep the best seen."""
    notes: list[str] = []
    best_c, best_s = None, float("inf")
    for src in sources:
        if kind not in src.kinds or not src.available:
            continue
        if FAILURES.get(src.name, 0) >= 2:
            continue  # this source kept failing during this run - don't waste time on it
        for q in simplify_queries(query):
            try:
                cands = src.search(q, kind, orientation)
            except (RateLimited, PermissionError) as e:
                FAILURES[src.name] = 99  # out of quota / bad key: stop using it this run
                notes.append(f"{src.name}: {e}")
                log(f"    {src.name}: {e} - skipping it for the rest of this run")
                break
            except Exception as e:  # one broken source must not stop the run
                FAILURES[src.name] = FAILURES.get(src.name, 0) + 1
                short = re.sub(r"(key=)[^&\s']+", r"\1***", str(e))[:160] if str(e) else type(e).__name__
                notes.append(f"{src.name}: {type(e).__name__}")
                log(f"    {src.name}: failed ({type(e).__name__}: {short}) - trying next source")
                break
            c, s = best(cands, orientation, needed, used)
            if c is not None:
                if q != query:
                    notes.append(f"{src.name}: no results for full query, used \"{q}\"")
                if s < best_s:
                    best_c, best_s = c, s
                break
        if best_c is not None and best_s <= GOOD_ENOUGH:
            break
    return best_c, best_s, notes


def check_image(path: Path, max_side: int = 1920) -> tuple[int, int, bool] | None:
    """If Pillow is installed: read the real image size (APIs sometimes report the original's size,
    not the file you get) and shrink huge originals (e.g. 6000px Flickr photos) to save disk space.
    Returns (width, height, resized) or None when Pillow is missing / the file can't be read."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            if max(im.size) <= max_side * 1.34:  # up to ~2560px is fine as it is
                return im.size[0], im.size[1], False
            fmt = im.format or "JPEG"
            im.thumbnail((max_side, max_side))
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            tmp = path.with_suffix(path.suffix + ".tmp")
            im.save(tmp, format=fmt, **({"quality": 90} if fmt == "JPEG" else {}))
            w, h = im.size
        os.replace(tmp, path)
        return w, h, True
    except Exception:
        return None


def credit_line(c: dict) -> str:
    title = c.get("title") or "Untitled"
    if len(title) > 80:
        title = title[:77] + "..."
    by = c.get("author") or "Unknown author"
    src = {"pexels": "Pexels", "pixabay": "Pixabay", "wikimedia": "Wikimedia Commons",
           "openverse": "Openverse"}.get(c["source"], c["source"])
    if c["source"] == "openverse" and c.get("provider"):
        src = f"{c['provider'].title()} via Openverse"
    lic = c.get("license") or "see source page"
    parts = [f"\"{title}\" by {by}", lic]
    parts.append(f"{src}: {c.get('page_url', '')}")
    if c.get("attribution_required") and c.get("license_url"):
        parts.append(f"license: {c['license_url']}")
    return " - ".join(parts)


def write_credits(path: Path, picks: list[dict]) -> None:
    must = [p for p in picks if p.get("attribution_required")]
    nice = [p for p in picks if not p.get("attribution_required")]
    lines = ["# Credits", "",
             f"Generated by broll-fetcher {__version__} on {datetime.now().strftime('%Y-%m-%d %H:%M')}.", ""]
    if must:
        lines += ["## Attribution required (Wikimedia Commons / Openverse / Creative Commons)", "",
                  "These licenses REQUIRE a credit. Keep these lines in your YouTube description.",
                  "Open each source page once and confirm the license before publishing.", ""]
        lines += [f"- {p['scene_id']}: {credit_line(p)}" for p in must] + [""]
    if nice:
        lines += ["## No credit required (Pexels, Pixabay, public domain / CC0) - credit still appreciated", ""]
        lines += [f"- {p['scene_id']}: {credit_line(p)}" for p in nice] + [""]
    lines += ["## Copy-paste block for the YouTube description", "", "```", "Footage & images:"]
    for p in must + nice:
        lines.append(credit_line(p))
    lines += ["```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download free stock footage/images for the stock scenes in scenes.json")
    ap.add_argument("scenes", help="path to scenes.json")
    ap.add_argument("--out", default="media/stock", help="folder for downloaded files (default: media/stock)")
    ap.add_argument("--sources", default=",".join(DEFAULT_ORDER),
                    help="comma list, tried in this order (default: pexels,pixabay,wikimedia,openverse)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be picked, download nothing")
    ap.add_argument("--scenes-out", default=None,
                    help="where to write scenes + chosen file paths (default: <scenes>.with_media.json next to input)")
    ap.add_argument("--force", action="store_true", help="re-download even if the file is already there")
    ap.add_argument("--no-cache", action="store_true", help="ignore the 24h search cache")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    # Windows: when output is piped (e.g. inside Codex) the console may not be UTF-8.
    # Never crash on an author name like "Jim \u9673" - print a ? instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    load_env()
    log = (lambda *_: None) if args.quiet else (lambda m: print(m, flush=True))

    scenes_path = Path(args.scenes)
    if not scenes_path.exists():
        print(f"error: {scenes_path} not found", file=sys.stderr)
        return 2
    doc, scenes = read_scenes(scenes_path)
    out_dir = Path(args.out)

    http = Http(cache_dir=HERE / ".cache" / "broll", quiet=args.quiet)
    if args.no_cache:
        http_get = http.get_json
        http.get_json = lambda *a, **k: http_get(*a, **{**k, "use_cache": False})  # type: ignore

    names = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    bad = [n for n in names if n not in SOURCE_CLASSES]
    if bad:
        print(f"error: unknown source(s): {', '.join(bad)}. Choose from: {', '.join(SOURCE_CLASSES)}", file=sys.stderr)
        return 2
    sources = []
    for n in names:
        cls = SOURCE_CLASSES[n]
        src = cls(http)
        if not src.available:
            log(f"note: {n} skipped - no {n.upper()}_API_KEY in .env (see README for a free key)")
        sources.append(src)
    if not any(s.available for s in sources):
        print("error: none of the chosen sources can run (add API keys to .env or include wikimedia/openverse)",
              file=sys.stderr)
        return 2

    # Previous manifest lets re-runs skip files that are already downloaded
    manifest_path = out_dir / "manifest.json"
    previous: dict[str, dict] = {}
    if manifest_path.exists() and not args.force:
        try:
            for e in json.loads(manifest_path.read_text(encoding="utf-8")).get("scenes", []):
                if e.get("status") == "ok":
                    previous[e["scene_id"]] = e
        except Exception:
            pass

    used: set[str] = set()
    entries: list[dict] = []
    stock_count = sum(1 for s in scenes if isinstance(s, dict) and s.get("visual_type") in STOCK_TYPES)
    log(f"broll-fetcher {__version__}: {len(scenes)} scenes, {stock_count} stock scenes to fetch"
        + (" (dry run)" if args.dry_run else ""))
    t0 = time.time()

    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or f"scene{idx + 1:02d}")
        vtype = scene.get("visual_type")
        if vtype not in STOCK_TYPES:
            continue
        kind = STOCK_TYPES[vtype]
        orientation = (scene.get("orientation") or "landscape").lower()
        needed = scene_seconds(scene)
        query = (scene.get("stock_query") or "").strip()
        if isinstance(scene.get("stock_query"), list):
            query = " ".join(scene["stock_query"])
        derived = False
        if not query:
            query = extract_keywords(scene.get("narration", ""))
            derived = True
        entry = {"scene_id": sid, "visual_type": vtype, "query": query, "query_from_narration": derived,
                 "orientation": orientation, "scene_seconds": round(needed, 2)}
        if not query:
            entry.update(status="not_found", note="no stock_query and no narration to derive one from")
            entries.append(entry)
            log(f"[{sid}] skipped - nothing to search for")
            continue
        log(f"[{sid}] {kind} for \"{query}\"" + (" (from narration)" if derived else "") + f", needs {needed:.1f}s")

        # Reuse an earlier download if it's still on disk
        prev = previous.get(sid)
        if prev and prev.get("query") == query and prev.get("file") and Path(prev["file"]).exists() and not args.dry_run:
            used.add(f"{prev['source']}:{prev['source_id']}")
            entries.append(prev)
            log(f"    already have {prev['file']} - keeping it (use --force to refetch)")
            continue

        c, sc, notes = search_scene(scene, kind, query, orientation, needed, sources, used, log)
        fallback = None
        if c is None and kind == "video":
            log("    no video found - looking for a still image instead (animate it with a slow zoom)")
            c, sc, more = search_scene(scene, "image", query, orientation, needed, sources, used, log)
            notes += more
            if c is not None:
                fallback = "image_instead_of_video"
        if c is None:
            entry.update(status="not_found", note="; ".join(notes) or "no matching result in any source")
            entries.append(entry)
            log("    nothing suitable found - try a simpler stock_query")
            continue

        used.add(key(c))
        used.add(c.download_url)
        fname = f"{sid}_{c.source}.{c.ext}"
        dest = out_dir / fname
        entry.update(
            status="dry_run" if args.dry_run else "ok",
            file=rel(dest), source=c.source, source_id=c.id, kind=c.kind, page_url=c.page_url,
            download_url=c.download_url, author=c.author, author_url=c.author_url, license=c.license,
            license_url=c.license_url, attribution_required=c.attribution_required, title=c.title,
            width=c.width, height=c.height, duration=c.duration, score=sc,
        )
        if c.extra.get("provider"):
            entry["provider"] = c.extra["provider"]
        if fallback:
            entry["fallback"] = fallback
        if c.kind == "video" and c.duration is not None and c.duration < needed:
            notes.append(f"clip is {c.duration:.0f}s but scene needs {needed:.1f}s - loop or slow it down")
        if notes:
            entry["note"] = "; ".join(notes)
        dims = f"{c.width}x{c.height}" + (f", {c.duration:.0f}s" if c.duration else "")
        log(f"    -> {c.source} #{c.id} ({dims}, {c.license}) by {c.author[:40]}")

        if not args.dry_run:
            try:
                size = http.download(c.download_url, dest)
                if c.kind == "image":
                    info = check_image(dest)
                    if info:
                        w, h, resized = info
                        entry["width"], entry["height"] = w, h
                        size = dest.stat().st_size
                        if resized:
                            log(f"    resized to {w}x{h} to save space")
                        if min(w, h) < 720:
                            msg = f"low resolution ({w}x{h}) - may look soft in a 1080p video"
                            entry["note"] = (entry.get("note", "") + "; " + msg).lstrip("; ")
                            log(f"    note: {msg}")
                entry["bytes"] = size
                log(f"    saved {rel(dest)} ({size / 1e6:.1f} MB)")
            except Exception as e:
                entry.update(status="error", note=(entry.get("note", "") + f"; download failed: {e}").lstrip("; "))
                log(f"    download failed: {e}")
        entries.append(entry)

    ok = [e for e in entries if e.get("status") in ("ok", "dry_run")]
    missing = [e for e in entries if e.get("status") not in ("ok", "dry_run")]

    if args.dry_run:
        print()
        print("DRY RUN - nothing downloaded. Picks:")
        for e in entries:
            if e.get("status") == "dry_run":
                print(f"  {e['scene_id']:<6} {e['source']:<10} {e.get('width')}x{e.get('height')}"
                      f"{' ' + str(round(e['duration'])) + 's' if e.get('duration') else ''}  {e['license']:<24} {e['page_url']}")
            else:
                print(f"  {e['scene_id']:<6} NOT FOUND  ({e.get('note', '')})")
        return 0 if not missing else 1

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "tool": f"broll-fetcher {__version__}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenes_file": rel(scenes_path),
        "sources_order": names,
        "scenes": entries,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_credits(out_dir / "CREDITS.md", [e for e in entries if e.get("status") == "ok"])

    # scenes.with_media.json: the original scenes + where each stock file landed
    by_id = {e["scene_id"]: e for e in entries}
    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or f"scene{idx + 1:02d}")
        e = by_id.get(sid)
        if not e:
            continue
        if e.get("status") == "ok":
            scene["media_path"] = e["file"]
            scene["media"] = {k: e.get(k) for k in ("file", "kind", "source", "page_url", "author", "license",
                                                    "license_url", "attribution_required", "width", "height",
                                                    "duration")}
            if e.get("fallback"):
                scene["media"]["fallback"] = e["fallback"]
        else:
            scene["media_path"] = None
            scene["media"] = {"status": e.get("status"), "note": e.get("note", "")}
    scenes_out = Path(args.scenes_out) if args.scenes_out else scenes_path.with_name(
        scenes_path.stem + ".with_media.json")
    scenes_out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    log("")
    log(f"done in {time.time() - t0:.0f}s: {len([e for e in entries if e.get('status') == 'ok'])} ready, "
        f"{len(missing)} missing; {http.stats['api_calls']} API calls, {http.stats['cache_hits']} from cache")
    log(f"  files:     {rel(out_dir)}/")
    log(f"  manifest:  {rel(manifest_path)}")
    log(f"  credits:   {rel(out_dir / 'CREDITS.md')}")
    log(f"  timeline:  {rel(scenes_out)}")
    for e in missing:
        log(f"  missing {e['scene_id']}: {e.get('note', '')}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
