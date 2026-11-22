"""
ImpactText: community workers text or say what they did; it becomes a record, a dashboard and a grant report.

RUN:  uvicorn app.main:app --reload     then open http://127.0.0.1:8000
PAGES (everyone: / /phone /login /me /security; everything else is admin-only, see app/auth.py)
  /            log page (phone-first: one box + a mic button)
  /login       sign in or make an account (optional for workers)
  /me          My notes: a signed-in person's own notes, totals and follow-ups
  /team        admins: accounts, roles, PIN resets
  /dashboard   numbers, charts, follow-ups, recent entries, CSV export
  /report      printable impact report for funders (Print -> Save as PDF)
  /phone       SMS simulator for the demo (same code path as a real text)
  /console     testing console: try notes, run evals, review labels, next steps
  /connect     phones (install, Siri, Android share) + Google/Microsoft/Apple calendars and spreadsheets
API
  POST /api/log            {"text", "worker"} -> saved entry + confirmation
  POST /sms                Twilio webhook (form: From, Body) -> TwiML reply
  GET  /api/entries        recent entries        PATCH/DELETE /api/entries/{id}
  GET  /api/summary?days=  dashboard numbers     GET /api/report?days=  report markdown
  GET  /api/export.csv     every record, for the org to keep
  GET  /api/stats          latency + AI cost numbers for the pitch
"""
import base64
import hashlib
import hmac
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app import auth, console, db, extract, impact, integrations, llm, metrics, privacy

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="ImpactText")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- who can open what (see app/auth.py) ----------
# Everyone, no sign-in: log a note, the SMS simulator, sign in / make an account, "My notes" (the page itself
# asks you to sign in), and the app shell. /sms is checked by Twilio's signature; /calendar and /feeds by their
# secret token (calendar apps and Google Sheets can't sign in). EVERYTHING ELSE needs an admin.
PUBLIC_PAGES = {"/", "/phone", "/login", "/me", "/security", "/manifest.webmanifest", "/sw.js", "/api/log", "/api/health", "/sms"}
PUBLIC_PREFIXES = ("/static/", "/api/auth/", "/api/my/", "/calendar/", "/feeds/")
OWN_ENTRY = re.compile(r"/api/entries/\d+")  # PATCH/DELETE: allowed for the owner or with the undo token, checked below


def _public(request: Request) -> bool:
    path = request.url.path
    if ".." in path:
        return False
    return (path in PUBLIC_PAGES or path.startswith(PUBLIC_PREFIXES)
            or (OWN_ENTRY.fullmatch(path) is not None and request.method in ("PATCH", "DELETE")))


@app.middleware("http")
async def require_admin(request: Request, call_next):
    if not _public(request) and not auth.is_admin(request):
        wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        back = quote(request.url.path, safe="/")
        if auth.needs_2fa_setup(request):  # right password, but admins also need the authenticator app
            if wants_page:
                return RedirectResponse("/security?next=" + back, status_code=303)
            return JSONResponse({"detail": "Turn on two-step sign-in first (/security). Admins need it."}, status_code=403)
        if wants_page:  # send people to the sign-in page, then back
            return RedirectResponse("/login?next=" + back, status_code=303)
        signed_in = auth.current_user(request) is not None
        return JSONResponse({"detail": "Admins only." if signed_in else "Sign in as an admin."},
                            status_code=403 if signed_in else 401)
    return await call_next(request)


@app.on_event("startup")
def say_admin_password():
    if not auth.admin_password_from_env():
        print(f"\n  Admin sign-in:  name 'admin'  password '{auth.admin_password()}'"
              f"\n  (made for you; set ADMIN_PASSWORD in .env to choose your own)\n", flush=True)


def page(name: str):
    return lambda: FileResponse(STATIC / name)


PAGES = {"/": "index.html", "/phone": "phone.html", "/login": "login.html", "/me": "me.html", "/security": "security.html",  # everyone
         "/dashboard": "dashboard.html", "/report": "report.html", "/console": "console.html",  # admins
         "/connect": "connect.html", "/team": "team.html"}
for route, file in PAGES.items():
    app.add_api_route(route, page(file), methods=["GET"], include_in_schema=False)

