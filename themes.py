"""Hot market themes (v80, owner 27.11.2026): which investing themes (AI chips, nuclear, quantum, space, cyber...) money and
attention are moving into or out of. Shown first in Market -> "Stocks and funds" (/api/themes).

Once a market day after the close (scheduler.py, "themes"), from GitHub Actions:
  1. prices + volume of every theme's stocks (Yahoo, 1 year daily, real prices) and of SPY;
  2. per theme (equal weight): return 1D/1W/1M/3M, against the S&P 500, how many members went up this week, how many are
     above their 50-day average and within 5% of their 52-week high, trading volume of the last 5 days against the 50 before;
  3. public attention: Wikipedia page views of the theme's articles, the last 7 days against the 4 weeks before (free,
     no sign-up; Wikimedia limits bursts, so one request every ~2.5 s);
  4. how many of our stored news headlines (macro_news) named the theme in the last 7 days against the 4 weeks before
     (counted by the database, one small answer);
  5. a fixed, written-down label per theme (THEME_LABEL_RULES): hot / heating / cooling / weak / quiet. A description of
     what happened, not a recommendation.
One row per day in theme_snapshots (~30KB, kept 120 days).

A theme or member is a line in THEMES. Members were checked against Yahoo on 27.11.2026 (GOLD is now Gold.com, SATS now
Strive, CYBR/PSTG/INFN/TERN/ZEAL gave no prices); a member with no prices is left out of that day and named in the log.
"""
import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

KEEP_DAYS = 120
CHUNK = 60
WIKI_GAP_SEC = 2.5
WIKI_UA = "SwingDesk-themes/1.0 (daily theme attention check)"

