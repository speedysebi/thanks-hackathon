"""
Two sides of the app.

  Regular people   No sign-in needed: log a note (web, voice, text), undo what they just saved.
                   Optional account (name + PIN) adds "My notes": their own totals, follow-ups, fix or remove their notes.
  Admins           Must sign in: dashboard, report, CSV export, console, calendar/spreadsheet links, team.
                   Built-in admin: name "admin" + ADMIN_PASSWORD from .env. If none is set, a random one is made
                   on first start, saved to data/admin_password.txt and printed in the server log.
                   Admins can also make any account an admin on /team.

Sessions are a signed cookie (HMAC-SHA256). The role is re-read from the database on every request,
so taking away admin or deleting an account takes effect at once.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
import unicodedata
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app import db, mfa

COOKIE = "it_session"
WORKER_DAYS, ADMIN_DAYS = 60, 7  # people stay signed in on their own phone; admin sessions are shorter
ADMIN_NAME = "admin"
router = APIRouter(prefix="/api/auth")


# ---------- secrets kept next to the database ----------
def _data_file(name: str, make) -> str:
    path = Path(db.DB_PATH).parent / name
    try:
        v = path.read_text(encoding="utf-8").strip()
        if v:
            return v
    except FileNotFoundError:
        pass
    v = make()
    path.parent.mkdir(exist_ok=True)
    path.write_text(v, encoding="utf-8")
    return v


def _secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or _data_file("session_secret.txt", lambda: secrets.token_urlsafe(32))).encode()


def admin_password_from_env() -> str | None:
    return os.getenv("ADMIN_PASSWORD") or os.getenv("APP_PASSWORD")  # APP_PASSWORD = the older name


def admin_password() -> str:
    return admin_password_from_env() or _data_file("admin_password.txt", lambda: secrets.token_urlsafe(9))


# ---------- PINs ----------
def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


_DUMMY = "AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def check_pin(pin: str, stored: str) -> bool:
    try:
        salt_b64, h_b64 = stored.split("$", 1)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), base64.b64decode(salt_b64), 200_000)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except ValueError:
        return False


# ---------- signed session cookie ----------
def _sign(s: str) -> str:
    return hmac.new(_secret(), s.encode(), hashlib.sha256).hexdigest()


def make_session(uid: int, days: int, mfa_ok: bool = False) -> str:
    body = f"{uid}|{int(time.time()) + days * 86400}|{int(mfa_ok)}"
    return base64.urlsafe_b64encode(body.encode()).decode() + "." + _sign(body)


def read_session(cookie: str | None) -> dict | None:
    """{'id', 'name', 'role', 'mfa'} for a valid cookie, else None. uid 0 = the built-in admin.
    'mfa' is True only if this sign-in passed the authenticator code AND two-step is still on for the account.
    If two-step is on but this cookie never passed a code (made before it was turned on), it's not a session."""
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    try:
        body = base64.urlsafe_b64decode(b64.encode()).decode()
        parts = body.split("|")
        if len(parts) == 2:  # cookie from before two-step sign-in existed
            parts.append("0")
        uid, exp, passed = int(parts[0]), int(parts[1]), parts[2] == "1"
    except (ValueError, UnicodeDecodeError, IndexError):
        return None
    if len(parts) != 3 or not _same(sig, _sign(body)) or exp < time.time():
        return None
    if uid == 0:
        u = {"id": 0, "name": "Admin", "role": "admin"}
    else:
        # Resolve the role from SQLite so account removal or a role change applies to existing cookies.
        row = db.get_user(uid)
        if not row:
            return None
        u = {"id": row["id"], "name": row["name"], "role": row["role"]}
    on = mfa.enabled(uid)
    if on and not passed:
        return None
    return u | {"mfa": on and passed}


def current_user(request: Request) -> dict | None:
    if not hasattr(request.state, "user"):
        request.state.user = read_session(request.cookies.get(COOKIE))
    return request.state.user


def is_admin(request: Request) -> bool:
    """Admin role AND this sign-in passed two-step. An admin without it can only reach the setup page."""
    u = current_user(request)
    return bool(u and u["role"] == "admin" and u["mfa"])


def needs_2fa_setup(request: Request) -> bool:
    u = current_user(request)
    return bool(u and u["role"] == "admin" and not u["mfa"])


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or (os.getenv("PUBLIC_URL") or "").startswith("https://")


def _set_cookie(request: Request, response: Response, uid: int, role: str, mfa_ok: bool = False) -> None:
    days = ADMIN_DAYS if role == "admin" else WORKER_DAYS
    response.set_cookie(COOKIE, make_session(uid, days, mfa_ok), max_age=days * 86400, httponly=True,
                        samesite="lax", secure=_secure(request), path="/")


# ---------- "password OK, now the code" (5 minutes, its own cookie) ----------
PENDING = "it_2fa"
PENDING_SECONDS = 300


