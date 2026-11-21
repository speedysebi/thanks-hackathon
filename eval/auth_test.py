"""
Admin side vs. regular-person side. Runs against a TEMPORARY database.
  python -m eval.auth_test
Regular people: log with no account; optional account (name + PIN) to see and fix their own notes.
Admins: everything else. Checks every route is either public on purpose or admin-only.
"""
import os
import sys
import tempfile
from pathlib import Path

from app import db

tmp = Path(tempfile.mkdtemp())
db.DB_PATH = tmp / "auth.db"  # never touch the real data
os.environ["ADMIN_PASSWORD"] = "admin-pw-123"

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, mfa  # noqa: E402
from app.main import PUBLIC_PAGES, PUBLIC_PREFIXES, app  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + str(detail) if detail else ''}")


def client(ip="testclient"):
    return TestClient(app, raise_server_exceptions=False, client=(ip, 50000))


def code(uid, offset=0):
    """Current authenticator code. Clears 'last used' first: these tests sign in more often than once per 30 s."""
    db.set_mfa(uid, last_step=0)
    return mfa.code_now(mfa.secret_for(uid), offset)


def enroll(c, uid):
    """Turn on two-step for whoever `c` is signed in as, the way the /security page does."""
    s = c.post("/api/auth/2fa/start").json()
    r = c.post("/api/auth/2fa/enable", json={"code": mfa.code_now(s["secret"].replace(" ", ""))})
    return s["secret"].replace(" ", ""), r


def sign_in(c, name, pin, uid):
    r = c.post("/api/auth/login", json={"name": name, "pin": pin}).json()
    if r.get("need_code"):
        r = c.post("/api/auth/code", json={"code": code(uid)}).json()
    return r


anon, dee, vaun, boss = client(), client(), client(), client()
HTML = {"accept": "text/html"}

# --- regular person, no account ---
r = anon.post("/api/log", json={"text": "fed 12 kids at sherman park", "worker": "Walk-in"})
check("no account · can log a note", r.status_code == 200, r.status_code)
walkin = r.json()
check("no account · gets an undo token", len(walkin.get("undo_token", "")) == 32)
for path in ("/", "/phone", "/login", "/me"):
    check(f"no account · page {path} opens", anon.get(path, headers=HTML).status_code == 200)
check("no account · SMS still works", anon.post("/sms", data={"From": "+14145550123", "Body": "fed 3 people"}).status_code == 200)
for path in ("/dashboard", "/report", "/console", "/connect", "/team"):
    r = anon.get(path, headers=HTML, follow_redirects=False)
    check(f"no account · {path} -> sign in", r.status_code == 303 and r.headers["location"] == f"/login?next={path}", r.status_code)
for path in ("/api/entries", "/api/summary", "/api/report", "/api/export.csv", "/api/stats", "/api/team",
             "/api/console/status", "/api/integrations", "/api/followups", "/docs", "/openapi.json"):
    check(f"no account · {path} locked", anon.get(path).status_code == 401)
check("no account · health hides AI errors", "last_ai_error" not in anon.get("/api/health").json())
check("no account · /api/my/entries says sign in", anon.get("/api/my/entries").status_code == 401)

# --- undo without an account ---
other = anon.post("/api/log", json={"text": "cleaned up the lot on center st", "worker": "Walk-in"}).json()
check("undo · wrong token refused", anon.delete(f"/api/entries/{other['entry']['id']}", headers={"X-Undo-Token": walkin["undo_token"]}).status_code == 403)
check("undo · no token refused", anon.delete(f"/api/entries/{other['entry']['id']}").status_code == 403)
check("undo · can't edit with no token", anon.patch(f"/api/entries/{other['entry']['id']}", json={"people_served": 999}).status_code == 403)
check("undo · own token works", anon.delete(f"/api/entries/{other['entry']['id']}", headers={"X-Undo-Token": other["undo_token"]}).status_code == 200)

