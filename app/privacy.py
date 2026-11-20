"""
Scrubs obvious personal details from a note BEFORE it is stored or sent anywhere.
Catches phone numbers, emails, street addresses and titled names ("Ms. Johnson").
It can't catch a bare first name ("took Tanya to court"); the AI summary rewrites those,
and the template report never quotes raw notes.
"""
import re

PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "[phone]"),
    (re.compile(r"\b\d{2,5}\s+(?:[NSEW]\.?\s+)?(?:\d{1,3}(?:st|nd|rd|th)|[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?\s+"
                r"(?:St|Street|Ave|Avenue|Blvd|Rd|Road|Dr|Drive|Ct|Court|Pl|Place|Ln|Lane|Pkwy|Way|Ter)\b\.?",
                re.IGNORECASE), "[address]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.?\s+(?!King\b)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "[name]"),
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
