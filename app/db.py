"""SQLite storage. One file (data/impact.db) the organization owns: copy it, back it up, export it as CSV."""
import json
import os
import sqlite3
import threading
from pathlib import Path

from app import privacy

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
-- Monthly completion records and claims serialize automatic send attempts.
CREATE TABLE IF NOT EXISTS report_sends (
    month      TEXT PRIMARY KEY,            -- YYYY-MM the report covers
    claimed_at TEXT,                        -- when a server started sending it
    sent_at    TEXT,                        -- NULL while being sent
    sent_to    TEXT NOT NULL DEFAULT ''
);
-- Keep accepted recipients even when a partial failure releases the month's send claim.
CREATE TABLE IF NOT EXISTS report_deliveries (
    month     TEXT NOT NULL,
    recipient TEXT NOT NULL,
    sent_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (month, recipient)
);
-- reply language a phone asked for (ESPANOL / ENGLISH). No row = follow the language of each text.
CREATE TABLE IF NOT EXISTS sms_lang (
    phone TEXT PRIMARY KEY,
    lang  TEXT NOT NULL                    -- en | es
);
-- a worker's texting phone, linked to their account so texts show on My notes (proved by texting LINK <code>)
CREATE TABLE IF NOT EXISTS phone_links (
    phone     TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    linked_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS link_codes (
    user_id    INTEGER PRIMARY KEY,         -- one live code per account; asking again replaces it
    code       TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL
);
-- two-step sign-in (authenticator app codes). uid 0 = the built-in admin. Required for admins, optional for others.
CREATE TABLE IF NOT EXISTS mfa (
    uid        INTEGER PRIMARY KEY,
    secret     TEXT,                        -- base32 TOTP secret once turned on
    pending    TEXT,                        -- secret shown during setup, until the first code proves the phone has it
    last_step  INTEGER NOT NULL DEFAULT 0,  -- last 30-second step used, so a code can't be replayed
    recovery   TEXT NOT NULL DEFAULT '[]',  -- JSON list of sha256 hashes of unused one-time backup codes
    enabled_at TEXT
);
"""

# columns added after the first version; old databases get them on first connect
_MIGRATIONS = {
    "phone": "ALTER TABLE entries ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
    "user_id": "ALTER TABLE entries ADD COLUMN user_id INTEGER",  # set when a signed-in person logs a note
    "client_id": "ALTER TABLE entries ADD COLUMN client_id TEXT",  # set by the phone so a resent offline note counts once
}
_ready: set[str] = set()
_schema_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if str(DB_PATH) not in _ready:
            with _schema_lock:
                if str(DB_PATH) not in _ready:
                    conn.executescript(SCHEMA)
                    # Serialize migrations across connections/processes as well as threads.
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        have = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
                        for col, sql in _MIGRATIONS.items():
                            if col not in have:
                                conn.execute(sql)
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id)")
                        try:
                            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_client "
                                         "ON entries(client_id) WHERE client_id IS NOT NULL")
                        except sqlite3.IntegrityError as exc:
                            raise RuntimeError(
                                "Duplicate client IDs exist in entries. Reconcile them before retrying; "
                                "see docs/DATA_CORRECTNESS.md. No entries were removed."
                            ) from exc
                        conn.execute("DROP INDEX IF EXISTS idx_entries_client")
                    _ready.add(str(DB_PATH))
    except Exception:
        conn.close()
        raise
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
            f"INSERT INTO entries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            "ON CONFLICT(client_id) WHERE client_id IS NOT NULL DO NOTHING", vals
        )
        if cur.rowcount == 0:
            # Another request saved this ID after our initial lookup. Return its entry;
            # the API still checks ownership before exposing contents or an undo token.
            row = conn.execute("SELECT * FROM entries WHERE client_id = ?", (entry["client_id"],)).fetchone()
            return {**_row(row), "duplicate": True}
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


def list_followups(since: str | None = None, worker: str | None = None) -> list[dict]:
    """All unresolved follow-ups, independent of the recent-entry limit."""
    q, args = "SELECT * FROM entries WHERE outcome = 'needs_follow_up'", []
    if since:
        q += " AND activity_date >= ?"
        args.append(since)
    q += " ORDER BY activity_date, id"
    with connect() as conn:
        rows = [_row(r) for r in conn.execute(q, args)]
    # SQLite lower() only handles ASCII; preserve case-insensitive accented names.
    return [e for e in rows if e["worker"].lower() == worker.lower()] if worker else rows


class InvalidEntryCounts(ValueError):
    pass


def update(entry_id: int, fields: dict) -> dict | None:
    allowed = {
        "activity_date", "activity_type", "summary", "people_served", "youth_served", "location",
        "neighborhood", "referred_to", "partners", "outcome", "follow_up", "hours", "worker",
    }
    fields = {k: (json.dumps(v) if k in LIST_FIELDS else v) for k, v in fields.items() if k in allowed}
    if not fields:
        return get(entry_id)
    for key in ("summary", "follow_up", "neighborhood", "location", "worker"):
        if key in fields:
            fields[key] = privacy.scrub(fields[key])
    with connect() as conn:
        # Read, validate and write under one lock: concurrent partial edits must
        # validate against each other's committed values, not a stale API read.
        conn.execute("BEGIN IMMEDIATE")
        current = get(entry_id, conn)
        if not current:
            return None
        people = fields.get("people_served", current["people_served"])
        youth = fields.get("youth_served", current["youth_served"])
        if youth > people:
            raise InvalidEntryCounts("Youth served cannot exceed people served.")
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
            "COALESCE(SUM(e.people_served), 0) AS people, "
            "EXISTS (SELECT 1 FROM mfa m WHERE m.uid = u.id AND m.secret IS NOT NULL) AS mfa "
            "FROM users u LEFT JOIN entries e ON e.user_id = u.id "
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
        conn.execute("DELETE FROM mfa WHERE uid = ?", (user_id,))
        conn.execute("DELETE FROM phone_links WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM link_codes WHERE user_id = ?", (user_id,))
        return conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0


# ---------- two-step sign-in ----------
def get_mfa(uid: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM mfa WHERE uid = ?", (uid,)).fetchone()
        return dict(r) if r else None


def set_mfa(uid: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in ("secret", "pending", "last_step", "recovery", "enabled_at")}
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO mfa (uid) VALUES (?)", (uid,))
        if fields:
            conn.execute(f"UPDATE mfa SET {', '.join(f'{k} = ?' for k in fields)} WHERE uid = ?", [*fields.values(), uid])


def use_mfa_step(uid: int, step: int) -> bool:
    """Atomically record that a code for `step` was used. False if that step (or a later one) was already used."""
    with connect() as conn:
        return conn.execute("UPDATE mfa SET last_step = ? WHERE uid = ? AND last_step < ?", (step, uid, step)).rowcount == 1


def delete_mfa(uid: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM mfa WHERE uid = ?", (uid,)).rowcount > 0


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


# ---------- monthly report emails ----------
def claim_report_send(month: str) -> bool:
    """True for exactly one caller per month. A claim left unsent for over an hour (crash mid-send) can be retaken."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("INSERT OR IGNORE INTO report_sends (month, claimed_at) VALUES (?, datetime('now','localtime'))",
                        (month,)).rowcount:
            return True
        return conn.execute("UPDATE report_sends SET claimed_at = datetime('now','localtime') WHERE month = ? "
                            "AND sent_at IS NULL AND claimed_at < datetime('now','localtime','-1 hour')",
                            (month,)).rowcount == 1


