"""Economic-data refresh — runs in GitHub Actions (econ-refresh.yml), NOT on Render.

WHY: from Render the FRED download host times out (even after 25 s) and the keyless BLS API quota is shared with
other Render customers, so the "actual"/"previous" numbers of the economic calendar never arrived. GitHub's
runners reach FRED normally. This job downloads every series the calendar needs and stores it in Postgres
(table econ_snapshots, key "series-<id>"); the server reads them from there (econ_calendar.fred_csv).

Safe by design: a series that cannot be downloaded keeps its previous stored copy; the job fails (red run) only
when NOTHING could be refreshed. Needs only the DATABASE_URL secret. Optional secrets FRED_API_KEY / BLS_API_KEY
are used when present, but are not required.

Manual run:   python econ_refresh.py
"""
import os
import sys
import time
from datetime import date, timedelta

import psycopg2

import econ_calendar as ec


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def download(series):
    cosd = (date.today() - timedelta(days=900)).isoformat()
    last = None
    for attempt in (1, 2, 3):
        try:
            if (os.environ.get("FRED_API_KEY") or "").strip():
                return ec._fred_api_rows(series, cosd)
            return ec._parse_fred_csv(ec._http_text(f"{ec.FRED_CSV}?id={series}&cosd={cosd}", timeout=60), series)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[econ_refresh] {series}: attempt {attempt} failed — {type(e).__name__}: {str(e)[:120]}")
            time.sleep(3 * attempt)
    if series in ec.BLS_FALLBACK_SERIES:        # CPI/PPI/jobs/unemployment/JOLTS: BLS public API as the second source
        try:
            return ec.bls_api_rows(series, 0)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[econ_refresh] {series}: BLS API failed — {str(e)[:120]}")
    raise last


def main():
    series = sorted({v["fred"]["series"] for v in ec.CATALOG.values() if v.get("fred")})
    ok = []
    for s in series:
        try:
            rows = download(s)
        except Exception as e:  # noqa: BLE001
            print(f"[econ_refresh] {s}: NOT refreshed ({type(e).__name__}) — the stored copy stays")
            continue
        ec.series_to_db(get_conn, s, rows)
        ok.append(s)
        print(f"[econ_refresh] {s}: {len(rows)} points, latest {rows[-1][0]} = {rows[-1][1]}")
    print(f"[econ_refresh] refreshed {len(ok)}/{len(series)} series")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
