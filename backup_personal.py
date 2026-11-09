"""
Backup of what cannot be recreated (v35c) — users, watchlists, trades, price alerts
===================================================================================
Runs in GitHub Actions (backup-personal.yml), started by scheduler.py once a weekday, while the database is awake anyway.

Why: Neon's free plan can go back only 6 hours, so a mistake noticed a day later was final. Everything else in the database
comes back by itself from the jobs. A full copy (206 MB) would use up the 5 GB a month of data leaving Neon; this is under 1 MB.

The repository is public, and so are its run logs and files: the copy is encrypted with the secret BACKUP_KEY before it leaves
the job, and only totals are ever printed. Without that key a copy cannot be read by anyone, including the owner.

  python backup_personal.py backup  <file>    needs DATABASE_URL, BACKUP_KEY; reads the copy back before it counts as done
  python backup_personal.py restore <file>    only ADDS rows that are missing, never changes or deletes (backup-restore.yml)
"""
import base64
import datetime
import decimal
import gzip
import json
import os
import sys

import psycopg2
import psycopg2.extras
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

TABLES = ("users", "watchlist", "trades", "price_alerts")      # users first: the others point to it
MAGIC = b"SDB1"
SALT_BYTES = 16
KDF_ROUNDS = 600_000
MIN_KEY_CHARS = 16


def _fernet(password: str, salt: bytes) -> Fernet:
    if len(password or "") < MIN_KEY_CHARS:
        raise ValueError(f"BACKUP_KEY is missing or shorter than {MIN_KEY_CHARS} characters")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ROUNDS)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


def encrypt(data: dict, password: str) -> bytes:
    salt = os.urandom(SALT_BYTES)
    plain = gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return MAGIC + salt + _fernet(password, salt).encrypt(plain)


def decrypt(blob: bytes, password: str) -> dict:
    if blob[:len(MAGIC)] != MAGIC:
        raise ValueError("not a backup file")
    salt = blob[len(MAGIC):len(MAGIC) + SALT_BYTES]
    token = blob[len(MAGIC) + SALT_BYTES:]
    return json.loads(gzip.decompress(_fernet(password, salt).decrypt(token)))


def _plain(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    raise TypeError(f"cannot store a {type(v).__name__}")


def read_tables(cur) -> dict:
    out = {"made_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tables": {}}
    for table in TABLES:                                        # fixed names, not user input
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
        if not cur.fetchone()[0]:
            continue
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        out["tables"][table] = json.loads(json.dumps({"columns": columns, "rows": [list(r) for r in rows]}, default=_plain))
    return out


def counts(data: dict) -> dict:
    return {t: len(v["rows"]) for t, v in data["tables"].items()}


def restore_tables(cur, data: dict) -> dict:
    """Adds the rows that are missing. Column names are used only if the table really has them today."""
    added = {}
    for table in TABLES:
        saved = data["tables"].get(table)
        if not saved:
            continue
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
        existing = {r[0] for r in cur.fetchall()}
        keep = [i for i, c in enumerate(saved["columns"]) if c in existing]
        names = ", ".join(f'"{saved["columns"][i]}"' for i in keep)
        marks = ", ".join(["%s"] * len(keep))
        added[table] = 0
        for row in saved["rows"]:
            values = [psycopg2.extras.Json(row[i]) if isinstance(row[i], (dict, list)) else row[i] for i in keep]
            cur.execute(f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT DO NOTHING", values)
            added[table] += max(cur.rowcount, 0)
        if "id" in existing:                                    # the next new row must not take a restored number
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table}), 1))")
    return added


def main(argv) -> int:
    if len(argv) != 3 or argv[1] not in ("backup", "restore"):
        print(__doc__)
        return 2
    action, path = argv[1], argv[2]
    password = os.environ.get("BACKUP_KEY", "")
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    try:
        cur = conn.cursor()
        if action == "backup":
            data = read_tables(cur)
            blob = encrypt(data, password)
            with open(path, "wb") as f:
                f.write(blob)
            with open(path, "rb") as f:                         # a copy that cannot be read back is not a copy
                if counts(decrypt(f.read(), password)) != counts(data):
                    raise RuntimeError("the copy does not read back the same")
            total = sum(counts(data).values())
            print(f"backup: {total} rows in {len(data['tables'])} tables, {len(blob)} bytes, read back and verified")
            if not total:
                print("[warn] backup: every table is empty")
        else:
            with open(path, "rb") as f:
                data = decrypt(f.read(), password)
            added = restore_tables(cur, data)
            conn.commit()
            print(f"restore: copy of {data['made_at']}: {sum(counts(data).values())} rows in it, {sum(added.values())} were missing and added")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    sys.exit(main(sys.argv))
