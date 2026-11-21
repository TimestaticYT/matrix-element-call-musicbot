"""Resolve internet radio URLs for direct playback by the call worker's ffmpeg.

Follows .m3u/.pls playlist links to the actual stream and reads the station name
from the Icecast/Shoutcast "icy-name" header. HLS (.m3u8) and anything we cannot
inspect are handed to ffmpeg unchanged.
"""

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_HEADERS = {"Icy-MetaData": "1", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) musicbot-radio"}
_PLAYLIST_TYPES = ("audio/x-mpegurl", "audio/mpegurl", "audio/x-scpls", "application/pls+xml", "audio/scpls")
_HLS_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl")
_PLS_FILE_RE = re.compile(r"^File\d+\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)


class RadioError(Exception):
    """User-facing problem with a radio URL."""


def _first_stream_url(text: str, base_url: str) -> Optional[str]:
    match = _PLS_FILE_RE.search(text)
    if match:
        return urljoin(base_url, match.group(1))
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return urljoin(base_url, line)
    return None


async def resolve_radio_stream(url: str) -> tuple[str, Optional[str]]:
    """Return (stream_url, station_name or None)."""
    current = url.strip()
    if urlparse(current).scheme not in {"http", "https"}:
        raise RadioError("Radio URLs must start with http:// or https://")

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            for _ in range(3):
                path = urlparse(current).path.lower()
                if path.endswith(".m3u8"):
                    return current, None
                async with session.get(current, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        raise RadioError(f"Server answered HTTP {resp.status}")
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    name = (resp.headers.get("icy-name") or "").strip() or None
                    final_url = str(resp.url)
                    # Hand ffmpeg the URL we asked for, not a redirect target: those often
                    # carry session tokens that expire, which would break reconnects.
                    if ctype in _HLS_TYPES:
                        return current, name
                    if ctype in _PLAYLIST_TYPES or path.endswith((".m3u", ".pls")):
                        text = (await resp.content.read(65536)).decode("utf-8", errors="replace")
                        nxt = _first_stream_url(text, final_url)
                        if not nxt:
                            raise RadioError("That playlist contains no stream URL")
                        current = nxt
                        continue
                    if ctype.startswith("text/html"):
                        raise RadioError("That URL is a web page, not an audio stream")
                    return current, name
            raise RadioError("Too many playlist redirections")
    except RadioError:
        raise
    except Exception as exc:
        # e.g. old Shoutcast servers answering "ICY 200 OK", which aiohttp can't parse:
        # ffmpeg can, so let it try.
        logger.info("Could not inspect radio URL %s (%s); passing it to ffmpeg as is", current, exc)
        return current, None
