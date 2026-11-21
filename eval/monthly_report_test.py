"""Monthly report email: the right month's numbers, sent once, retried on failure, admin-only.

Run: python -m eval.monthly_report_test (temporary database, fake mail server, no paid AI calls).
"""
import gc
import os
import tempfile
import unittest
import warnings
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db, extract, llm, monthly_report
from app.main import app


class FakeSMTP:
    sent: list = []
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

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise OSError("mail server down")
        FakeSMTP.sent.append(msg)


MAIL = {"SMTP_HOST": "smtp.example.org", "SMTP_USER": "impact@example.org", "SMTP_PASSWORD": "x",
        "REPORT_TO": "director@example.org, grants@example.org", "SESSION_SECRET": "monthly-tests-only",
        "ADMIN_PASSWORD": "y" * 20, "ADMIN_TOTP_SECRET": "JBSWY3DPEHPK3PXP"}


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
