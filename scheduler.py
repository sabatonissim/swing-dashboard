"""Built-in scheduler — starts the GitHub Actions workflows at the right moments, without any
extra service or sign-up.

WHY: GitHub's own `schedule` trigger ran this repo's jobs 3-12 hours late (CLAUDE.md 19.6). A
`workflow_dispatch` call, however, starts a run within seconds. The API server on Render is already
running 24/7, so it makes the call itself, at exact America/New_York times (daylight saving is
handled by the time zone, no twice-a-year edits).

HOW IT STAYS AWAKE: Render's free web service sleeps after 15 minutes without traffic. While the
scheduler is enabled the server pings its own public URL (/api/ping, which does not touch the
database) every 10 minutes. A restart of the Render instance while nobody visits can still leave it
asleep — the existing GitHub "Keep Render Awake" workflow (slow but eventually fires) or any visitor
wakes it, and on wake-up the scheduler CATCHES UP on a slot it missed (see `grace_min`).

EXACTLY ONCE: every slot is claimed in Postgres (table scheduler_runs) before it is dispatched, so a
restart, two overlapping instances during a deploy, or a catch-up never starts the same slot twice.

SETUP (once): create a fine-grained GitHub token for THIS repository only with
"Actions: Read and write" and put it in Render as the environment variable GITHUB_DISPATCH_TOKEN.
Without it the scheduler stays off and the server behaves exactly as before.
Optional env vars: GITHUB_REPO (default sabatonissim/swing-dashboard), SCHEDULER_GIT_REF (default
main), SCHEDULER_KEEPALIVE=0 (turn the self-ping off).
"""
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

GITHUB_REPO = os.environ.get("GITHUB_REPO", "sabatonissim/swing-dashboard")
GIT_REF = os.environ.get("SCHEDULER_GIT_REF", "main")
TOKEN_ENV = "GITHUB_DISPATCH_TOKEN"
TICK_SECONDS = 30
KEEPALIVE_EVERY = 600          # seconds
MAX_ATTEMPTS = 3

# kind "daily": fires once at `at` (ET). kind "interval": every `every_min` minutes, aligned to the
# clock, optionally only inside `window` = (start_minute_of_day, end_minute_of_day) ET (inclusive).
# `grace_min`: how late a missed slot may still be fired (catch-up after the server woke up late).
JOBS = [
    dict(name="scan_open",  workflow="stock-scanner.yml",   kind="daily",    at=(9, 45),  weekdays_only=True,  grace_min=300),
    dict(name="scan_close", workflow="stock-scanner.yml",   kind="daily",    at=(16, 10), weekdays_only=True,  grace_min=300),
    dict(name="quick",      workflow="earnings-refresh.yml", kind="interval", every_min=20, window=(6 * 60, 20 * 60), weekdays_only=True,  grace_min=25),
    dict(name="news",       workflow="news-aggregator.yml", kind="interval", every_min=30, window=None,               weekdays_only=False, grace_min=35),
    # economic-calendar numbers (BLS API/FRED -> Postgres): every 30 min 08:00-10:30 ET (08:30 CPI/PPI/jobs, 10:00 JOLTS), plus once in the evening.
    # Kept sparse on purpose: the keyless BLS API allows 25 requests/day per IP.
    dict(name="econ",       workflow="econ-refresh.yml",    kind="interval", every_min=30, window=(8 * 60, 10 * 60 + 30), weekdays_only=True,  grace_min=35),
    dict(name="econ_eve",   workflow="econ-refresh.yml",    kind="daily",    at=(18, 30), weekdays_only=False, grace_min=300),
    # v21: company fundamentals built in advance (fundamentals.py -> fundamentals_cache), after the evening scan
    # saved the analyst estimates and before the next morning
    dict(name="fundamentals", workflow="fundamentals-refresh.yml", kind="daily", at=(5, 0), weekdays_only=False, grace_min=300),
    # v22: market breadth (S&P 500 above 50/200-day, new highs/lows) hourly while the market is open + after the close
    dict(name="breadth",     workflow="market-breadth.yml",  kind="interval", every_min=60, window=(10 * 60, 16 * 60), weekdays_only=True, grace_min=40),
    dict(name="breadth_eod", workflow="market-breadth.yml",  kind="daily",    at=(16, 25), weekdays_only=True,  grace_min=300),
    # v22: insider trades (SEC Form 4) every 30 min on weekdays; each run also sweeps part of the companies (backfill)
    dict(name="insider",     workflow="insider-refresh.yml", kind="interval", every_min=30, window=(6 * 60, 22 * 60), weekdays_only=True, grace_min=25),
]

