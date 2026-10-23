"""Tiny, free keyword extractor: turns a narration sentence into a short stock-search query."""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = set("""
a about above after again against all almost also although always am among an and another any anyone anything are
aren't around as at away back be became because become been before being below between beyond both but by can
cannot could couldn't did didn't do does doesn't doing don't down during each either else enough even ever every
everyone everything few first for from further get gets getting give given go goes going gone got had hadn't has
hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's however i i'd
i'll i'm i've if in into is isn't it it's its itself just know known last least less let let's like likely made make
makes many may maybe me might more most much must my myself never new next no nobody none nor not nothing now of off
often on once one only onto or other others our ours ourselves out over own part per perhaps quite rather really
right said same say says see seen she she'd she'll she's should shouldn't since so some someone something sometimes
still such take than that that's the their theirs them themselves then there there's these they they'd they'll
they're they've thing things think this those though through thus to today together too toward towards under until
up upon us use used very via want was wasn't way we we'd we'll we're we've well were weren't what what's when when's
where where's whether which while who who's whom whose why why's will with within without won't would wouldn't yet
you you'd you'll you're you've your yours yourself yourselves imagine actually basically literally every single
just year years time times day days people world lot lots kind story here's let's look looks looking called call
""".split())


def extract_keywords(text: str, max_words: int = 3) -> str:
    """Return a short search query from narration text.

    Keeps multi-word proper names ("Eiffel Tower", "New York") together, then adds the most
    frequent meaningful words. No AI, no network, works offline.
    """
    if not text:
        return ""
    text = text.replace("\u2019", "'")
    # 1) Proper-noun phrases: consecutive Capitalized words not at sentence start
    proper: list[str] = []
    for sent in re.split(r"[.!?]+\s*", text):
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sent)
        run: list[str] = []
        for idx, w in enumerate(words):
            is_cap = w[0].isupper() and w.lower() not in STOPWORDS
            if is_cap and (idx > 0 or len(words) == 1 or (idx + 1 < len(words) and words[idx + 1][0].isupper())):
                run.append(w)
            else:
                if run:
                    proper.append(" ".join(run))
                run = []
        if run:
            proper.append(" ".join(run))

    # 2) Content words by frequency (longer words win ties)
    tokens = [t.lower().strip("'-") for t in re.findall(r"[A-Za-z][A-Za-z'\-]+", text)]
    tokens = [t.removesuffix("'s") for t in tokens]
    tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    counts = Counter(tokens)
    order = {t: i for i, t in reversed(list(enumerate(tokens)))}
    ranked = sorted(counts, key=lambda t: (-counts[t], -min(len(t), 9), order[t]))

    picked: list[str] = []
    used_words: set[str] = set()
    for p in proper:
        if len(picked) >= 2:
            break
        if p.lower() not in used_words:
            picked.append(p)
            used_words.update(p.lower().split())
    for t in ranked:
        if sum(len(p.split()) for p in picked) >= max_words:
            break
        if t not in used_words:
            picked.append(t)
            used_words.add(t)
    return " ".join(picked)


def simplify_queries(query: str) -> list[str]:
    """Fallback queries when the full one finds nothing: 'a b c d' -> ['a b c d', 'a b c', 'a b', 'a']."""
    words = query.split()
    out = []
    for n in range(len(words), 0, -1):
        q = " ".join(words[:n])
        if q not in out:
            out.append(q)
        if len(out) >= 3:
            break
    return out
