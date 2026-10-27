"""
Google News link -> the real article URL (v18d). Shared by pipeline_b_news_aggregator.py (GitHub Actions) and
api_server.py (Render). Standard library only, so neither side needs a new package.

Why: ~85% of our headlines come from Google News search feeds, whose links (news.google.com/rss/articles/CBMi...)
only open through JavaScript. The article preview in the drawer could not follow them, so most news boxes showed
nothing beyond the headline even though the real publisher was known. Google's own page for the link carries a
signature + timestamp; posting them back to Google's "batchexecute" endpoint returns the publisher's URL (the same
call the Google News web app makes). Checked 27.10.2026 from a home connection: a Daily Sabah link decoded to
dailysabah.com/business/economy/... . Whether Google answers from GitHub/Render addresses is checked in their logs.

Never raises: returns None whenever anything does not look as expected (the caller keeps the Google link).
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Optional

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def is_google_news(url: str) -> bool:
    try:
        return (urllib.parse.urlparse(url or "").hostname or "").lower().endswith("news.google.com")
    except Exception:  # noqa: BLE001
        return False


def decode_google_news_url(url: str, timeout: float = 8.0) -> Optional[str]:
    try:
        m = re.search(r"/articles/([A-Za-z0-9_-]+)", urllib.parse.urlparse(url).path)
        if not m:
            return None
        art_id = m.group(1)
        req = urllib.request.Request(f"https://news.google.com/articles/{art_id}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read(800_000).decode("utf-8", errors="replace")
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="(\d+)"', page)
        if not (sig and ts):
            return None
        inner = json.dumps(
            ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
              "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             art_id, int(ts.group(1)), sig.group(1)],
            separators=(",", ":"))
        body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}).encode()
        req = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
            headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(200_000).decode("utf-8", errors="replace")
        # answer = ")]}'" line, then a JSON list; the Fbv4je entry holds a JSON string ["garturlres", "<url>", ...]
        try:
            data = json.loads(text.split("\n", 1)[1] if text.startswith(")]}'") else text)
        except ValueError:
            m = re.search(r'garturlres\\",\\"(https?://[^"\\]+)\\"', text)
            real = m.group(1) if m else None
            return real if real and not is_google_news(real) else None
        for row in data:
            if isinstance(row, list) and len(row) > 2 and row[:2] == ["wrb.fr", "Fbv4je"] and isinstance(row[2], str):
                res = json.loads(row[2])
                real = res[1] if isinstance(res, list) and len(res) > 1 and res[0] == "garturlres" else None
                if isinstance(real, str) and real.startswith(("https://", "http://")) and not is_google_news(real) \
                        and len(real) <= 2000:
                    return real
        return None
    except Exception:  # noqa: BLE001
        return None