# news: Postgres regular expressions (\y = word boundary), matched case-insensitively against the English headline
THEMES: List[dict] = [
    dict(key="ai_chips", he="שבבי AI", en="AI chips", etf="SMH",
         members="NVDA AMD AVGO MRVL ARM TSM ALAB CRDO MPWR QCOM INTC LSCC SITM AMBA NVTS",
         wiki=["Nvidia", "AI_accelerator"], news=r"\y(chips?|chipmakers?|semiconductors?|gpus?|nvidia|broadcom|amd)\y"),
    dict(key="chip_equipment", he="ציוד לייצור שבבים", en="Chip equipment", etf="SOXX",
         members="AMAT LRCX KLAC ASML TER ONTO ENTG AMKR CAMT NVMI ACMR FORM KLIC AEHR",
         wiki=["ASML_Holding", "Photolithography"],
         news=r"\y(chip ?making equipment|chip equipment|asml|applied materials|lam research|kla|lithography)\y"),
    dict(key="memory", he="זיכרון ואחסון", en="Memory & storage", etf=None,
         members="MU SNDK WDC STX NTAP",
         wiki=["High_Bandwidth_Memory", "Micron_Technology"],
         news=r"\y(memory chips?|dram|nand|hbm|micron|sandisk|seagate|western digital)\y"),
    dict(key="optical", he="תקשורת אופטית ורשתות", en="Optical & networking", etf=None,
         members="ANET CIEN COHR LITE AAOI FN CLS POET NOK GLW",
         wiki=["Silicon_photonics", "Co-packaged_optics"],
         news=r"\y(optical|photonics|arista|ciena|lumentum|coherent|corning)\y"),
    dict(key="neocloud", he="ניאו-קלאוד ושרתי AI", en="Neoclouds & AI servers", etf=None,
         members="CRWV NBIS IREN APLD CIFR WULF CORZ GLXY SMCI",
         wiki=["Neocloud", "CoreWeave", "Nebius_Group"],
         news=r"\y(neoclouds?|coreweave|nebius|super micro|ai infrastructure|ai data cent(er|re)s?)\y"),
    dict(key="dc_power", he="חשמל לחוות שרתים", en="Power for data centers", etf="GRID",
         members="VRT ETN GEV PWR VST CEG TLN NRG BE MOD POWL HUBB PRIM EME FIX",
         wiki=["GE_Vernova", "Vertiv", "Data_center"],
         news=r"\y(power grid|electricity demand|power demand|grid|vertiv|ge vernova|vistra|constellation energy)\y"),
    dict(key="nuclear", he="אנרגיה גרעינית ואורניום", en="Nuclear & uranium", etf="URA",
         members="CCJ OKLO SMR LEU NNE BWXT UEC UUUU NXE DNN LTBR URG",
         wiki=["Small_modular_reactor", "Uranium", "Oklo"],
         news=r"\y(nuclear|uranium|reactors?|smrs?|oklo|cameco|nuscale)\y"),
    dict(key="quantum", he="מחשוב קוונטי", en="Quantum computing", etf="QTUM",
         members="IONQ RGTI QBTS QUBT ARQQ LAES QMCO",
         wiki=["Quantum_computing", "IonQ"], news=r"\y(quantum)\y"),
    dict(key="space", he="חלל ולוויינים", en="Space & satellites", etf="UFO",
         members="RKLB ASTS LUNR PL RDW BKSY FLY VOYG IRDM GSAT SPCE MNTS",
         wiki=["Rocket_Lab", "AST_SpaceMobile"],
         news=r"\y(space ?x|rocket lab|satellites?|spacecraft|rocket launch|nasa|space stocks?)\y"),
    dict(key="cyber", he="סייבר", en="Cybersecurity", etf="CIBR",
         members="CRWD PANW ZS NET FTNT S OKTA TENB RPD QLYS VRNS CHKP RBRK SAIL",
         wiki=["Computer_security", "CrowdStrike"],
         news=r"\y(cyber\w*|hack(ers?|ed|ing)?|ransomware|data breach|crowdstrike|palo alto networks|zscaler|fortinet)\y"),
    dict(key="drones_defense", he="רחפנים וטכנולוגיית ביטחון", en="Drones & defense tech", etf="SHLD",
         members="PLTR KTOS AVAV RCAT UMAC ONDS DPRO AIRO PDYN BBAI KITT",
         wiki=["Unmanned_aerial_vehicle", "Palantir_Technologies"],
         news=r"\y(drones?|defen[cs]e (tech|stocks|spending|budget)|pentagon|palantir|kratos|aerovironment)\y"),
    dict(key="robotics", he="רובוטיקה", en="Robotics", etf="BOTZ",
         members="SYM SERV RR TER ISRG CGNX ROK ZBRA PDYN ARBE",
         wiki=["Humanoid_robot"], news=r"\y(robots?|robotics|humanoids?|robotaxis?|automation)\y"),
    dict(key="crypto", he="מניות קריפטו", en="Crypto stocks", etf=None,
         members="COIN MSTR HOOD MARA RIOT CLSK CRCL BLSH BMNR SBET GEMI HUT BTDR",
         wiki=["Bitcoin", "Stablecoin"],
         news=r"\y(bitcoin|crypto\w*|stablecoins?|ether(eum)?|coinbase|microstrategy|strategy inc)\y"),
    dict(key="gold", he="כורי זהב וכסף", en="Gold & silver miners", etf="GDX",
         members="NEM AEM B WPM FNV KGC AU AGI RGLD PAAS HMY GFI",
         wiki=["Gold_as_an_investment"], news=r"\y(gold|silver|precious metals?|bullion)\y"),
    dict(key="obesity", he="תרופות הרזיה", en="Weight-loss drugs", etf=None,
         members="LLY NVO VKTX HIMS GPCR ALT AMGN",
         wiki=["Semaglutide", "Tirzepatide"],
         news=r"\y(obesity|weight[- ]loss|glp-1|wegovy|zepbound|ozempic|mounjaro|eli lilly|novo nordisk)\y"),
    dict(key="ai_software", he="תוכנה ו-AI", en="AI software", etf="IGV",
         members="PLTR APP SNOW DDOG MDB NOW CRM AI SOUN BBAI PATH TEM ESTC GTLB",
         wiki=["AI_agent", "Generative_artificial_intelligence"],
         news=r"\y(ai agents?|agentic|software stocks?|saas|palantir|servicenow|salesforce|snowflake)\y"),
    dict(key="solar", he="אנרגיה סולארית", en="Solar", etf="TAN",
         members="FSLR ENPH SEDG RUN NXT ARRY CSIQ JKS SHLS",
         wiki=["Solar_power"], news=r"\y(solar)\y"),
    dict(key="batteries", he="סוללות וליתיום", en="Batteries & lithium", etf="LIT",
         members="QS ENVX SLDP EOSE FLNC AMPX ALB SQM LAC SGML",
         wiki=["Lithium-ion_battery", "Solid-state_battery"],
         news=r"\y(batter(y|ies)|lithium|energy storage)\y"),
    dict(key="rare_earths", he="מתכות נדירות ומינרלים קריטיים", en="Rare earths & critical minerals", etf="REMX",
         members="MP USAR UAMY CRML TMC NB ALM",
         wiki=["Rare-earth_element"], news=r"\y(rare earths?|critical minerals?|antimony|mp materials)\y"),
    dict(key="china_tech", he="טכנולוגיה סינית", en="China tech", etf="KWEB",
         members="BABA PDD JD BIDU NIO XPEV LI BILI TME NTES FUTU",
         wiki=["Alibaba_Group"],
         news=r"\y(alibaba|baidu|tencent|chinese (tech|stocks)|china tech|hang seng)\y"),
    dict(key="evtol", he="מוניות מעופפות (eVTOL)", en="Flying taxis (eVTOL)", etf=None,
         members="JOBY ACHR EH EVEX EVTL BETA",
         wiki=["EVTOL", "Joby_Aviation", "Archer_Aviation"],
         news=r"\y(evtol|air taxis?|flying (taxis?|cars?)|joby|archer aviation)\y"),
]