# --- optional accounts ---
check("signup · works", dee.post("/api/auth/signup", json={"name": "Dee", "pin": "4141"}).status_code == 200)
check("signup · name taken (any case)", client().post("/api/auth/signup", json={"name": "dee", "pin": "9999"}).status_code == 409)
check("signup · 'admin' not allowed", client().post("/api/auth/signup", json={"name": "Admin", "pin": "1234"}).status_code == 400)
check("signup · short PIN refused", client().post("/api/auth/signup", json={"name": "Shorty", "pin": "12"}).status_code == 422)
check("signup · weak 4-letter password refused", client().post("/api/auth/signup", json={"name": "Letters", "pin": "abcd"}).status_code == 422)
check("signup · PIN not stored", "4141" not in db.user_by_name("Dee")["pin_hash"])
vaun.post("/api/auth/signup", json={"name": "Vaun", "pin": "246810"})
me = dee.get("/api/auth/me").json()
check("me · signed in as worker", me == {"signed_in": True, "name": "Dee", "role": "worker", "admin": False, "mfa": False, "needs_2fa": False}, me)

e = dee.post("/api/log", json={"text": "sent a mom to 414 life, call her back friday", "worker": "someone else"}).json()["entry"]
check("account · note credited to the account, not the typed name", e["worker"] == "Dee" and e["user_id"] is not None)
mine = dee.get("/api/my/entries").json()
check("account · My notes shows it", [x["id"] for x in mine["entries"]] == [e["id"]] and mine["totals"]["follow_ups"] == 1, mine["totals"])
check("account · others' notes not in My notes", vaun.get("/api/my/entries").json()["entries"] == [])
check("account · can mark own follow-up done", dee.patch(f"/api/entries/{e['id']}", json={"outcome": "resolved"}).json()["outcome"] == "resolved")
check("account · can't change someone else's note", vaun.patch(f"/api/entries/{e['id']}", json={"outcome": "unknown"}).status_code == 403)
check("account · can't delete someone else's note", vaun.delete(f"/api/entries/{e['id']}").status_code == 403)
check("account · can't delete a walk-in's note", dee.delete(f"/api/entries/{walkin['entry']['id']}").status_code == 403)
check("account · worker can't open the dashboard", dee.get("/dashboard", headers=HTML, follow_redirects=False).status_code == 303)
check("account · worker can't read everyone's notes", dee.get("/api/entries").status_code == 403)
check("account · worker can't make themselves admin", dee.patch(f"/api/team/{db.user_by_name('Dee')['id']}", json={"role": "admin"}).status_code == 403)

# --- sign in / out ---
x = client()
check("login · wrong PIN", x.post("/api/auth/login", json={"name": "Dee", "pin": "0000"}).status_code == 401)
check("login · unknown name, same message", x.post("/api/auth/login", json={"name": "Nobody", "pin": "0000"}).json()["detail"] == "That name and PIN don't match.")
check("login · name is case-insensitive", x.post("/api/auth/login", json={"name": "DEE", "pin": "4141"}).status_code == 200)
x.post("/api/auth/logout")
check("logout · signed out", x.get("/api/auth/me").json()["signed_in"] is False)
g = client()
codes = [g.post("/api/auth/login", json={"name": "Vaun", "pin": f"{i:04d}"}).status_code for i in range(6)]
check("login · 6th wrong PIN in a row is blocked", codes[-1] == 429, codes)
check("login · blocked even with the right PIN for a while", g.post("/api/auth/login", json={"name": "Vaun", "pin": "246810"}).status_code == 429)
check("login · a stranger's bad tries don't lock Vaun out on her own phone",
      client("10.0.0.7").post("/api/auth/login", json={"name": "Vaun", "pin": "246810"}).status_code == 200)
