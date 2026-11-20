"""
Turns a messy field note ("fed 12 kids at sherman park, sent 1 mom to 414 life") into a clean record.

Two extractors:
  ai_extract()       Claude with a strict JSON schema (used when ANTHROPIC_API_KEY is set)
  keyword_extract()  free, offline rules. It is the fallback when there's no key or the API fails,
                     and the BASELINE we compare the AI against in eval/run_eval.py
"""
import json
import re
from datetime import date, timedelta

from app import llm

ACTIVITY_TYPES = [
    "crisis_response",       # shootings, fires, storms, emergencies
    "violence_prevention",   # mediation, interrupting conflict, retaliation prevention
    "youth_program",         # youth activities, mentoring, sports, parks programming
    "food_meals",            # meals, groceries, food drives
    "referral",              # connected someone to a service (main point of the entry)
    "outreach",              # door knocking, canvassing, check-ins, community meetings
    "cleanup",               # neighborhood or park cleanups
    "pet_support",           # Pet Division: pet food, vet help, rehoming
    "donation_distribution", # clothes, supplies, coats, school supplies
    "other",
]
OUTCOMES = ["resolved", "in_progress", "needs_follow_up", "unknown"]

# Known Milwaukee partners so the dashboard counts "414 LIFE" and "414 life" as one org.
KNOWN_ORGS = {
    "414 life": "414 LIFE", "414life": "414 LIFE",
    "211": "IMPACT 211", "impact 211": "IMPACT 211",
    "red cross": "American Red Cross",
    "hunger task force": "Hunger Task Force",
    "feeding america": "Feeding America Eastern WI",
    "salvation army": "Salvation Army",
    "sojourner": "Sojourner Family Peace Center",
    "office of violence prevention": "Office of Violence Prevention", "ovp": "Office of Violence Prevention",
    "mps": "Milwaukee Public Schools",
    "mpd": "Milwaukee Police", "police": "Milwaukee Police",
    "guest house": "Guest House of Milwaukee",
    "repairers of the breach": "Repairers of the Breach",
    "st. ben": "St. Benedict the Moor", "st ben": "St. Benedict the Moor",
    "wisconsin humane": "Wisconsin Humane Society", "humane society": "Wisconsin Humane Society",
    "madacc": "MADACC",
    "boys and girls club": "Boys & Girls Clubs", "boys & girls club": "Boys & Girls Clubs",
    "legal aid": "Legal Aid Society", "energy assistance": "Energy Assistance",
    "children's wisconsin": "Children's Wisconsin", "froedtert": "Froedtert Hospital",
}

NEIGHBORHOODS = [
    "Sherman Park", "Washington Park", "Metcalfe Park", "Amani", "Harambee", "Lindsay Heights",
    "Midtown", "Garden Homes", "Borchert Field", "Franklin Heights", "Arlington Heights",
    "Sunset Heights", "Uptown", "Concordia", "Walnut Hill", "Park West", "Havenwoods",
    "Old North Milwaukee", "Thurston Woods", "Silver Spring", "Capitol Heights", "Rufus King",
    "Clarke Square", "Muskego Way", "Layton Park", "Downtown", "Riverwest", "Bronzeville",
]
ZIP_TO_HOOD = {
    "53206": "Amani / Metcalfe Park", "53208": "Washington Park", "53210": "Sherman Park",
    "53216": "Sunset Heights", "53205": "Lindsay Heights", "53209": "Old North Milwaukee",
    "53212": "Harambee", "53218": "Silver Spring", "53225": "Capitol Heights", "53204": "Clarke Square",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "activity_type": {"type": "string", "enum": ACTIVITY_TYPES},
        "summary": {"type": "string", "description": "One plain-language line for a funder report. No personal names."},
        "people_served": {"type": "integer", "description": "Total people directly helped, including youth. 0 if unknown."},
        "youth_served": {"type": "integer", "description": "How many of those were under 18. 0 if none or unknown."},
        "location": {"type": "string", "description": "Place as stated (park, street, school). '' if none."},
        "neighborhood": {"type": "string", "description": "Milwaukee neighborhood name if stated or clearly implied. '' if unsure."},
        "referred_to": {"type": "array", "items": {"type": "string"}, "description": "Organizations people were connected/referred to."},
        "partners": {"type": "array", "items": {"type": "string"}, "description": "Organizations that worked alongside (not referrals)."},
        "outcome": {"type": "string", "enum": OUTCOMES},
        "follow_up": {"type": "string", "description": "Concrete next step if one is needed, else ''."},
        "hours": {"type": "number", "description": "Hours spent if stated, else 0."},
        "days_ago": {"type": "integer", "description": "0 = today, 1 = yesterday, etc. based on the note."},
        "sensitive": {"type": "boolean", "description": "True if the note involves violence, injury, minors in danger, or legal trouble."},
    },
    "required": [
        "activity_type", "summary", "people_served", "youth_served", "location", "neighborhood",
        "referred_to", "partners", "outcome", "follow_up", "hours", "days_ago", "sensitive",
    ],
    "additionalProperties": False,
}

