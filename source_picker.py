"""Pick the best SoundCloud search result for a text query.

SoundCloud search results are ranked by how well the uploader + title cover the
query words; play counts only break near-ties. Things that rarely match what was
asked for are filtered or penalised:

- 30-second previews (label tracks for non-Go+ listeners) are dropped
- remixes, covers, nightcore, sped-up/slowed versions, mashups, ... are penalised
  unless the query asks for them

Official major-label uploads are often DRM-protected and cannot be downloaded at
all; the caller tries candidates in order and moves on when one fails.
"""

import math
import re
from typing import Optional

PREVIEW_MAX_SECONDS = 31.0

_WORD_RE = re.compile(r"[a-z0-9]+")
# Words that say nothing about which song is meant
_STOPWORDS = {
    "the", "a", "an", "and", "x", "feat", "ft", "featuring", "with", "by", "official",
    "audio", "video", "lyrics", "lyric", "hd", "hq", "music", "original", "version", "prod",
}
# Words marking a different version than the original
_VERSION_WORDS = {
    "remix", "cover", "nightcore", "sped", "speed", "slowed", "reverb", "bassboosted", "boosted",
    "8d", "karaoke", "instrumental", "mashup", "bootleg", "flip", "rework", "vip", "acapella",
    "acoustic", "live", "piano", "lofi", "phonk", "edit", "tiktok",
}


def _tokens(text: str) -> list[str]:
    text = (text or "").casefold().replace("&", " and ")
    return _WORD_RE.findall(text)


def score_candidate(query: str, candidate: dict) -> Optional[dict]:
    """Score one search result. Returns None for unusable results (previews)."""
    duration = candidate.get("duration")
    if isinstance(duration, (int, float)) and duration <= PREVIEW_MAX_SECONDS:
        return None

    query_words = [t for t in _tokens(query) if t not in _STOPWORDS] or _tokens(query)
    text_words = _tokens(f"{candidate.get('uploader') or ''} {candidate.get('title') or ''}")
    if not query_words or not text_words:
        return None

    query_set, text_set = set(query_words), set(text_words)
    content_words = {t for t in text_set if t not in _STOPWORDS} or text_set
    matched = query_set & text_set
    coverage = len(matched) / len(query_set)          # how much of the query is there
    precision = len(matched) / len(content_words)    # how much extra stuff is in the title
    similarity = 0.75 * coverage + 0.25 * precision

    unwanted_versions = (text_set & _VERSION_WORDS) - query_set
    if unwanted_versions:
        similarity -= 0.3

    plays = candidate.get("view_count") or 0
    try:
        plays = max(0, int(plays))
    except (TypeError, ValueError):
        plays = 0
    # Popularity only breaks near-ties: 10k plays +0.08, 10M plays +0.14
    score = similarity + 0.02 * math.log10(plays + 1)

    return {
        **candidate,
        "similarity": round(similarity, 3),
        "score": round(score, 3),
        "plays": plays,
        "unwanted_versions": sorted(unwanted_versions),
    }


def rank_soundcloud(
    query: str,
    candidates: list[dict],
    *,
    min_similarity: float,
    min_plays: int = 0,
) -> list[dict]:
    """Usable candidates, best first, that pass the given thresholds."""
    scored = [s for s in (score_candidate(query, c) for c in candidates) if s]
    good = [s for s in scored if s["similarity"] >= min_similarity and s["plays"] >= min_plays]
    return sorted(good, key=lambda s: s["score"], reverse=True)