bad = [client(f"10.1.0.{i}").post("/api/auth/login", json={"name": "admin", "pin": "x"}).status_code for i in range(6)]
check("login · 6 bad admin tries from elsewhere don't lock the real admin out",
      client("10.9.9.9").post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"}).status_code == 200, bad)
spread = [client(f"10.2.{i // 250}.{i % 250}").post("/api/auth/login", json={"name": "Vaun", "pin": f"{i:04d}"}).status_code for i in range(52)]
check("login · guessing spread over many addresses still stalls (50 per name)", spread[-1] == 429, spread[-3:])
auth._fails.clear()

# --- names that look like 'admin' ---
for n in ("Admin", " admin ", "ａｄｍｉｎ", "admin​", "аdmin", "a.d.m.i.n", "Administrator", "admin2"):
    check(f"signup · {n!r} refused", client().post("/api/auth/signup", json={"name": n, "pin": "1234"}).status_code == 400)
check("signup · normal name with 'admin' inside is fine", client().post("/api/auth/signup", json={"name": "Badminton Ray", "pin": "1234"}).status_code == 200)
r = client().post("/api/auth/signup", json={"name": "Jo​ey", "pin": "1234"})
check("signup · invisible characters removed from names", r.status_code == 200 and r.json()["name"] == "Joey", r.json())

# --- someone retyping another person's note ---
r = anon.post("/api/log", json={"text": "sent a mom to 414 life, call her back friday", "worker": "Dee"}).json()
check("duplicate · retyping Dee's note: no undo token, no note text", r["undo_token"] is None and r["entry"]["id"] is None, r)
check("duplicate · resend from the same phone still gets its own note back", True)  # covered under 'resend' below

# --- cookie tampering ---
t = client()
t.cookies.set(auth.COOKIE, auth.make_session(0, 1)[:-4] + "beef")
check("cookie · forged signature ignored", t.get("/api/entries").status_code == 401)
t.cookies.set(auth.COOKIE, "MHw5OTk5OTk5OTk5.abc")
check("cookie · garbage ignored", t.get("/api/auth/me").json()["signed_in"] is False)
expired = auth.make_session(0, -1)
t.cookies.set(auth.COOKIE, expired)
check("cookie · expired ignored", t.get("/api/entries").status_code == 401)
check("cookie · non-ASCII signature: 401, not a crash", client().get("/api/entries", headers={"cookie": "it_session=MHwx.\xe9".encode("latin-1")}).status_code == 401)
check("undo · non-ASCII token: 403, not a crash", anon.delete(f"/api/entries/{walkin['entry']['id']}", headers={"X-Undo-Token": ("\xe9" * 32).encode("latin-1")}).status_code == 403)
t.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=False))
check("cookie · admin cookie that skipped two-step: no admin data", t.get("/api/entries").status_code == 403)

# --- admin ---
check("admin · wrong password", boss.post("/api/auth/login", json={"name": "admin", "pin": "nope"}).status_code == 401)
r = boss.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"}).json()
check("admin · password right, but two-step not set up yet", r["role"] == "admin" and r["setup_2fa"] is True, r)
check("admin · no admin data before two-step", boss.get("/api/entries").status_code == 403)
loc = boss.get("/dashboard", headers=HTML, follow_redirects=False).headers.get("location")
check("admin · admin page sends you to set it up", loc == "/security?next=/dashboard", loc)
check("admin · setup page opens", boss.get("/security", headers=HTML).status_code == 200)
check("admin · me says needs_2fa", boss.get("/api/auth/me").json()["needs_2fa"] is True)
s = boss.post("/api/auth/2fa/start").json()
check("2fa · setup gives a QR code and a typeable key", s["qr_svg"].startswith("<svg") and len(s["secret"].replace(" ", "")) == 32
      and s["uri"].startswith("otpauth://totp/ImpactText:Admin?secret="), s["uri"])
check("2fa · wrong code doesn't turn it on", boss.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400)
r = boss.post("/api/auth/2fa/enable", json={"code": mfa.code_now(s["secret"].replace(" ", ""))})
admin_backup = r.json().get("backup_codes", [])
check("2fa · right code turns it on, 10 backup codes", r.status_code == 200 and len(admin_backup) == 10, r.json())
check("2fa · stored hashed (backup codes not in the database)", all(c not in str(db.get_mfa(0)) for c in admin_backup))
check("admin · same browser is admin right away", boss.get("/api/entries").status_code == 200)
check("2fa · admins can't turn it off", boss.post("/api/auth/2fa/disable", json={"code": code(0)}).status_code == 403)
x = client()
r = x.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"})
check("admin · next sign-in: password alone gets no session", r.json() == {"need_code": True} and auth.COOKIE not in x.cookies, r.json())
check("admin · ...and nothing admin", x.get("/api/entries").status_code == 401)
check("2fa · wrong code refused", x.post("/api/auth/code", json={"code": "123456"}).status_code == 401)
good = code(0)
check("2fa · right code signs in", x.post("/api/auth/code", json={"code": good}).json()["role"] == "admin" and x.get("/api/entries").status_code == 200)
y = client()
y.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"})
check("2fa · same code can't be used twice", y.post("/api/auth/code", json={"code": good}).status_code == 401)
check("2fa · backup code works", y.post("/api/auth/code", json={"code": admin_backup[0]}).status_code == 200 and y.get("/api/entries").status_code == 200)
z = client()
z.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"})
check("2fa · backup code only works once", z.post("/api/auth/code", json={"code": admin_backup[0]}).status_code == 401)
check("2fa · code step without the password step refused", client().post("/api/auth/code", json={"code": code(0)}).status_code == 401)
w = client()
w.cookies.set(auth.PENDING, auth.make_session(0, 1))  # a session cookie isn't a "password OK" ticket
check("2fa · session cookie can't stand in for the password step", w.post("/api/auth/code", json={"code": code(0)}).status_code == 401)
lim = client()
lim.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"})
tries = [lim.post("/api/auth/code", json={"code": f"{i:06d}"}).status_code for i in range(6)]
check("2fa · code guessing locked after 5", tries[-1] == 429, tries)
auth._fails.clear()
for path in ("/dashboard", "/report", "/console", "/connect", "/team"):
    check(f"admin · {path} opens", boss.get(path, headers=HTML).status_code == 200)
