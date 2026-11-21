"""
Smoke test against a RUNNING server (CI runs it inside the Docker image after it starts).
  python -m eval.smoke URL ADMIN_PASSWORD ADMIN_TOTP_SECRET [--expect-existing]
--expect-existing: run again after a restart; checks the note saved the first time is still there (the disk works).
"""
import sys

import httpx

from app import mfa

url, password, secret = sys.argv[1:4]
again = "--expect-existing" in sys.argv
CLIENT_ID = "ci-smoke-note-0001"
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + str(detail) if detail not in ('', None) else ''}")


anon = httpx.Client(base_url=url, timeout=20)
check("health", anon.get("/api/health").json().get("ok") is True)
r = anon.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
check("signed out: dashboard sends you to sign in", r.status_code == 303 and r.headers["location"].startswith("/login"), r.status_code)
check("signed out: no admin data", anon.get("/api/entries").status_code == 401)
check("pages and static files are in the image", anon.get("/security").status_code == 200 and anon.get("/static/nav.js").status_code == 200)

# behind the host's proxy, sign-in limits must use the visitor's address (X-Forwarded-For), not the proxy's
for _ in range(6):
    anon.post("/api/auth/login", json={"name": "admin", "pin": "wrong-guess"}, headers={"X-Forwarded-For": "203.0.113.9"})
locked = anon.post("/api/auth/login", json={"name": "admin", "pin": password}, headers={"X-Forwarded-For": "203.0.113.9"})
check("proxy: the guessing address is locked out", locked.status_code == 429, locked.status_code)

admin = httpx.Client(base_url=url, timeout=20, headers={"X-Forwarded-For": "198.51.100.4"})
r = admin.post("/api/auth/login", json={"name": "admin", "pin": password})
check("proxy: the real admin elsewhere isn't locked out; password asks for the code", r.json() == {"need_code": True}, r.text[:120])
ok = False
for offset in (0, 1):  # a code works once; after a quick restart the current one may already be used
    if admin.post("/api/auth/code", json={"code": mfa.code_now(secret, offset)}).status_code == 200:
        ok = True
        break
check("two-step code signs the admin in", ok)
check("admin sees the data", admin.get("/api/entries").status_code == 200)

r = anon.post("/api/log", json={"text": "fed 15 kids at sherman park", "worker": "CI", "client_id": CLIENT_ID})
check("anyone can log a note (database + log files writable on /data)", r.status_code == 200, r.text[:160])
entries = admin.get("/api/entries", params={"limit": 100000}).json()
saved = [e for e in entries if e.get("client_id") == CLIENT_ID]
check("the note is saved exactly once" + (" and survived the restart" if again else ""), len(saved) == 1, len(saved))

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
