"""Logs every request to data/events.jsonl so we can show real numbers at finals."""
import json
import time
from pathlib import Path
from statistics import mean

LOG = Path(__file__).resolve().parent.parent / "data" / "events.jsonl"


def record(event: str, latency_ms: float, ok: bool = True, **extra) -> None:
    LOG.parent.mkdir(exist_ok=True)
    row = {"t": time.time(), "event": event, "latency_ms": round(latency_ms, 1), "ok": ok, **extra}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def summary() -> dict:
    if not LOG.exists():
        return {"requests": 0}
    rows = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        return {"requests": 0}
    lat = sorted(r["latency_ms"] for r in rows if r["event"] == "log") or [0]
    return {
        "requests": len(rows),
        "success_rate": round(sum(r["ok"] for r in rows) / len(rows) * 100, 1),
        "avg_log_latency_ms": round(mean(lat), 1),
        "p95_log_latency_ms": lat[max(0, int(len(lat) * 0.95) - 1)],
        "by_channel": {c: sum(r.get("channel") == c for r in rows) for c in {r.get("channel") for r in rows} if c},
    }
