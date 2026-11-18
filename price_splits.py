"""
Stock splits that Yahoo has not applied yet (v65, 18.11.2026).
=================================================================
Yahoo's daily prices are split-adjusted: after a 4-for-1 split every older price is divided by 4, so the chart has no
jump. But for a day or so after the split it can send the OLD prices with the split already marked: EMBJ split 4:1 on
17.11.2026, Yahoo still had 78.73 for 13.11 and 18.90 for 17.11, and the site showed EMBJ "-75.99%" in the movers, the
heatmap and the daily summary; the scanner saw the same false crash.

fix_unadjusted_splits(df) finds each day Yahoo marks a split on ("Stock Splits" column: 4.0 = 4-for-1, 0.1 = 1-for-10)
and looks at the prices around it. The last close before the split / the first price from that day is about the split
ratio -> the older rows were not adjusted yet: their prices are divided by the ratio and their volume multiplied by it.
About 1 -> Yahoo already adjusted them: nothing changes (dividing again would make a false jump the other way).

Used by the scanner (pipeline_a_scanner.py) and the API server (api_server.py), so the scan and the stock page agree.
Needs the "Stock Splits" column: Ticker.history() has it by default, yf.download only with actions=True.
"""
import math

PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close")
SPLIT_COLUMN = "Stock Splits"


def _first_valid(values):
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > 0:
            return f
    return None


def split_not_applied(prev_close: float, first_after: float, ratio: float) -> bool:
    """True when the jump between the two prices looks like the split itself (closer to the ratio than to no change)."""
    if not prev_close or not first_after or not ratio or ratio <= 0 or ratio == 1:
        return False
    jump = math.log(prev_close / first_after)
    return abs(jump - math.log(ratio)) < abs(jump)


def fix_unadjusted_splits(df):
    """The same DataFrame with the not-yet-adjusted rows corrected (a copy when anything changes). Never raises."""
    try:
        if df is None or len(df) < 2 or SPLIT_COLUMN not in df.columns or "Close" not in df.columns:
            return df
        splits = [(i, float(s)) for i, s in enumerate(df[SPLIT_COLUMN].tolist())
                  if s is not None and isinstance(s, (int, float)) and math.isfinite(s) and s > 0 and s != 1]
        if not splits:
            return df
        out = df.copy()
        cols = [c for c in PRICE_COLUMNS if c in out.columns]
        for i, ratio in splits:
            if i == 0:
                continue
            closes = out["Close"].tolist()
            prev = _first_valid(reversed(closes[:i]))
            after = []
            for j in range(i, len(out)):
                if "Open" in out.columns:
                    after.append(out["Open"].iloc[j])
                after.append(out["Close"].iloc[j])
            first = _first_valid(after)
            if not split_not_applied(prev, first, ratio):
                continue
            for c in cols:
                k = out.columns.get_loc(c)
                out.iloc[:i, k] = out.iloc[:i, k] / ratio
            if "Volume" in out.columns:
                out["Volume"] = out["Volume"].astype(float)
                k = out.columns.get_loc("Volume")
                out.iloc[:i, k] = out.iloc[:i, k] * ratio
            print(f"[info] split {ratio:g}:1 on {out.index[i]} not applied by Yahoo yet — older prices divided by {ratio:g}")
        return out
    except Exception as e:  # noqa: BLE001 — a failed correction must never stop a scan or a page
        print(f"[warn] split check skipped: {type(e).__name__}: {e}")
        return df
