"""Scores candidates and picks the best one for a scene."""
from __future__ import annotations

import math

from .sources import Candidate

TARGET_RATIO = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1.0}
GOOD_ENOUGH = 3.0      # a score at or below this stops the search (no need to ask the next source)
MIN_SHORT_SIDE = 480   # anything smaller looks bad on YouTube


def score(c: Candidate, orientation: str, needed_seconds: float) -> float:
    """Lower is better. 0 = perfect: right shape, 1080p, long enough, top search result."""
    s = 0.0
    target = TARGET_RATIO.get(orientation, 16 / 9)
    if c.width and c.height:
        ratio = c.width / c.height
        s += abs(math.log(ratio / target)) * 10  # 16:9 vs 4:3 ~ 2.9, landscape vs portrait ~ 11
        short = min(c.width, c.height)
        if short < 1080:
            s += (1080 - short) / 1080 * 6          # 720p ~ +2, 540p ~ +3
    else:
        s += 4  # unknown size
    if c.kind == "video":
        dur = c.duration or 0
        if dur < needed_seconds:
            s += 3 + (needed_seconds - dur)          # too short: will need looping/slow-mo
        else:
            s += min((dur - needed_seconds) * 0.02, 1.5)  # very long clips = bigger downloads
    s += 0.15 * c.rank                               # trust the source's own relevance a bit
    return round(s, 3)


def acceptable(c: Candidate) -> bool:
    if c.width and c.height and min(c.width, c.height) < MIN_SHORT_SIDE:
        return False
    return bool(c.download_url)


def best(cands: list[Candidate], orientation: str, needed_seconds: float,
         used: set[str]) -> tuple[Candidate | None, float]:
    pool = [c for c in cands if acceptable(c) and key(c) not in used and c.download_url not in used]
    if not pool:
        return None, float("inf")
    scored = sorted(((score(c, orientation, needed_seconds), c) for c in pool), key=lambda t: t[0])
    return scored[0][1], scored[0][0]


def key(c: Candidate) -> str:
    return f"{c.source}:{c.id}"
