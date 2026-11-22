"""
Fills the database with SYNTHETIC demo entries (no real people) so the dashboard has something to show.
  python -m app.seed            # adds ~160 entries over the last 90 days
  python -m app.seed --reset    # wipes entries first
Entries are marked extractor='seed' so they can be told apart from real logs.
"""
import random
import sys
from datetime import date, timedelta

from app import db

WORKERS = ["Jordan", "Dee", "Marcus", "Tasha", "Andre", "Keisha", "Luis", "Volunteer …0187"]
HOODS = ["Sherman Park", "Washington Park", "Amani / Metcalfe Park", "Sunset Heights", "Lindsay Heights",
         "Harambee", "Midtown", "Old North Milwaukee"]
HOOD_W = [22, 18, 14, 14, 9, 8, 8, 7]

# (activity, summary template, people range, youth share, referral choices, partner choices, outcome weights)
TEMPLATES = [
    ("food_meals", "Served hot meals to {n} residents at {place}.", (15, 60), .35, [], ["Hunger Task Force", "St. Benedict the Moor"], (8, 1, 0, 2)),
    ("youth_program", "Ran an evening basketball session for {n} teens at {place}.", (6, 25), 1, [], ["Boys & Girls Clubs"], (6, 1, 0, 3)),
    ("youth_program", "Mentoring circle with {n} young men about staying out of conflict.", (4, 12), 1, [], [], (5, 2, 1, 2)),
    ("crisis_response", "Responded to a shooting near {place}; supported the family on scene for several hours.", (2, 6), .3, ["414 LIFE", "Sojourner Family Peace Center"], [], (1, 3, 5, 1)),
    ("crisis_response", "Helped a family displaced by a house fire find emergency shelter.", (3, 7), .5, ["American Red Cross", "IMPACT 211"], [], (4, 2, 3, 1)),
    ("violence_prevention", "Mediated a dispute between two groups of young men before it escalated.", (2, 8), .6, ["414 LIFE"], ["Office of Violence Prevention"], (4, 3, 3, 1)),
    ("referral", "Connected a mother facing eviction to legal help and rent assistance.", (1, 4), .5, ["Legal Aid Society", "IMPACT 211"], [], (2, 3, 5, 1)),
    ("referral", "Connected a man just out of the hospital to housing and food resources.", (1, 1), 0, ["Guest House of Milwaukee", "Hunger Task Force"], [], (3, 3, 3, 1)),
    ("outreach", "Door-to-door check-ins on {n} households after the weekend violence.", (10, 40), .2, [], [], (3, 1, 1, 5)),
    ("cleanup", "Neighborhood cleanup at {place} with {n} volunteers and residents.", (8, 30), .4, [], ["Milwaukee Public Schools"], (9, 0, 0, 1)),
    ("donation_distribution", "Handed out winter coats and hygiene kits to {n} people.", (20, 70), .4, [], ["Repairers of the Breach", "Salvation Army"], (9, 0, 0, 1)),
    ("pet_support", "Pet Division delivered food and vet vouchers to {n} pet owners.", (3, 15), 0, ["Wisconsin Humane Society", "MADACC"], [], (7, 1, 1, 1)),
]
TEMPLATE_W = [12, 10, 5, 5, 4, 6, 6, 4, 8, 5, 6, 4]
PLACES = {"Sherman Park": "Sherman Park", "Washington Park": "Washington Park", "Amani / Metcalfe Park": "Metcalfe Park",
          "Sunset Heights": "Sunset Playfield", "Lindsay Heights": "Alice's Garden", "Harambee": "Johnson's Park",
          "Midtown": "Moody Park", "Old North Milwaukee": "Lincoln Park"}
OUTCOMES = ["resolved", "in_progress", "needs_follow_up", "unknown"]
FOLLOW_UPS = ["Call back Friday to check on housing.", "Bring groceries Monday.", "Check that the family got their shelter placement.",
              "Follow up with 414 LIFE on mediation.", "Get the teen signed up for the summer jobs program.", "Check on the mom after court date."]


def make(day: date, rng: random.Random) -> dict:
    act, tmpl, (lo, hi), ys, refs, parts, ow = rng.choices(TEMPLATES, TEMPLATE_W)[0]
    hood = rng.choices(HOODS, HOOD_W)[0]
    n = rng.randint(lo, hi)
    outcome = rng.choices(OUTCOMES, ow)[0]
    if outcome == "needs_follow_up" and (date.today() - day).days > 28 and rng.random() < .7:
        outcome = "resolved"  # like real life: most follow-ups from last month have been done
    summary = tmpl.format(n=n, place=PLACES[hood])
    return {
        "activity_date": day.isoformat(), "worker": rng.choices(WORKERS, [30, 14, 12, 12, 10, 9, 8, 5])[0],
        "channel": rng.choices(["sms", "voice", "web"], [6, 3, 2])[0], "raw_text": summary,
        "activity_type": act, "summary": summary, "people_served": n, "youth_served": round(n * ys * rng.uniform(.7, 1)) if ys < 1 else n,
        "location": PLACES[hood], "neighborhood": hood,
        "referred_to": rng.sample(refs, k=rng.randint(1, len(refs))) if refs else [],
        "partners": rng.sample(parts, k=1) if parts and rng.random() < .5 else [],
        "outcome": outcome, "follow_up": rng.choice(FOLLOW_UPS) if outcome == "needs_follow_up" else "",
        "hours": rng.choice([1, 1.5, 2, 2, 3, 4]), "sensitive": act in ("crisis_response", "violence_prevention"),
        "extractor": "seed",
    }


def main(reset: bool) -> None:
    rng = random.Random(53216)
    if reset:
        db.clear_all()
    today, count = date.today(), 0
    for back in range(90, 0, -1):
        day = today - timedelta(days=back)
        busy = 1.6 if day.weekday() >= 4 else 1.0           # Fri-Sun are busier
        busy *= 1 + (90 - back) / 120                         # adoption grows over time
        for _ in range(rng.choices([0, 1, 2, 3], [3, 4, 2, 1])[0] if busy < 1.5 else rng.choices([1, 2, 3, 4], [3, 4, 2, 1])[0]):
            db.insert(make(day, rng))
            count += 1
    print(f"Seeded {count} synthetic entries into {db.DB_PATH}")


if __name__ == "__main__":
    main("--reset" in sys.argv)
