"""Spanish text replies: commands, language detection, the ESPANOL / ENGLISH choice, one-text cost.

Run: python -m eval.spanish_sms_test (temporary database, no paid AI calls).
"""
import gc
import os
import re
import tempfile
import unittest
import warnings
from html import unescape
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db, llm, sms_text
from app.main import app

# same rule as eval/sms_cost.py (importing it would run its whole sweep)
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\[~]|€")


def segments(msg: str) -> int:
    if all(ch in GSM7 or ch in GSM7_EXT for ch in msg):
        return 1 if len(msg) <= 160 else 2
    return 1 if len(msg) <= 70 else 2

PHONE = "+14145550133"


class SpanishSmsTests(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore", category=ResourceWarning)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "es.db"
        for var in ("TWILIO_AUTH_TOKEN", "PUBLIC_URL"):
            os.environ.pop(var, None)
        self.no_ai = patch.object(llm, "_client", None)
        self.no_ai.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.c.close()
        self.no_ai.stop()
        db._ready.discard(str(db.DB_PATH))
        db.DB_PATH = self.old_path
        gc.collect()
        self.tmp.cleanup()

    def text(self, body, phone=PHONE):
        r = self.c.post("/sms", data={"From": phone, "Body": body})
        self.assertEqual(r.status_code, 200, r.text)
        msg = unescape(re.search(r"<Message>(.*)</Message>", r.text, re.S).group(1))
        self.assertEqual(segments(msg), 1, msg)  # every reply, either language, costs one text
        return msg

    def test_spanish_commands_reply_in_spanish(self):
        self.assertIn("Escriba lo que hizo", self.text("AYUDA"))
        self.assertIn("Gracias Dee", self.text("NOMBRE Dee"))
        self.assertIn("Este mes", self.text("yo"))
        self.assertIn("nada que deshacer", self.text("DESHACER"))

    def test_spanish_note_gets_spanish_confirmation(self):
        msg = self.text("dimos comida a 20 niños en sherman park")
        self.assertTrue(msg.startswith("Anotado #"), msg)
        self.assertIn("DESHACER para borrar", msg)
        self.assertIn("Borrado #", self.text("DESHACER"))

    def test_english_unchanged(self):
        msg = self.text("fed 20 kids at sherman park")
        self.assertTrue(msg.startswith("Logged #"), msg)
        self.assertIn("UNDO to remove", msg)
        self.assertIn("Text what you did", self.text("HELP"))
        self.assertIn("Removed #", self.text("UNDO"))

    def test_spanish_place_names_alone_stay_english(self):
        self.assertTrue(self.text("met with 3 moms at La Causa").startswith("Logged #"))

    def test_choice_sticks_until_changed(self):
        self.assertIn("español", self.text("ESPAÑOL"))
        self.assertTrue(self.text("fed 20 kids at sherman park").startswith("Anotado #"))
        self.assertIn("Escriba", self.text("HELP"))
        self.assertIn("English", self.text("ENGLISH"))
        self.assertTrue(self.text("cleaned up 2 blocks on center st").startswith("Logged #"))

    def test_replies_use_only_plain_text_characters(self):
        for key in sms_text.TEXT["es"]:
            out = sms_text.t("es", key, name="Dee", id=1, n=1, people=1, bits="x", word="x", y=1, orgs="x")
            self.assertFalse(set(out) & set("áíóúÁÍÓÚ"), out)

    def test_note_starting_with_nombre_is_a_note(self):
        self.assertTrue(self.text("nombre tags para 30 niños en la escuela").startswith("Anotado #"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
