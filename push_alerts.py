"""Push notifications ("alerts", v19) — shared by api_server.py (Render) and pipeline_a_scanner.py (GitHub Actions).

What a signed-in user can get, on every device where they turned notifications on (browser or the installed app):
  1. a NEW scanner signal on a stock in their watchlist (sent by the scanner right after it saves the signals);
  2. a price alert they set ("KO above $90") — checked by the quick refresh (~every 20 minutes in market hours);
  3. (v20d) earnings of a watchlist stock: a reminder before the report and the reported figures after it
     (pipeline_a_scanner.earnings_alerts_job, quick refresh). Same on/off switch as 1 (watch_signals);
  4. (v20e) ~40 minutes before a major US economic release (rates, inflation, jobs, GDP) — api_server's macro alert
     thread, from the economic calendar. Its own switch (watch_macro), off until the user turns it on;
  5. (v22b) an insider of a watchlist stock bought, or sold outside a pre-arranged plan, shares in the open market
     (SEC Form 4, insider_trades.py). Its own switch (watch_insider), on by default.
  6. (v23) a morning brief at 08:00 ET on weekdays: today's economic releases, today's reports (watchlist first) and
     the last scan's signals — api_server's alert thread. Its own switch (watch_morning), off until turned on.
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
import re
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
        ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS watch_macro BOOLEAN DEFAULT FALSE;
        ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS watch_insider BOOLEAN DEFAULT TRUE;
        ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS watch_morning BOOLEAN DEFAULT FALSE;
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


def send_to_user(cur, user_id: int, make_payload, only_signals: bool = False, only_macro: bool = False,
                 only_insider: bool = False, only_morning: bool = False) -> int:
    """Sends to every device of one user. make_payload(lang) -> {title, body, url, tag}. Returns the number delivered.
    only_signals / only_macro / only_insider / only_morning = only devices with that switch on. The caller commits."""
    cur.execute("""SELECT id, endpoint, p256dh, auth, lang, watch_signals, COALESCE(watch_macro, FALSE),
                          COALESCE(watch_insider, TRUE), COALESCE(watch_morning, FALSE)
                   FROM push_subscriptions WHERE user_id = %s""", (user_id,))
    sent = 0
    for sid, endpoint, p256dh, auth, lang, watch_signals, watch_macro, watch_insider, watch_morning in cur.fetchall():
        if ((only_signals and not watch_signals) or (only_macro and not watch_macro) or (only_insider and not watch_insider)
                or (only_morning and not watch_morning)):
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


INSIDER_MIN_VALUE = 50_000     # smaller insider trades are not pushed (still shown on the stock page)

_ROLE_HE = [("chief executive", 'מנכ"ל'), ("ceo", 'מנכ"ל'), ("chief financial", 'סמנכ"ל כספים'), ("cfo", 'סמנכ"ל כספים'),
            ("chief operating", 'סמנכ"ל תפעול'), ("coo", 'סמנכ"ל תפעול'), ("chief technology", 'סמנכ"ל טכנולוגיה'),
            ("general counsel", "יועץ משפטי"), ("chief legal", "יועץ משפטי"), ("chair", 'יו"ר'),
            ("executive vice president", "סגן נשיא בכיר"), ("evp", "סגן נשיא בכיר"),
            ("senior vice president", "סגן נשיא בכיר"), ("svp", "סגן נשיא בכיר"), ("vice president", "סגן נשיא"),
            ("president", "נשיא")]


def _insider_role(tr: dict, en: bool) -> str:
    title = (tr.get("owner_title") or "").strip()
    if title:
        if en:
            return title
        low = title.lower()
        for key, he in _ROLE_HE:
            if key in low:
                return he
        return title
    if tr.get("is_director"):
        return "Director" if en else "דירקטור"
    if tr.get("is_ten_pct"):
        return "10% owner" if en else "בעל 10%"
    return "Insider" if en else "בעל עניין"


def _insider_payload(tr: dict, lang: str) -> dict:
    """Factual: who, bought/sold, how many shares, at what price, total. Never advice."""
    tk, en, buy = tr["ticker"], lang == "en", tr["code"] == "P"
    role = _insider_role(tr, en)
    shares = f"{tr['shares']:,.0f}"
    price = f" (${tr['price']:,.2f} {'avg' if en else 'בממוצע'})" if tr.get("price") else ""
    v = tr.get("value")
    value = (f", ${v / 1e3:,.0f}K" if v < 1e6 else f", {_big(v)}") if v else ""
    name = (tr.get("owner_name") or "").split(";")[0].strip()
    if en:
        title = f"{tk}: insider {'buy' if buy else 'sale'}"
        body = f"{role} {name} {'bought' if buy else 'sold'} {shares} shares in the open market{value}{price}."
    else:
        title = f"{tk}: {'קנייה' if buy else 'מכירה'} של בעל עניין"
        body = f"{role} {name} {'קנה' if buy else 'מכר'} {shares} מניות בשוק{value}{price}."
    return {"title": title, "body": re.sub(r"\s+", " ", body)[:200], "url": f"/?ticker={tk}", "tag": f"ins-{tk}"}


def notify_watchlist_insider(conn, trades: list) -> int:
    """v22b. trades = new insider rows grouped per filing and direction: {ticker, accession, code 'P'|'S', owner_name,
    owner_title, is_director, is_ten_pct, shares, price (average), value, planned}. Sent once per (user, filing,
    direction) to devices with the insider switch on (watch_insider, on by default). Pushed: every open-market buy, and
    sales that were NOT pre-planned (10b5-1 plan sales are scheduled months ahead and frequent) — each from
    INSIDER_MIN_VALUE up. Everything is still on the stock page."""
    if not ready() or not trades:
        return 0
    wanted = [t for t in trades if (t.get("value") or 0) >= INSIDER_MIN_VALUE
              and (t["code"] == "P" or (t["code"] == "S" and not t.get("planned")))]
    if not wanted:
        return 0
    by_ticker = {}
    for t in wanted:
        by_ticker.setdefault(t["ticker"], []).append(t)
    cur = conn.cursor()
    ensure_tables(cur)
    cur.execute("""SELECT DISTINCT w.user_id, w.ticker FROM watchlist w
                   JOIN push_subscriptions p ON p.user_id = w.user_id AND COALESCE(p.watch_insider, TRUE)
                   WHERE w.ticker = ANY(%s)""", (list(by_ticker),))
    pairs = cur.fetchall()
    sent = 0
    for user_id, tk in pairs:
        for t in by_ticker.get(tk, []):
            if not _first_time(cur, user_id, f"ins:{t['accession']}:{t['code']}"):
                continue
            sent += send_to_user(cur, user_id, lambda lang, t=t: _insider_payload(t, lang), only_insider=True)
    conn.commit()
    cur.close()
    print(f"[push] watchlist insider trades: {len(wanted)} filing(s) to push, {len(pairs)} user/ticker match(es), {sent} delivered")
    return sent


MACRO_ALERT_MINUTES = 40       # a major US release this many minutes ahead (or less) triggers the reminder


def notify_macro_events(conn, slot_utc: datetime, events: list) -> int:
    """v20e. One reminder per release time (several releases often share 8:30 ET — GDP + PCE + Core PCE), sent once
    per user (push_log 'macro:<time>') to devices with the economic-events switch on (watch_macro, off by default).
    events = calendar rows ({name_he, name_en}) of high-impact releases at slot_utc. Factual: what and when."""
    if not ready() or not events:
        return 0
    from zoneinfo import ZoneInfo
    mins = max(1, round((slot_utc - datetime.now(timezone.utc)).total_seconds() / 60))
    t_il = slot_utc.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:%M")
    t_et = slot_utc.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
    names_he = ", ".join(e.get("name_he") or e.get("name_en") or "" for e in events)
    names_en = ", ".join(e.get("name_en") or "" for e in events)

    def payload(lang):
        if lang == "en":
            return {"title": f"Major US data in {mins} min", "body": f"{t_et} ET: {names_en}"[:200],
                    "url": "/?page=pulse", "tag": "macro"}
        return {"title": f"נתון כלכלי חשוב בעוד {mins} דקות", "body": f"{t_il} (שעון ישראל): {names_he}"[:200],
                "url": "/?page=pulse", "tag": "macro"}

    cur = conn.cursor()
    ensure_tables(cur)
    cur.execute("SELECT DISTINCT user_id FROM push_subscriptions WHERE watch_macro")
    users = [r[0] for r in cur.fetchall()]
    sent = 0
    for user_id in users:
        if _first_time(cur, user_id, f"macro:{slot_utc.strftime('%Y-%m-%dT%H:%MZ')}"):
            sent += send_to_user(cur, user_id, payload, only_macro=True)
    conn.commit()
    cur.close()
    print(f"[push] macro events at {t_et} ET ({names_en}): {len(users)} user(s), {sent} delivered")
    return sent


def _morning_payload(lang: str, events: list, earnings: list, n_earnings: int, n_signals: int, watch_signals: list) -> dict:
    """v23. Factual, short: today's economic releases (Israel time in Hebrew, ET in English), notable reports today
    (watchlist first), and what the last scan found. Never advice."""
    from zoneinfo import ZoneInfo
    en = lang == "en"
    tz = ZoneInfo("America/New_York" if en else "Asia/Jerusalem")
    lines = []
    if events:
        parts = [f"{e.get('name_en' if en else 'name_he') or e.get('name_en') or ''} {e['when'].astimezone(tz).strftime('%H:%M')}"
                 for e in events[:3]]
        more = len(events) - 3
        lines.append(("Today: " if en else "היום: ") + ", ".join(parts)
                     + (f" +{more}" if more > 0 else "") + (" ET" if en else ""))
    else:
        lines.append("No major US economic data today." if en else "אין היום נתונים כלכליים חשובים בארה\"ב.")
    if earnings:
        when = {"bmo": ("before the open", "לפני הפתיחה"), "amc": ("after the close", "אחרי הסגירה")}
        parts = []
        for tk, session, mine in earnings[:4]:
            w = when.get(session)
            parts.append(tk + (f" ({w[0] if en else w[1]})" if mine and w else ""))
        more = n_earnings - len(parts)
        lines.append(("Reports: " if en else "דוחות: ") + ", ".join(parts) + (f" +{more}" if more > 0 else ""))
    sig = (f"Last scan: {n_signals} signal{'s' if n_signals != 1 else ''}" if en else f"הסריקה האחרונה: {n_signals} איתותים")
    if watch_signals:
        sig += (" — watchlist: " if en else " — מרשימת המעקב: ") + ", ".join(watch_signals[:4])
    lines.append(sig)
    return {"title": "Morning brief" if en else "תדריך בוקר", "body": "\n".join(lines)[:300], "url": "/", "tag": "morning"}


def notify_morning_brief(conn, day, events: list) -> int:
    """v23. Once per user per (ET) day (push_log 'morning:<date>'), to devices with the morning-brief switch on
    (watch_morning, off until the user turns it on). events = today's high/medium US releases [{name_he, name_en,
    impact, when (aware datetime)}], high first. Sent by api_server's alert thread before the US open."""
    if not ready():
        return 0
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()
    cur.execute("SELECT DISTINCT user_id FROM push_subscriptions WHERE watch_morning")
    users = [r[0] for r in cur.fetchall()]
    if not users:
        cur.close()
        return 0
    # shared for everyone: today's reports (largest companies first) and how many signals the last scan saved
    cur.execute("""SELECT ticker, session FROM earnings_calendar WHERE report_date = %s
                   ORDER BY market_cap DESC NULLS LAST""", (day,))
    reports = cur.fetchall()
    # "the last scan" = rows within 2 hours of the newest one (scans are 6+ hours apart; on a Monday it is Friday's)
    last_scan = "timestamp > (SELECT MAX(timestamp) FROM scanned_stocks) - INTERVAL '2 hours'"
    cur.execute(f"SELECT COUNT(DISTINCT ticker) FROM scanned_stocks WHERE {last_scan}")
    n_signals = cur.fetchone()[0] or 0
    sent = 0
    for user_id in users:
        if not _first_time(cur, user_id, f"morning:{day.isoformat()}"):
            continue
        cur.execute("SELECT ticker FROM watchlist WHERE user_id = %s", (user_id,))
        watch = {r[0] for r in cur.fetchall()}
        mine = [(tk, s, True) for tk, s in reports if tk in watch]
        others = [(tk, s, False) for tk, s in reports if tk not in watch]
        earnings = (mine + others)[:4]
        watch_sig = []
        if watch:
            cur.execute(f"""SELECT DISTINCT ticker FROM scanned_stocks
                            WHERE ticker = ANY(%s) AND {last_scan} ORDER BY ticker""", (list(watch),))
            watch_sig = [r[0] for r in cur.fetchall()]
        sent += send_to_user(cur, user_id, lambda lang, e=earnings, w=watch_sig:
                             _morning_payload(lang, events, e, len(reports), n_signals, w), only_morning=True)
    conn.commit()
    cur.close()
    print(f"[push] morning brief {day}: {len(users)} user(s), {len(events)} event(s), {len(reports)} report(s), {sent} delivered")
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