_done = {}        # slot key -> outcome (this process)
_attempts = {}    # slot key -> failed attempts
_last_error = {"msg": None, "at": None}
_started = {"thread": None}


def enabled():
    return bool(os.environ.get(TOKEN_ENV)) and os.environ.get("SCHEDULER_ENABLED", "1") != "0"


# ------------------------------------------------------------------ slot arithmetic
def _is_slot(job, t_et):
    """Is this exact ET minute one of the job's scheduled slots?"""
    if job["weekdays_only"] and t_et.weekday() >= 5:
        return False
    if job["kind"] == "daily":
        return (t_et.hour, t_et.minute) == job["at"]
    mins = t_et.hour * 60 + t_et.minute
    if mins % job["every_min"] != 0:
        return False
    w = job.get("window")
    return True if not w else (w[0] <= mins <= w[1])


def _floor_minute(now_utc):
    return now_utc.replace(second=0, microsecond=0)


def latest_due_slot(job, now_utc):
    """The most recent slot at or before `now_utc` that is still inside the grace period, else None."""
    base = _floor_minute(now_utc)
    for i in range(job["grace_min"] + 1):
        t_utc = base - timedelta(minutes=i)
        if _is_slot(job, t_utc.astimezone(ET)):
            return t_utc
    return None


def next_slot(job, now_utc):
    base = _floor_minute(now_utc)
    for i in range(1, 8 * 24 * 60):
        t_utc = base + timedelta(minutes=i)
        if _is_slot(job, t_utc.astimezone(ET)):
            return t_utc
    return None


def slot_key(job, slot_utc):
    return f"{job['name']}|{slot_utc.strftime('%Y-%m-%dT%H:%MZ')}"