# the label of a theme, from its equal-weight numbers against the S&P 500 (rel_* = theme minus S&P, in percentage points).
# Checked in this order; the first that fits is the label. Written in plain words on the site under the table.
THEME_LABEL_RULES = {
    "hot":     dict(rel_1m_min=8.0, rel_1w_min=0.0, above50_share_min=0.6),
    "heating": dict(rel_1w_min=3.0, up_1w_share_min=0.6, vol_ratio_min=1.2),
    "cooling": dict(rel_3m_min=10.0, rel_1m_max=-5.0),
    "weak":    dict(rel_1m_max=-8.0, rel_1w_max=0.0),
}
LABEL_ORDER = ["hot", "heating", "cooling", "weak", "quiet"]
NEAR_HIGH = 0.05          # within 5% of the 52-week closing high
SERIES_DAYS = 63          # chart: the last ~3 months
# v80b (owner 27.11: "the month's return is not the same number" as the chart): one line per period the table sorts by,
# each starting at 0 on that period's first day, so its last point = the return in the table (the equal-weight average)
PERIOD_DAYS = {"1w": 5, "1m": 21, "3m": SERIES_DAYS}


def members_of(theme: dict) -> List[str]:
    return theme["members"].split()


def all_tickers() -> List[str]:
    out = {"SPY"}
    for t in THEMES:
        out.update(members_of(t))
        if t.get("etf"):
            out.add(t["etf"])
    return sorted(out)


def download(tickers: List[str]):
    """(close, volume) DataFrames: dates x tickers, real prices (auto_adjust=False, like the scanner)."""
    import yfinance as yf
    closes, vols = [], []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        for attempt in range(3):
            try:
                df = yf.download(chunk, period="1y", interval="1d", group_by="column", auto_adjust=False,
                                 threads=True, progress=False)
                if df is not None and not df.empty:
                    closes.append(df["Close"])
                    vols.append(df["Volume"])
                    break
            except Exception as e:  # noqa: BLE001
                print(f"[warn] themes: download chunk {i // CHUNK + 1} attempt {attempt + 1} failed: {e}")
            time.sleep(3 * (attempt + 1))
    if not closes:
        raise RuntimeError("no price data downloaded")
    got = set()
    for part in closes:
        got |= set(part.columns[part.notna().any()])
    lost = [t for t in tickers if t not in got]
    if lost:                     # threads=True can drop a random ticker (market_breadth.py v31b) — once more, one at a time
        time.sleep(2)
        try:
            df = yf.download(lost, period="1y", interval="1d", group_by="column", auto_adjust=False,
                             threads=False, progress=False)
            if df is not None and not df.empty:
                as_frame = lambda s: s.to_frame(lost[0]) if isinstance(s, pd.Series) else s
                closes.append(as_frame(df["Close"]))
                vols.append(as_frame(df["Volume"]))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] themes: retry failed: {e}")

    def tidy(parts):
        df = pd.concat(parts, axis=1)
        idx = pd.to_datetime(df.index)
        df.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
        df = df[~df.index.duplicated(keep="last")].sort_index().dropna(axis=1, how="all")
        return df.loc[:, ~df.columns.duplicated()].apply(pd.to_numeric, errors="coerce")

    close = tidy(closes)
    return close, tidy(vols).reindex(index=close.index, columns=close.columns)


def _ret(s: pd.Series, n: int) -> Optional[float]:
    """Return over the last n sessions of the shared calendar (v80c: a missing day = the last price before it, as on the
    chart line — counting n of the stock's own rows took a different week when one day was missing: memory 27.11)."""
    s = s.ffill()
    if len(s) <= n or pd.isna(s.iloc[-1 - n]) or not s.iloc[-1 - n] or pd.isna(s.iloc[-1]):
        return None
    return round((float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1) * 100, 2)


def _avg(vals) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def member_stats(close: pd.Series, volume: pd.Series) -> Optional[dict]:
    """One stock's numbers, or None when it has too little history (a new listing still counts from 22 sessions)."""
    c = close.dropna()
    if len(c) < 22:
        return None
    last = float(c.iloc[-1])
    hi = float(c.max())
    dv = (close * volume).dropna()
    vol_ratio = None
    if len(dv) >= 30:
        recent, before = dv.iloc[-5:].mean(), dv.iloc[-55:-5].mean()
        vol_ratio = round(float(recent / before), 2) if before and before > 0 else None
    sma50 = float(c.iloc[-50:].mean()) if len(c) >= 50 else None
    return {
        "r1d": _ret(close, 1), "r1w": _ret(close, 5), "r1m": _ret(close, 21), "r3m": _ret(close, 63),
        "from_high": round((last / hi - 1) * 100, 1) if hi else None,
        "above50": (last > sma50) if sma50 else None,
        "vol_ratio": vol_ratio,
        "price": round(last, 2),
    }


def basket_series(close: pd.DataFrame, tickers: List[str], days: int = SERIES_DAYS) -> List[Optional[float]]:
    """Equal-weight theme line over the last `days` sessions, 0 = the first day (each member rebased, then averaged)."""
    part = close[tickers].iloc[-(days + 1):]
    base = part.iloc[0]
    ok = [t for t in tickers if pd.notna(base.get(t)) and base[t]]
    if not ok:
        return []
    norm = part[ok].ffill().div(base[ok]) - 1
    return [None if pd.isna(v) else round(float(v) * 100, 1) for v in norm.mean(axis=1)]


