"""
Keyword extractor v2: the free, offline fallback (and the baseline the AI is measured against).
Tuned only on eval/dev.jsonl; eval/test.jsonl is held out.

Ideas vs v1:
  - counts: add up every "<number> <people word>" group, treat "N of them kids" as a subset,
    count "a mom" / "the family" / "family of 5", skip ages, times and street numbers
  - activity: a few strong signals win outright (pets, de-escalation); otherwise the activity
    mentioned FIRST in the note is the main one
  - outcome: future plans -> needs_follow_up, waiting words -> in_progress, finished actions -> resolved
  - orgs: "w/ X", "met with X", "X truck" are partners; otherwise an org named in a note was a referral
"""
import re

from app.extract import KNOWN_ORGS, NEIGHBORHOODS

YOUTH = r"kids?|teens?|youth|students?|boys|girls|children|middle schoolers|young (?:men|guys|people)|niños|ninos|jovenes|jóvenes"
ADULT = (r"people|folks|adults|residents|neighbors|families|family|seniors|moms|mothers|owners|"
         r"volunteers|households|houses|homes|plates|meals|coats|backpacks|turkeys|kits|men|women|ladies|"
         r"mom|mother|dad|father|man|woman|senior|person|personas|familias|familia|vecinos")
# number, then up to 3 filler words, then a people/item word. Skips "27th", "17 yr old", "2am", "3 hours".
COUNT = re.compile(
    rf"(?<![\w.])(\d{{1,4}})(?!\s*(?:st|nd|rd|th|yr|year|am|pm|hours?|hrs?|days?|bags|blocks?|x)\b)"
    rf"\s+((?:[a-z'&-]+\s+){{0,3}}?)({YOUTH}|{ADULT})\b"
)
SUBSET = re.compile(rf"(\d{{1,4}})\s+of\s+them\s+(?:\w+\s+)?({YOUTH})")
SINGLE_ADULT = r"\b(?:a|one|1|the|this|her|his)\s+(?:\w+\s+)?(?:mom|mother|dad|father|man|woman|lady|grandma|grandmother|grandpa|senior|guy|dude|family|owner|neighbor|person|resident)\b|\b(?:dude|lady|grandma|guy)\b"
SINGLE_YOUTH = r"^(?:kid|teen|child)\b|\b(?:a|one|1|the|this|her|his)\s+(?:\w+\s+)?(?:kid|teen|child|boy|girl|son|daughter)\b|\bteen (?:girl|boy)\b|\b(?:1[0-7]|[1-9])\s*(?:yr|year)s?[\s-]*old\b"

ACTIVITY_WORDS = [
    ("pet_support", r"\b(?<!hot )(?:dogs?|cats?|pets?|pupp(?:y|ies)|kittens?|vet|strays?|pit mix)\b"),
    ("violence_prevention", r"retaliat|mediat|about to fight|talked (?:them|him|her|it) down|calmed (?:it|them|him|her) down|squash|de-?escalat|"
                            r"(?:about|on) (?:gun )?violence|gun violence|threatened|out of going to fight"),
    ("crisis_response", r"\bshooting\b|\bshot\b|\bstabbed\b|\bfire\b|incendio|\bstorm\b|tornado|\bflood|\bcrash|jumped\b|missing|hasn'?t been home|domestic|evacuat"),
    ("outreach", r"door ?knock|canvass|flyers?|check(?:ed)? (?:on|in)\b|community meeting|\bmet (?:w|with)\b|talked w(?:ith)? neighbors|walked"),
    ("cleanup", r"clean ?up|cleaned|trash|litter|glass"),
    ("food_meals", r"\bfed\b|\bfeed|\bfood\b|comida|meals?\b|grocer|lunch|dinner|breakfast|plates|cookout|turkeys|hot dogs|pantry"),
    ("donation_distribution", r"coats?\b|cloth|donat|supplies|backpacks?|diapers?|formula|hygiene|giveaway"),
    ("youth_program", r"basketball|\bball\b|mentor|tutor|youth ambassadors?|league|haircuts|teens|kids"),
    ("referral", r"\brefer|connected|hooked .* up|linked|got (?:him|her|them) (?:into|in to|to)\b|called .* for (?:her|him|them)|gave (?:her|him|them) .* number|sent \d* ?(?:\w+ )?to\b"),
]
# These decide the activity outright when present (they're rarely incidental).
STRONG = ["pet_support", "violence_prevention"]