for path in ("/api/entries", "/api/summary", "/api/export.csv", "/api/team", "/api/console/status", "/api/integrations"):
    check(f"admin · {path}", boss.get(path).status_code == 200)
check("admin · can edit anyone's note", boss.patch(f"/api/entries/{e['id']}", json={"people_served": 2}).json()["people_served"] == 2)
team = boss.get("/api/team").json()["users"]
check("admin · team lists accounts, no PIN hashes", {"Dee", "Vaun"} <= {u["name"] for u in team} and all("pin_hash" not in u for u in team))
vid = db.user_by_name("Vaun")["id"]
vaun = client()
sign_in(vaun, "Vaun", "246810", vid)
boss.patch(f"/api/team/{vid}", json={"role": "admin"})
check("admin · promoted, no two-step yet: still no admin data", vaun.get("/api/entries").status_code == 403)
check("admin · promoted: told to set up two-step", vaun.get("/api/auth/me").json()["needs_2fa"] is True)
_, r = enroll(vaun, vid)
check("admin · promoted + two-step on: admin at once", r.status_code == 200 and vaun.get("/api/entries").status_code == 200)
check("team · shows who has two-step", {u["name"]: u["mfa"] for u in boss.get("/api/team").json()["users"]}["Vaun"] == 1)
boss.patch(f"/api/team/{vid}", json={"role": "worker"})
check("admin · demote: takes effect at once", vaun.get("/api/entries").status_code == 403)
boss.patch(f"/api/team/{vid}", json={"pin": "135790"})
check("admin · reset PIN (two-step still asked)", client().post("/api/auth/login", json={"name": "Vaun", "pin": "135790"}).json() == {"need_code": True})
check("team · worker can't reset someone's two-step", dee.post(f"/api/team/{vid}/reset-2fa").status_code == 403)
check("team · admin resets Vaun's two-step (lost phone)", boss.post(f"/api/team/{vid}/reset-2fa").status_code == 200 and not mfa.enabled(vid))
check("team · after reset, PIN alone signs in again", client().post("/api/auth/login", json={"name": "Vaun", "pin": "135790"}).json()["role"] == "worker")

