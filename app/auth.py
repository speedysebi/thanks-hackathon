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
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app import db

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


def make_session(uid: int, days: int) -> str:
    body = f"{uid}|{int(time.time()) + days * 86400}"
    return base64.urlsafe_b64encode(body.encode()).decode() + "." + _sign(body)


def read_session(cookie: str | None) -> dict | None:
    """{'id', 'name', 'role'} for a valid cookie, else None. uid 0 = the built-in admin."""
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    try:
        body = base64.urlsafe_b64decode(b64.encode()).decode()
        uid_s, exp_s = body.split("|")
        uid, exp = int(uid_s), int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(sig, _sign(body)) or exp < time.time():
        return None
    if uid == 0:
        return {"id": 0, "name": "Admin", "role": "admin"}
    u = db.get_user(uid)
    return {"id": u["id"], "name": u["name"], "role": u["role"]} if u else None


def current_user(request: Request) -> dict | None:
    if not hasattr(request.state, "user"):
        request.state.user = read_session(request.cookies.get(COOKIE))
    return request.state.user


def is_admin(request: Request) -> bool:
    u = current_user(request)
    return bool(u and u["role"] == "admin")


def _set_cookie(request: Request, response: Response, uid: int, role: str) -> None:
    days = ADMIN_DAYS if role == "admin" else WORKER_DAYS
    secure = request.url.scheme == "https" or (os.getenv("PUBLIC_URL") or "").startswith("https://")
    response.set_cookie(COOKIE, make_session(uid, days), max_age=days * 86400, httponly=True, samesite="lax",
                        secure=secure, path="/")


# ---------- "undo" without an account ----------
def undo_token(entry_id: int) -> str:
    """Handed back when a note is saved, so whoever saved it (signed in or not) can undo it from that screen."""
    return _sign(f"undo:{entry_id}")[:32]


def check_undo_token(entry_id: int, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token, undo_token(entry_id))


# ---------- slow down PIN guessing ----------
_fails: dict[str, list[float]] = {}
MAX_FAILS, WINDOW = 5, 600


def _limited(key: str) -> bool:
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < WINDOW]
    return len(_fails[key]) >= MAX_FAILS


def _fail(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


# ---------- routes ----------
class Credentials(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    pin: str = Field(min_length=4, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name is empty")
        return v


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    key = "name:" + body.name.lower()
    ip = "ip:" + (request.client.host if request.client else "?")
    if _limited(key) or _limited(ip + ":" + body.name.lower()):
        raise HTTPException(429, "Too many wrong tries. Wait 10 minutes and try again.")
    if body.name.lower() == ADMIN_NAME:
        if hmac.compare_digest(body.pin.encode(), admin_password().encode()):
            _set_cookie(request, response, 0, "admin")
            return {"name": "Admin", "role": "admin"}
    else:
        u = db.user_by_name(body.name)
        if u and check_pin(body.pin, u["pin_hash"]):
            db.update_user(u["id"], last_login=time.strftime("%Y-%m-%d %H:%M:%S"))
            _set_cookie(request, response, u["id"], u["role"])
            return {"name": u["name"], "role": u["role"]}
    _fail(key)
    _fail(ip + ":" + body.name.lower())
    raise HTTPException(401, "That name and PIN don't match.")


@router.post("/signup")
def signup(body: Credentials, request: Request, response: Response):
    if body.name.lower() == ADMIN_NAME:
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
            "admin": bool(u and u["role"] == "admin")}