FOLLOW_UP = r"need(?:s)? to get back|need to|gotta|got to|call (?:her|him|them|his|her) (?:back|mom)|call his mom|check (?:on|in|back)|follow up with|next week|tomorrow|(?:on |this )?(?:monday|tuesday|wednesday|thursday|friday)\b"
IN_PROGRESS = r"still|following up|court|coming tomorrow|interview|waiting|staying w|in progress|getting|working on"
DONE = r"\b(?:drop|no fights|fed|got|gave|handed|passed|served|helped|took|drove|dropped|ran|did|cleaned|calmed|talked|spoke|placed|rehomed|picked up|took them|ayudamos|dimos|went|connected|planned|gone)\b"
UNKNOWN_FIRST = r"door ?knock|flyers|canvass|talked w(?:ith)? neighbors|checking on folks"


_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_ONES |= {"a dozen": 12, "dozen": 12, "couple": 2, "a couple": 2, "a couple of": 2, "uno": 1, "dos": 2, "tres": 3,
          "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
          "doce": 12, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_NUMWORD = re.compile(
    r"\b(?:(?P<h>" + "|".join(k for k in _ONES if " " not in k) + r") hundred(?: and)?\s?)?"
    r"(?:(?P<t>" + "|".join(_TENS) + r")(?:[\s-](?P<o>" + "|".join(k for k in _ONES if " " not in k) + r"))?"
    r"|(?P<u>a couple of|a couple|a dozen|dozen|couple|" + "|".join(k for k in _ONES if " " not in k) + r"))?\b"
)


def _words_to_digits(t: str) -> str:
    """Dictation writes 'twenty two kids' / 'a dozen teens'; turn number words into digits.
    'one' and 'a' stay as words when they aren't counts ('one of them', 'one mom' is fine either way)."""
    def repl(m: re.Match) -> str:
        if not m.group(0).strip():
            return m.group(0)
        n = (_ONES[m.group("h")] * 100) if m.group("h") else 0
        if m.group("t"):
            n += _TENS[m.group("t")] + (_ONES[m.group("o")] if m.group("o") else 0)
        elif m.group("u"):
            n += _ONES[m.group("u")]
        trail = " " if m.group(0).endswith(" ") else ""
        return f"{n}{trail}"
    return _NUMWORD.sub(repl, t)


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'").replace("'", "")
    t = re.sub(r"\s+", " ", t)
    return _words_to_digits(t)


def count_people(t: str) -> tuple[int, int]:
    people = youth = 0
    subset_spans = [m.span() for m in SUBSET.finditer(t)]
    for m in SUBSET.finditer(t):
        youth += int(m.group(1))
    for m in COUNT.finditer(t):
        if any(a <= m.start() < b for a, b in subset_spans):
            continue
        n, noun = int(m.group(1)), m.group(3)
        if re.search(r"\b(?:groups?|crews?|sets?|cars?|blocks?|schools?|churches)\b", m.group(2)):
            continue  # "2 groups of young guys" is not 2 people
        if noun == "volunteers" and re.search(r"\b(?:with|w/?)\s+\d+\s+volunteers", t[max(0, m.start() - 6):m.end()]) and re.search(r"help|clear", t):
            continue  # "helped the family clear it w 4 volunteers": volunteers aren't the people served
        people += n
        if re.fullmatch(YOUTH, noun):
            youth += n
    fam = re.search(r"family of (\d+)", t)
    if fam:
        people += int(fam.group(1))
    if people:
        # "fed 12 kids, sent a mom to 414 LIFE": the mom is one more person helped
        people += len(re.findall(r"\b(?:sent|connected|referred|drove|took|gave|got) (?:a|one) (?:\w+ )?"
                                 r"(?:mom|mother|dad|father|man|woman|lady|grandma|senior|neighbor)\b", t))
    if people == 0:
        people = len(re.findall(SINGLE_ADULT, t)) and 1
        y1 = 1 if re.search(SINGLE_YOUTH, t) else 0
        youth = max(youth, y1)
        if people and y1 and re.search(r"(?:mom|mother|dad|father) (?:met us|came|was there)|w(?:ith)? his mom|w(?:ith)? her mom", t):
            people = 1 + y1  # the youth and their parent
        else:
            people = max(people, y1)
    return max(people, youth), youth


def activity(t: str) -> str:
    for name in STRONG:
        pat = dict(ACTIVITY_WORDS)[name]
        if re.search(pat, t):
            return name
    first = {}
    for name, pat in ACTIVITY_WORDS:
        m = re.search(pat, t)
        if m:
            first[name] = m.start()
    if not first:
        return "other"
    # a crisis anywhere outranks routine work, unless the note is clearly outreach about it ("after the shootings")
    if "crisis_response" in first and not ("outreach" in first and first["outreach"] < first["crisis_response"]):
        return "crisis_response"
    return min(first, key=first.get)


def outcome(t: str, act: str) -> str:
    if re.search(FOLLOW_UP, t) and not re.search(r"coming tomorrow|interview (?:monday|tuesday|wednesday|thursday|friday)|court is", t):
        return "needs_follow_up"
    if re.search(IN_PROGRESS, t):
        return "in_progress"
    if re.search(UNKNOWN_FIRST, t):
        return "unknown"
    if re.search(DONE, t) or re.search(r"\b\w+ed\b", t):
        return "resolved"
    return "unknown"


def orgs(t: str) -> tuple[list[str], list[str]]:
    found = []  # (position, name)
    for key, name in KNOWN_ORGS.items():
        k = key.replace("'", "")
        tail = r"(?!\w)" if len(k) <= 4 else ""
        for m in re.finditer(rf"(?<!\w){re.escape(k)}{tail}", t):
            if name not in [n for _, n in found]:
                found.append((m.start(), name))
    if re.search(r"employ milwaukee", t):
        found.append((t.index("employ milwaukee"), "Employ Milwaukee"))
    found.sort()
    referred, partners, last = [], [], None
    for pos, name in found:
        before, after = t[max(0, pos - 12):pos], t[pos:pos + 40]
        if name == "Milwaukee Police":
            kind = "partner"
        elif re.search(r"(?:and|&|,)\s*$", before) and last:
            kind = last
        elif re.search(r"(?:\bw/?|with|met w|at the|from)\s*$", before) or re.search(r"^[\w\s&.]+?\btruck\b", after):
            kind = "partner"
        else:
            kind = "referral"
        (referred if kind == "referral" else partners).append(name)
        last = kind
    return referred, partners


def neighborhood(t: str) -> str:
    hood = next((h for h in NEIGHBORHOODS if h.lower() in t), "")
    if not hood and "parque sherman" in t:
        hood = "Sherman Park"
    if not hood:
        z = re.search(r"\b(532\d\d)\b", t)
        hood = z.group(1) if z else ""
    return hood


def extract(text: str) -> dict:
    t = _norm(text)
    act = activity(t)
    people, youth = count_people(t)
    referred, partners = orgs(t)
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", t)
    follow = re.search(rf"[^.,;]*(?:{FOLLOW_UP})[^.,;]*", t)
    return {
        "activity_type": act,
        "summary": text.strip()[:140],
        "people_served": people,
        "youth_served": youth,
        "location": "",
        "neighborhood": neighborhood(t),
        "referred_to": referred,
        "partners": partners,
        "outcome": outcome(t, act),
        "follow_up": follow.group(0).strip().capitalize() if follow else "",
        "hours": float(hours.group(1)) if hours else 0,
        "days_ago": 1 if "yesterday" in t else 0,
        "sensitive": bool(re.search(r"shoot|\bshot\b|gun|stab|abuse|police|arrest|overdose|domestic|jumped|threat", t)),
        "extractor": "keywords",
    }
