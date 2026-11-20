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
-- optional accounts: regular people can sign in to see and fix their own notes; admins see everything
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    name_key   TEXT NOT NULL UNIQUE,        -- lowercased name, so "Dee" and "dee" are one account
    pin_hash   TEXT NOT NULL,               -- pbkdf2 salt$hash, never the PIN itself
    role       TEXT NOT NULL DEFAULT 'worker',  -- worker | admin
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_login TEXT
);
"""

# columns added after the first version; old databases get them on first connect
_MIGRATIONS = {
    "phone": "ALTER TABLE entries ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
    "user_id": "ALTER TABLE entries ADD COLUMN user_id INTEGER",  # set when a signed-in person logs a note
    "client_id": "ALTER TABLE entries ADD COLUMN client_id TEXT",  # set by the phone so a resent offline note counts once
}
_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if str(DB_PATH) not in _ready:
        conn.executescript(SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
        for col, sql in _MIGRATIONS.items():
            if col not in have:
                conn.execute(sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_client ON entries(client_id)")
        conn.commit()
        _ready.add(str(DB_PATH))
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
        "partners", "outcome", "follow_up", "hours", "sensitive", "extractor", "phone", "user_id", "client_id",
    ]
    entry = {"phone": "", "user_id": None, "client_id": None, **entry}
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


def by_client_id(client_id: str) -> dict | None:
    """The phone tags each note with a random id; if the same note arrives twice (resent after a lost reply), it's one note."""
    with connect() as conn:
        r = conn.execute("SELECT * FROM entries WHERE client_id = ? ORDER BY id LIMIT 1", (client_id,)).fetchone()
        return _row(r) if r else None


def entries_for_user(user_id: int, since: str) -> list[dict]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM entries WHERE user_id = ? AND activity_date >= ? ORDER BY activity_date DESC, id DESC",
            (user_id, since))]


# ---------- accounts ----------
def create_user(name: str, pin_hash: str, role: str = "worker") -> dict | None:
    """None if the name is taken."""
    try:
        with connect() as conn:
            cur = conn.execute("INSERT INTO users (name, name_key, pin_hash, role) VALUES (?, ?, ?, ?)",
                               (name, name.lower(), pin_hash, role))
            return get_user(cur.lastrowid, conn)
    except sqlite3.IntegrityError:
        return None


def get_user(user_id: int, conn: sqlite3.Connection | None = None) -> dict | None:
    conn = conn or connect()
    r = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def user_by_name(name: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE name_key = ?", (name.strip().lower(),)).fetchone()
        return dict(r) if r else None


def list_users() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT u.id, u.name, u.role, u.created_at, u.last_login, COUNT(e.id) AS entries, "
            "COALESCE(SUM(e.people_served), 0) AS people FROM users u LEFT JOIN entries e ON e.user_id = u.id "
            "GROUP BY u.id ORDER BY u.role = 'admin' DESC, u.name_key")]


def update_user(user_id: int, **fields) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in ("role", "pin_hash", "last_login") and v is not None}
    with connect() as conn:
        if fields:
            conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), user_id])
        return get_user(user_id, conn)


def delete_user(user_id: int) -> bool:
    """Their notes stay (the work still happened) but are no longer linked to an account."""
    with connect() as conn:
        conn.execute("UPDATE entries SET user_id = NULL WHERE user_id = ?", (user_id,))
        return conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0


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
