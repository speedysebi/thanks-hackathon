"""Linking a worker's texting phone to their account (My notes -> Link my phone -> text LINK 123456).

Run: python -m eval.link_phone_test (temporary database, no paid AI calls).
"""
import gc
import os
import re
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db, llm
from app.main import app

PHONE, OTHER = "+14145550123", "+14145550177"  # fictional 555-01xx numbers, like the /phone simulator


class LinkPhoneTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore", category=ResourceWarning)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "link.db"
        self.env = patch.dict(os.environ, {"SESSION_SECRET": "link-phone-tests-only", "ADMIN_PASSWORD": "x" * 20})
        self.env.start()
        for var in ("TWILIO_AUTH_TOKEN", "PUBLIC_URL"):
            os.environ.pop(var, None)
        self.no_ai = patch.object(llm, "_client", None)
        self.no_ai.start()
        auth._fails.clear()
        self.dee = db.create_user("Dee", auth.hash_pin("4821"))
        self.web = TestClient(app)
        self.web.cookies.set(auth.COOKIE, auth.make_session(self.dee["id"], 1))
        self.anon = TestClient(app)

    def tearDown(self):
        self.web.close()
        self.anon.close()
        self.no_ai.stop()
        self.env.stop()
        db._ready.discard(str(db.DB_PATH))
        db.DB_PATH = self.old_path
        gc.collect()
        self.tmp.cleanup()

    def text(self, body, phone=PHONE):
        r = self.anon.post("/sms", data={"From": phone, "Body": body})
        self.assertEqual(r.status_code, 200, r.text)
        return re.search(r"<Message>(.*)</Message>", r.text).group(1)

    def code(self):
        r = self.web.post("/api/my/phone/code")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["code"]

    def test_link_credits_old_and_new_texts_to_account(self):
        self.text("fed 12 kids at sherman park")
        reply = self.text("LINK " + self.code())
        self.assertIn("Linked to Dee", reply)
        self.text("handed out 30 coats at metcalfe park")
        mine = self.web.get("/api/my/entries").json()["entries"]
        self.assertEqual(len(mine), 2)
        self.assertTrue(all(e["worker"] == "Dee" for e in mine[:1]))
        self.assertEqual(self.web.get("/api/my/phone").json()["phones"], ["…0123"])
        self.assertNotIn("Text NAME", self.text("cleaned up 2 blocks"))

    def test_code_works_once_and_only_for_its_account(self):
        code = self.code()
        self.assertIn("Linked", self.text("link " + code))
        self.assertIn("didn't work", self.text("LINK " + code, OTHER))
        self.assertIsNone(db.linked_user(OTHER))

    def test_new_code_replaces_old_one(self):
        old = self.code()
        new = self.code()
        if old != new:
            self.assertIn("didn't work", self.text("LINK " + old))
        self.assertIn("Linked", self.text("LINK " + new))

    def test_wrong_guesses_are_limited(self):
        real = self.code()
        wrong = f"{(int(real) + 1) % 10**6:06d}"
        for _ in range(5):
            self.assertIn("didn't work", self.text("LINK " + wrong))
        self.assertIn("Too many", self.text("LINK " + real))
        self.assertIsNone(db.linked_user(PHONE))

    def test_guessing_from_many_numbers_is_limited_too(self):
        real = self.code()
        wrong = f"{(int(real) + 1) % 10**6:06d}"
        for i in range(30):
            self.text("LINK " + wrong, f"+141455501{i + 10:02d}")
        self.assertIn("Too many", self.text("LINK " + real, "+14145550199"))

    def test_signed_out_and_builtin_admin_cannot_get_codes(self):
        self.assertEqual(self.anon.post("/api/my/phone/code").status_code, 401)
        with TestClient(app) as admin:
            admin.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=True))
            self.assertEqual(admin.post("/api/my/phone/code").status_code, 401)

    def test_unlink_stops_crediting_new_texts(self):
        self.text("LINK " + self.code())
        self.text("fed 12 kids at sherman park")
        self.assertEqual(self.web.delete("/api/my/phone").json()["unlinked"], 1)
        self.text("handed out 30 coats")
        self.assertEqual(len(self.web.get("/api/my/entries").json()["entries"]), 1)

    def test_deleting_account_removes_link(self):
        self.text("LINK " + self.code())
        db.delete_user(self.dee["id"])
        self.assertIsNone(db.linked_user(PHONE))

    def test_note_starting_with_link_is_still_a_note(self):
        self.assertIn("Logged", self.text("linked 3 families with 414 LIFE"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
