"""
Builds eval/voice.jsonl from eval/test.jsonl the way phone dictation writes notes:
numbers as words ("twenty kids"), no punctuation, no capitals. Labels are unchanged.
  python -m eval.make_voice_set
"""
import json
import re
from pathlib import Path

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + words(n % 100))
    return str(n)


def voiceify(text: str) -> str:
    # keep "27th", ZIPs, and org names dictation writes as digits ("414 LIFE", "211")
    t = re.sub(r"\b(?!414\b|211\b)(\d{1,3})\b(?!\s*(?:st|nd|rd|th)\b)", lambda m: words(int(m.group(1))), text)
    t = re.sub(r"[.,!?;:()]", "", t).replace("&", "and").replace(" w ", " with ").replace(" w/ ", " with ")
    return re.sub(r"\s+", " ", t).strip().lower()


if __name__ == "__main__":
    here = Path(__file__).parent
    rows = [json.loads(line) for line in (here / "test.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out = [{**r, "text": voiceify(r["text"])} for r in rows]
    (here / "voice.jsonl").write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")
    for r in out[:5]:
        print(r["text"])
