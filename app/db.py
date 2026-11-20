"""SQLite storage. One file (data/impact.db) the organization owns: copy it, back it up, export it as CSV."""
import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("IMPACT_DB") or Path(__file__).resolve().parent.parent / "data" / "impact.db")

LIST_FIELDS = ("referred_to", "partners")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    activity_date TEXT NOT NULL,
    worker        TEXT NOT NULL,
    channel       TEXT NOT NULL,           -- web | sms | voice | seed
    raw_text      TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    summary       TEXT NOT NULL,
    people_served INTEGER NOT NULL DEFAULT 0,
    youth_served  INTEGER NOT NULL DEFAULT 0,
    location      TEXT NOT NULL DEFAULT '',
    neighborhood  TEXT NOT NULL DEFAULT '',
    referred_to   TEXT NOT NULL DEFAULT '[]',
    partners      TEXT NOT NULL DEFAULT '[]',
    outcome       TEXT NOT NULL DEFAULT 'unknown',
    follow_up     TEXT NOT NULL DEFAULT '',
    hours         REAL NOT NULL DEFAULT 0,
    sensitive     INTEGER NOT NULL DEFAULT 0,
    extractor     TEXT NOT NULL DEFAULT 'ai',
    phone         TEXT NOT NULL DEFAULT ''  -- SMS sender; identifies the worker for UNDO/ME (names can be shared)
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(activity_date);
CREATE TABLE IF NOT EXISTS workers (
    phone TEXT PRIMARY KEY,
    name  TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if "phone" not in {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}:  # databases made before this column
        conn.execute("ALTER TABLE entries ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    return conn


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for f in LIST_FIELDS:
        d[f] = json.loads(d[f] or "[]")
    d["sensitive"] = bool(d["sensitive"])
    if d.get("phone"):  # identify the sender without handing out workers' full numbers
        d["phone"] = "…" + d["phone"][-4:]
    return d


def insert(entry: dict) -> dict:
    cols = [
        "activity_date", "worker", "channel", "raw_text", "activity_type", "summary",
        "people_served", "youth_served", "location", "neighborhood", "referred_to",
        "partners", "outcome", "follow_up", "hours", "sensitive", "extractor", "phone",
    ]
    entry = {"phone": "", **entry}
    vals = [json.dumps(entry[c]) if c in LIST_FIELDS else entry[c] for c in cols]
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO entries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
        )
        return get(cur.lastrowid, conn)


def get(entry_id: int, conn: sqlite3.Connection | None = None) -> dict | None:
    conn = conn or connect()
    r = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _row(r) if r else None


def list_entries(since: str | None = None, limit: int = 500) -> list[dict]:
    q, args = "SELECT * FROM entries", []
    if since:
        q, args = q + " WHERE activity_date >= ?", [since]
    q += " ORDER BY activity_date DESC, id DESC LIMIT ?"
    with connect() as conn:
        return [_row(r) for r in conn.execute(q, [*args, limit])]


def update(entry_id: int, fields: dict) -> dict | None:
    allowed = {
        "activity_date", "activity_type", "summary", "people_served", "youth_served", "location",
        "neighborhood", "referred_to", "partners", "outcome", "follow_up", "hours", "worker",
    }
    fields = {k: (json.dumps(v) if k in LIST_FIELDS else v) for k, v in fields.items() if k in allowed}
    if not fields:
        return get(entry_id)
    with connect() as conn:
        conn.execute(
            f"UPDATE entries SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
            [*fields.values(), entry_id],
        )
        return get(entry_id, conn)


def delete(entry_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,)).rowcount > 0


def recent_duplicate(worker: str, raw_text: str, phone: str = "", minutes: int = 10) -> dict | None:
    """Same person, same words, a few minutes apart = a double send (bad signal, double tap). Don't count it twice.
    "Same person" = same phone for texts (two workers can share a name), same worker name for the web."""
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM entries WHERE worker = ? AND phone = ? AND lower(trim(raw_text)) = lower(trim(?)) "
            "AND created_at >= datetime('now','localtime',?) ORDER BY id DESC LIMIT 1",
            (worker, phone, raw_text, f"-{int(minutes)} minutes"),
        ).fetchone()
        return _row(r) if r else None


def last_entry_for(phone: str) -> dict | None:
    """This phone's own most recent text from the last 24h (UNDO never reaches older records or anyone else's)."""
    if not phone:
        return None
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM entries WHERE phone = ? AND channel = 'sms' AND extractor != 'seed' "
            "AND created_at >= datetime('now','localtime','-1 day') ORDER BY id DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return _row(r) if r else None


def entries_for_phone(phone: str, since: str) -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM entries WHERE phone = ? AND activity_date >= ? ORDER BY id DESC", (phone, since))]


def worker_name(phone: str) -> str | None:
    with connect() as conn:
        r = conn.execute("SELECT name FROM workers WHERE phone = ?", (phone,)).fetchone()
        return r["name"] if r else None


def set_worker_name(phone: str, name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO workers (phone, name) VALUES (?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET name = excluded.name",
            (phone, name),
        )


def clear_all() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM entries")
