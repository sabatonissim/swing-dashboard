"""
Database meter (v38) — how much each part of the project reads from the database, by its own count.
====================================================================================================
WHY: the free Neon plan stops the database after 5 GB a month of data sent OUT of it ("network transfer"), and in
November 2026 that ran close (4.33 GB by the 9th, after bugs early in the month). Each fix so far was a guess from reading
code, checked a day later against a screenshot. Postgres's own statistics (pg_stat_statements) do not help here: Neon
clears them every time the database falls asleep, many times a day (neon.com/docs/extensions/pg_stat_statements).

WHAT: every psycopg2 connection opened after install() is counted: connections, queries, rows read and an ESTIMATE of
the bytes read (the size of the values as text, as Postgres sends them, + 4 bytes a value and 7 a row). The handshake
of a connection (~5 KB, also an estimate) is added by data_health.py, which reports the totals every morning
(check "usage.db"). The counts go to table db_usage (day UTC, source) on a connection that is about to close anyway:
no extra connection and no extra wake-up of the database, except at the end of a run that left none open.

SOURCES: a GitHub job = its file name + its -- options ("pipeline_a_scanner --quick"). The API server: install("server"),
then each request is named by its first two path parts ("server /api/stock"), and loops in the background by
set_source() ("server scheduler"), else "server".

SAFE BY DESIGN: a failure in here never reaches the code that uses the database (every step is in try/except, a lost
count is only a lost count), and without psycopg2.extensions (the browser check of tests/) install() does nothing.
"""
import atexit
import contextvars
import json
import os
import sys
import threading
import time
import weakref
from collections.abc import Mapping
from datetime import datetime, timezone

KEEP_DAYS = 90
SERVER_FLUSH_EVERY = 600          # seconds; a job writes on every close (it is short), the server at most every 10 minutes

_lock = threading.Lock()
_pending = {}                     # source -> [connections, queries, rows, bytes]
_source = contextvars.ContextVar("db_meter_source", default=None)
_state = {"installed": False, "default": "unknown", "flush_every": 0, "last_flush": 0.0, "table": False,
          "prune": False, "real_connect": None, "dsn": None, "conns": None, "cursor_classes": {}}


# ------------------------------------------------------------------ counting (pure, tests/test_core.py)
def value_bytes(v) -> int:
    if v is None:
        return 0
    if isinstance(v, str):
        return len(v.encode("utf-8", "replace"))
    if isinstance(v, (bytes, bytearray, memoryview)):
        return 2 + 2 * len(v)                     # bytea is sent as hex text
    if isinstance(v, (dict, list)):
        return len(json.dumps(v, default=str, ensure_ascii=False).encode("utf-8", "replace"))
    return len(str(v))


def row_bytes(row) -> int:
    values = row.values() if isinstance(row, Mapping) else row
    try:
        return 7 + sum(4 + value_bytes(v) for v in values)
    except TypeError:                              # a single value, not a row
        return 11 + value_bytes(row)


def current_source() -> str:
    return _source.get() or _state["default"]


def set_source(name: str):
    """Names what the current request / thread reads. Returns a token for reset_source()."""
    return _source.set(name)


def reset_source(token) -> None:
    try:
        _source.reset(token)
    except Exception:                              # noqa: BLE001 — a token from another context
        pass


def request_source(path: str) -> str:
    """'/api/stock/KO' -> 'server /api/stock': one line per endpoint, not one per ticker."""
    return "server " + ("/".join(path.split("/")[:3]) or "/")


def add(source: str, connections: int = 0, queries: int = 0, rows: int = 0, nbytes: int = 0) -> None:
    with _lock:
        c = _pending.setdefault(source, [0, 0, 0, 0])
        c[0] += connections
        c[1] += queries
        c[2] += rows
        c[3] += nbytes


def take_pending() -> dict:
    with _lock:
        out = {k: v for k, v in _pending.items() if any(v)}
        _pending.clear()
    return out


def put_back(counts: dict) -> None:
    for source, (c, q, r, b) in counts.items():
        add(source, c, q, r, b)


def write_counts(cur, counts: dict, day) -> None:
    """One statement for all sources. The table is created by the first write of a process."""
    if not _state["table"]:
        cur.execute("""CREATE TABLE IF NOT EXISTS db_usage (
            day DATE NOT NULL, source TEXT NOT NULL, connections INTEGER NOT NULL DEFAULT 0, queries INTEGER NOT NULL DEFAULT 0,
            rows BIGINT NOT NULL DEFAULT 0, bytes BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (day, source))""")
        _state["table"] = True
    values, params = [], []
    for source, (c, q, r, b) in sorted(counts.items()):
        values.append("(%s, %s, %s, %s, %s, %s)")
        params += [day, source[:120], c, q, r, b]
    cur.execute("INSERT INTO db_usage (day, source, connections, queries, rows, bytes) VALUES " + ", ".join(values) +
                """ ON CONFLICT (day, source) DO UPDATE SET connections = db_usage.connections + EXCLUDED.connections,
                       queries = db_usage.queries + EXCLUDED.queries, rows = db_usage.rows + EXCLUDED.rows,
                       bytes = db_usage.bytes + EXCLUDED.bytes""", params)
    if _state["prune"]:
        _state["prune"] = False
        cur.execute("DELETE FROM db_usage WHERE day < CURRENT_DATE - %s", (KEEP_DAYS,))


