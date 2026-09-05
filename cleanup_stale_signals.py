"""
One-off maintenance script — NOT part of the scheduled scans.
====================================================================
Collapses stale re-triggered duplicate signals out of scanned_stocks.

Why this exists: before the freshness-check fixes to
detect_horizontal_resistance_breakout, detect_ascending_triangle, and
detect_descending_trendline_breakout, those patterns kept re-flagging
the SAME underlying breakout every single scan for as long as the
stock stayed above the level — sometimes for weeks or months. Each of
those re-triggers got its own row in scanned_stocks, which inflates the
Backtest page's signal counts (hence ">5000 signals" looking off) and
biases win-rate/avg-return stats, since dozens of rows for one real
event aren't independent trials.

This isn't limited to those 3 patterns specifically — ANY pattern that
happens to fire on the same ticker several days in a row (even
legitimately, e.g. RSI staying oversold for 3 straight days) has the
same "correlated, not independent" problem for backtest purposes. So
the rule here is general: for each (ticker, pattern_type), if the next
occurrence comes within COLLAPSE_GAP_DAYS of the previous one, treat it
as the same signal still showing up rather than a new one, and keep
only the FIRST row of that run. Keeping the first (not last) also
matches what a real trader would have actually done — entered at the
original breakout price, not a later, higher one from a re-trigger.

Usage:
    python cleanup_stale_signals.py            # dry run — prints what WOULD be deleted, changes nothing
    python cleanup_stale_signals.py --confirm  # actually deletes

Requires DATABASE_URL in the environment, same as the other pipeline
scripts in this repo.
"""
import os
import sys
import psycopg2

DB_URL = os.environ.get("DATABASE_URL")
COLLAPSE_GAP_DAYS = 3  # occurrences of the same (ticker, pattern) within this many
                        # days of each other are treated as one continuing signal


def main():
    if not DB_URL:
        print("[error] DATABASE_URL is not set.")
        sys.exit(1)

    confirm = "--confirm" in sys.argv

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, ticker, pattern_type, timestamp
        FROM scanned_stocks
        WHERE pattern_type IS NOT NULL
        ORDER BY ticker, pattern_type, timestamp ASC
        """
    )
    rows = cur.fetchall()

    to_delete = []
    last_key = None
    last_ts = None
    kept = 0

    for row_id, ticker, pattern_type, ts in rows:
        key = (ticker, pattern_type)
        is_new_run = (key != last_key) or (last_ts is None) or ((ts - last_ts).days > COLLAPSE_GAP_DAYS)
        if is_new_run:
            kept += 1
        else:
            to_delete.append(row_id)
        last_key = key
        last_ts = ts  # always advances, so a long chain of close-together
                       # re-triggers collapses to just its first row, not
                       # just pairs of consecutive ones

    print(f"Scanned {len(rows)} signal rows across scanned_stocks.")
    print(f"  Would KEEP:   {kept}")
    print(f"  Would DELETE: {len(to_delete)} (stale re-triggers of an already-counted signal)")

    if not confirm:
        print("\nDry run only — nothing was changed. Re-run with --confirm to actually delete.")
        cur.close()
        conn.close()
        return

    if to_delete:
        cur.execute("DELETE FROM scanned_stocks WHERE id = ANY(%s)", (to_delete,))
        conn.commit()
        print(f"\nDeleted {cur.rowcount} rows.")
    else:
        print("\nNothing to delete.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
