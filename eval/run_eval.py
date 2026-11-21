"""
How accurately do we turn field notes into records? Compares the free keyword BASELINE with the AI.
  python -m eval.run_eval              # keywords only (free), dev + test sets
  python -m eval.run_eval --ai         # keywords vs AI (needs ANTHROPIC_API_KEY; ~60 calls)
  python -m eval.run_eval -v --set dev # show every miss on one set
Labels: eval/dev.jsonl (tune on this) and eval/test.jsonl (held out: report this, don't tune on it).
"""
import json
import sys
import time
from pathlib import Path

from app import extract, llm

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles

FIELDS = ["activity_type", "people_served", "youth_served", "neighborhood", "referred_to", "outcome"]
def load(name: str) -> list[dict]:
    path = Path(__file__).parent / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def match(field: str, got, want) -> bool:
    if field == "referred_to":
        return {o.lower() for o in got} == {o.lower() for o in want}
    if field == "neighborhood":
        g, w = (got or "").lower(), (want or "").lower()
        return g == w or (bool(g) and bool(w) and (g in w or w in g))
    return got == want


def run(name: str, fn, NOTES: list[dict]) -> dict:
    per_field = {f: 0 for f in FIELDS}
    all_right, misses, start = 0, [], time.perf_counter()
    for n in NOTES:
        got = extract._clean(fn(n["text"]))
        ok = {f: match(f, got[f], n[f]) for f in FIELDS}
        for f, v in ok.items():
            per_field[f] += v
        all_right += all(ok.values())
        misses += [(n["text"][:60], f, got[f], n[f]) for f, v in ok.items() if not v]
    total = len(NOTES)
    return {
        "name": name,
        "field_accuracy": round(100 * sum(per_field.values()) / (total * len(FIELDS)), 1),
        "notes_fully_right": round(100 * all_right / total, 1),
        "per_field": {f: round(100 * v / total) for f, v in per_field.items()},
        "sec_per_note": round((time.perf_counter() - start) / total, 2),
        "misses": misses,
    }


def show(r: dict, verbose: bool) -> None:
    print(f"\n== {r['name']} ==  field accuracy {r['field_accuracy']}%  ·  notes 100% right {r['notes_fully_right']}%  ·  {r['sec_per_note']}s/note")
    print("   " + "  ".join(f"{f}: {v}%" for f, v in r["per_field"].items()))
    if verbose:
        for text, f, got, want in r["misses"]:
            print(f"   ✗ {f:14} got {got!r:30} want {want!r:30} | {text}")


if __name__ == "__main__":
    verbose = "-v" in sys.argv
    sets = [sys.argv[sys.argv.index("--set") + 1]] if "--set" in sys.argv else ["dev", "test", "voice"]
    extractors = [("Keywords v2 (free)", extract.keyword_extract)]
    if "--v1" in sys.argv:
        extractors.insert(0, ("Keywords v1", extract.keyword_extract_v1))
    if "--ai" in sys.argv:
        if not llm.available():
            sys.exit("Set ANTHROPIC_API_KEY in .env to run the AI eval.")
        extractors.append((f"AI ({llm.MODEL}, effort {llm.EFFORT})", extract.ai_extract))
    results = []
    for split in sets:
        for name, fn in extractors:
            r = run(f"{name} · {split}", fn, load(split))
            show(r, verbose)
            results.append(r)
    if llm.USAGE["calls"]:
        u = llm.USAGE
        print()
        print(f"AI tokens: {u['input_tokens']} in / {u['output_tokens']} out over {u['calls']} calls")
    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps([{k: v for k, v in r.items() if k != "misses"} for r in results], indent=2))
    print()
    print(f"Saved {out}")
    if "--min" in sys.argv:  # CI gate: fail if any set drops below this field accuracy
        floor = float(sys.argv[sys.argv.index("--min") + 1])
        low = [f"{r['name']}: {r['field_accuracy']}%" for r in results if r["field_accuracy"] < floor]
        print(f"Below {floor}%: {', '.join(low)}" if low else f"All sets at or above {floor}%.")
        sys.exit(1 if low else 0)
