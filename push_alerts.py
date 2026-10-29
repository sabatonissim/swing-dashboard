"""Push notifications ("alerts", v19) — shared by api_server.py (Render) and pipeline_a_scanner.py (GitHub Actions).

What a signed-in user can get, on every device where they turned notifications on (browser or the installed app):
  1. a NEW scanner signal on a stock in their watchlist (sent by the scanner right after it saves the signals);
  2. a price alert they set ("KO above $90") — checked by the quick refresh (~every 20 minutes in market hours);
  3. (v20d) earnings of a watchlist stock: a reminder before the report and the reported figures after it
     (pipeline_a_scanner.earnings_alerts_job, quick refresh). Same on/off switch as 1 (watch_signals).
Texts are factual ("JCI: new scanner signal — horizontal resistance breakout"), never advice.

Web Push (the standard behind browser notifications) needs no paid service: the browser vendor (Google / Mozilla /
Apple / Microsoft) delivers the message. Our server signs each message with a VAPID key pair:
  VAPID_PUBLIC_KEY  — given to the browser when it subscribes (public);
  VAPID_PRIVATE_KEY — signs the messages (SECRET: Render env + GitHub secret, never in the repo).
Library: pywebpush (requirements.txt). Without the key or the library everything here quietly does nothing.

Safety: the server only ever POSTs to push-service hosts (PUSH_HOSTS) — a subscription "endpoint" is a URL the browser
gives us, so any other host is refused (the server must not be made to call arbitrary addresses). A subscription the
push service reports as gone (404/410) is deleted.
"""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from pywebpush import webpush, WebPushException
except Exception:  # noqa: BLE001 — library missing -> alerts disabled, nothing else breaks
    webpush, WebPushException = None, Exception

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
# "subject" = how a push service can reach the sender: a mailto: or https: address. The site address, no e-mail needed.
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "https://swing-desk-tau.vercel.app").strip()
SITE = "https://swing-desk-tau.vercel.app"

PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
              "push.services.mozilla.com", "web.push.apple.com", "api.push.apple.com", "notify.windows.com")
MAX_SUBS_PER_USER = 10
MAX_ALERTS_PER_USER = 50


def ready() -> bool:
    return webpush is not None and bool(VAPID_PRIVATE_KEY)


