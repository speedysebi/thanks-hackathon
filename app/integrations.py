"""
Connections to the apps people already use. No developer accounts or OAuth needed: everything uses open formats.

  Calendars  /calendar/<token>.ics       follow-ups as a subscribable calendar:
                                          Google Calendar (From URL), Outlook (Subscribe from web), Apple Calendar (webcal://)
             /api/entries/<id>.ics       one follow-up, for "Add to calendar" (Apple, or any app that opens .ics)
             /api/followups              open follow-ups + ready-made Google / Outlook "add event" links
  Sheets     /feeds/<token>.csv          live data link: Google Sheets =IMPORTDATA(...), Excel Data > From Web, Numbers
  Phones     Siri Shortcut + Android share sheet use the normal POST /api/log (see /connect for setup)

The <token> links work without the admin sign-in (calendar apps and Google Sheets can't log in), so the token
is the secret. Anyone with the link can read the follow-ups / data: share it like a password, and make a
new one at /connect if it leaks. Sensitive entries never put details into calendar events.
"""
import csv
import hmac
import io
import os
import re
import secrets
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app import db

TOKEN_FILE = Path(db.DB_PATH).parent / "feed_token.txt"
router = APIRouter()


# ---------- the secret link token ----------
def token() -> str:
    if os.getenv("FEED_TOKEN"):
        return os.environ["FEED_TOKEN"]
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(t) >= 20:
            return t
    except FileNotFoundError:
        pass
    return rotate_token()


def rotate_token() -> str:
    t = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t


def _check(given: str) -> None:
    if not hmac.compare_digest(given.encode(), token().encode()):
        raise HTTPException(404)  # don't reveal that the path exists


def base_url(request: Request) -> str:
    return (os.getenv("PUBLIC_URL") or str(request.base_url)).rstrip("/")


