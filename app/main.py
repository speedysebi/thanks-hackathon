"""
ImpactText: community workers text or say what they did; it becomes a record, a dashboard and a grant report.

RUN:  uvicorn app.main:app --reload     then open http://127.0.0.1:8000
PAGES
  /            log page (phone-first: one box + a mic button)
  /dashboard   numbers, charts, follow-ups, recent entries, CSV export
  /report      printable impact report for funders (Print -> Save as PDF)
  /phone       SMS simulator for the demo (same code path as a real text)
API
  POST /api/log            {"text", "worker"} -> saved entry + confirmation
  POST /sms                Twilio webhook (form: From, Body) -> TwiML reply
  GET  /api/entries        recent entries        PATCH/DELETE /api/entries/{id}
  GET  /api/summary?days=  dashboard numbers     GET /api/report?days=  report markdown
  GET  /api/export.csv     every record, for the org to keep
  GET  /api/stats          latency + AI cost numbers for the pitch
"""
import base64
import csv
import hashlib
import hmac
import io
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app import db, extract, impact, llm, metrics, privacy

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="ImpactText")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def require_password(request: Request, call_next):
    """Set APP_PASSWORD in .env before real data goes in: then the dashboard, API and export need it
    (browser login box: any username + that password). /sms stays open for Twilio, which is checked by signature."""
    password = os.getenv("APP_PASSWORD")
    if password and request.url.path != "/sms":
        given = ""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                given = base64.b64decode(auth[6:]).decode().partition(":")[2]
            except ValueError:
                pass
        if not hmac.compare_digest(given.encode(), password.encode()):
            return Response("Password required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="ImpactText"'})
    return await call_next(request)


def page(name: str):
    return lambda: FileResponse(STATIC / name)


for route, file in {"/": "index.html", "/dashboard": "dashboard.html", "/report": "report.html", "/phone": "phone.html"}.items():
    app.add_api_route(route, page(file), methods=["GET"], include_in_schema=False)


# ---------- logging ----------
class LogIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)
    worker: str = Field(default="Volunteer", max_length=60)
    channel: str = "web"

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("note is empty")
        return v


def log_note(text: str, worker: str, channel: str, phone: str = "") -> dict:
    start = time.perf_counter()
    text = privacy.scrub(text.strip()[:2000])  # phone numbers, addresses, "Ms. Johnson" never get stored or sent out
    worker = worker.strip() or "Volunteer"
    dup = db.recent_duplicate(worker, text, phone)
    if dup:
        return {**dup, "duplicate": True}
    fields = extract.extract(text)
    entry = db.insert({**fields, "raw_text": text, "worker": worker, "channel": channel, "phone": phone})
    metrics.record("log", (time.perf_counter() - start) * 1000, ok=True, channel=channel, extractor=fields["extractor"])
    return entry


@app.post("/api/log")
def api_log(body: LogIn):
    entry = log_note(body.text, body.worker, body.channel if body.channel in ("web", "voice") else "web")
    return {"entry": entry, "confirmation": extract.confirmation(entry)}


@app.get("/api/entries")
def api_entries(days: int = 90, limit: int = 200):
    return db.list_entries(since=(date.today() - timedelta(days=days)).isoformat(), limit=limit)


class EntryPatch(BaseModel):
    activity_type: Literal[tuple(extract.ACTIVITY_TYPES)] | None = None
    outcome: Literal[tuple(extract.OUTCOMES)] | None = None
    summary: str | None = Field(default=None, max_length=300)
    people_served: int | None = Field(default=None, ge=0, le=5000)
    youth_served: int | None = Field(default=None, ge=0, le=5000)
    neighborhood: str | None = Field(default=None, max_length=80)
    follow_up: str | None = Field(default=None, max_length=300)
    hours: float | None = Field(default=None, ge=0, le=24)


@app.patch("/api/entries/{entry_id}")
def api_update(entry_id: int, body: EntryPatch):
    entry = db.update(entry_id, body.model_dump(exclude_none=True))
    if not entry:
        raise HTTPException(404)
    return entry


@app.delete("/api/entries/{entry_id}")
def api_delete(entry_id: int):
    if not db.delete(entry_id):
        raise HTTPException(404)
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


def _valid_twilio(request: Request, form: dict) -> bool:
    """Checks Twilio's X-Twilio-Signature when TWILIO_AUTH_TOKEN is set (skip for local demo)."""
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        return True
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
    rows = db.list_entries(limit=100000)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "referred_to": "; ".join(r["referred_to"]), "partners": "; ".join(r["partners"])})
    buf.seek(0)
    name = f"comforce-impact-{date.today().isoformat()}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
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
def health():
    return {"ok": True, "model": llm.MODEL, "ai": llm.available(),
            "ai_failures": llm.USAGE["failures"], "last_ai_error": llm.USAGE["last_error"]}