def theme_label(s: dict) -> str:
    r = THEME_LABEL_RULES
    rel_1w, rel_1m, rel_3m = s.get("rel_1w"), s.get("rel_1m"), s.get("rel_3m")
    n = s.get("n") or 0
    up_share = (s.get("up_1w") or 0) / n if n else 0
    above_share = (s.get("above50") or 0) / n if n else 0
    vol = s.get("vol_ratio")
    if rel_1m is not None and rel_1w is not None and rel_1m >= r["hot"]["rel_1m_min"] \
            and rel_1w >= r["hot"]["rel_1w_min"] and above_share >= r["hot"]["above50_share_min"]:
        return "hot"
    if rel_1w is not None and vol is not None and rel_1w >= r["heating"]["rel_1w_min"] \
            and up_share >= r["heating"]["up_1w_share_min"] and vol >= r["heating"]["vol_ratio_min"]:
        return "heating"
    if rel_3m is not None and rel_1m is not None and rel_3m >= r["cooling"]["rel_3m_min"] \
            and rel_1m <= r["cooling"]["rel_1m_max"]:
        return "cooling"
    if rel_1m is not None and rel_1w is not None and rel_1m <= r["weak"]["rel_1m_max"] and rel_1w <= r["weak"]["rel_1w_max"]:
        return "weak"
    return "quiet"


def theme_stats(theme: dict, close: pd.DataFrame, volume: pd.DataFrame, spy: dict) -> Optional[dict]:
    members, missing = [], []
    for t in members_of(theme):
        st = member_stats(close[t], volume[t]) if t in close.columns else None
        if st is None:
            missing.append(t)
        else:
            members.append(dict(ticker=t, **st))
    if len(members) < 3:
        return None
    vols = sorted(m["vol_ratio"] for m in members if m["vol_ratio"] is not None)
    out = {
        "key": theme["key"], "he": theme["he"], "en": theme["en"], "n": len(members), "missing": missing,
        "r1d": _avg(m["r1d"] for m in members), "r1w": _avg(m["r1w"] for m in members),
        "r1m": _avg(m["r1m"] for m in members), "r3m": _avg(m["r3m"] for m in members),
        "up_1w": sum(1 for m in members if (m["r1w"] or 0) > 0),
        "above50": sum(1 for m in members if m["above50"]),
        "near_high": sum(1 for m in members if m["from_high"] is not None and m["from_high"] >= -NEAR_HIGH * 100),
        "vol_ratio": vols[len(vols) // 2] if vols else None,          # the median member: one wild stock does not move it
    }
    for p in ("1w", "1m", "3m"):
        a, b = out[f"r{p}"], spy.get(f"r{p}")
        out[f"rel_{p}"] = round(a - b, 2) if a is not None and b is not None else None
    etf = theme.get("etf")
    if etf and etf in close.columns:
        c = close[etf]
        out["etf"] = {"ticker": etf, "r1w": _ret(c, 5), "r1m": _ret(c, 21), "r3m": _ret(c, 63)}
    out["label"] = theme_label(out)
    names = [m["ticker"] for m in members]
    out["series"] = {p: basket_series(close, names, n) for p, n in PERIOD_DAYS.items()}
    out["members"] = sorted(members, key=lambda m: -(m["r1w"] if m["r1w"] is not None else -999))
    return out


def wiki_views(titles: List[str], end: date) -> Optional[dict]:
    """Sum of the theme's articles: views of the last 7 days, the weekly average of the 4 weeks before, and the ratio."""
    start = end - timedelta(days=40)
    days: Dict[str, int] = {}
    for title in titles:
        url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
               f"{requests.utils.quote(title, safe='')}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00")
        for attempt in range(3):
            time.sleep(WIKI_GAP_SEC)
            try:
                r = requests.get(url, headers={"User-Agent": WIKI_UA}, timeout=20)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] themes: wikipedia {title}: {type(e).__name__}")
                continue
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code != 200:
                print(f"[warn] themes: wikipedia {title}: HTTP {r.status_code}")
                break
            for it in r.json().get("items", []):
                d = str(it.get("timestamp", ""))[:8]
                days[d] = days.get(d, 0) + int(it.get("views") or 0)
            break
    return attention(days)


def attention(days: Dict[str, int]) -> Optional[dict]:
    """{yyyymmdd: views} -> last 7 days vs the weekly average of the 28 before (needs all 35 days)."""
    keys = sorted(days)
    if len(keys) < 35:
        return None
    last7 = sum(days[k] for k in keys[-7:])
    prev = sum(days[k] for k in keys[-35:-7]) / 4
    return {"last7": last7, "prev_week_avg": round(prev), "ratio": round(last7 / prev, 2) if prev else None}


def news_counts(cur) -> Dict[str, dict]:
    """Headlines that named each theme: last 7 days, and the weekly average of the 4 weeks before. One query, one row."""
    parts, params = [], []
    for t in THEMES:
        parts.append("COUNT(*) FILTER (WHERE timestamp >= NOW() - INTERVAL '7 days' AND summary_en ~* %s)")
        parts.append("COUNT(*) FILTER (WHERE timestamp < NOW() - INTERVAL '7 days' AND summary_en ~* %s)")
        params += [t["news"], t["news"]]
    cur.execute(f"SELECT {', '.join(parts)} FROM macro_news WHERE timestamp >= NOW() - INTERVAL '35 days'", params)
    row = cur.fetchone()
    out = {}
    for i, t in enumerate(THEMES):
        last7, before = int(row[2 * i] or 0), int(row[2 * i + 1] or 0)
        out[t["key"]] = {"last7": last7, "prev_week_avg": round(before / 4, 1)}
    return out