# --- optional two-step for a regular person ---
dee2 = client()
sign_in(dee2, "Dee", "4141", db.user_by_name("Dee")["id"])
did = db.user_by_name("Dee")["id"]
check("2fa · optional: off by default for workers", dee2.get("/api/auth/2fa").json() == {"enabled": False, "required": False, "backup_codes_left": 0, "set_in_env": False, "builtin_admin": False})
check("2fa · worker not pushed to set it up", dee2.get("/api/auth/me").json()["needs_2fa"] is False)
old_dee = client()
sign_in(old_dee, "Dee", "4141", did)
_, r = enroll(dee2, did)
check("2fa · worker can turn it on", r.status_code == 200 and dee2.get("/api/my/entries").status_code == 200)
check("2fa · turning it on signs out her other phones", old_dee.get("/api/my/entries").status_code == 401)
check("2fa · turning it on doesn't make her an admin", dee2.get("/api/entries").status_code == 403)
check("2fa · worker sign-in now asks for the code", client().post("/api/auth/login", json={"name": "Dee", "pin": "4141"}).json() == {"need_code": True})
check("2fa · turn off needs a real code", dee2.post("/api/auth/2fa/disable", json={"code": "000000"}).status_code == 400)
check("2fa · worker can turn it off", dee2.post("/api/auth/2fa/disable", json={"code": code(did)}).status_code == 200 and not mfa.enabled(did))
check("2fa · signed out people can't start setup", anon.post("/api/auth/2fa/start").status_code == 401)
dee = dee2
r = boss.post("/api/team", json={"name": "Keisha", "pin": "2468", "role": "worker"})
check("admin · add a person", r.status_code == 200 and r.json()["pin_hash"] is None)
check("admin · add duplicate refused", boss.post("/api/team", json={"name": "keisha", "pin": "2468"}).status_code == 409)
did = db.user_by_name("Dee")["id"]
boss.delete(f"/api/team/{did}")
check("admin · remove account: their session ends", dee.get("/api/my/entries").status_code == 401)
check("admin · remove account: their notes stay", db.get(e["id"]) is not None and db.get(e["id"])["user_id"] is None)
check("admin · health shows AI errors", "last_ai_error" in boss.get("/api/health").json())

# --- one note, sent twice by the phone (lost reply, offline queue) ---
cid = "phone-abc12345"
a = anon.post("/api/log", json={"text": "fed 30 people at the church", "worker": "Q", "client_id": cid}).json()["entry"]
b = anon.post("/api/log", json={"text": "fed 30 people at the church", "worker": "Q", "client_id": cid}).json()["entry"]
check("resend · same client_id counts once", a["id"] == b["id"] and b.get("duplicate") is True)
check("resend · bad client_id rejected", anon.post("/api/log", json={"text": "fed 3", "client_id": "x'; drop"}).status_code == 422)

# --- texting: once online, nobody can pretend to be a real worker's phone ---
os.environ["TWILIO_AUTH_TOKEN"] = "test-token"
real = anon.post("/sms", data={"From": "+14145551234", "Body": "UNDO"})
check("sms · online: unsigned text from a real number refused", real.status_code == 403, real.status_code)
forged = anon.post("/sms", data={"From": "+14145551234", "Body": "ME"}, headers={"X-Twilio-Signature": "bm9wZQ=="})
check("sms · online: wrong Twilio signature refused", forged.status_code == 403)
check("sms · online: the /phone simulator's fictional 555-01xx numbers still work",
      anon.post("/sms", data={"From": "+14145550142", "Body": "HELP"}).status_code == 200)
os.environ.pop("TWILIO_AUTH_TOKEN")
os.environ["PUBLIC_URL"] = "https://impact.example.org"
check("sms · https PUBLIC_URL but no Twilio token: real numbers still refused",
      anon.post("/sms", data={"From": "+14145551234", "Body": "ME"}).status_code == 403)
os.environ.pop("PUBLIC_URL")

# --- every route is public on purpose or admin-only ---
public_ok = {"/api/log", "/api/health", "/sms", "/", "/phone", "/login", "/me", "/security", "/manifest.webmanifest", "/sw.js"}


def all_routes():
    """Every (method, path), including routes inside included routers (FastAPI 0.16x keeps those nested)."""
    stack, seen = list(app.routes), set()
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack += r.original_router.routes
        elif getattr(r, "methods", None):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                seen.add((m, r.path))
    return sorted(seen)


import re  # noqa: E402

routes, leaks = all_routes(), []
for m, path in routes:
    if path.startswith("/static") or path in public_ok or path.startswith(("/api/auth/", "/api/my/")):
        continue
    url = re.sub(r"\{[^}]+\}", "1", path).replace("/labels/1", "/labels/test")
    status = anon.request(m, url, headers=HTML, follow_redirects=False).status_code
    by_token_or_owner = path.startswith(("/calendar/", "/feeds/")) or path == "/api/entries/{entry_id}"
    if status not in ((403, 404, 422) if by_token_or_owner else (401, 303)):  # 422 = empty edit, rejected before the owner check
        leaks.append(f"{m} {path} -> {status}")
check(f"routes · all {len(routes)} routes: nothing else is open to signed-out people", not leaks and len(routes) > 40, leaks or len(routes))
check("routes · public list is what we expect", PUBLIC_PAGES == public_ok, PUBLIC_PAGES ^ public_ok)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
