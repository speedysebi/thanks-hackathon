"""Monthly report email: the right month's numbers, sent once, retried on failure, admin-only.

Run: python -m eval.monthly_report_test (temporary database, fake mail server, no paid AI calls).
"""
import gc
import io
import os
import runpy
import smtplib
import tempfile
import unittest
import warnings
from datetime import date
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db, extract, llm, monthly_report
from app.main import app


class FakeSMTP:
    sent: list = []
    attempts: list = []
    refused: dict = {}
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        pass

    def send_message(self, msg, to_addrs=None):
        if FakeSMTP.fail:
            raise OSError("mail server down")
        recipients = list(to_addrs)
        FakeSMTP.attempts.append(recipients)
        refused = {address: error for address, error in FakeSMTP.refused.items() if address in recipients}
        if len(refused) == len(recipients):
            raise smtplib.SMTPRecipientsRefused(refused)
        FakeSMTP.sent.append(msg)
        return refused


MAIL = {"SMTP_HOST": "smtp.example.org", "SMTP_USER": "impact@example.org", "SMTP_PASSWORD": "x",
        "REPORT_TO": "director@example.org, grants@example.org", "SESSION_SECRET": "monthly-tests-only",
        "ADMIN_PASSWORD": "y" * 20, "ADMIN_TOTP_SECRET": "JBSWY3DPEHPK3PXP", "REPORT_SCHEDULER": "off"}


class MonthlyReportTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore", category=ResourceWarning)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "monthly.db"
        self.env = patch.dict(os.environ, MAIL)
        self.env.start()
        self.no_ai = patch.object(llm, "_client", None)
        self.no_ai.start()
        FakeSMTP.sent, FakeSMTP.fail = [], False
        FakeSMTP.attempts, FakeSMTP.refused = [], {}
        self.smtp = patch.object(monthly_report.smtplib, "SMTP", FakeSMTP)
        self.smtp.start()

    def tearDown(self):
        self.smtp.stop()
        self.no_ai.stop()
        self.env.stop()
        db._ready.discard(str(db.DB_PATH))
        db.DB_PATH = self.old_path
        gc.collect()
        self.tmp.cleanup()

    def entry(self, day: str, people: int, **extra):
        fields = extract._clean(extract.keyword_extract(f"fed {people} people"))
        return db.insert({**fields, "worker": "Tester", "channel": "web", "raw_text": f"fed {people} people at 2411 N 35th St",
                          "activity_date": day, "people_served": people, "youth_served": 0, **extra})

    def test_only_that_months_entries_count(self):
        self.entry("2026-09-30", 100)
        self.entry("2026-10-01", 7)
        self.entry("2026-10-31", 5)
        self.entry("2026-11-01", 100)
        r = monthly_report.build("2026-10")
        self.assertEqual(r["summary"]["people_served"], 12)
        self.assertEqual(r["summary"]["entries"], 2)
        self.assertIn("October 2026", r["subject"])
        self.assertIn("In October 2026", r["text"])
        self.assertNotIn("2411", r["text"] + r["html"])  # raw notes never go out

    def test_sends_once_in_first_week_and_retries_after_failure(self):
        self.entry("2026-10-05", 9)
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 9)))  # past the first week
        FakeSMTP.fail = True
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(db.report_sends(), [])
        FakeSMTP.fail = False
        out = monthly_report.send_if_due(date(2026, 11, 1))
        self.assertEqual(out["month"], "2026-10")
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 2)))  # not twice
        self.assertEqual(len(FakeSMTP.sent), 1)
        msg = FakeSMTP.sent[0]
        self.assertEqual(msg["To"], "director@example.org, grants@example.org")
        self.assertEqual(db.report_sends()[0]["month"], "2026-10")

    def test_january_reports_december(self):
        self.assertEqual(monthly_report.last_month(date(2027, 1, 3)), "2026-12")

    def test_partial_refusal_retries_only_missing_recipients_after_reopening_database(self):
        FakeSMTP.refused = {"grants@example.org": (450, b"Mailbox temporarily unavailable")}
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(db.report_sends(), [])
        self.assertEqual(db.report_recipients_sent("2026-10"), {"director@example.org"})

        # Reinitialize storage: accepted recipients must survive outside process memory.
        db._ready.discard(str(db.DB_PATH))
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 2)))
        self.assertEqual(db.report_recipients_sent("2026-10"), {"director@example.org"})
        FakeSMTP.refused = {}
        out = monthly_report.send_if_due(date(2026, 11, 3))
        self.assertEqual(out["to"], ["director@example.org", "grants@example.org"])
        self.assertEqual(FakeSMTP.attempts, [
            ["director@example.org", "grants@example.org"],
            ["grants@example.org"], ["grants@example.org"],
        ])
        self.assertEqual(db.report_sends()[0]["sent_to"], "director@example.org, grants@example.org")
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 4)))
        self.assertEqual(len(FakeSMTP.attempts), 3)

    def test_all_recipients_refused_are_not_recorded_as_sent(self):
        FakeSMTP.refused = {address: (450, b"Try later")
                            for address in ("director@example.org", "grants@example.org")}
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(db.report_sends(), [])
        self.assertEqual(db.report_recipients_sent("2026-10"), set())
        FakeSMTP.refused = {}
        self.assertIsNotNone(monthly_report.send_if_due(date(2026, 11, 2)))
        self.assertEqual(FakeSMTP.attempts, [["director@example.org", "grants@example.org"]] * 2)

    def test_recorded_recipients_skip_smtp_after_interrupted_completion(self):
        # SMTP delivery was recorded, but the server stopped before marking the month complete.
        db.mark_report_recipients_sent("2026-10", ["director@example.org", "grants@example.org"])
        out = monthly_report.send_if_due(date(2026, 11, 1))
        self.assertEqual(out["month"], "2026-10")
        self.assertEqual(FakeSMTP.attempts, [])
        self.assertEqual(db.report_sends()[0]["month"], "2026-10")

    def test_send_now_reports_partial_failure_and_scheduler_resumes(self):
        FakeSMTP.refused = {"grants@example.org": (450, b"Try later")}
        with TestClient(app) as admin:
            admin.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=True))
            r = admin.post("/api/report/email", json={"month": "2026-10"})
            self.assertEqual(r.status_code, 502)
            self.assertEqual(admin.get("/api/report/email").json()["sent"], [])
        FakeSMTP.refused = {}
        self.assertIsNotNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(FakeSMTP.attempts[-1], ["grants@example.org"])

    def test_manual_resend_deliberately_sends_to_everyone(self):
        self.assertIsNotNone(monthly_report.send_if_due(date(2026, 11, 1)))
        with TestClient(app) as admin:
            admin.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=True))
            r = admin.post("/api/report/email", json={"month": "2026-10"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(FakeSMTP.attempts, [["director@example.org", "grants@example.org"]] * 2)

    def test_cli_send_marks_month_complete_for_scheduler(self):
        with patch("sys.argv", ["monthly_report", "--month", "2026-10", "--send"]), redirect_stdout(io.StringIO()):
            runpy.run_path(monthly_report.__file__, run_name="__main__")
        self.assertEqual(db.report_sends()[0]["month"], "2026-10")
        self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(len(FakeSMTP.attempts), 1)

    def test_display_names_and_duplicate_recipients_use_exact_smtp_envelope(self):
        with patch.dict(os.environ, {"REPORT_TO": '"Director, ComForce" <director@example.org>, director@example.org'}):
            out = monthly_report.send_if_due(date(2026, 11, 1))
        self.assertEqual(out["to"], ["director@example.org"])
        self.assertEqual(FakeSMTP.attempts, [["director@example.org"]])

    def test_nothing_happens_without_settings(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            self.assertFalse(monthly_report.configured())
            self.assertIsNone(monthly_report.send_if_due(date(2026, 11, 1)))
        self.assertEqual(FakeSMTP.sent, [])

    def test_html_escapes_entry_text(self):
        self.assertIn("&lt;script&gt;", monthly_report._html("x", "- **<script>alert(1)</script>**"))

    def test_send_now_is_admin_only(self):
        with TestClient(app) as anon:
            self.assertIn(anon.post("/api/report/email", json={}).status_code, (401, 403))
            self.assertIn(anon.get("/api/report/email").status_code, (401, 403))
        with TestClient(app) as admin:
            admin.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=True))
            r = admin.post("/api/report/email", json={"month": "2026-10"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(admin.get("/api/report/email").json()["sent"][0]["month"], "2026-10")
            self.assertEqual(admin.post("/api/report/email", json={"month": "2026-13"}).status_code, 422)
        self.assertEqual(len(FakeSMTP.sent), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
