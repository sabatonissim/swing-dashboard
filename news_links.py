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
                      r"buy or (sell|wait|hold)|"
                      # v47 (live feed 11.11.2026): "+102%, +40%: ... these AI-picked winners", "We told you first: these
                      # AI-picked small-cap stocks are up +49%", "New Strong Buy Stocks for November 11th",
                      # "Top 3 Real Estate Stocks That Could Blast Off In Q4"
                      r"ai-picked|we told you first|strong buy stocks?|blast off|"
                      # v44 (live feed 10.11.2026): "As Crude Oil Prices Retreat, Sell Soybean Oil Now" \u2014 advice, not news
                      r"(buy|sell|short)\s+(?:[\w&'-]+\s+){1,4}now(?!\s+that)|buying opportunity|"
                      r"screaming buys?)\b"       # "Tech Sell-Off: Two AI Stocks That Are Screaming Buys Right Now" (11.11.2026)
                      r"|^\s*(?:top\s+)?\d+\s+[\w\s&'-]{0,40}\bstocks?\b", re.I)
# v44: a newsletter's table of contents as a headline ("Hot inflation report puts Fed on track ...; The best banks to
# work for in 2026\u2014and more in Bankers' Hours")
# v47: a quote page and round-ups as headlines ("Crude Oil Dec 26 (CL=F) Stock Price, News, Quote & History", "AM Markets
# Need to Know: Fed faces inflation test, Anthropic IPO demand surges, and more", "Dow Jones Top Energy Headlines at 4 AM ET: ...")
_FORMAT_JUNK_RE = re.compile(r"^\s*(watch|video|listen|podcast)\s*[:\-\u2013\u2014]|^\s*q\s*[.:]\s|\band more (in|from)\b|"
                             r"\bstock price, news, quote\b|,? and more\s*$|\btop [\w ]{0,20}headlines at \d", re.I)
# v37: machine-made pages about one company's report: 1,029 of the 1,564 headlines of 5.11.2026 were tagged as earnings, most
# of them these. The stock's own page finds its company's news separately (_feed_clean with promo_only).
EARNINGS_CHATTER_RE = re.compile(r":\s*(fiscal\s+)?q[1-4]\s+earnings snapshot\s*$|\bearnings call (highlights|transcript|is live)\b|"
                                 r"\bsaid on its q[1-4] earnings call\b|\breleases? (first|second|third|fourth)[- ]quarter earnings\b|"
                                 # v44: machine-made previews of one company's report ("Home Depot (HD) Reports Next Week: Wall
                                 # Street Expects Earnings Growth", "Earnings Preview: Spire (SR) Q4 Earnings Expected to Decline")
                                 r"\bearnings preview\b|\breports next week\b|\bearnings expected to (decline|grow|rise|fall)\b|"
                                 r"\bexpected to beat earnings estimates\b|\banalysts estimate\b.*\bto report\b|"
                                 # v47: "Lowe's Companies, Inc. to Host Third Quarter 2026 Earnings Conference Call on Nov. 18",
                                 # "How Investors May Respond To Ross Stores Stock Ahead Of Earnings Growth Expectations",
                                 # "How Earnings Beat Will Impact Cloudflare Stock Investors", "(BOBS) Could Be 7% Undervalued As ..."
                                 r"\bto host\b.*\bearnings (conference )?call\b|\bhow investors may respond\b|"
                                 r"\bhow earnings beat will impact\b|\bcould be \d+% (under|over)valued\b", re.I)
# Countries whose domestic news rarely moves US stocks — dropped unless the headline also ties it to the US / world markets
# v37: also the adjectives. "Philippine Inflation Surges to 7.8%" passed and made the overview of a day the S&P 500 fell 4.36%.
LOCAL_RE = re.compile(r"\b(india|indian|russians?|ethiopia|ethiopian|pakistan|pakistani|nigeria|nigerian|kenya|kenyan|bangladesh|"
                      r"sri lanka|philippines?|filipino|indonesia|indonesian|vietnam|vietnamese|thailand|thai|malaysia|malaysian|"
                      r"argentina|argentine|egypt|egyptian|south africa|south african|ghana|zimbabwe|uganda|tanzania|sudan|"
                      r"myanmar|nepal|peru|peruvian|colombia|colombian|"
                      # v44: "Brazil's central bank signals high bar for rate hikes", "Poland's Maslowska sees growing case ..."
                      r"brazil|brazilian|poland|polish|turkey|turkish|hungary|hungarian|czech|romania|romanian|chile|chilean|"
                      # v47: "Moldova to fully join EU sanctions against Russia and Belarus", "Irish annual CPI inflation rises to 4.3%"
                      r"moldova|moldovan|belarus|belarusian|cuba|cuban|serbia|serbian|bulgaria|bulgarian|ireland|irish|"
                      r"turkmenistan|azerbaijan|kazakhstan|uzbekistan|lebanon|lebanese)\b", re.I)
