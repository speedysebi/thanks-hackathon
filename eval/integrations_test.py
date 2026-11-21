"""
Checks the phone app + calendar/spreadsheet links. Runs against a TEMPORARY database and token.
  python -m eval.integrations_test
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from app import db

tmp = Path(tempfile.mkdtemp())
db.DB_PATH = tmp / "integrations.db"  # never touch the real data
os.environ.pop("FEED_TOKEN", None)
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["ADMIN_TOTP_SECRET"] = "JBSWY3DPEHPK3PXP"  # admins need two-step sign-in; a fixed test authenticator

from fastapi.testclient import TestClient  # noqa: E402

from app import integrations  # noqa: E402
from app.main import app  # noqa: E402

integrations.TOKEN_FILE = tmp / "feed_token.txt"
sys.stdout.reconfigure(encoding="utf-8")
c = TestClient(app)
assert c.post("/api/auth/login", json={"name": "admin", "pin": "test-admin"}).json() == {"need_code": True}  # admin side
from app import mfa  # noqa: E402
assert c.post("/api/auth/code", json={"code": mfa.code_now("JBSWY3DPEHPK3PXP")}).status_code == 200
anon = TestClient(app)  # a regular person, not signed in
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")


# --- due dates ---
mon = date(2026, 11, 16)  # a Monday
def due(follow_up="", raw=""):
    return integrations.due_date({"created_at": f"{mon} 10:00:00", "activity_date": str(mon), "follow_up": follow_up, "raw_text": raw})
for text, want in [("call her back friday", mon + timedelta(days=4)), ("check on him tomorrow", mon + timedelta(days=1)),
                   ("bring groceries monday", mon + timedelta(days=7)), ("follow up next week", mon + timedelta(days=7)),
                   ("call back in 3 days", mon + timedelta(days=3)), ("call her", mon + timedelta(days=1)),
                   ("sat with the family, call back", mon + timedelta(days=1))]:
    check(f"due · {text!r}", due(text) == want, f"got {due(text)} want {want}")
check("due · follow-up line wins over the note", due("call back tuesday", "on friday we fed 20 kids") == mon + timedelta(days=1))

# --- data ---
e1 = c.post("/api/log", json={"text": "connected a mom to 211 for rent, need to call her back friday", "worker": "Dee"}).json()["entry"]
e2 = c.post("/api/log", json={"text": "shooting on center st, stayed w the family, check on them tomorrow", "worker": "Vaun"}).json()["entry"]
c.patch(f"/api/entries/{e1['id']}", json={"outcome": "needs_follow_up", "follow_up": "Call her back Friday, about rent; Ms. note, with \\ backslash"})
c.patch(f"/api/entries/{e2['id']}", json={"outcome": "needs_follow_up"})

info = c.get("/api/integrations").json()
tok = info["calendar_url"].rsplit("/", 1)[1][:-4]
check("integrations · token is long", len(tok) >= 20)
check("integrations · localhost detected", c.get("/api/integrations", headers={"host": "127.0.0.1:8000"}).json()["local_only"] is True)

# --- calendar feed ---
r = c.get(f"/calendar/{tok}.ics")
body = r.text
check("ics · 200 + text/calendar", r.status_code == 200 and r.headers["content-type"].startswith("text/calendar"))
check("ics · CRLF line endings", "\r\n" in body and "\n" not in body.replace("\r\n", ""))
check("ics · lines folded to 75 bytes", all(len(line.encode()) <= 75 for line in body.split("\r\n")))
check("ics · 2 events", body.count("BEGIN:VEVENT") == 2)
check("ics · special chars escaped", "\\;" in body and "\\," in body and "\\\\" in body)
unfolded = body.replace("\r\n ", "")
check("ics · sensitive case has no details", "sensitive" in unfolded and "center st" not in unfolded.lower() and "shooting" not in unfolded.lower())
check("ics · per-worker feed", c.get(f"/calendar/{tok}.ics?worker=Dee").text.count("BEGIN:VEVENT") == 1)
check("ics · wrong token -> 404", c.get("/calendar/" + "x" * 32 + ".ics").status_code == 404)
check("ics · single entry download", c.get(f"/api/entries/{e1['id']}.ics").text.count("BEGIN:VEVENT") == 1)

fu = c.get("/api/followups").json()
check("followups · links for Google / Outlook / Apple", all(set(f["links"]) == {"google", "outlook_work", "outlook_personal", "ics"} for f in fu) and len(fu) == 2)
check("followups · Google link is a template", fu[0]["links"]["google"].startswith("https://calendar.google.com/calendar/render?action=TEMPLATE"))

# --- spreadsheet feed ---
r = c.get(f"/feeds/{tok}.csv")
check("csv · 200 with rows", r.status_code == 200 and r.text.count("\n") >= 3)
check("csv · wrong token -> 404", c.get("/feeds/nope.csv").status_code == 404)

# --- new links revoke old ones ---
check("new-link · needs header", c.post("/api/integrations/new-link").status_code == 400)
c.post("/api/integrations/new-link", headers={"X-ImpactText": "1"})
check("new-link · old calendar link stops working", c.get(f"/calendar/{tok}.ics").status_code == 404)
tok2 = c.get("/api/integrations").json()["calendar_url"].rsplit("/", 1)[1][:-4]
check("new-link · new one works", tok2 != tok and c.get(f"/calendar/{tok2}.ics").status_code == 200)

# --- not signed in: feeds stay reachable by token, the admin side is locked ---
check("signed out · calendar feed open with token", anon.get(f"/calendar/{tok2}.ics").status_code == 200)
check("signed out · csv feed open with token", anon.get(f"/feeds/{tok2}.csv").status_code == 200)
check("signed out · manifest + sw + icons open", all(anon.get(p).status_code == 200 for p in ("/manifest.webmanifest", "/sw.js", "/static/icons/icon-192.png")))
check("signed out · /api/followups locked", anon.get("/api/followups").status_code == 401)
check("signed out · /api/integrations (holds the token) locked", anon.get("/api/integrations").status_code == 401)
check("signed out · /api/entries/<id>.ics locked", anon.get(f"/api/entries/{e1['id']}.ics").status_code == 401)
r = anon.get("/connect", headers={"accept": "text/html"}, follow_redirects=False)
check("signed out · /connect sends you to sign in", r.status_code == 303 and r.headers["location"].startswith("/login"))
check("signed out · /static/../api/entries can't sneak past", anon.get("/static/../api/entries").status_code in (401, 404))
check("signed out · /calendar/../api/entries can't sneak past", anon.get("/calendar/../api/entries").status_code in (401, 404))

# --- phone app ---
m = c.get("/manifest.webmanifest")
check("manifest · served as manifest+json", m.headers["content-type"].startswith("application/manifest+json") and m.json()["share_target"]["action"] == "/")
check("sw · served from root", c.get("/sw.js").headers["content-type"].startswith("text/javascript"))
for page in ("/", "/dashboard", "/report", "/phone", "/console", "/connect", "/login", "/me", "/team"):
    html = c.get(page).text
    check(f"page {page} · manifest + install icon + shared nav", 'rel="manifest"' in html and "apple-touch-icon" in html and "/static/nav.js" in html)

# --- notes typed offline keep their day ---
two_days_ago = (date.today() - timedelta(days=2)).isoformat()
e = c.post("/api/log", json={"text": "fed 9 kids at the rec yesterday", "worker": "Offline", "logged_on": two_days_ago}).json()["entry"]
check("offline · 'yesterday' counts from the day it was typed", e["activity_date"] == (date.today() - timedelta(days=3)).isoformat(), e["activity_date"])
e = c.post("/api/log", json={"text": "fed 4 kids at the rec", "worker": "Offline", "logged_on": "2020-01-01"}).json()["entry"]
check("offline · very old logged_on ignored", e["activity_date"] == date.today().isoformat(), e["activity_date"])

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