def build(close: pd.DataFrame, volume: pd.DataFrame) -> dict:
    if "SPY" not in close.columns:
        raise RuntimeError("no SPY prices")
    s = close["SPY"]
    spy = {"r1d": _ret(s, 1), "r1w": _ret(s, 5), "r1m": _ret(s, 21), "r3m": _ret(s, 63)}
    themes, skipped = [], []
    for t in THEMES:
        st = theme_stats(t, close, volume, spy)
        if st is None:
            skipped.append(t["key"])
            continue
        if st["missing"]:
            print(f"themes: {t['key']}: no usable prices for {', '.join(st['missing'])}")
        themes.append(st)
    if skipped:
        print(f"[warn] themes: too few members with prices, left out: {', '.join(skipped)}")
    day = s.dropna().index[-1].date()
    return {"day": day.isoformat(), "spy": spy,
            "spy_series": {p: basket_series(close, ["SPY"], n) for p, n in PERIOD_DAYS.items()},
            "series_days": {p: [d.date().isoformat() for d in close.index[-(n + 1):]] for p, n in PERIOD_DAYS.items()},
            "themes": themes}


# ------------------------------------------------------------------ v80d: Reddit (ApeWisdom), stage 2 (owner 27.11.2026)
# ApeWisdom counts how many times each ticker was named on the stock subreddits (wallstreetbets, stocks, investing...) in
# the last 24 hours, and in the 24 hours before. Free, no key; no history, so every run stores its own day (extra column).
APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{}"
APEWISDOM_MAX_PAGES = 10
# Tickers that are trading shorthand written in capitals on Reddit ("DTE" = days to expiration, "ES" = S&P futures, "IT",
# "ALL", "ON"...): never counted. v80e (owner: "on Reddit and X a stock is written with $, like $NOW"): ApeWisdom already
# counts a word ticker only as $NOW / $now (its methodology page; 27.11: "AI" not listed at all, "NOW" 7) — so AI, NOW, S,
# B, RUN, NET, PATH... are counted; only shorthand that it still counts in capitals (DTE 39, ES 22, IT 15, ALL 17) is here.
REDDIT_SKIP = set("""A ALL AM AN ARE AT ATH BIG BY CAN CASH CEO CPI DD DO DTE EOD ES ET EV FED FOR FUD GDP GO HAS HE HOLD IPO
IQ IS IT KEY LOVE LOW ME MOD NEXT ON ONE OPEN OR PLAY PR PS REAL SO TOP TV U UP USA VS WELL YOU""".split())
ETF_NAME_RE = re.compile(r"\b(ETF|ETN|Trust|Fund|ProShares|Direxion|iShares|SPDR|Vanguard|Invesco|Index)\b", re.I)
TALKED_MIN = 15           # mentions in 24 hours before a jump is shown at all
TALKED_RATIO = 3.0        # ... and at least 3x its usual
TALKED_SHOW = 10
REDDIT_HISTORY_DAYS = 10  # stored days the "usual" is averaged over (until 5 exist: the 24 hours before)
REDDIT_MIN_HISTORY = 5


def fetch_reddit() -> Dict[str, dict]:
    """{ticker: {"m": mentions 24h, "m24": the 24h before, "name": ...}} of every ticker ApeWisdom lists."""
    out: Dict[str, dict] = {}
    pages = 1
    p = 1
    while p <= min(pages, APEWISDOM_MAX_PAGES):
        try:
            r = requests.get(APEWISDOM_URL.format(p), headers={"User-Agent": "Mozilla/5.0 (SwingDesk themes)"}, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] themes: reddit page {p}: {type(e).__name__}: {e}")
            break
        pages = int(data.get("pages") or 1)
        for x in data.get("results") or []:
            t = str(x.get("ticker") or "").upper().strip()
            if t:
                out[t] = {"m": int(x.get("mentions") or 0), "m24": int(x.get("mentions_24h_ago") or 0),
                          "name": html.unescape(str(x.get("name") or ""))}
        p += 1
        time.sleep(1)
    return out


def reddit_theme_sums(rd: Dict[str, dict]) -> Dict[str, dict]:
    """Per theme: mentions of its members (word tickers left out), and the 24 hours before."""
    out = {}
    for t in THEMES:
        names = [m for m in members_of(t) if m not in REDDIT_SKIP]
        out[t["key"]] = {"mentions": sum(rd[m]["m"] for m in names if m in rd),
                         "prev_day": sum(rd[m]["m24"] for m in names if m in rd)}
    return out