# v44: another country's own stock index is not US market news even when the headline names the US as the reason
# ("Canadian stock market today: TSX futures drift higher in critical US inflation test")
FOREIGN_INDEX_RE = re.compile(r"\b(tsx|canadian (stocks?|shares|markets?|stock market)|sensex|nifty|asx ?200|bovespa)\b", re.I)
GLOBAL_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(america|american|fed|wall street|trump|washington|oil|crude|opec|global|"
                       r"world|dollar|treasur(y|ies)|tariffs?|nasdaq|s&p|chips?)\b)")
# v47: "sanctions" used to be in the list above, so the word itself let far-away news in ("Poland passes bill targeting
# crypto assets under sanctions", "Lukashenka calls for dialogue with Poland, criticizes sanctions"). And a sanctions
# headline must say who or what is hit that matters to US markets ("France threatens sanctions against EPR
# organisations over low packaging fees" does not).
_SANCTIONS_RE = re.compile(r"\bsanction(s|ed|ing)?\b", re.I)
_SANCTIONS_SUBJECT_RE = re.compile(r"\bU\.?S\.?\b|(?i:\b(america|american|trump|treasury|washington|white house|congress|"
                                   r"ofac|russia|russian|iran|iranian|china|chinese|venezuela|north korea|oil|crude|"
                                   r"energy|gas|tankers?|banks?|chips?|exports?)\b)")
_LOCAL_MARKET_RE = re.compile(r"\b(stocks?|markets?|shares)\b", re.I)
_SUBSTANCE_RE = re.compile(r"\d|%|\$|\b(stocks?|shares|markets?|fed|rates?|earnings|oil|gold|bitcoin|economy)\b", re.I)


def is_unwanted_headline(title: str) -> bool:
    """True for promotional listicles, video/Q&A items, too-short meaningless titles and far-away domestic news."""
    t = (title or "").strip()
    if not t or PROMO_RE.search(t) or _FORMAT_JUNK_RE.search(t) or EARNINGS_CHATTER_RE.search(t) or FOREIGN_INDEX_RE.search(t):
        return True
    if len(t.split()) < 5 and not _SUBSTANCE_RE.search(t):
        return True
    if _SANCTIONS_RE.search(t) and not _SANCTIONS_SUBJECT_RE.search(t):
        return True
    return bool(LOCAL_RE.search(t)) and not GLOBAL_RE.search(t) and not _LOCAL_MARKET_RE.search(t)


# ------------------------------------------------------------------
# v47 (live feed 11.11.2026). The Google News search for "war OR sanctions OR oil prices ..." returns whoever wrote those
# words: a state news agency of Iran, a Belarusian exile channel, local radio stations, a plastics trade paper. 13 of the
# 25 headlines on the home page came from such outlets. From THAT feed only established news outlets are taken (the
# owner approved the list). Matched against the outlet name in lower case.
# ------------------------------------------------------------------
TRUSTED_OUTLETS_RE = re.compile(
    r"reuters|bloomberg|cnbc|\bwsj\b|wall street journal|associated press|\bap news\b|^ap$|financial times|^ft$|"
    r"marketwatch|yahoo|barron|new york times|washington post|axios|fox business|fox news|economist|\bbbc\b|\bcnn\b|"
    r"politico|guardian|nbc news|abc news|cbs news|\bnpr\b|\bpbs\b|forbes|fortune|business insider|investing\.com|"
    r"seeking alpha|^the hill$|semafor|al jazeera|nikkei|south china morning post|france 24|^dw$|deutsche welle|"
    r"euronews|sky news|times of israel|jerusalem post|oilprice|s&p global|rigzone|benzinga|morningstar|usa today|"
    r"los angeles times|telegraph|^time$|^the times$|foreign policy|kitco|investopedia|\bupi\b|\bafp\b|"
    r"federal reserve|^bls$")


def is_trusted_outlet(name: str) -> bool:
    return bool(TRUSTED_OUTLETS_RE.search((name or "").strip().lower()))


