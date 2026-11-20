"""
Scrubs obvious personal details from a note BEFORE it is stored or sent anywhere.
Catches phone numbers, emails, street addresses and titled names ("Ms. Johnson").
It can't catch a bare first name ("took Tanya to court"); the AI summary rewrites those,
and the template report never quotes raw notes.

Careful: the note's headcounts must survive. "took 12 teens to court" is NOT an address,
so a number followed by a people word (or a filler word) never starts an address.
"""
import re

# A number followed by one of these is a count or a phrase, not a house number.
_NOT_HOUSE_NUMBER = (
    r"(?!\s+(?:kids?|teens?|youth|students?|boys|girls|children|people|folks|adults|residents|neighbors|"
    r"families|family|seniors|moms?|mothers?|dads?|fathers?|men|women|ladies|households|homes|houses|"
    r"plates|meals|volunteers|owners|coats|backpacks|bags|blocks|of|to|at|the|and|with|in|on|for|from)\b)"
)
_DIR = r"(?:[NSEW]\.?\s+|(?:North|South|East|West)\s+)?"
# Suffixes that are almost never ordinary words: safe to match in lowercase ("2411 n 35th st").
_CLEAR_SUFFIX = r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Ln|Lane|Pkwy|Parkway|Ter|Terrace)"
# Suffixes that are also everyday words ("court", "way", "dr", "place"): need a Capitalized street name.
_WORD_SUFFIX = r"(?:Ct|Court|Dr|Drive|Pl|Place|Way)"

PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "[phone]"),
    # "2411 N 35th St", "2411 n 35th st", "800 W Center Street", "3000 W Lisbon Ave"
    (re.compile(rf"\b\d{{2,5}}{_NOT_HOUSE_NUMBER}\s+{_DIR}(?:\d{{1,3}}(?:st|nd|rd|th)|[A-Za-z]+)(?:\s+[A-Za-z]+)?\s+{_CLEAR_SUFFIX}\b\.?",
                re.IGNORECASE), "[address]"),
    # "4500 W Spring Ct", "1200 Hampton Way": case-sensitive street name so "12 teens to court" is left alone
    (re.compile(rf"\b\d{{2,5}}{_NOT_HOUSE_NUMBER}\s+{_DIR}(?:\d{{1,3}}(?:st|nd|rd|th)|[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?\s+{_WORD_SUFFIX}\b\.?"),
     "[address]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.?\s+(?!King\b|Martin\b)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "[name]"),
    # lowercase, as typed on a phone: "mrs smith needs..." / "miss jones at ..."
    (re.compile(r"\b(?:ms|mrs|mr)\.?\s+(?!(?:at|on|from|needs)\b)[a-z]+"
                r"(?:\s+(?!(?:at|on|from|needs|said|says|who|and|to|for|with|w|is|was|got)\b)[a-z]+)?"
                r"(?=\s+(?:at|on|from|needs|said|says|who|and|to|for|with|w|is|was|got)\b|[,.]|$)",
                re.IGNORECASE), "[name]"),
    # "miss" is also a verb ("miss the bus"), so only when a name-like word follows and then a clause
    (re.compile(r"\bmiss\s+(?!the\b|a\b|out\b|it\b|him\b|her\b|them\b|you\b)[a-z]+(?=\s+(?:at|on|from|needs|said|says|who)\b|,)",
                re.IGNORECASE), "[name]"),
]


def scrub(text: str) -> str:
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text
