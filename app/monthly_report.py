"""The monthly impact report, emailed to ComForce on its own.

Early each month (days 1-7) the running server emails last month's report to REPORT_TO, once. It uses any
mail account that allows SMTP: a Gmail or Outlook account with an app password works and costs nothing.
Admins can also send it now from the report page, and from the command line:

  python -m app.monthly_report                  # print last month's email, send nothing
  python -m app.monthly_report --send           # send last month's report now (even if already sent)
  python -m app.monthly_report --month 2026-10  # another month

Settings (.env): SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, REPORT_TO (comma-separated),
REPORT_FROM (defaults to SMTP_USER). Leave SMTP_HOST or REPORT_TO blank and nothing is ever sent.

What's in the email: the same funder-ready report as /report (numbers from the database; stories only from
non-sensitive, AI-written summaries). No raw notes, no phone numbers, no CSV attachment.
"""
import argparse
import html
import os
import re
import smtplib
import ssl
import threading
import time
from calendar import monthrange
from datetime import date, timedelta
from email.message import EmailMessage

from app import db, impact, llm

SEND_WINDOW_DAYS = 7  # only send in the first week, so turning this on mid-month doesn't email a stale report
CHECK_EVERY = 3600


def configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("REPORT_TO"))


def last_month(today: date | None = None) -> str:
    first = (today or date.today()).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _bounds(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def build(month: str) -> dict:
    """Report for one calendar month: {"subject", "markdown", "text", "html", "summary"}."""
    start, end = _bounds(month)
    entries = [e for e in db.list_entries(since=start.isoformat(), limit=1_000_000)
               if e["activity_date"] <= end.isoformat()]
    days = (end - start).days + 1
    s = impact.summarize(entries, days)
    s["since"] = start.isoformat()
    label = start.strftime("%B %Y")
    md = impact.report(s, entries)["markdown"]
    md = md.replace(f"In the last {days} days", f"In {label}", 1)
    url = os.getenv("PUBLIC_URL", "").rstrip("/")
    footer = (f"\n\nFull report, charts and Save as PDF: {url}/report?days=30" if url else "") + \
             "\n\nSent automatically by ImpactText. Numbers come straight from the records; nothing is estimated." + \
             (" The text was drafted by AI from those numbers: read it before forwarding to a funder."
              if llm.available() and entries else "")
    text = f"ComForce MKE impact report: {label}\n\n{md}{footer}"
    return {"subject": f"ComForce MKE impact report: {label} ({s['people_served']:,} people served)",
            "markdown": md, "text": text, "html": _html(label, md + footer), "summary": s, "month": month}


def _html(label: str, md: str) -> str:
    """Just enough Markdown for an email: headings, bullets, bold, paragraphs."""
    out, items = [], []

    def inline(s: str) -> str:
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s))

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for block in re.split(r"\n\s*\n|\n(?=[-#])", md.strip()):
        line = block.strip()
        if line.startswith("- "):
            items.append(inline(line[2:]))
            continue
        flush()
        if line.startswith("#"):
            out.append(f"<h2 style='font-size:17px;margin:18px 0 6px'>{inline(line.lstrip('#').strip())}</h2>")
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return (f"<div style='font:15px/1.5 system-ui,sans-serif;max-width:640px;color:#161616'>"
            f"<h1 style='font-size:22px'>ComForce MKE impact report: {html.escape(label)}</h1>{''.join(out)}</div>")


def send(month: str, smtp=None) -> dict:
    """Email the report for `month` now. Raises on any mail error."""
    if not configured():
        raise RuntimeError("Set SMTP_HOST and REPORT_TO first (see .env.example).")
    r = build(month)
    to = [a.strip() for a in os.environ["REPORT_TO"].split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"], msg["To"] = r["subject"], ", ".join(to)
    msg["From"] = os.getenv("REPORT_FROM") or os.getenv("SMTP_USER") or "impacttext@localhost"
    msg.set_content(r["text"])
    msg.add_alternative(r["html"], subtype="html")
    port = int(os.getenv("SMTP_PORT", "587"))
    if smtp is None:  # 465 = encrypted from the start; 587 = upgrade with STARTTLS
        smtp = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp(os.environ["SMTP_HOST"], port, timeout=30) as s:
        if port not in (25, 465):
            s.starttls(context=ssl.create_default_context())
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return {"month": month, "to": to, "subject": r["subject"]}


def send_if_due(today: date | None = None, smtp=None) -> dict | None:
    """Once per month, in the first week: send last month's report. Safe to call as often as you like."""
    today = today or date.today()
    if not configured() or today.day > SEND_WINDOW_DAYS:
        return None
    month = last_month(today)
    if not db.claim_report_send(month):  # already sent (or being sent right now)
        return None
    try:
        out = send(month, smtp)
    except Exception as e:
        db.release_report_send(month)  # try again at the next check
        print(f"[monthly report] {month} not sent, will retry: {e}", flush=True)
        return None
    db.mark_report_sent(month, ", ".join(out["to"]))
    print(f"[monthly report] {month} sent to {', '.join(out['to'])}", flush=True)
    return out


def start_scheduler() -> None:
    """A background thread in the web server. One server (see LAUNCH_CHECKLIST), so no outside scheduler."""
    if not configured() or os.getenv("REPORT_SCHEDULER", "on") == "off":
        return

    def loop():
        while True:
            try:
                send_if_due()
            except Exception as e:  # never take the server down over an email
                print(f"[monthly report] check failed: {e}", flush=True)
            time.sleep(CHECK_EVERY)

    threading.Thread(target=loop, name="monthly-report", daemon=True).start()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--month", default=last_month(), help="YYYY-MM (default: last month)")
    p.add_argument("--send", action="store_true", help="email it now")
    a = p.parse_args()
    if a.send:
        print(send(a.month))
    else:
        r = build(a.month)
        print("Subject:", r["subject"], "\n")
        print(r["text"])