app.include_router(auth.router)
app.include_router(console.router)
app.include_router(integrations.router)


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():  # served from / so it can make the whole app work offline
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# ---------- logging ----------
class LogIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    worker: str = Field(default="Volunteer", max_length=60)
    channel: str = "web"
    # set by the phone when a note typed offline is sent later, so the activity keeps the day it happened
    logged_on: date | None = None
    # random id made by the phone for this note; a resend (lost reply, offline queue) with the same id counts once
    client_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("note is empty")
        return v


def log_note(text: str, worker: str, channel: str, phone: str = "", logged_on: date | None = None,
             user_id: int | None = None, client_id: str | None = None) -> dict:
    start = time.perf_counter()
    if client_id:
        same = db.by_client_id(client_id)
        if same:
            return {**same, "duplicate": True}
    text = privacy.scrub(text.strip()[:2000])  # phone numbers, addresses, "Ms. Johnson" never get stored or sent out
    worker = worker.strip() or "Volunteer"
    dup = db.recent_duplicate(worker, text, phone)
    if dup:
        return {**dup, "duplicate": True}
    fields = extract.extract(text)
    late = (date.today() - logged_on).days if logged_on else 0
    if 0 < late <= 14:  # "yesterday" in a note typed 3 days ago, sent today = 4 days ago
        fields["activity_date"] = (date.fromisoformat(fields["activity_date"]) - timedelta(days=late)).isoformat()
    entry = db.insert({**fields, "raw_text": text, "worker": worker, "channel": channel, "phone": phone,
                       "user_id": user_id, "client_id": client_id})
    metrics.record("log", (time.perf_counter() - start) * 1000, ok=True, channel=channel, extractor=fields["extractor"])
    return entry


@app.post("/api/log")
def api_log(body: LogIn, request: Request):
    """Open to everyone. Signed in: the note is credited to your account and shows on My notes."""
    u = auth.current_user(request)
    account = u if u and u["id"] else None  # the built-in admin isn't a person; it logs under the typed name
    entry = log_note(body.text, account["name"] if account else body.worker,
                     body.channel if body.channel in ("web", "voice") else "web",
                     logged_on=body.logged_on, user_id=account["id"] if account else None, client_id=body.client_id)
    if entry.get("duplicate"):
        # Counted once. Only the same phone resending its own note (same random client_id, same account or
        # none) gets the note and its undo back; anyone else retyping it learns nothing and can't remove it.
        same_sender = (body.client_id and entry.get("client_id") == body.client_id
                       and entry.get("user_id") == (account["id"] if account else None))
        if not same_sender:
            return {"entry": {"id": None, "duplicate": True, "summary": "Already saved a moment ago. It counts once."},
                    "confirmation": "Already saved. It counts once.", "undo_token": None}
    return {"entry": entry, "confirmation": extract.confirmation(entry), "undo_token": auth.undo_token(entry["id"])}


@app.get("/api/entries")
def api_entries(days: int = 90, limit: int = 200):
    return db.list_entries(since=(date.today() - timedelta(days=days)).isoformat(), limit=limit)


def _may_change(request: Request, entry: dict, undo: str | None = None) -> bool:
    """Admins: any note. Signed-in people: their own. Anyone: the note they just saved, for 24 hours (undo token)."""
    if auth.is_admin(request):
        return True
    u = auth.current_user(request)
    if u and entry.get("user_id") == u["id"]:
        return True
    fresh = entry["created_at"] >= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400))
    return fresh and auth.check_undo_token(entry["id"], undo)


@app.get("/api/my/entries")
def my_entries(request: Request, days: int = 30):
    u = auth.current_user(request)
    if not u or not u["id"]:
        raise HTTPException(401, "Sign in to see your notes.")
    days = max(1, min(days, 3650))
    mine = db.entries_for_user(u["id"], (date.today() - timedelta(days=days)).isoformat())
    return {"name": u["name"], "days": days, "entries": mine,
            "totals": {"activities": len(mine), "people": sum(e["people_served"] for e in mine),
                       "youth": sum(e["youth_served"] for e in mine), "hours": round(sum(e["hours"] for e in mine), 1),
                       "follow_ups": sum(e["outcome"] == "needs_follow_up" for e in mine)}}


