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
        # v18e: the signature sits at the very END of a large page (/articles/ ~1.07MB, /rss/articles/ ~0.6MB, measured
        # 27.10.2026) — v18d read only the first 800KB, so every decode failed ("decoded 0, 5 failed" in the log).
        req = urllib.request.Request(f"https://news.google.com/rss/articles/{art_id}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read(3_000_000).decode("utf-8", errors="replace")
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


# ------------------------------------------------------------------
# v28 (live feed 3.11.2026): headline clean-up rules, shared by the news pipeline (before saving) and api_server
# (rows already saved). Junk seen on the site: "War Laura Thalassa OOP", "Q. Is this a credit card?", "Watch - Amer: ...",
# "+113%, +43%, See Which Stocks Are In ... Our AI Picks", India food inflation / Russian utility bills / Ethiopia —
# and outlet taglines glued to titles and names ("... - Euronext Markets: Real-time Stock Market Data | live").
# ------------------------------------------------------------------
PROMO_RE = re.compile(r"\b(stocks? to (buy|watch|own|bet on)|to bet on|should you (buy|sell|invest|bet)|buy now|"
                      r"is it (time to buy|a buy)|smart buy|buy the dip|"
                      r"stocks? (for|to) (retirees|beginners)|our (ai |top )?picks|stock picks|"
                      r"(to )?buy (ahead of|before) (the )?(print|earnings|results|report)|"   # v28d: "To Buy Ahead of the Print or Wait"
                      r"buy or (sell|wait|hold))\b"
                      r"|^\s*\d+\s+[\w\s&'-]{0,40}\bstocks?\b", re.I)
_FORMAT_JUNK_RE = re.compile(r"^\s*(watch|video|listen|podcast)\s*[:\-\u2013\u2014]|^\s*q\s*[.:]\s", re.I)
# v37: machine-made pages about one company's report: 1,029 of the 1,564 headlines of 5.11.2026 were tagged as earnings, most
# of them these. The stock's own page finds its company's news separately (_feed_clean with promo_only).
EARNINGS_CHATTER_RE = re.compile(r":\s*(fiscal\s+)?q[1-4]\s+earnings snapshot\s*$|\bearnings call (highlights|transcript|is live)\b|"
                                 r"\bsaid on its q[1-4] earnings call\b|\breleases? (first|second|third|fourth)[- ]quarter earnings\b", re.I)
# Countries whose domestic news rarely moves US stocks — dropped unless the headline also ties it to the US / world markets
# v37: also the adjectives. "Philippine Inflation Surges to 7.8%" passed and made the overview of a day the S&P 500 fell 4.36%.
LOCAL_RE = re.compile(r"\b(india|indian|russians?|ethiopia|ethiopian|pakistan|pakistani|nigeria|nigerian|kenya|kenyan|bangladesh|"
                      r"sri lanka|philippines?|filipino|indonesia|indonesian|vietnam|vietnamese|thailand|thai|malaysia|malaysian|"
                      r"argentina|argentine|egypt|egyptian|south africa|south african|ghana|zimbabwe|uganda|tanzania|sudan|"
                      r"myanmar|nepal|peru|peruvian|colombia|colombian)\b", re.I)
GLOBAL_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(america|american|fed|wall street|trump|washington|oil|crude|opec|global|"
                       r"world|dollar|treasur(y|ies)|tariffs?|sanctions?|nasdaq|s&p|chips?)\b)")
_LOCAL_MARKET_RE = re.compile(r"\b(stocks?|markets?|shares)\b", re.I)
_SUBSTANCE_RE = re.compile(r"\d|%|\$|\b(stocks?|shares|markets?|fed|rates?|earnings|oil|gold|bitcoin|economy)\b", re.I)


def is_unwanted_headline(title: str) -> bool:
    """True for promotional listicles, video/Q&A items, too-short meaningless titles and far-away domestic news."""
    t = (title or "").strip()
    if not t or PROMO_RE.search(t) or _FORMAT_JUNK_RE.search(t) or EARNINGS_CHATTER_RE.search(t):
        return True
    if len(t.split()) < 5 and not _SUBSTANCE_RE.search(t):
        return True
    return bool(LOCAL_RE.search(t)) and not GLOBAL_RE.search(t) and not _LOCAL_MARKET_RE.search(t)


def clean_publisher(name: str) -> str:
    """Outlet name without its tagline: "The Daily News | Texas' Oldest Newspaper" -> "The Daily News",
    "Euronext Markets: Real-time Stock Market Data | live" -> "Euronext Markets"."""
    name = (name or "").strip()
    short = re.split(r"\s+[|\u2013\u2014]\s+|:\s+", name)[0].strip()
    return short if len(short) >= 2 else name


def strip_outlet_suffix(title: str, publisher: str) -> str:
    """A title without a trailing " - <outlet name>" (Google News adds it; long outlet names escaped the old 40-char rule)."""
    title = (title or "").strip()
    for name in {(publisher or "").strip(), clean_publisher(publisher)}:
        if name and title.endswith(" - " + name):
            return title[: -len(" - " + name)].strip()
    short = clean_publisher(publisher)
    i = title.rfind(" - " + short) if short else -1
    if i > 0 and len(short) >= 4:          # the translated tail of a long tagline: cut from the outlet name on
        return title[:i].strip()
    return title