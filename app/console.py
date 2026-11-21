"""
Testing console API (/console page): try notes without saving, run the evals and robustness checks,
review the eval labels, and see what's left before a real pilot.
Nothing here changes real entries except "reset demo data", which only touches extractor='seed' rows.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import auth, db, extract, llm, mfa, privacy, seed

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
REVIEWS = EVAL / "label_reviews.json"
RESULTS = EVAL / "results.json"
SWEEP = EVAL / "sweep_results.json"
SETS = ("dev", "test", "voice")

router = APIRouter(prefix="/api/console")


def _counts() -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT extractor = 'seed' AS demo, channel, COUNT(*) AS n FROM entries GROUP BY demo, channel"
        ).fetchall()
    out = {"demo": 0, "real": 0, "real_by_channel": {}}
    for r in rows:
        if r["demo"]:
            out["demo"] += r["n"]
        else:
            out["real"] += r["n"]
            out["real_by_channel"][r["channel"]] = r["n"]
    return out


def _reviews() -> dict:
    try:
        return json.loads(REVIEWS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@router.get("/status")
def status():
    counts = _counts()
    results = _read_json(RESULTS) or []
    ai_rows = [r for r in results if r["name"].startswith("AI")]
    reviews = _reviews()
    test_reviewed = sum(1 for k in reviews if k.startswith("test:"))
    test_flagged = sum(1 for k, v in reviews.items() if k.startswith("test:") and v["verdict"] == "wrong")
    u = llm.USAGE
    env = {
        "ai": llm.available(), "model": llm.MODEL, "effort": llm.EFFORT,
        "ai_calls": u["calls"], "ai_failures": u["failures"], "last_ai_error": u["last_error"],
        "twilio": bool(os.getenv("TWILIO_AUTH_TOKEN")), "public_url": os.getenv("PUBLIC_URL") or "",
        "password": bool(auth.admin_password_from_env()),
    }

    def step(id, title, done, detail, how="", manual=False, warn=False):
        return {"id": id, "title": title, "done": done, "detail": detail, "how": how, "manual": manual, "warn": warn}

    ai_test = next((r for r in ai_rows if r["name"].endswith("test")), None)
    kw_test = next((r for r in results if r["name"].startswith("Keywords v2") and r["name"].endswith("test")), None)
    steps = [
        step("key", "Add an Anthropic API key", env["ai"],
             f"AI is on: {env['model']}, effort {env['effort']}." if env["ai"] else "Without a key every note uses the keyword fallback.",
             "Put ANTHROPIC_API_KEY=... in .env, then restart the server."),
        step("ai_works", "Confirm AI calls succeed", env["ai"] and u["calls"] > 0 and u["failures"] == 0,
             (f"{u['failures']} failed call(s). Last error: {u['last_error']}" if u["failures"]
              else f"{u['calls']} successful call(s) since the server started." if u["calls"]
              else "No AI calls yet since the server started."),
             "Use 'Try a note' below. If it fails, the error shows here.", warn=bool(u["failures"])),
        step("ai_eval", "Run the AI eval on the held-out test set", bool(ai_test),
             (f"AI {ai_test['field_accuracy']}% vs keywords {kw_test['field_accuracy'] if kw_test else '?'}% on test."
              if ai_test else "The PR's main claim (AI beats the 91% keyword baseline) hasn't been measured yet."),
             "Click 'Run eval' with 'Include AI' checked (about 30 AI calls per set)."),
        step("sweep", "Pick the model: Opus vs Sonnet vs Haiku, accuracy per dollar", SWEEP.exists(),
             "Results saved in eval/sweep_results.json." if SWEEP.exists() else "Decides the monthly cost you quote to ComForce.",
             "In a terminal: python -m eval.ai_sweep --quick"),
        step("labels", "Second opinion on the 30 held-out labels", test_reviewed >= 30,
             f"{test_reviewed}/30 reviewed" + (f", {test_flagged} flagged as wrong." if test_flagged else "."),
             "Use 'Review labels' below. Someone other than the person who wrote them should do it.",
             warn=bool(test_flagged)),
        step("password", "Choose the admin password", env["password"],
             "Set in .env." if env["password"] else "The admin side uses a made-up password (data/admin_password.txt, also printed when the server starts).",
             "Put ADMIN_PASSWORD=... in .env and restart. Share it only with admins."),
        step("backup", "Save the admin's backup codes", mfa.env_admin_secret() is not None or mfa.backup_codes_left(0) > 0,
             "The built-in admin's authenticator is set by ADMIN_TOTP_SECRET." if mfa.env_admin_secret()
             else f"{mfa.backup_codes_left(0)} of 10 backup codes left for the built-in admin.",
             "Two-step sign-in is on (you're signed in). Keep the backup codes in a password manager; "
             "if they run out, make new ones on /security."),
        step("online", "Put it online", bool(env["public_url"]),
             "PUBLIC_URL is set." if env["public_url"] else "Running on this computer only.",
             "Follow docs/LAUNCH_CHECKLIST.md (Render blueprint, admin setup, who owns which account)."),
        step("twilio", "Connect a real Twilio number", env["twilio"] and bool(env["public_url"]),
             "Signature check is on." if env["twilio"] else "Texts only work through the /phone simulator.",
             "See docs/SMS_SETUP.md; set TWILIO_AUTH_TOKEN and PUBLIC_URL."),
        step("pilot", "Pilot with real workers", counts["real"] >= 20,
             f"{counts['real']} real entries, {counts['demo']} demo entries.",
             "When the real pilot starts, clear the demo data so the report only counts real work."),
    ]
    return {"env": env, "counts": counts, "steps": steps}


class TryIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    ai: bool = True


@router.post("/try")
def try_note(body: TryIn):
    """Runs the full pipeline (scrub -> extract -> clean) WITHOUT saving."""
    scrubbed = privacy.scrub(body.text.strip())
    out = {"original": body.text, "scrubbed": scrubbed, "keywords": extract._clean(extract.keyword_extract(scrubbed))}
    if body.ai and llm.available():
        try:
            out["ai"] = extract._clean(extract.ai_extract(scrubbed))
        except Exception as e:
            llm.USAGE["failures"] += 1
            llm.USAGE["last_error"] = f"{type(e).__name__}: {e}"[:200]
            out["ai_error"] = llm.USAGE["last_error"]
    return out


class EvalIn(BaseModel):
    sets: list[Literal["dev", "test", "voice"]] = ["test"]
    ai: bool = False


@router.post("/eval")
def run_eval(body: EvalIn):
    from eval import run_eval as ev

    if body.ai and not llm.available():
        raise HTTPException(400, "No ANTHROPIC_API_KEY set, so the AI eval can't run.")
    extractors = [("Keywords v2 (free)", extract.keyword_extract)]
    if body.ai:
        extractors.append((f"AI ({llm.MODEL}, effort {llm.EFFORT})", extract.ai_extract))
    runs = []
    for split in body.sets:
        for name, fn in extractors:
            r = ev.run(f"{name} · {split}", fn, ev.load(split))
            r["set"], r["extractor"] = split, name
            r["misses"] = [{"text": t, "field": f, "got": g, "want": w} for t, f, g, w in r["misses"]]
            runs.append(r)
    # keep the CLI's results file in sync so the next-steps list sees AI runs
    old = {r["name"]: r for r in (_read_json(RESULTS) or [])}
    old |= {r["name"]: {k: v for k, v in r.items() if k not in ("misses", "set", "extractor")} for r in runs}
    RESULTS.write_text(json.dumps(list(old.values()), indent=2), encoding="utf-8")
    return {"fields": ev.FIELDS, "runs": runs}


@router.post("/robustness")
def robustness():
    """Runs eval/robustness.py in a separate process (it uses its own temporary database)."""
    try:
        p = subprocess.run([sys.executable, "-m", "eval.robustness"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", timeout=300,
                           # no API key: the checks are about the app, and with a key every check would be a paid AI call
                           env={**{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Robustness checks took over 5 minutes.")
    checks = []
    for line in p.stdout.splitlines():
        if line.startswith(("PASS", "FAIL")):
            ok = line.startswith("PASS")
            label, _, detail = line[6:].partition("  ")
            checks.append({"ok": ok, "label": label, "detail": detail})
    return {"checks": checks, "passed": sum(c["ok"] for c in checks), "total": len(checks),
            "error": p.stderr[-1500:] if p.returncode and not checks else ""}


@router.get("/labels/{split}")
def labels(split: Literal["dev", "test", "voice"]):
    from eval import run_eval as ev

    reviews = _reviews()
    return [{"i": i, **n, "review": reviews.get(f"{split}:{i}")} for i, n in enumerate(ev.load(split))]


class ReviewIn(BaseModel):
    split: Literal["dev", "test", "voice"]
    i: int = Field(ge=0, lt=1000)
    verdict: Literal["ok", "wrong"] | None
    comment: str = Field(default="", max_length=500)


@router.post("/labels")
def review_label(body: ReviewIn):
    """Saved to eval/label_reviews.json so it can be committed and discussed."""
    reviews = _reviews()
    key = f"{body.split}:{body.i}"
    if body.verdict is None:
        reviews.pop(key, None)
    else:
        reviews[key] = {"verdict": body.verdict, "comment": body.comment.strip()}
    REVIEWS.write_text(json.dumps(dict(sorted(reviews.items(), key=lambda kv: (kv[0].split(":")[0], int(kv[0].split(":")[1])))),
                                  indent=2, ensure_ascii=False), encoding="utf-8")
    return {"saved": key}


@router.post("/demo-data")
def reset_demo(action: Literal["reseed", "clear"] = "reseed"):
    """Replaces (or removes) the synthetic demo entries. Real entries are never touched."""
    with db.connect() as conn:
        removed = conn.execute("DELETE FROM entries WHERE extractor = 'seed'").rowcount
    if action == "reseed":
        seed.main(reset=False)
    return {"removed": removed, "counts": _counts()}
