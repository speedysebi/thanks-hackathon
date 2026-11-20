"""Turns entries into the numbers funders ask for, plus a plain-language impact report."""
from collections import Counter
from datetime import date, timedelta

from app import llm

LABELS = {
    "crisis_response": "Crisis response", "violence_prevention": "Violence prevention",
    "youth_program": "Youth programs", "food_meals": "Food & meals", "referral": "Referrals",
    "outreach": "Outreach", "cleanup": "Cleanups", "pet_support": "Pet support",
    "donation_distribution": "Donations & supplies", "other": "Other",
}


def summarize(entries: list[dict], days: int) -> dict:
    people = sum(e["people_served"] for e in entries)
    by_type = Counter()
    people_by_type = Counter()
    for e in entries:
        by_type[e["activity_type"]] += 1
        people_by_type[e["activity_type"]] += e["people_served"]
    hoods = Counter()
    for e in entries:
        hoods[e["neighborhood"] or "Not recorded"] += e["people_served"]
    referrals = Counter(o for e in entries for o in e["referred_to"])
    partners = Counter(o for e in entries for o in e["partners"])
    workers = Counter(e["worker"] for e in entries)

    # weekly series, oldest -> newest, so the chart shows a full window even for empty weeks
    today = date.today()
    weeks = []
    for i in range(max(1, days // 7) - 1, -1, -1):
        start = today - timedelta(days=today.weekday() + 7 * i)
        end = start + timedelta(days=7)
        wk = [e for e in entries if start.isoformat() <= e["activity_date"] < end.isoformat()]
        weeks.append({"week": start.isoformat(), "entries": len(wk), "people": sum(e["people_served"] for e in wk)})

    open_follow_ups = [e for e in entries if e["outcome"] == "needs_follow_up"]
    known = [e for e in entries if e["outcome"] != "unknown"]
    return {
        "days": days,
        "since": (today - timedelta(days=days)).isoformat(),
        "entries": len(entries),
        "people_served": people,
        "youth_served": sum(e["youth_served"] for e in entries),
        "referrals": sum(len(e["referred_to"]) for e in entries),
        "hours": round(sum(e["hours"] for e in entries), 1),
        "workers": len(workers),
        "open_follow_ups": len(open_follow_ups),
        "resolved_rate": round(100 * sum(e["outcome"] == "resolved" for e in known) / len(known)) if known else None,
        "by_type": [{"key": k, "label": LABELS.get(k, k), "entries": n, "people": people_by_type[k]} for k, n in by_type.most_common()],
        "by_neighborhood": [{"label": k, "people": n} for k, n in hoods.most_common(8)],
        "referral_orgs": [{"label": k, "count": n} for k, n in referrals.most_common(8)],
        "partner_orgs": [{"label": k, "count": n} for k, n in partners.most_common(8)],
        "by_worker": [{"label": k, "entries": n} for k, n in workers.most_common(10)],
        "weekly": weeks,
    }


REPORT_SYSTEM = """You write short impact reports for ComForce MKE (Community Task Force Milwaukee), a resident-led group doing crisis response, violence prevention and youth work on Milwaukee's north side.
Audience: grant funders and city partners. Tone: warm, factual, confident. No hype words.
Use ONLY the numbers and examples provided. Never invent figures, names or quotes. Never name people who were helped.
Format: plain Markdown. Sections: a 2-3 sentence opening paragraph; "## What we did" (bullets, one per activity type with counts); "## Where" (1-2 sentences); "## Connecting people to help" (referral partners); "## Stories from the field" (2-3 anonymized one-sentence examples taken from the entry summaries); "## Still in progress" (open follow-ups, one line). Under 350 words."""


def _facts(s: dict, entries: list[dict]) -> str:
    lines = [
        f"Period: last {s['days']} days (since {s['since']})",
        f"Activities logged: {s['entries']} by {s['workers']} workers/volunteers",
        f"People served: {s['people_served']} (youth: {s['youth_served']})",
        f"Referrals made: {s['referrals']}; volunteer hours recorded: {s['hours']}",
        f"Open follow-ups: {s['open_follow_ups']}; resolved rate where known: {s['resolved_rate']}%",
        "By activity: " + "; ".join(f"{t['label']} {t['entries']} activities / {t['people']} people" for t in s["by_type"]),
        "By neighborhood (people): " + "; ".join(f"{h['label']} {h['people']}" for h in s["by_neighborhood"]),
        "Referred to: " + "; ".join(f"{o['label']} x{o['count']}" for o in s["referral_orgs"]),
        "Partners: " + "; ".join(f"{o['label']} x{o['count']}" for o in s["partner_orgs"]),
        "Example entry summaries (pick from these, non-sensitive only):",
    ]
    examples = [e for e in entries if not e["sensitive"]][:25]
    lines += [f"- {e['summary']}" for e in examples]
    return "\n".join(lines)


def _template(s: dict, entries: list[dict]) -> str:
    """Free, offline report used when there's no API key."""
    top = ", ".join(f"{t['label'].lower()} ({t['entries']})" for t in s["by_type"][:3]) or "community work"
    out = [
        f"In the last {s['days']} days, ComForce MKE workers and volunteers logged **{s['entries']} activities** "
        f"that reached **{s['people_served']:,} people**, including **{s['youth_served']:,} young people**. "
        f"Most of the work was {top}.",
        "## What we did",
        *[f"- **{t['label']}**: {t['entries']} {'activity' if t['entries'] == 1 else 'activities'}, {t['people']:,} people" for t in s["by_type"]],
        "## Where",
        "People served by neighborhood: " + ", ".join(f"{h['label']} ({h['people']})" for h in s["by_neighborhood"][:5]) + ".",
        "## Connecting people to help",
        (f"We made {s['referrals']} referrals, most often to " + ", ".join(o["label"] for o in s["referral_orgs"][:4]) + ".")
        if s["referral_orgs"] else "No referrals were recorded this period.",
        "## Stories from the field",
        *[f"- {e['summary']}" for e in [e for e in entries if not e["sensitive"]][:3]],
        "## Still in progress",
        f"{s['open_follow_ups']} cases are flagged for follow-up.",
    ]
    return "\n\n".join(out)


def report(s: dict, entries: list[dict]) -> dict:
    if llm.available() and entries:
        try:
            return {"markdown": llm.text_call(_facts(s, entries), system=REPORT_SYSTEM), "writer": "ai"}
        except Exception as e:
            print(f"[report] AI failed, using template: {e}")
    return {"markdown": _template(s, entries), "writer": "template"}
