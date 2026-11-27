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
import json
import os
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
    s = s.dropna()
    if len(s) <= n or not s.iloc[-1 - n]:
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
        "r1d": _ret(c, 1), "r1w": _ret(c, 5), "r1m": _ret(c, 21), "r3m": _ret(c, 63),
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
    out["series"] = basket_series(close, [m["ticker"] for m in members])
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
    return {"day": day.isoformat(), "spy": spy, "spy_series": basket_series(close, ["SPY"]),
            "series_days": [d.date().isoformat() for d in close.index[-(SERIES_DAYS + 1):]],
            "themes": themes}


def save(payload: dict) -> None:
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
        cur.execute("SELECT labels FROM theme_snapshots WHERE day < %s ORDER BY day DESC LIMIT 1", (payload["day"],))
        prev = cur.fetchone()
        prev_labels = json.loads(prev[0]) if prev and prev[0] else {}
        for t in payload["themes"]:
            t["label_prev"] = prev_labels.get(t["key"])
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        labels = json.dumps({t["key"]: t["label"] for t in payload["themes"]})
        cur.execute("""
            INSERT INTO theme_snapshots (day, updated_at, labels, payload) VALUES (%s, NOW(), %s, %s)
            ON CONFLICT (day) DO UPDATE SET updated_at = NOW(), labels = EXCLUDED.labels, payload = EXCLUDED.payload""",
                    (payload["day"], labels, json.dumps(payload, separators=(",", ":"))))
        cur.execute("DELETE FROM theme_snapshots WHERE day < %s", (date.fromisoformat(payload["day"]) - timedelta(days=KEEP_DAYS),))
        conn.commit()
    finally:
        conn.close()


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
    save(payload)
    size = len(json.dumps(payload, separators=(",", ":")))
    print(f"themes: saved {payload['day']} — {len(payload['themes'])} themes, {size // 1024}KB, "
          f"{int(time.time() - started)}s; " + ", ".join(f"{t['key']} {t['label']}" for t in payload["themes"]))
    return 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    sys.exit(main())