# ------------------------------------------------------------------ database claim (exactly once)
def _claim(get_conn, key):
    """True = we claimed it, False = somebody already fired it, None = database unreachable."""
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS scheduler_runs (
                slot_key TEXT PRIMARY KEY, fired_at TIMESTAMPTZ DEFAULT NOW(), http_status INTEGER)""")
            cur.execute("INSERT INTO scheduler_runs (slot_key) VALUES (%s) ON CONFLICT DO NOTHING", (key,))
            claimed = cur.rowcount == 1
            conn.commit()
            cur.close()
            return claimed
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] could not claim {key} in the database ({type(e).__name__}: {e}) — firing anyway")
        return None


def _db_update(get_conn, key, status=None, delete=False):
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            if delete:
                cur.execute("DELETE FROM scheduler_runs WHERE slot_key = %s", (key,))
            else:
                cur.execute("UPDATE scheduler_runs SET http_status = %s WHERE slot_key = %s", (status, key))
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] bookkeeping for {key} failed: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ GitHub dispatch
def _dispatch(workflow):
    r = requests.post(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches",
        headers={
            "Authorization": f"Bearer {os.environ.get(TOKEN_ENV, '')}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "swing-desk-scheduler",
        },
        json={"ref": GIT_REF},
        timeout=20,
    )
    return r.status_code, r.text[:200]


def tick(get_conn, now_utc=None):
    """One scheduler pass. Returns a list of (slot key, outcome) for what it did."""
    now = now_utc or datetime.now(UTC)
    did = []
    for job in JOBS:
        slot = latest_due_slot(job, now)
        if slot is None:
            continue
        key = slot_key(job, slot)
        if key in _done:
            continue
        claim = _claim(get_conn, key)
        if claim is False:
            _done[key] = "fired earlier (by another instance or before a restart)"
            continue
        try:
            status, body = _dispatch(job["workflow"])
        except Exception as e:  # noqa: BLE001
            status, body = 0, f"{type(e).__name__}: {e}"
        if status == 204:
            _done[key] = "dispatched"
            _db_update(get_conn, key, status=204)
            late = int((now - slot).total_seconds() // 60)
            print(f"[scheduler] {job['workflow']} dispatched for slot {key}" + (f" ({late} min late — catch-up)" if late >= 3 else ""))
            did.append((key, "dispatched"))
        else:
            _attempts[key] = _attempts.get(key, 0) + 1
            _last_error.update(msg=f"{job['workflow']}: HTTP {status} {body}", at=now.isoformat())
            print(f"[scheduler] dispatch of {job['workflow']} FAILED (attempt {_attempts[key]}/{MAX_ATTEMPTS}): HTTP {status} {body}")
            if claim:
                _db_update(get_conn, key, delete=True)          # release the claim so the next tick can retry
            if _attempts[key] >= MAX_ATTEMPTS:
                _done[key] = f"gave up after {MAX_ATTEMPTS} failures (HTTP {status})"
            did.append((key, f"failed HTTP {status}"))
    for k in [k for k in _done if k.split("|", 1)[1] < (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ")]:
        _done.pop(k, None)
        _attempts.pop(k, None)
    return did


# ------------------------------------------------------------------ keep-alive + thread
def _self_ping():
    base = os.environ.get("RENDER_EXTERNAL_URL") or "https://swing-dashboard-3btx.onrender.com"
    try:
        requests.get(base.rstrip("/") + "/api/ping", timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] self-ping failed: {type(e).__name__}: {e}")


def _loop(get_conn):
    time.sleep(20)                       # let the app finish booting
    last_ping = 0.0
    while True:
        try:
            tick(get_conn)
        except Exception:  # noqa: BLE001
            print("[scheduler] tick crashed:\n" + traceback.format_exc())
        if os.environ.get("SCHEDULER_KEEPALIVE", "1") != "0" and time.time() - last_ping >= KEEPALIVE_EVERY:
            _self_ping()
            last_ping = time.time()
        time.sleep(TICK_SECONDS)


def start(get_conn):
    """Call once at server start. No token -> nothing happens."""
    if not enabled():
        print(f"[scheduler] off ({TOKEN_ENV} is not set) — workflows are not started from the server.")
        return False
    if _started["thread"] and _started["thread"].is_alive():
        return True
    t = threading.Thread(target=_loop, args=(get_conn,), daemon=True, name="swing-scheduler")
    t.start()
    _started["thread"] = t
    print("[scheduler] on — jobs: " + ", ".join(f"{j['name']}→{j['workflow']}" for j in JOBS))
    return True


def status(get_conn=None):
    now = datetime.now(UTC)
    recent = []
    if get_conn:
        try:
            conn = get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT slot_key, fired_at, http_status FROM scheduler_runs ORDER BY fired_at DESC LIMIT 12")
                recent = [{"slot": r[0], "fired_at": r[1].isoformat() if r[1] else None, "http_status": r[2]} for r in cur.fetchall()]
                cur.close()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            recent = [{"error": f"{type(e).__name__}: {e}"}]
    return {
        "enabled": enabled(),
        "thread_alive": bool(_started["thread"] and _started["thread"].is_alive()),
        "token_configured": bool(os.environ.get(TOKEN_ENV)),
        "now_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "next_slots": [
            {"job": j["name"], "workflow": j["workflow"],
             "next_et": (next_slot(j, now).astimezone(ET).strftime("%a %Y-%m-%d %H:%M %Z") if next_slot(j, now) else None)}
            for j in JOBS
        ],
        "last_error": _last_error,
        "recent_runs": recent,
    }