def _set_pending(request: Request, response: Response, uid: int) -> None:
    body = f"2fa|{uid}|{int(time.time()) + PENDING_SECONDS}"
    response.set_cookie(PENDING, base64.urlsafe_b64encode(body.encode()).decode() + "." + _sign(body),
                        max_age=PENDING_SECONDS, httponly=True, samesite="strict", secure=_secure(request),
                        path="/api/auth")


def _read_pending(request: Request) -> int | None:
    v = request.cookies.get(PENDING) or ""
    if "." not in v:
        return None
    b64, sig = v.rsplit(".", 1)
    try:
        body = base64.urlsafe_b64decode(b64.encode()).decode()
        tag, uid_s, exp_s = body.split("|")
        uid, exp = int(uid_s), int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    if tag != "2fa" or not _same(sig, _sign(body)) or exp < time.time():
        return None
    return uid


# ---------- "undo" without an account ----------
def undo_token(entry_id: int) -> str:
    """Handed back when a note is saved, so whoever saved it (signed in or not) can undo it from that screen."""
    return _sign(f"undo:{entry_id}")[:32]


def check_undo_token(entry_id: int, token: str | None) -> bool:
    return bool(token) and _same(token, undo_token(entry_id))


def _same(a: str, b: str) -> bool:
    """Constant-time compare that doesn't crash (500) on non-ASCII input."""
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8", "replace"))


# ---------- slow down PIN guessing ----------
_fails: dict[str, list[float]] = {}
MAX_FAILS, WINDOW = 5, 600
# per IP + name: 5 tries. Per IP across all names: 30. Per name from everywhere: 50, so one stranger
# can't lock a real person (or the admin) out with 5 bad tries, but a spread-out guessing run still stalls.
LIMITS = {"ipname": 5, "ip": 30, "name": 50}


def _limited(key: str, most: int = MAX_FAILS) -> bool:
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < WINDOW]
    return len(_fails[key]) >= most