SYSTEM = f"""You turn short field notes from Milwaukee community workers (ComForce MKE volunteers) into structured records for their impact reports.

Rules:
- Notes are typed or dictated fast, often from a phone, with typos and slang. Read generously.
- PRIVACY: never write the name of a person who was helped. Say "a mother", "a teen", "a family". Worker and organization names are fine.
- Only record what the note says. Do not invent numbers: if a count isn't given, use 0, except "a family"/"a mom" etc. which you may count (a person = 1; a family with no size given = 1).
- people_served includes youth_served. youth = under 18 (kids, teens, students).
- activity_type: pick the single main activity. Use "referral" only if connecting someone to a service is the main thing that happened.
- outcome: resolved = the need was met; in_progress = being worked on; needs_follow_up = someone must do something next; unknown = can't tell.
- Normalize these organization names when they appear: {", ".join(sorted(set(KNOWN_ORGS.values())))}.
- Neighborhood: use the Milwaukee neighborhood if the note names it or a well-known landmark in it (Sherman Park, Washington Park, etc.). If only a ZIP is given, leave it as the ZIP code. Don't guess from street names.
- summary: one sentence, past tense, plain language, no names of people helped."""


# Variant for the prompt experiment (eval/ai_sweep.py). Examples are invented, not taken from eval/ sets.
SYSTEM_EXAMPLES = SYSTEM + """

Examples (note -> key fields):
- "fed 15 kids + their 5 moms at the rec, mom of one needs a ride to court tues" -> food_meals, people 20, youth 15, outcome needs_follow_up, follow_up "Give a mother a ride to court Tuesday."
- "shooting by the corner store on center, stayed w the family, 414 life coming by tmrw" -> crisis_response, people 1, referred_to ["414 LIFE"], outcome in_progress
- "w/ hunger task force passed out boxes to 80 families" -> food_meals, people 80, partners ["Hunger Task Force"], referred_to [], outcome resolved
- "checked on the family from the fire, they're good now" -> outreach, people 1, outcome resolved"""


def ai_extract(text: str, model: str | None = None, effort: str | None = None, system: str = SYSTEM) -> dict:
    data = llm.json_call(text, system=system, schema=SCHEMA, model=model, effort=effort)
    data["extractor"] = "ai"
    return data


def _num_before(text: str, words: str) -> int:
    nums = [int(n) for n in re.findall(rf"(\d+)\s*(?:\w+\s){{0,2}}?(?:{words})", text)]
    return max(nums) if nums else 0