def lan_url(request: Request) -> str | None:
    """The address a phone on the same Wi-Fi can use when the server runs with --host 0.0.0.0."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    port = request.url.port or 8000
    return f"http://{ip}:{port}"


# ---------- when is a follow-up due? ----------
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# full names or unambiguous short forms only: "sat with the family" isn't Saturday
_DAY_RE = re.compile(r"\b(monday|tuesday|tues|wednesday|weds|thursday|thurs|friday|saturday|sunday)\b")


def due_date(e: dict) -> date:
    """Reads 'tomorrow', 'friday', 'next week', 'in 3 days' from the follow-up / note. Default: the day after."""
    # counted from the day the note was typed; demo entries are all inserted "now", so they use the activity day
    typed = e.get("created_at") if e.get("extractor") != "seed" else None
    base = date.fromisoformat((typed or e["activity_date"])[:10])
    # the follow-up line says when; the raw note may mention past days ("on friday we fed..."), so it's only a fallback
    t = (e.get("follow_up") or e.get("raw_text") or "").lower()
    if re.search(r"\b(today|tonight|later today)\b", t):
        return base
    if re.search(r"\b(tomorrow|tmrw|tmr|tomorow|mañana)\b", t):
        return base + timedelta(days=1)
    m = re.search(r"\bin (\d{1,2}) days?\b", t)
    if m:
        return base + timedelta(days=int(m.group(1)))
    m = _DAY_RE.search(t)
    if m:
        target = next(i for i, d in enumerate(_DAYS) if d.startswith(m.group(1)[:3]))
        return base + timedelta(days=(target - base.weekday() - 1) % 7 + 1)  # the next one, never today
    if "next week" in t:
        return base + timedelta(days=7 - base.weekday())  # next Monday
    return base + timedelta(days=1)


def _event(e: dict) -> dict:
    if e["sensitive"]:
        title = f"Follow-up #{e['id']} (sensitive: details in ImpactText)"
        details = "This case is marked sensitive, so no details are put in the calendar. Open the ImpactText dashboard."
    else:
        title = "Follow up: " + (e["follow_up"] or e["summary"])[:120]
        details = "\n".join(x for x in [
            e["summary"],
            f"Where: {e['neighborhood']}" if e["neighborhood"] else "",
            f"Referred to: {', '.join(e['referred_to'])}" if e["referred_to"] else "",
            f"Logged by {e['worker']} on {e['activity_date']} (#{e['id']})",
        ] if x)
    return {"id": e["id"], "title": title, "details": details, "due": due_date(e), "sensitive": e["sensitive"],
            "worker": e["worker"]}


def open_followups(worker: str | None = None, days: int = 120) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = [e for e in db.list_entries(since=since, limit=1_000_000) if e["outcome"] == "needs_follow_up"]
    if worker:
        rows = [e for e in rows if e["worker"].lower() == worker.lower()]
    return [_event(e) for e in rows]


# ---------- iCalendar (RFC 5545) ----------
def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 bytes are split with CRLF + space (RFC 5545 3.1)."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def ics(events: list[dict], name: str, link: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ImpactText//Follow-ups//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(name)}", "X-PUBLISHED-TTL:PT1H",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
    for ev in events:
        lines += ["BEGIN:VEVENT", f"UID:impacttext-followup-{ev['id']}@impacttext", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{ev['due']:%Y%m%d}", f"DTEND;VALUE=DATE:{ev['due'] + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_ics_text(ev['title'])}", f"DESCRIPTION:{_ics_text(ev['details'] + chr(10) + link)}",
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def add_links(ev: dict, link: str) -> dict:
    start, end = ev["due"], ev["due"] + timedelta(days=1)
    body = ev["details"] + "\n" + link
    outlook = urlencode({"subject": ev["title"], "body": body, "startdt": start.isoformat(),
                         "enddt": end.isoformat(), "allday": "true", "path": "/calendar/action/compose", "rru": "addevent"}, quote_via=quote)
    return {
        "google": "https://calendar.google.com/calendar/render?" + urlencode(
            {"action": "TEMPLATE", "text": ev["title"], "dates": f"{start:%Y%m%d}/{end:%Y%m%d}", "details": body}, quote_via=quote),
        "outlook_work": "https://outlook.office.com/calendar/0/deeplink/compose?" + outlook,
        "outlook_personal": "https://outlook.live.com/calendar/0/deeplink/compose?" + outlook,
        "ics": f"/api/entries/{ev['id']}.ics",
    }


# ---------- routes ----------
@router.get("/calendar/{tok}.ics", include_in_schema=False)
def calendar_feed(tok: str, request: Request, worker: str | None = None):
    _check(tok)
    name = f"ImpactText follow-ups ({worker})" if worker else "ImpactText follow-ups"
    body = ics(open_followups(worker), name, base_url(request) + "/dashboard")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="impacttext-followups.ics"', "Cache-Control": "no-store"})


@router.get("/api/entries/{entry_id}.ics")
def entry_ics(entry_id: int, request: Request):
    e = db.get(entry_id)
    if not e:
        raise HTTPException(404)
    body = ics([_event(e)], "ImpactText follow-up", base_url(request) + "/dashboard")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="followup-{entry_id}.ics"'})


@router.get("/api/followups")
def api_followups(request: Request, worker: str | None = None):
    link = base_url(request) + "/dashboard"
    return [{**ev, "due": ev["due"].isoformat(), "links": add_links(ev, link)} for ev in open_followups(worker)]


def csv_text() -> str:
    rows = db.list_entries(limit=1_000_000)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "referred_to": "; ".join(r["referred_to"]), "partners": "; ".join(r["partners"])})
    return buf.getvalue()


@router.get("/feeds/{tok}.csv", include_in_schema=False)
def csv_feed(tok: str):
    _check(tok)
    return Response(csv_text(), media_type="text/csv; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/api/integrations")
def api_integrations(request: Request):
    base, t = base_url(request), token()
    cal = f"{base}/calendar/{t}.ics"
    host = request.url.hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1")
    return {
        "base": base,
        "local_only": local and not os.getenv("PUBLIC_URL"),
        "https": base.startswith("https://"),
        "lan_url": lan_url(request) if local else None,
        "password": False,  # logging a note (Siri, share) needs no sign-in; only the admin side does
        "calendar_url": cal,
        "webcal_url": "webcal://" + cal.split("://", 1)[1],
        "google_subscribe": "https://calendar.google.com/calendar/r?" + urlencode({"cid": "webcal://" + cal.split("://", 1)[1]}),
        "outlook_subscribe": "https://outlook.office.com/calendar/0/addfromweb?" + urlencode({"url": cal, "name": "ImpactText follow-ups"}),
        "csv_url": f"{base}/feeds/{t}.csv",
        "log_url": f"{base}/api/log",
        "workers": sorted({e["worker"] for e in db.list_entries(limit=1_000_000)}),
        "token_from_env": bool(os.getenv("FEED_TOKEN")),
    }


@router.post("/api/integrations/new-link")
def api_new_link(request: Request):
    if request.headers.get("X-ImpactText") != "1":  # a plain cross-site form can't set this header
        raise HTTPException(400, "Missing X-ImpactText header.")
    if os.getenv("FEED_TOKEN"):
        raise HTTPException(400, "FEED_TOKEN is set in .env; change it there.")
    rotate_token()
    return {"ok": True}
