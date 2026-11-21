"""
Two-step sign-in: a 6-digit code from an authenticator app (Google Authenticator, Microsoft Authenticator,
Authy, 1Password, iPhone Passwords...). Standard TOTP (RFC 6238: SHA-1, 30 seconds, 6 digits), no SMS, no
outside service, nothing to pay for.

  Admins       REQUIRED. An admin who signs in without it can only reach the setup page until it's on.
  Everyone     Optional, on /security. Once on, signing in asks for the code too.

Each code works once (replays are refused), and setup hands out 10 one-time backup codes for a lost phone.
Lost phone and no backup codes: an admin can reset it on /team; for the built-in admin run
`python -m app.mfa reset admin` on the server.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import time
from urllib.parse import quote

from app import db

STEP, DIGITS, ISSUER = 30, 6, "ImpactText"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS).zfill(DIGITS)


def code_now(secret: str, offset: int = 0) -> str:
    """The current code (tests and the reset tool use this)."""
    return _code(secret, int(time.time()) // STEP + offset)


def matching_step(secret: str, code: str) -> int | None:
    """The 30-second step this code belongs to, allowing one step of clock drift either way."""
    code = "".join(code.split())
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = int(time.time()) // STEP
    for step in (now, now - 1, now + 1):
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}", safe=":")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(text: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:  # the page falls back to "type this key"
        return None
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return img.to_string(encoding="unicode")


# ---------- one-time backup codes ----------
def _h(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def new_recovery_codes(n: int = 10) -> tuple[list[str], str]:
    """(codes to show once, JSON of their hashes to store). 40 random bits each, so a plain hash is enough."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(3)}" for _ in range(n)]
    return codes, json.dumps([_h(c) for c in codes])


# ---------- per-person state ----------
def env_admin_secret() -> str | None:
    """ADMIN_TOTP_SECRET in .env pre-sets the built-in admin's authenticator (handy for a shared admin phone and tests)."""
    s = (os.getenv("ADMIN_TOTP_SECRET") or "").strip().replace(" ", "").upper()
    return s or None


def secret_for(uid: int) -> str | None:
    if uid == 0 and env_admin_secret():
        return env_admin_secret()
    row = db.get_mfa(uid)
    return row["secret"] if row and row["secret"] else None


def enabled(uid: int) -> bool:
    return secret_for(uid) is not None


def verify(uid: int, code: str) -> bool:
    """A fresh authenticator code, or an unused backup code (which is then used up)."""
    secret = secret_for(uid)
    if not secret or not code:
        return False
    step = matching_step(secret, code)
    if step is not None:
        db.set_mfa(uid)  # make sure a row exists to hold last_step (env-secret admin)
        return db.use_mfa_step(uid, step)
    row = db.get_mfa(uid)
    hashes = json.loads(row["recovery"]) if row else []
    h = _h(code)
    for stored in hashes:
        if hmac.compare_digest(stored, h):
            hashes.remove(stored)
            db.set_mfa(uid, recovery=json.dumps(hashes))
            return True
    return False


def backup_codes_left(uid: int) -> int:
    row = db.get_mfa(uid)
    return len(json.loads(row["recovery"])) if row else 0


if __name__ == "__main__":  # python -m app.mfa reset admin|<name>
    if len(sys.argv) != 3 or sys.argv[1] != "reset":
        sys.exit("usage: python -m app.mfa reset admin|<account name>")
    who = sys.argv[2]
    uid = 0 if who.lower() == "admin" else (db.user_by_name(who) or {}).get("id")
    if uid is None:
        sys.exit(f"No account named {who!r}.")
    db.delete_mfa(uid)
    note = " (ADMIN_TOTP_SECRET is set in .env: remove it too)" if uid == 0 and env_admin_secret() else ""
    print(f"Two-step sign-in turned off for {who}. They'll be asked to set it up again on next sign-in.{note}")
