"""
How many SMS segments do our replies cost? Twilio bills per segment:
  plain GSM-7 text: 160 chars (153 per part when split); any other char (·, →, …, emoji): 70 chars (67 per part).
  python -m eval.sms_cost
"""
import statistics
import sys
import tempfile
from pathlib import Path

from app import db

db.DB_PATH = Path(tempfile.mkdtemp()) / "sms.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from eval.run_eval import load  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\\[~]|€")


def segments(msg: str) -> int:
    if all(ch in GSM7 or ch in GSM7_EXT for ch in msg):
        n = sum(2 if ch in GSM7_EXT else 1 for ch in msg)
        return 1 if n <= 160 else -(-n // 153)
    n = len(msg.encode("utf-16-le")) // 2
    return 1 if n <= 70 else -(-n // 67)


c = TestClient(app)
notes = [n["text"] for n in load("dev") + load("test")]
replies = []
for i, text in enumerate(notes):
    xml = c.post("/sms", data={"From": f"+1414555{i:04d}", "Body": text}).text
    replies.append(xml.split("<Message>")[1].split("</Message>")[0].replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
for cmd in ("HELP", "ME", "UNDO", "NAME Dee", "AYUDA", "YO", "DESHACER", "NOMBRE Dee", "ESPAÑOL",
            "dimos comida a 20 niños y 5 familias en sherman park, llevé a una mamá a 414 life, llamarla el viernes"):
    xml = c.post("/sms", data={"From": "+14145550001", "Body": cmd}).text
    replies.append(xml.split("<Message>")[1].split("</Message>")[0].replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
segs = [segments(r) for r in replies]
print(f"{len(replies)} replies · avg {statistics.mean(segs):.2f} segments · max {max(segs)} · "
      f"{sum(s == 1 for s in segs)}/{len(segs)} fit in one segment · avg {statistics.mean(len(r) for r in replies):.0f} chars")
print("non-GSM chars used:", sorted({ch for r in replies for ch in r if ch not in GSM7 and ch not in GSM7_EXT}))
print("longest:", max(replies, key=len))
if max(segs) > 1:  # CI gate: every reply must fit in one text (one charge)
    sys.exit(f"{sum(s > 1 for s in segs)} replies need more than one segment")