def keyword_extract_v1(text: str) -> dict:
    t = text.lower()
    rules = [
        ("crisis_response", r"shoot|shot|fire|storm|tornado|flood|crash|emergenc|evict"),
        ("violence_prevention", r"mediat|interrupt|beef|retaliat|conflict|fight|de-?escalat|peace"),
        ("pet_support", r"\bdog|\bcat\b|pet|vet\b|puppy|kitten"),
        ("food_meals", r"meal|food|fed\b|feed|grocer|lunch|dinner|breakfast|plates|cookout"),
        ("donation_distribution", r"coat|cloth|donat|supplies|backpack|diaper|hygiene"),
        ("cleanup", r"clean ?up|trash|litter"),
        ("youth_program", r"youth|teen|kids|basketball|mentor|tutor"),
        ("referral", r"refer|connect|sent (?:her|him|them)|hooked .* up|linked"),
        ("outreach", r"outreach|door|canvass|check(?:ed)? in|meeting|flyer"),
    ]
    activity = next((name for name, pat in rules if re.search(pat, t)), "other")

    youth = _num_before(t, r"kids?|teens?|youth|students?|boys|girls|children")
    people = max(_num_before(t, r"people|folks|families|family|residents|adults|neighbors|plates|meals|kids?|teens?|youth|students?|children"), youth)
    if people == 0 and re.search(r"\ba (mom|mother|dad|father|family|man|woman|teen|kid|young)", t):
        people = 1

    orgs = []
    for key, name in KNOWN_ORGS.items():
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", t) and name not in orgs:
            orgs.append(name)
    hood = next((h for h in NEIGHBORHOODS if h.lower() in t), "")
    if not hood:
        z = re.search(r"\b(532\d\d)\b", t)
        hood = z.group(1) if z else ""

    if re.search(r"follow ?up|need[s]? to|tomorrow|call back|check on", t):
        outcome = "needs_follow_up"
    elif re.search(r"got (?:her|him|them)|resolved|all set|taken care|placed|housed", t):
        outcome = "resolved"
    else:
        outcome = "unknown"
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)", t)
    return {
        "activity_type": activity,
        "summary": text.strip()[:140],
        "people_served": people,
        "youth_served": youth,
        "location": "",
        "neighborhood": hood,
        "referred_to": orgs if re.search(r"refer|connect|sent|linked|call(?:ed)?|hooked", t) else [],
        "partners": [] if re.search(r"refer|connect|sent|linked", t) else orgs,
        "outcome": outcome,
        "follow_up": "",
        "hours": float(hours.group(1)) if hours else 0,
        "days_ago": 1 if "yesterday" in t else 0,
        "sensitive": bool(re.search(r"shoot|shot|gun|stab|abuse|police|arrest|overdose", t)),
        "extractor": "keywords",
    }


def keyword_extract(text: str) -> dict:
    """Current keyword extractor (v2, app/rules.py). v1 above is kept for the experiment log."""
    from app import rules

    return rules.extract(text)


def _clean(d: dict) -> dict:
    """Make any extractor's output safe to store, whatever the model returned."""
    if d.get("activity_type") not in ACTIVITY_TYPES:
        d["activity_type"] = "other"
    if d.get("outcome") not in OUTCOMES:
        d["outcome"] = "unknown"
    for k in ("people_served", "youth_served", "days_ago"):
        d[k] = max(0, int(d.get(k) or 0))
    d["youth_served"] = min(d["youth_served"], 5000)
    d["people_served"] = min(max(d["people_served"], d["youth_served"]), 5000)  # a typo can't wreck the dashboard
    d["hours"] = min(max(0.0, float(d.get("hours") or 0)), 24.0)  # same limit as editing an entry
    for k in ("referred_to", "partners"):
        d[k] = [KNOWN_ORGS.get(o.lower().strip(), o.strip()) for o in d.get(k) or [] if o.strip()]
    hood = (d.get("neighborhood") or "").strip()
    d["neighborhood"] = ZIP_TO_HOOD.get(hood, hood)
    d["activity_date"] = (date.today() - timedelta(days=min(d.pop("days_ago"), 60))).isoformat()
    return d


def extract(text: str) -> dict:
    """AI when available; falls back to keywords so logging never fails in the field."""
    if llm.available():
        try:
            return _clean(ai_extract(text))
        except Exception as e:  # network down, rate limit, refusal: still save the note
            llm.USAGE["failures"] += 1
            llm.USAGE["last_error"] = f"{type(e).__name__}: {e}"[:200]
            print(f"[extract] AI failed, using keywords: {e}")
    return _clean(keyword_extract(text))


def confirmation(entry: dict) -> str:
    """The short text a worker sees after logging.
    Plain GSM-7 characters only: one "·", "→" or emoji makes a text cost 70-char segments instead of 160."""
    from app.impact import LABELS

    bits = [LABELS.get(entry["activity_type"], entry["activity_type"])]
    n, y = entry["people_served"], entry["youth_served"]
    if n:
        bits.append(f"{n} {'person' if n == 1 else 'people'}" + (f" ({y} youth)" if y else ""))
    if entry["neighborhood"]:
        bits.append(entry["neighborhood"])
    if entry["referred_to"]:
        bits.append("ref " + ", ".join(entry["referred_to"]))
    if entry["outcome"] == "needs_follow_up":
        bits.append("follow-up flagged")
    return f"Logged #{entry['id']}: " + ", ".join(bits) + "."


if __name__ == "__main__":  # quick manual test: python -m app.extract "fed 12 kids at sherman park"
    import sys

    print(json.dumps(extract(" ".join(sys.argv[1:]) or "fed 12 kids at sherman park"), indent=2))