def _fail(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


# ---------- routes ----------
class Credentials(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    pin: str = Field(min_length=4, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        # fullwidth/odd forms -> normal letters, drop invisible characters (zero-width spaces etc.)
        v = "".join(c for c in unicodedata.normalize("NFKC", v) if unicodedata.category(c) != "Cf")
        v = " ".join(v.split())
        if not v:
            raise ValueError("name is empty")
        return v


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


# letters from other alphabets that look like the Latin ones in "admin"
_LOOKALIKE = str.maketrans("аԁіıɩⅰмոռпṁḿ", "adiiiimnnnmm")


def looks_like_admin(name: str) -> bool:
    """'аdmin' (Cyrillic а), 'Admin ', 'a.d.m.i.n', 'ADMIN 1'... can't be account names."""
    skeleton = "".join(c for c in name_key(name).translate(_LOOKALIKE) if c.isalpha())
    return skeleton.startswith(ADMIN_NAME) or skeleton == "administrator"


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    name = name_key(body.name)
    ip = "ip:" + (request.client.host if request.client else "?")
    keys = {"name": "name:" + name, "ip": ip, "ipname": ip + ":" + name}
    if any(_limited(k, LIMITS[kind]) for kind, k in keys.items()):
        raise HTTPException(429, "Too many wrong tries. Wait 10 minutes and try again.")
    who = None
    if body.name.lower() == ADMIN_NAME:
        if hmac.compare_digest(body.pin.encode(), admin_password().encode()):
            who = {"id": 0, "name": "Admin", "role": "admin"}
    else:
        u = db.user_by_name(body.name)
        if u and check_pin(body.pin, u["pin_hash"]):
            who = u
        elif not u:
            check_pin(body.pin, _DUMMY)  # same work either way, so timing doesn't reveal which names exist
    if not who:
        for k in keys.values():
            _fail(k)
        raise HTTPException(401, "That name and PIN don't match.")
    response.delete_cookie(COOKIE, path="/")  # a half-finished sign-in never keeps an older session
    if mfa.enabled(who["id"]):  # password was right; the session only comes after the code
        _set_pending(request, response, who["id"])
        return {"need_code": True}
    return _finish(request, response, who, mfa_ok=False)


def _finish(request: Request, response: Response, who: dict, mfa_ok: bool) -> dict:
    if who["id"]:
        db.update_user(who["id"], last_login=time.strftime("%Y-%m-%d %H:%M:%S"))
    _set_cookie(request, response, who["id"], who["role"], mfa_ok)
    response.delete_cookie(PENDING, path="/api/auth")
    # an admin without two-step gets a session that can only open the setup page
    return {"name": who["name"], "role": who["role"], "setup_2fa": who["role"] == "admin" and not mfa_ok}


class Code(BaseModel):
    code: str = Field(min_length=6, max_length=20)


@router.post("/code")
def login_code(body: Code, request: Request, response: Response):
    """Second step of signing in: the 6-digit code from the authenticator app, or a backup code."""
    uid = _read_pending(request)
    if uid is None:
        raise HTTPException(401, "That took too long. Start again with your name and PIN.")
    key = f"code:{uid}"
    if _limited(key):
        raise HTTPException(429, "Too many wrong codes. Wait 10 minutes and try again.")
    if not mfa.verify(uid, body.code):
        _fail(key)
        raise HTTPException(401, "That code didn't work. Codes change every 30 seconds; use the newest one.")
    who = {"id": 0, "name": "Admin", "role": "admin"} if uid == 0 else db.get_user(uid)
    if not who:
        raise HTTPException(401, "That account was removed.")
    return _finish(request, response, who, mfa_ok=True)


@router.post("/signup")
def signup(body: Credentials, request: Request, response: Response):
    if looks_like_admin(body.name):
        raise HTTPException(400, "Pick a different name.")
    if not body.pin.isdigit() and len(body.pin) < 6:
        raise HTTPException(422, "Use a PIN of 4+ numbers, or a password of 6+ characters.")
    u = db.create_user(body.name, hash_pin(body.pin))
    if not u:
        raise HTTPException(409, "That name is taken. Sign in instead, or add your last initial.")
    _set_cookie(request, response, u["id"], u["role"])
    return {"name": u["name"], "role": u["role"]}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    u = current_user(request)
    return {"signed_in": bool(u), "name": u["name"] if u else None, "role": u["role"] if u else None,
            "admin": is_admin(request), "mfa": bool(u and u["mfa"]), "needs_2fa": needs_2fa_setup(request)}


# ---------- turning two-step sign-in on and off (/security) ----------
def _signed_in(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Sign in first.")
    return u


@router.get("/2fa")
def twofa_status(request: Request):
    u = _signed_in(request)
    return {"enabled": u["mfa"], "required": u["role"] == "admin", "backup_codes_left": mfa.backup_codes_left(u["id"]),
            "set_in_env": u["id"] == 0 and mfa.env_admin_secret() is not None, "builtin_admin": u["id"] == 0}


@router.post("/2fa/start")
def twofa_start(request: Request):
    """Make a new secret and show it as a QR code. Nothing changes until /2fa/enable gets a code from it."""
    u = _signed_in(request)
    if u["mfa"]:
        raise HTTPException(409, "Two-step sign-in is already on. Turn it off first to move it to a new phone.")
    secret = mfa.new_secret()
    db.set_mfa(u["id"], pending=secret)
    uri = mfa.otpauth_uri(secret, u["name"])
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr_svg": mfa.qr_svg(uri)}


@router.post("/2fa/enable")
def twofa_enable(body: Code, request: Request, response: Response):
    u = _signed_in(request)
    row = db.get_mfa(u["id"])
    if u["mfa"] or not row or not row["pending"]:
        raise HTTPException(409, "Start setup again.")
    key = f"setup:{u['id']}"
    if _limited(key):
        raise HTTPException(429, "Too many wrong codes. Wait 10 minutes and try again.")
    step = mfa.matching_step(row["pending"], body.code)
    if step is None:
        _fail(key)
        raise HTTPException(400, "That code didn't match. Check the phone's clock is set automatically, and use the newest code.")
    codes, hashes = mfa.new_recovery_codes()
    db.set_mfa(u["id"], secret=row["pending"], pending=None, last_step=step, recovery=hashes,
               enabled_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    _set_cookie(request, response, u["id"], u["role"], mfa_ok=True)  # this phone stays signed in; others must sign in again
    return {"enabled": True, "backup_codes": codes}


@router.post("/2fa/backup-codes")
def twofa_new_backup_codes(body: Code, request: Request):
    u = _signed_in(request)
    if not u["mfa"] or not mfa.verify(u["id"], body.code):
        raise HTTPException(400, "Enter a current code from your authenticator app.")
    codes, hashes = mfa.new_recovery_codes()
    db.set_mfa(u["id"], recovery=hashes)
    return {"backup_codes": codes}


@router.post("/2fa/disable")
def twofa_disable(body: Code, request: Request, response: Response):
    u = _signed_in(request)
    if u["role"] == "admin":
        raise HTTPException(403, "Admins must keep two-step sign-in on. To move it to a new phone, ask another admin to reset it on Team.")
    if not u["mfa"]:
        raise HTTPException(409, "It's already off.")
    key = f"code:{u['id']}"
    if _limited(key) or not mfa.verify(u["id"], body.code):
        _fail(key)
        raise HTTPException(400, "Enter a current code from your authenticator app.")
    db.delete_mfa(u["id"])
    _set_cookie(request, response, u["id"], u["role"], mfa_ok=False)
    return {"enabled": False}