class EntryPatch(BaseModel):
    activity_type: Literal[tuple(extract.ACTIVITY_TYPES)] | None = None
    outcome: Literal[tuple(extract.OUTCOMES)] | None = None
    summary: str | None = Field(default=None, max_length=300)
    people_served: int | None = Field(default=None, ge=0, le=5000)
    youth_served: int | None = Field(default=None, ge=0, le=5000)
    neighborhood: str | None = Field(default=None, max_length=80)
    follow_up: str | None = Field(default=None, max_length=300)
    hours: float | None = Field(default=None, ge=0, le=24)


def _own(request: Request, entry_id: int, undo: str | None = None) -> dict:
    entry = db.get(entry_id)
    if not entry:
        raise HTTPException(404)
    if not _may_change(request, entry, undo):
        raise HTTPException(403, "You can only change your own notes.")
    return entry


@app.patch("/api/entries/{entry_id}")
def api_update(entry_id: int, body: EntryPatch, request: Request):
    _own(request, entry_id, request.headers.get("X-Undo-Token"))
    try:
        entry = db.update(entry_id, body.model_dump(exclude_none=True))
    except db.InvalidEntryCounts as exc:
        raise HTTPException(422, str(exc)) from exc
    if not entry:
        raise HTTPException(404)
    return entry


@app.delete("/api/entries/{entry_id}")
def api_delete(entry_id: int, request: Request):
    _own(request, entry_id, request.headers.get("X-Undo-Token"))
    db.delete(entry_id)
    return {"deleted": entry_id}


# ---------- SMS (Twilio) ----------
# "NAME Maria" / "NAME Maria Lopez". A note that just starts with the word ("Name tags for 30 kids") is a note.
NAME_CMD = re.compile(r"^name\s+([^\d\s][^\d]{0,40}?)\s*$", re.IGNORECASE)

HELP = (
    "Text what you did, like: fed 20 kids at Sherman Park, sent a mom to 414 LIFE. "
    "Also: NAME + your name, UNDO, ME (your totals)"
)


def _twiml(msg: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(msg)}</Message></Response>',
                    media_type="application/xml")


FAKE_NUMBER = re.compile(r"\+1\d{3}55501\d{2}")  # 555-0100..0199: reserved for fiction, never a real phone


def _valid_twilio(request: Request, form: dict) -> bool:
    """Real texts must carry Twilio's X-Twilio-Signature. Without one, only the /phone simulator's fictional
    555-01xx numbers are accepted once the app is online (TWILIO_AUTH_TOKEN set or an https PUBLIC_URL), so
    nobody can pretend to be a worker's real number to UNDO their text or read their totals.
    On a laptop with neither set, anything goes (local demo)."""
    token = os.getenv("TWILIO_AUTH_TOKEN")
    online = bool(token) or os.getenv("PUBLIC_URL", "").startswith("https://")
    if not request.headers.get("X-Twilio-Signature") or not token:
        return not online or FAKE_NUMBER.fullmatch(form.get("From", "")) is not None
    url = os.getenv("PUBLIC_URL", "").rstrip("/") + "/sms" if os.getenv("PUBLIC_URL") else str(request.url)
    payload = url + "".join(k + form[k] for k in sorted(form))
    digest = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(digest, request.headers.get("X-Twilio-Signature", ""))