def endpoint_ok(endpoint: str) -> bool:
    try:
        u = urlparse(endpoint or "")
    except Exception:  # noqa: BLE001
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and len(endpoint) <= 1000 and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            lang TEXT DEFAULT 'he',
            watch_signals BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_ok TIMESTAMPTZ,
            fails INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS push_subscriptions_user ON push_subscriptions (user_id);
        CREATE TABLE IF NOT EXISTS price_alerts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
            price REAL NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            triggered_at TIMESTAMPTZ,
            triggered_price REAL
        );
        CREATE INDEX IF NOT EXISTS price_alerts_open ON price_alerts (ticker) WHERE triggered_at IS NULL;
        CREATE TABLE IF NOT EXISTS push_log (
            user_id INTEGER NOT NULL,
            ref TEXT NOT NULL,
            sent_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, ref)
        );
    """)


def _send_one(sub: dict, payload: dict) -> str:
    """'ok' | 'gone' (subscription no longer valid) | 'error'."""
    if not ready() or not endpoint_ok(sub["endpoint"]):
        return "error"
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=6 * 3600,
            timeout=10,
        )
        return "ok"
    except WebPushException as e:  # noqa: PERF203
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):
            return "gone"
        print(f"[push] send failed ({code}): {str(e)[:160]}")
        return "error"
    except Exception as e:  # noqa: BLE001
        print(f"[push] send failed: {type(e).__name__}: {str(e)[:160]}")
        return "error"


def send_to_user(cur, user_id: int, make_payload, only_signals: bool = False) -> int:
    """Sends to every device of one user. make_payload(lang) -> {title, body, url, tag}. Returns the number delivered.
    The caller commits."""
    cur.execute("SELECT id, endpoint, p256dh, auth, lang, watch_signals FROM push_subscriptions WHERE user_id = %s",
                (user_id,))
    sent = 0
    for sid, endpoint, p256dh, auth, lang, watch_signals in cur.fetchall():
        if only_signals and not watch_signals:
            continue
        res = _send_one({"endpoint": endpoint, "p256dh": p256dh, "auth": auth}, make_payload(lang or "he"))
        if res == "ok":
            sent += 1
            cur.execute("UPDATE push_subscriptions SET last_ok = NOW(), fails = 0 WHERE id = %s", (sid,))
        elif res == "gone":
            cur.execute("DELETE FROM push_subscriptions WHERE id = %s", (sid,))
        else:
            cur.execute("UPDATE push_subscriptions SET fails = fails + 1 WHERE id = %s", (sid,))
            cur.execute("DELETE FROM push_subscriptions WHERE id = %s AND fails >= 20", (sid,))
    return sent


def _first_time(cur, user_id: int, ref: str) -> bool:
    """True once per (user, ref): the same signal is never pushed twice to the same person."""
    cur.execute("INSERT INTO push_log (user_id, ref) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, ref))
    return cur.rowcount == 1


def notify_watchlist_signals(conn, signals: list) -> int:
    """signals = [(ticker, trigger_he, trigger_en)] just saved by the scanner. One message per (user, ticker, day)."""
    if not ready() or not signals:
        return 0
    by_ticker = {}
    for tk, he, en in signals:
        by_ticker.setdefault(tk, (he or en or "", en or he or ""))
    day = datetime.now(timezone.utc).date().isoformat()
    cur = conn.cursor()
    ensure_tables(cur)
    cur.execute("""SELECT DISTINCT w.user_id, w.ticker FROM watchlist w
                   JOIN push_subscriptions p ON p.user_id = w.user_id AND p.watch_signals
                   WHERE w.ticker = ANY(%s)""", (list(by_ticker),))
    pairs = cur.fetchall()
    sent = 0
    for user_id, tk in pairs:
        if not _first_time(cur, user_id, f"sig:{tk}:{day}"):
            continue
        he, en = by_ticker[tk]

        def payload(lang, tk=tk, he=he, en=en):
            return {"title": f"{tk}: " + ("new scanner signal" if lang == "en" else "איתות חדש בסורק"),
                    "body": (en if lang == "en" else he)[:200], "url": f"/?ticker={tk}", "tag": f"sig-{tk}"}
        sent += send_to_user(cur, user_id, payload, only_signals=True)
    conn.commit()
    cur.close()
    print(f"[push] watchlist signals: {len(pairs)} user/ticker match(es), {sent} notification(s) delivered")
    return sent


def watched_tickers(conn) -> list:
    """Tickers on the watchlist of someone who has a device with watchlist notifications on (v20d)."""
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()
    cur.execute("""SELECT DISTINCT w.ticker FROM watchlist w
                   JOIN push_subscriptions p ON p.user_id = w.user_id AND p.watch_signals""")
    out = [r[0] for r in cur.fetchall()]
    cur.close()
    return out


def _usd(v) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _big(v) -> str:
    a = abs(v)
    s = f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M"
    return ("-$" if v < 0 else "$") + s


def _pct(v) -> str:
    return f"{'+' if v >= 0 else '-'}{abs(v):.1f}%"


def _earnings_payload(ev: dict, lang: str) -> dict:
    """Factual texts only — what was reported vs the estimate, never "buy/sell"."""
    tk, en = ev["ticker"], lang == "en"
    url, tag = f"/?ticker={tk}", f"earn-{tk}"
    if ev["kind"] == "pre":
        when_en = {"bmo": "before the open", "amc": "after the close"}.get(ev.get("session"), "")
        when_he = {"bmo": "לפני הפתיחה", "amc": "אחרי הסגירה"}.get(ev.get("session"), "")
        # a Monday report announced on Friday is "on Mon Nov 02", not "tomorrow"
        day_en = "today" if ev["is_today"] else "tomorrow" if ev["is_next_day"] else "on " + ev["report_date"].strftime("%a %b %d")
        day_he = "היום" if ev["is_today"] else "מחר" if ev["is_next_day"] else "ב-" + ev["report_date"].strftime("%d.%m")
        est = ev.get("eps_estimate")
        if en:
            body = f"Reports {day_en} {when_en}".strip() + (f". EPS estimate {_usd(est)}." if est is not None else ".")
            return {"title": f"{tk}: earnings {day_en}", "body": body, "url": url, "tag": tag}
        body = f"מדווחת {day_he} {when_he}".strip() + (f". צפי EPS {_usd(est)}." if est is not None else ".")
        return {"title": f"{tk}: דוח רבעוני {day_he}", "body": body, "url": url, "tag": tag}
    # kind == "res": the result is out
    parts_en, parts_he = [], []
    act, est, sur = ev.get("eps_actual"), ev.get("eps_estimate"), ev.get("surprise_pct")
    if act is not None:
        tail = f" ({_pct(sur)})" if sur is not None else ""
        parts_en.append(f"EPS {_usd(act)}" + (f" vs est. {_usd(est)}" if est is not None else "") + tail)
        parts_he.append(f"EPS {_usd(act)}" + (f" מול צפי {_usd(est)}" if est is not None else "") + tail)
    ract, rest, rsur = ev.get("revenue_actual"), ev.get("revenue_estimate"), ev.get("revenue_surprise_pct")
    if ract is not None:
        tail = f" ({_pct(rsur)})" if rsur is not None else ""
        parts_en.append(f"revenue {_big(ract)}" + (f" vs est. {_big(rest)}" if rest is not None else "") + tail)
        parts_he.append(f"הכנסות {_big(ract)}" + (f" מול צפי {_big(rest)}" if rest is not None else "") + tail)
    if en:
        return {"title": f"{tk} reported earnings", "body": "; ".join(parts_en) + ".", "url": url, "tag": tag}
    return {"title": f"{tk} פרסמה דוח רבעוני", "body": "; ".join(parts_he) + ".", "url": url, "tag": tag}


def notify_watchlist_earnings(conn, events: list) -> int:
    """v20d. events = [{ticker, kind: 'pre'|'res', report_date (date), session, eps_estimate, eps_actual, surprise_pct,
    revenue_* (optional), is_today, is_next_day}]. 'pre' = a reminder before the report, 'res' = the reported figures.
    Each (user, ticker, report date, kind) is sent once (push_log); only devices with watchlist notifications on."""
    if not ready() or not events:
        return 0
    cur = conn.cursor()
    ensure_tables(cur)
    by_ticker = {}
    for ev in events:
        by_ticker.setdefault(ev["ticker"], []).append(ev)
    cur.execute("""SELECT DISTINCT w.user_id, w.ticker FROM watchlist w
                   JOIN push_subscriptions p ON p.user_id = w.user_id AND p.watch_signals
                   WHERE w.ticker = ANY(%s)""", (list(by_ticker),))
    pairs = cur.fetchall()
    sent = 0
    for user_id, tk in pairs:
        for ev in by_ticker.get(tk, []):
            if not _first_time(cur, user_id, f"earn-{ev['kind']}:{tk}:{ev['report_date'].isoformat()}"):
                continue
            sent += send_to_user(cur, user_id, lambda lang, ev=ev: _earnings_payload(ev, lang), only_signals=True)
    conn.commit()
    cur.close()
    print(f"[push] watchlist earnings: {len(events)} event(s), {len(pairs)} user/ticker match(es), {sent} delivered")
    return sent


def open_alert_tickers(conn) -> list:
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()
    cur.execute("SELECT DISTINCT ticker FROM price_alerts WHERE triggered_at IS NULL")
    out = [r[0] for r in cur.fetchall()]
    cur.close()
    return out


def check_price_alerts(conn, prices: dict) -> int:
    """prices = {ticker: last price}. Every open alert whose level was crossed fires once and is closed."""
    if not prices:
        return 0
    cur = conn.cursor()
    cur.execute("""SELECT id, user_id, ticker, direction, price FROM price_alerts
                   WHERE triggered_at IS NULL AND ticker = ANY(%s)""", (list(prices),))
    fired = 0
    for aid, user_id, tk, direction, level in cur.fetchall():
        p = prices.get(tk)
        if p is None or not ((direction == "above" and p >= level) or (direction == "below" and p <= level)):
            continue
        cur.execute("UPDATE price_alerts SET triggered_at = NOW(), triggered_price = %s WHERE id = %s", (p, aid))
        fired += 1
        if not ready():
            continue

        def payload(lang, tk=tk, direction=direction, level=level, p=p):
            if lang == "en":
                word = "rose above" if direction == "above" else "fell below"
                return {"title": f"{tk} {word} ${level:,.2f}", "body": f"Last price ${p:,.2f} (your price alert).",
                        "url": f"/?ticker={tk}", "tag": f"px-{tk}"}
            word = "עלתה מעל" if direction == "above" else "ירדה מתחת ל-"
            return {"title": f"{tk} {word} ${level:,.2f}", "body": f"מחיר אחרון ${p:,.2f} (התראת מחיר שקבעת).",
                    "url": f"/?ticker={tk}", "tag": f"px-{tk}"}
        send_to_user(cur, user_id, payload)
    conn.commit()
    cur.close()
    print(f"[push] price alerts: {fired} triggered out of {len(prices)} watched ticker(s)")
    return fired