def flush_through(conn, force: bool = False) -> bool:
    """Writes the counts on `conn` (open, about to be closed by its owner). Uncommitted work there is rolled back first,
    which closing it would have done anyway."""
    if not force and time.time() - _state["last_flush"] < _state["flush_every"]:
        return False
    counts = take_pending()
    if not counts:
        return False
    try:
        if getattr(conn, "closed", 1):
            raise RuntimeError("connection already closed")
        if not conn.autocommit and conn.info.transaction_status != 0:     # 0 = idle (psycopg2.extensions.TRANSACTION_STATUS_IDLE)
            conn.rollback()
        cur = _plain_cursor(conn)
        write_counts(cur, counts, datetime.now(timezone.utc).date())
        cur.close()
        if not conn.autocommit:
            conn.commit()
        _state["last_flush"] = time.time()
        return True
    except Exception as e:                         # noqa: BLE001
        put_back(counts)
        try:
            conn.rollback()
        except Exception:                          # noqa: BLE001
            pass
        print(f"[db_meter] could not save the counts ({type(e).__name__}: {str(e)[:120]})")
        return False


def _plain_cursor(conn):
    import psycopg2.extensions as ext
    return ext.connection.cursor(conn, cursor_factory=ext.cursor)


# ------------------------------------------------------------------ the psycopg2 hook
class _Counting:
    """Mixed into whatever cursor class the caller asked for (plain, RealDictCursor, DictCursor)."""

    def _count_rows(self, rows):
        try:
            add(current_source(), rows=len(rows), nbytes=sum(row_bytes(r) for r in rows))
        except Exception:                          # noqa: BLE001
            pass

    def execute(self, *a, **k):
        add(current_source(), queries=1)
        return super().execute(*a, **k)

    def executemany(self, *a, **k):
        add(current_source(), queries=1)
        return super().executemany(*a, **k)

    def fetchone(self):
        row = super().fetchone()
        if row is not None:
            self._count_rows([row])
        return row

    def fetchmany(self, *a, **k):
        rows = super().fetchmany(*a, **k)
        self._count_rows(rows)
        return rows

    def fetchall(self):
        rows = super().fetchall()
        self._count_rows(rows)
        return rows

    def __iter__(self):
        for row in super().__iter__():
            self._count_rows([row])
            yield row


def _counting_class(base):
    cls = _state["cursor_classes"].get(base)
    if cls is None:
        cls = _state["cursor_classes"][base] = type("Counting" + base.__name__, (_Counting, base), {})
    return cls


def _make_connection_class(ext):
    class MeteredConnection(ext.connection):
        def cursor(self, *a, **k):
            try:
                base = k.get("cursor_factory") or self.cursor_factory or ext.cursor
                k["cursor_factory"] = _counting_class(base)
            except Exception:                      # noqa: BLE001
                pass
            return super().cursor(*a, **k)

        def close(self):
            try:
                flush_through(self)
            except Exception:                      # noqa: BLE001
                pass
            return super().close()
    return MeteredConnection


def _job_name() -> str:
    stem = os.path.splitext(os.path.basename(sys.argv[0] or "python"))[0] or "python"
    return " ".join([stem] + [a for a in sys.argv[1:] if a.startswith("--")])[:120]


def install(name: str = None, flush_every: int = 0, prune: bool = False) -> bool:
    """Call once at the start of a process (a job's `if __name__ == "__main__":`, the server at import)."""
    if _state["installed"]:
        return True
    try:
        import psycopg2
        import psycopg2.extensions as ext
        conn_class = _make_connection_class(ext)
    except Exception:                              # noqa: BLE001 — no real psycopg2 (the browser check): nothing to count
        return False
    real = psycopg2.connect
    _state.update(installed=True, default=name or _job_name(), flush_every=flush_every, prune=prune,
                  real_connect=real, conns=weakref.WeakSet())

    def connect(*a, **k):
        if "connection_factory" not in k:
            k["connection_factory"] = conn_class
        conn = real(*a, **k)
        add(current_source(), connections=1)
        try:
            _state["dsn"] = (a, {kk: vv for kk, vv in k.items() if kk != "connection_factory"})
            _state["conns"].add(conn)
        except Exception:                          # noqa: BLE001
            pass
        return conn

    psycopg2.connect = connect
    atexit.register(_flush_at_exit)
    return True


def _flush_at_exit() -> None:
    """The run is ending: through a connection still open, else one short connection (only if something was counted)."""
    try:
        with _lock:
            if not any(any(v) for v in _pending.values()):
                return
        for conn in list(_state["conns"] or []):
            if not getattr(conn, "closed", 1) and flush_through(conn, force=True):
                return
        if _state["dsn"] and _state["real_connect"]:
            a, k = _state["dsn"]
            conn = _state["real_connect"](*a, **dict(k, connect_timeout=10))
            add("db_meter (end of run)", connections=1)
            try:
                flush_through(conn, force=True)
            finally:
                conn.close()
    except Exception as e:                         # noqa: BLE001
        print(f"[db_meter] counts of this run not saved ({type(e).__name__})")
