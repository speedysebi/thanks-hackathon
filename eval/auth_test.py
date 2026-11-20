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

from app import auth  # noqa: E402
from app.main import PUBLIC_PAGES, PUBLIC_PREFIXES, app  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + str(detail) if detail else ''}")


def client():
    return TestClient(app, raise_server_exceptions=False)


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
check("me · signed in as worker", me == {"signed_in": True, "name": "Dee", "role": "worker", "admin": False}, me)

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
auth._fails.clear()

# --- cookie tampering ---
t = client()
t.cookies.set(auth.COOKIE, auth.make_session(0, 1)[:-4] + "beef")
check("cookie · forged signature ignored", t.get("/api/entries").status_code == 401)
t.cookies.set(auth.COOKIE, "MHw5OTk5OTk5OTk5.abc")
check("cookie · garbage ignored", t.get("/api/auth/me").json()["signed_in"] is False)
expired = auth.make_session(0, -1)
t.cookies.set(auth.COOKIE, expired)
check("cookie · expired ignored", t.get("/api/entries").status_code == 401)

# --- admin ---
check("admin · wrong password", boss.post("/api/auth/login", json={"name": "admin", "pin": "nope"}).status_code == 401)
check("admin · sign in", boss.post("/api/auth/login", json={"name": "admin", "pin": "admin-pw-123"}).json()["role"] == "admin")
for path in ("/dashboard", "/report", "/console", "/connect", "/team"):
    check(f"admin · {path} opens", boss.get(path, headers=HTML).status_code == 200)
for path in ("/api/entries", "/api/summary", "/api/export.csv", "/api/team", "/api/console/status", "/api/integrations"):
    check(f"admin · {path}", boss.get(path).status_code == 200)
check("admin · can edit anyone's note", boss.patch(f"/api/entries/{e['id']}", json={"people_served": 2}).json()["people_served"] == 2)
team = boss.get("/api/team").json()["users"]
check("admin · team lists accounts, no PIN hashes", {u["name"] for u in team} == {"Dee", "Vaun"} and all("pin_hash" not in u for u in team))
vid = db.user_by_name("Vaun")["id"]
boss.patch(f"/api/team/{vid}", json={"role": "admin"})
check("admin · promote: takes effect at once", vaun.get("/api/entries").status_code == 200)
boss.patch(f"/api/team/{vid}", json={"role": "worker"})
check("admin · demote: takes effect at once", vaun.get("/api/entries").status_code == 403)
boss.patch(f"/api/team/{vid}", json={"pin": "135790"})
check("admin · reset PIN", client().post("/api/auth/login", json={"name": "Vaun", "pin": "135790"}).status_code == 200)
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

# --- every route is public on purpose or admin-only ---
public_ok = {"/api/log", "/api/health", "/sms", "/", "/phone", "/login", "/me", "/manifest.webmanifest", "/sw.js"}


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
