"""
AI experiment: which model / effort / prompt gives the best accuracy per dollar?
Needs ANTHROPIC_API_KEY. Scores the HELD-OUT test set (and dev) for each config.
  python -m eval.ai_sweep                 # full grid (~9 configs x 60 notes = ~540 calls; a few dollars at most)
  python -m eval.ai_sweep --quick         # 3 configs, test set only (~90 calls)
Writes eval/sweep_results.json and prints a table to paste into docs/EXPERIMENTS.md.
"""
import json
import sys
from functools import partial
from pathlib import Path

from app import extract, llm
from eval.run_eval import load, run

sys.stdout.reconfigure(encoding="utf-8")

# $ per million tokens (input, output). Check https://claude.com/pricing before quoting.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
NOTES_PER_MONTH = 300

FULL = [(m, e, p) for m in PRICES for e in ("low", "medium") for p in ("base",)] + \
       [(m, "low", "examples") for m in PRICES]
QUICK = [("claude-opus-5-5", "low", "base"), ("claude-sonnet-5-5", "low", "base"), ("claude-haiku-5-5", "low", "base")]


def main() -> None:
    if not llm.available():
        sys.exit("Set ANTHROPIC_API_KEY in .env first.")
    quick = "--quick" in sys.argv
    splits = ["test"] if quick else ["dev", "test"]
    rows = []
    for model, effort, prompt in (QUICK if quick else FULL):
        system = extract.SYSTEM_EXAMPLES if prompt == "examples" else extract.SYSTEM
        fn = partial(extract.ai_extract, model=model, effort=effort, system=system)
        for split in splits:
            before = dict(llm.USAGE)
            try:
                r = run(f"{model} · {effort} · {prompt} · {split}", fn, load(split))
            except Exception as e:  # one bad config shouldn't kill the sweep
                print(f"!! {model} {effort} {prompt}: {e}")
                break
            calls = llm.USAGE["calls"] - before["calls"]
            tin = llm.USAGE["input_tokens"] - before["input_tokens"]
            tout = llm.USAGE["output_tokens"] - before["output_tokens"]
            pin, pout = PRICES[model]
            per_note = (tin * pin + tout * pout) / 1e6 / max(calls, 1)
            rows.append({"model": model, "effort": effort, "prompt": prompt, "split": split,
                         "field_accuracy": r["field_accuracy"], "notes_fully_right": r["notes_fully_right"],
                         "per_field": r["per_field"], "sec_per_note": round((llm.USAGE["seconds"] - before["seconds"]) / max(calls, 1), 2),
                         "usd_per_note": round(per_note, 5), "usd_per_month": round(per_note * NOTES_PER_MONTH, 2)})
            x = rows[-1]
            print(f"{model:18} {effort:6} {prompt:8} {split:4}  acc {x['field_accuracy']:5}%  all-right {x['notes_fully_right']:5}%  "
                  f"{x['sec_per_note']}s/note  ${x['usd_per_note']}/note  ${x['usd_per_month']}/mo")
    Path(__file__).with_name("sweep_results.json").write_text(json.dumps(rows, indent=2))
    print("\n| Model | Effort | Prompt | Set | Field acc. | Notes 100% right | s/note | $/month (300 notes) |\n|---|---|---|---|---|---|---|---|")
    for x in rows:
        print(f"| {x['model']} | {x['effort']} | {x['prompt']} | {x['split']} | {x['field_accuracy']}% | {x['notes_fully_right']}% | {x['sec_per_note']} | ${x['usd_per_month']} |")


if __name__ == "__main__":
    main()