def usual(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if len(values) >= REDDIT_MIN_HISTORY else None


def talked_about(rd: Dict[str, dict], history: List[dict]) -> List[dict]:
    """Stocks whose Reddit mentions jumped: >= TALKED_MIN today and >= TALKED_RATIO x the usual (the average of the stored
    days, or the 24 hours before while there are fewer than REDDIT_MIN_HISTORY). ETFs and word tickers left out."""
    theme_of: Dict[str, List[str]] = {}
    for t in THEMES:
        for m in members_of(t):
            theme_of.setdefault(m, []).append(t["key"])
    out = []
    for tk, x in rd.items():
        if tk in REDDIT_SKIP or x["m"] < TALKED_MIN or ETF_NAME_RE.search(x["name"]):
            continue
        base = usual([h.get(tk, 0) for h in history])
        base_kind = "usual" if base is not None else "day_before"
        if base is None:
            base = x["m24"]
        ratio = x["m"] / max(base, 3)
        if ratio >= TALKED_RATIO:
            out.append({"ticker": tk, "name": x["name"], "mentions": x["m"], "base": base, "base_kind": base_kind,
                        "ratio": round(ratio, 1), "themes": theme_of.get(tk, [])})
    out.sort(key=lambda r: (-r["ratio"], -r["mentions"]))
    return out[:TALKED_SHOW]


# ------------------------------------------------------------------ v80d: words rising in our headlines
PHRASE_STOP = set("""a about above after again against all also amid an and any are as at back be been before being below
between both but by can could day days did do does down during each early few for from further had has have he her his how
i if in into is it its just late latest made make makes may might more most much near new news no nor not now of off on
once one only or other our out over own per said same say says see sees set she should since so some still such than that
the their them then there these they this those through to today too under until up us u.s. very was we week weekly were
what when where which while who why will with would year years yet you your vs via amid ahead set sets gets get got
stock stocks share shares market markets price prices report reports reported percent pct record high higher low lower
rise rises rose fall falls fell gain gains gained drop drops dropped jump jumps surge surges slump slumps climb climbs
trading trade trades investors investor analysts analyst wall street dow nasdaq s&p index futures quarter quarterly
monday tuesday wednesday thursday friday saturday sunday january february march april june july august september
october november december first second third fourth biggest big top best amp
vow vows vowed find finds found remain remains remained raise raises raising raised mount mounts mounting enough aren't
isn't won't can't don't doesn't didn't despite unlikely uncomfortably look looks looking eye eyes weigh weighs weighing
seen push pushes pushing plan plans planned warn warns warned expect expects expected focus return returns returned
mixed ease eases eased fear fears hope hopes data could should might reinstate reinstates reinstated name names named
take takes taking keep keeps hit hits face faces facing pressure boost boosts lead leads led move moves amid again like
key big ahead set""".split())
PHRASE_MIN = 6            # times in the last 7 days
PHRASE_RATIO = 3.0        # against the weekly average of the 4 weeks before (+1)
PHRASE_SHOW = 12
PHRASE_EVERY_DAYS = 6     # read the headlines again only once a week (~0.7MB from the database)
PHRASES_VERSION = 2       # v80e: a change in the rules = a new number, so the next run does not wait the week


PHRASE_MAX_WORDS = 5


def headline_words(title: str) -> List[str]:
    """The words in order; a number or a break (: , ; . ! ? | dash) stays as a token of its own, so no pair is made across
    it ("rate cap 10%: banks" must not give "cap banks")."""
    text = re.sub(r"[:;,.!?|\u2014\u2013()\[\]\"]|\s-\s", " | ", str(title or "").lower().replace("\u2019", "'"))
    words = re.findall(r"\||[a-z0-9][a-z0-9&'\-%]*", text)
    return [re.sub(r"'s$", "", w).strip("'-") for w in words]          # "nvidia's ceo" = "nvidia ceo"


def headline_phrases(title: str) -> set:
    """The two-word phrases of one headline (each once), neither word common or about the market in general."""
    words = headline_words(title)
    out = set()
    for a, b in zip(words, words[1:]):
        if a in PHRASE_STOP or b in PHRASE_STOP or len(a) < 2 or len(b) < 2 or a[0].isdigit() or b[0].isdigit():
            continue
        out.add(f"{a} {b}")
    return out


def rising_phrases(rows: List[tuple], now: datetime) -> List[dict]:
    """rows = (headline, time) or (headline, Hebrew headline, time). Phrases in at least PHRASE_MIN different headlines in
    the last 7 days and PHRASE_RATIO x more than in an average week of the 4 before, with the newest headline that has it
    (and its Hebrew, when stored). v80e: the same headline from several outlets counts once (27.11: "aren't enough" = 15
    copies of one headline)."""
    recent: Dict[str, int] = {}
    before: Dict[str, int] = {}
    example: Dict[str, tuple] = {}
    seen = set()
    cut = now - timedelta(days=7)
    for row in rows:
        title, title_he, ts = (row[0], None, row[1]) if len(row) == 2 else row
        if ts is None:
            continue
        key = (ts >= cut, re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip())
        if key in seen:
            continue
        seen.add(key)
        bucket = recent if ts >= cut else before
        for ph in headline_phrases(title):
            bucket[ph] = bucket.get(ph, 0) + 1
            if ts >= cut and (ph not in example or ts > example[ph][2]):
                example[ph] = (title, title_he, ts)
    out = []
    for ph, n in recent.items():
        prev = before.get(ph, 0) / 4
        if n >= PHRASE_MIN and n >= PHRASE_RATIO * (prev + 1):
            out.append({"phrase": ph, "last7": n, "prev_week_avg": round(prev, 1), "example": example[ph][0],
                        "example_he": example[ph][1] or None})
    # v80e: pairs that overlap in the same headlines ("credit card", "card rate", "rate cap") are one phrase: the words of
    # the example headline that they cover together ("credit card rate cap", up to PHRASE_MAX_WORDS); the numbers are
    # those of the pair named most often in the 5 weeks
    out.sort(key=lambda r: (-(r["last7"] + 4 * r["prev_week_avg"]), r["phrase"]))
    groups: List[dict] = []
    for r in out:
        words = set(r["phrase"].split())
        hit = [g for g in groups if g["rep"]["example"] == r["example"] and g["words"] & words]
        if hit:                                   # joins every group it overlaps ("a b" + "c d", then "b c")
            g = hit[0]
            for other in hit[1:]:
                g["pairs"] += other["pairs"]
                g["words"] |= other["words"]
                groups.remove(other)
            g["pairs"].append(r["phrase"])
            g["words"] |= words
        else:
            groups.append({"rep": r, "pairs": [r["phrase"]], "words": words})
    kept = []
    for g in groups:
        rep = dict(g["rep"])
        toks = headline_words(rep["example"])
        at = sorted({i for i in range(len(toks) - 1) for ph in g["pairs"] if f"{toks[i]} {toks[i + 1]}" == ph})
        if at and at == list(range(at[0], at[-1] + 1)) and at[-1] + 2 - at[0] <= PHRASE_MAX_WORDS:
            rep["phrase"] = " ".join(toks[at[0]:at[-1] + 2])
        kept.append(rep)
    kept.sort(key=lambda r: (-(r["last7"] / (r["prev_week_avg"] + 1)), -r["last7"]))
    return kept[:PHRASE_SHOW]


# ------------------------------------------------------------------ v80d: S&P 500 sub-industries that move together
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
SUBIND_MIN = 3


def sp500_groups() -> Dict[str, dict]:
    """{ticker: {"sub": GICS sub-industry, "sector": ...}}"""
    import csv
    import io
    r = requests.get(SP500_CSV, timeout=20)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        t = str(row.get("Symbol") or "").strip().upper().replace(".", "-")
        if t:
            out[t] = {"sub": (row.get("GICS Sub-Industry") or "").strip(), "sector": (row.get("GICS Sector") or "").strip()}
    return out


def subindustry_stats(close: pd.DataFrame, groups: Dict[str, dict], spy: dict) -> List[dict]:
    """Equal-weight return of every GICS sub-industry with SUBIND_MIN+ stocks, against the S&P 500."""
    by: Dict[str, List[str]] = {}
    for t, g in groups.items():
        if g["sub"] and t in close.columns:
            by.setdefault(g["sub"], []).append(t)
    out = []
    for sub, names in by.items():
        rets = {t: {p: _ret(close[t], n) for p, n in PERIOD_DAYS.items()} for t in names}
        rets = {t: r for t, r in rets.items() if r["1m"] is not None}
        if len(rets) < SUBIND_MIN:
            continue
        row = {"name": sub, "sector": groups[next(iter(rets))]["sector"], "n": len(rets),
               "up_1w": sum(1 for r in rets.values() if (r["1w"] or 0) > 0)}
        for p in PERIOD_DAYS:
            row["r" + p] = _avg(r[p] for r in rets.values())
            row["rel_" + p] = round(row["r" + p] - spy["r" + p], 2) if row["r" + p] is not None and spy.get("r" + p) is not None else None
        row["members"] = [t for t, _ in sorted(rets.items(), key=lambda kv: -(kv[1]["1m"] or -999))]
        out.append(row)
    out.sort(key=lambda r: -(r["rel_1m"] if r["rel_1m"] is not None else -999))
    return out


# ------------------------------------------------------------------ store
def save(payload: dict, reddit: Optional[Dict[str, dict]] = None, quotes: Optional[Dict[str, dict]] = None) -> None:
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])          # opened only now: the database sleeps while we download
    try:
        cur = conn.cursor()
        try:
            counts = news_counts(cur)
        except Exception as e:  # noqa: BLE001 — a news count problem never loses the day's prices
            conn.rollback()
            print(f"[warn] themes: news count failed: {type(e).__name__}: {e}")
            counts = {}
        for t in payload["themes"]:
            t["news"] = counts.get(t["key"])
        cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_snapshots (
                day DATE PRIMARY KEY,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                labels TEXT,
                payload TEXT NOT NULL
            )""")
        cur.execute("ALTER TABLE theme_snapshots ADD COLUMN IF NOT EXISTS extra TEXT")      # v80d: Reddit day, phrases
        cur.execute("SELECT labels FROM theme_snapshots WHERE day < %s ORDER BY day DESC LIMIT 1", (payload["day"],))
        prev = cur.fetchone()
        prev_labels = json.loads(prev[0]) if prev and prev[0] else {}
        for t in payload["themes"]:
            t["label_prev"] = prev_labels.get(t["key"])
        cur.execute("SELECT day, extra FROM theme_snapshots WHERE day < %s AND extra IS NOT NULL ORDER BY day DESC LIMIT %s",
                    (payload["day"], REDDIT_HISTORY_DAYS))
        olds = [json.loads(e) for _, e in cur.fetchall() if e]
        extra = {}
        if reddit:
            sums = reddit_theme_sums(reddit)
            hist_t = [o.get("reddit_themes") or {} for o in olds if o.get("reddit_themes")]
            for t in payload["themes"]:
                s = sums.get(t["key"], {})
                avg = usual([h.get(t["key"], 0) for h in hist_t])
                s["usual"] = avg
                s["ratio"] = round(s["mentions"] / max(avg, 1), 2) if avg is not None else None
                t["reddit"] = s
            hist_k = [o.get("reddit_tickers") or {} for o in olds if o.get("reddit_tickers")]
            payload["talked"] = talked_about(reddit, hist_k)
            for r in payload["talked"]:
                r.update((quotes or {}).get(r["ticker"], {}))
            top = sorted(((k, v["m"]) for k, v in reddit.items() if v["m"] >= 3), key=lambda kv: -kv[1])[:400]
            extra["reddit_tickers"] = dict(top)
            extra["reddit_themes"] = {k: v["mentions"] for k, v in sums.items()}
        # headline phrases: once a week (the last stored list is reused in between)
        last_ph = next((o.get("phrases") for o in olds if o.get("phrases")), None)
        due = (not last_ph or last_ph.get("v") != PHRASES_VERSION
               or (date.fromisoformat(payload["day"]) - date.fromisoformat(last_ph["computed"])).days >= PHRASE_EVERY_DAYS)
        if due:
            try:
                cur.execute("SELECT summary_en, summary_he, timestamp FROM macro_news WHERE timestamp >= NOW() - INTERVAL '35 days'")
                now = datetime.now(timezone.utc)
                last_ph = {"computed": payload["day"], "v": PHRASES_VERSION, "items": rising_phrases(cur.fetchall(), now)}
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                print(f"[warn] themes: headline phrases failed: {type(e).__name__}: {e}")
        if last_ph:
            payload["phrases"] = last_ph
            extra["phrases"] = last_ph
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        labels = json.dumps({t["key"]: t["label"] for t in payload["themes"]})
        cur.execute("""
            INSERT INTO theme_snapshots (day, updated_at, labels, payload, extra) VALUES (%s, NOW(), %s, %s, %s)
            ON CONFLICT (day) DO UPDATE SET updated_at = NOW(), labels = EXCLUDED.labels, payload = EXCLUDED.payload,
                extra = EXCLUDED.extra""",
                    (payload["day"], labels, json.dumps(payload, separators=(",", ":")),
                     json.dumps(extra, separators=(",", ":")) if extra else None))
        cur.execute("DELETE FROM theme_snapshots WHERE day < %s", (date.fromisoformat(payload["day"]) - timedelta(days=KEEP_DAYS),))
        conn.commit()
    finally:
        conn.close()


def reddit_quotes(rd: Dict[str, dict]) -> Dict[str, dict]:
    """1D / 1W return of every stock that could make the Reddit list (most are not in our themes)."""
    cands = sorted(t for t, x in rd.items()
                   if t not in REDDIT_SKIP and x["m"] >= TALKED_MIN and not ETF_NAME_RE.search(x["name"]))
    if not cands:
        return {}
    try:
        close, _ = download(cands)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] themes: reddit stocks prices: {e}")
        return {}
    return {t: {"r1d": _ret(close[t], 1), "r1w": _ret(close[t], 5)} for t in cands if t in close.columns}


def main() -> int:
    started = time.time()
    tickers = all_tickers()
    close, volume = download(tickers)
    never = sorted(set(tickers) - set(close.columns))
    if never:
        print(f"[warn] themes: no prices at all for: {', '.join(never)}")
    payload = build(close, volume)
    end = date.fromisoformat(payload["day"])
    for th in payload["themes"]:
        spec = next(x for x in THEMES if x["key"] == th["key"])
        th["wiki"] = wiki_views(spec["wiki"], end)
    # v80d: S&P 500 sub-industries (a separate download — a failure only leaves this part out)
    try:
        groups = sp500_groups()
        sp_close, _ = download(sorted(set(groups) | {"SPY"}))
        payload["subind"] = subindustry_stats(sp_close, groups, payload["spy"])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] themes: sub-industries failed: {type(e).__name__}: {e}")
    reddit = fetch_reddit()
    if len(reddit) < 100:
        print(f"[warn] themes: reddit answered with {len(reddit)} tickers — left out today")
        reddit = {}
    quotes = reddit_quotes(reddit)                  # before the database is opened: it sleeps while we download
    save(payload, reddit, quotes)
    size = len(json.dumps(payload, separators=(",", ":")))
    print(f"themes: saved {payload['day']} — {len(payload['themes'])} themes, {size // 1024}KB, "
          f"{len(payload.get('subind') or [])} sub-industries, reddit {len(reddit)} tickers, "
          f"talked {len(payload.get('talked') or [])}, phrases {len((payload.get('phrases') or {}).get('items') or [])}, "
          f"{int(time.time() - started)}s; " + ", ".join(f"{t['key']} {t['label']}" for t in payload["themes"]))
    return 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    sys.exit(main())