# ------------------------------------------------------------------
# v47: the same story from several outlets. The list showed "Oil prices rise ... Strait of Hormuz" four times in a row
# (25 headlines = about 9 stories). Rows are still SAVED one by one — the key-events strip and the daily summary count
# how many outlets reported a story — and are folded into one line when the list is SHOWN.
# Two headlines tell the same story when the words they share are a large enough part of all the words of both
# (weighted Jaccard). A word's weight is its rarity among the headlines compared (idf): "Hormuz" or "CoreWeave" says
# what the story is, "Fed" on a day when every headline names the Fed says little. Words for "went up" / "went down"
# are folded into one each, so "oil rises" and "oil rebounds" meet while "oil rises" and "oil falls" do not.
# Measured on the 100 headlines of 11.11.2026 (tests/fixtures/news_2026_11_11.json): real pairs score 0.25 and up; at
# 0.21-0.24 real and false pairs mix ("Trump, Netanyahu clash ... Iran war" / "Trump says US won Iran war" = 0.24), so
# the line is 0.25 — a missed pair leaves the list as it was, a wrong pair hides a story behind another.
# ------------------------------------------------------------------
_STORY_STOP = frozenset((
    "a an the and or but as at by for from in into of on to with after amid over under than that this these its it is are "
    "was were be been will would could may might can says said say new more most out off how why what when who vs per "
    "about ahead despite while now still near toward towards his her their our your not no yes has have had amp").split())
_STORY_UP = frozenset((
    "rise rises rose rising gain gains gained climb climbs climbed jump jumps jumped rally rallies rallied surge surges "
    "surged advance advances advanced rebound rebounds rebounded soar soars soared pop pops popped higher "
    "recover recovers recovered up").split())
_STORY_DOWN = frozenset((
    "fall falls fell falling drop drops dropped slip slips slipped slide slides slid sink sinks sank tumble tumbles "
    "tumbled plunge plunges plunged retreat retreats retreated lower decline declines declined slump slumps down").split())
STORY_SAME = 0.25              # common weight / weight of all the words of the two headlines
STORY_MIN_SHARED = 3           # ... and at least this many common words
STORY_WINDOW_H = 36
# "Oil prices rise as ...", "Oil rebounds after ...", "... push oil prices up": on the same half day these are one story
# whatever reason each outlet gives — the words after "as" differ too much for the word count above (5 oil headlines
# stayed apart). Only for things with ONE price (not "stocks": European, memory and neocloud stocks are different stories).
STORY_MOVE_WINDOW_H = 12
_MOVE_SUBJECT = {"oil": "oil", "crude": "oil", "brent": "oil", "wti": "oil", "gold": "gold", "silver": "silver",
                 "dollar": "dollar", "bitcoin": "bitcoin", "ethereum": "ethereum"}
_MOVE_RE = re.compile(r"\b(" + "|".join(_MOVE_SUBJECT) + r")\s+(?:(?:prices?|futures)\s+)?([a-z]+)\b", re.I)


def story_move(title: str):
    """("oil", "up") for a headline that says a commodity / currency moved, else None."""
    for m in _MOVE_RE.finditer((title or "").lower()):
        way = "up" if m.group(2) in _STORY_UP else "down" if m.group(2) in _STORY_DOWN else None
        if way:
            return _MOVE_SUBJECT[m.group(1)], way
    return None


def story_words(title: str) -> frozenset:
    out = set()
    for w in re.findall(r"[a-z0-9][a-z0-9&.%$-]*", (title or "").lower().replace("’", "'").replace("'s ", " ")):
        w = w.strip(".-")
        if len(w) < 2 or w in _STORY_STOP:
            continue
        if w in _STORY_UP:
            w = "(up)"
        elif w in _STORY_DOWN:
            w = "(down)"
        elif len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return frozenset(out)


def story_groups(titles: list, hours: list = None) -> list:
    """titles in the order they are shown (newest first); hours = each one's age in hours (optional). Returns groups of
    indexes, each group in the given order, groups ordered by their first member."""
    import math
    words = [story_words(t) for t in titles]
    n = len(words)
    df: dict = {}
    for ws in words:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    weight = {w: math.log(1 + n / c) for w, c in df.items()}
    total = [sum(weight[w] for w in ws) for ws in words]
    moves = [story_move(t) for t in titles]

    def same(i, j):
        apart = abs(hours[i] - hours[j]) if hours else 0.0
        if apart > STORY_WINDOW_H:
            return 0.0
        if moves[i] and moves[i] == moves[j] and apart <= STORY_MOVE_WINDOW_H:
            return 1.0
        shared = words[i] & words[j]
        if len(shared) < STORY_MIN_SHARED:
            return 0.0
        # opposite directions are different stories ("oil rises" / "oil falls")
        if ("(up)" in words[i] and "(down)" in words[j] and "(up)" not in words[j]) or \
           ("(down)" in words[i] and "(up)" in words[j] and "(up)" not in words[i]):
            return 0.0
        common = sum(weight[w] for w in shared)
        return common / (total[i] + total[j] - common)

    groups: list = []
    for i in range(n):
        best, best_score = None, STORY_SAME
        for g in groups:
            s = max(same(j, i) for j in g)
            if s >= best_score:
                best, best_score = g, s
        if best is None:
            groups.append([i])
        else:
            best.append(i)
    return groups


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