@app.post("/sms")
async def sms(request: Request):
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not _valid_twilio(request, form):
        raise HTTPException(403)
    phone, body = form.get("From", "unknown"), form.get("Body", "").strip()
    name = db.worker_name(phone)
    cmd = body.upper()

    named = NAME_CMD.match(body)
    if named and len(named.group(1).split()) <= 3:
        new_name = named.group(1).strip()[:60]
        db.set_worker_name(phone, new_name)
        return _twiml(f"Thanks {new_name[:30]}! Just text what you did any time.")
    if cmd in ("HELP", "?", "INFO"):
        return _twiml(HELP)
    worker = name or f"Volunteer …{phone[-4:]}"
    if cmd == "UNDO":
        last = db.last_entry_for(phone)  # by phone, so a shared or copied name can't undo someone else's entry
        if last and db.delete(last["id"]):
            return _twiml(f"Removed #{last['id']}.")
        return _twiml("Nothing to undo.")
    if cmd in ("ME", "TOTAL", "STATS"):
        since = date.today().replace(day=1).isoformat()
        mine = db.entries_for_phone(phone, since)
        people = sum(e["people_served"] for e in mine)
        return _twiml(f"This month: {len(mine)} activities, {people} people helped. Thank you!")
    if len(body) < 3:
        return _twiml(HELP)

    entry = log_note(body, worker, "sms", phone)
    if entry.get("duplicate"):
        return _twiml(f"Already got that one (#{entry['id']}). Nothing new was added.")
    reply = extract.confirmation(entry) + " UNDO to remove."
    if not name:
        reply += " Text NAME + your name for credit."
    return _twiml(reply)


# ---------- numbers + report ----------
def _window(days: int) -> list[dict]:
    return db.list_entries(since=(date.today() - timedelta(days=days)).isoformat(), limit=1_000_000)


@app.get("/api/summary")
def api_summary(days: int = 30):
    return impact.summarize(_window(days), days)


@app.get("/api/report")
def api_report(days: int = 30):
    start = time.perf_counter()
    entries = _window(days)
    s = impact.summarize(entries, days)
    r = impact.report(s, entries)
    metrics.record("report", (time.perf_counter() - start) * 1000, ok=True, writer=r["writer"])
    return {"summary": s, **r}


@app.get("/api/export.csv")
def export_csv():
    name = f"comforce-impact-{date.today().isoformat()}.csv"
    return StreamingResponse(iter([integrations.csv_text()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/stats")
def stats():
    s = metrics.summary()
    u = llm.USAGE
    # $/token from .env so the number stays right if you switch models (defaults = Claude Opus 5.5)
    cin, cout = float(os.getenv("PRICE_IN_PER_M", "4")), float(os.getenv("PRICE_OUT_PER_M", "20"))
    cost = (u["input_tokens"] * cin + u["output_tokens"] * cout) / 1e6
    s["ai"] = {**u, "model": llm.MODEL, "est_cost_usd": round(cost, 4),
               "cost_per_call_usd": round(cost / u["calls"], 5) if u["calls"] else None}
    return s


@app.get("/api/health")
def health(request: Request):
    out = {"ok": True, "ai": llm.available()}
    if auth.is_admin(request):  # error text can mention keys or model names: admins only
        out |= {"model": llm.MODEL, "ai_failures": llm.USAGE["failures"], "last_ai_error": llm.USAGE["last_error"]}
    return out


# ---------- team (admins) ----------
@app.get("/api/team")
def team():
    return {"users": db.list_users(), "admin_password_from_env": bool(auth.admin_password_from_env())}


class TeamNew(auth.Credentials):
    role: Literal["worker", "admin"] = "worker"


@app.post("/api/team")
def team_add(body: TeamNew):
    if auth.looks_like_admin(body.name):
        raise HTTPException(400, "Pick a different name.")
    u = db.create_user(body.name, auth.hash_pin(body.pin), body.role)
    if not u:
        raise HTTPException(409, "That name is taken.")
    return {**u, "pin_hash": None}


class TeamPatch(BaseModel):
    role: Literal["worker", "admin"] | None = None
    pin: str | None = Field(default=None, min_length=4, max_length=128)


@app.patch("/api/team/{user_id}")
def team_update(user_id: int, body: TeamPatch):
    if not db.get_user(user_id):
        raise HTTPException(404)
    return db.update_user(user_id, role=body.role, pin_hash=auth.hash_pin(body.pin) if body.pin else None) | {"pin_hash": None}


@app.post("/api/team/{user_id}/reset-2fa")
def team_reset_2fa(user_id: int):
    """Lost phone: turns two-step off for that person. An admin is asked to set it up again before admin pages open."""
    if not db.get_user(user_id):
        raise HTTPException(404)
    db.delete_mfa(user_id)
    return {"reset": user_id}


@app.delete("/api/team/{user_id}")
def team_delete(user_id: int):
    if not db.delete_user(user_id):
        raise HTTPException(404)
    return {"deleted": user_id}