def release_report_send(month: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM report_sends WHERE month = ? AND sent_at IS NULL", (month,))


def mark_report_sent(month: str, to: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO report_sends (month) VALUES (?) ON CONFLICT(month) DO NOTHING", (month,))
        conn.execute("UPDATE report_sends SET sent_at = datetime('now','localtime'), sent_to = ? WHERE month = ?", (to, month))


def report_recipients_sent(month: str) -> set[str]:
    with connect() as conn:
        return {r["recipient"] for r in conn.execute(
            "SELECT recipient FROM report_deliveries WHERE month = ?", (month,))}


def mark_report_recipients_sent(month: str, recipients: list[str]) -> None:
    with connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO report_deliveries (month, recipient) VALUES (?, ?)",
                         [(month, recipient) for recipient in recipients])


def report_sends() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM report_sends WHERE sent_at IS NOT NULL ORDER BY month DESC LIMIT 12")]


# ---------- linking a texting phone to an account ----------
def set_link_code(user_id: int, code: str, minutes: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM link_codes WHERE expires_at < datetime('now','localtime')")
        conn.execute("INSERT INTO link_codes (user_id, code, expires_at) VALUES (?, ?, datetime('now','localtime',?)) "
                     "ON CONFLICT(user_id) DO UPDATE SET code = excluded.code, expires_at = excluded.expires_at",
                     (user_id, code, f"+{int(minutes)} minutes"))


def use_link_code(code: str, phone: str) -> dict | None:
    """A live code texted from `phone`: link that phone to the code's account, credit its earlier texts
    to the account, and use the code up. Returns the account, or None for a wrong or expired code."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT user_id FROM link_codes WHERE code = ? AND expires_at >= datetime('now','localtime')",
                         (code,)).fetchone()
        if not r:
            return None
        uid = r["user_id"]
        user = get_user(uid, conn)
        conn.execute("DELETE FROM link_codes WHERE user_id = ?", (uid,))
        if not user:
            return None
        conn.execute("INSERT INTO phone_links (phone, user_id) VALUES (?, ?) "
                     "ON CONFLICT(phone) DO UPDATE SET user_id = excluded.user_id, linked_at = datetime('now','localtime')",
                     (phone, uid))
        conn.execute("UPDATE entries SET user_id = ? WHERE phone = ? AND user_id IS NULL", (uid, phone))
        conn.execute("INSERT INTO workers (phone, name) VALUES (?, ?) ON CONFLICT(phone) DO UPDATE SET name = excluded.name",
                     (phone, user["name"]))
        return user


def linked_user(phone: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT u.* FROM phone_links p JOIN users u ON u.id = p.user_id WHERE p.phone = ?",
                         (phone,)).fetchone()
        return dict(r) if r else None


def phones_for_user(user_id: int) -> list[str]:
    """Last 4 digits only: the full number never leaves the server."""
    with connect() as conn:
        return ["…" + r["phone"][-4:] for r in conn.execute(
            "SELECT phone FROM phone_links WHERE user_id = ? ORDER BY linked_at", (user_id,))]


def unlink_phones(user_id: int) -> int:
    """Stop crediting new texts to the account. Notes already credited stay (the work still happened)."""
    with connect() as conn:
        return conn.execute("DELETE FROM phone_links WHERE user_id = ?", (user_id,)).rowcount


def sms_lang(phone: str) -> str | None:
    with connect() as conn:
        r = conn.execute("SELECT lang FROM sms_lang WHERE phone = ?", (phone,)).fetchone()
        return r["lang"] if r else None


def set_sms_lang(phone: str, lang: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO sms_lang (phone, lang) VALUES (?, ?) ON CONFLICT(phone) DO UPDATE SET lang = excluded.lang",
                     (phone, lang))


def clear_all() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM entries")
