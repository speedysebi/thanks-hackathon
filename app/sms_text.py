"""Text-message replies in English and Spanish.

Which language a reply uses:
  1. the phone's choice, if it ever texted ESPANOL / ENGLISH (kept in the sms_lang table);
  2. otherwise the language of this message: a Spanish command (AYUDA, DESHACER, YO, NOMBRE...) or a note
     that reads as Spanish gets a Spanish reply;
  3. otherwise English.

Every reply must stay plain GSM-7 so it costs one text (eval/sms_cost.py checks). GSM-7 has é ñ ü ¿ ¡ but
not á í ó ú, so those are written without the accent at send time (gsm()). SMS readers are used to that.
"""
import re
import unicodedata

from app import db
from app.impact import LABELS

LABELS_ES = {
    "crisis_response": "Respuesta a crisis", "violence_prevention": "Prevención de violencia",
    "youth_program": "Programa juvenil", "food_meals": "Comida", "referral": "Referido",
    "outreach": "Alcance comunitario", "cleanup": "Limpieza", "pet_support": "Ayuda con mascotas",
    "donation_distribution": "Donaciones", "other": "Otro",
}

TEXT = {
    "en": {
        "help": "Text what you did, like: fed 20 kids at Sherman Park, sent a mom to 414 LIFE. "
                "Also: NAME + your name, UNDO, ME (your totals), LINK + code from My notes",
        "named": "Thanks {name}! Just text what you did any time.",
        "removed": "Removed #{id}.",
        "nothing": "Nothing to undo.",
        "me": "This month: {n} activities, {people} people helped. Thank you!",
        "dup": "Already got that one (#{id}). Nothing new was added.",
        "undo_hint": " UNDO to remove.",
        "name_hint": " Text NAME + your name for credit.",
        "logged": "Logged #{id}: {bits}.",
        "people": "{n} {word}", "person": "person", "persons": "people", "youth": " ({y} youth)",
        "ref": "ref {orgs}", "follow": "follow-up flagged",
        "link_many": "Too many wrong codes. Wait 10 minutes and try again.",
        "link_bad": "That code didn't work. Get a new one on My notes and text LINK + the code.",
        "linked": "Linked to {name}. Your texts now show on My notes.",
        "lang_set": "OK, replies in English from now on. Text ESPANOL for Spanish.",
    },
    "es": {
        "help": "Escriba lo que hizo, como: dimos comida a 20 niños en Sherman Park. "
                "También: NOMBRE + su nombre, DESHACER, YO (sus totales), ENGLISH",
        "named": "¡Gracias {name}! Mande lo que hizo cuando quiera.",
        "removed": "Borrado #{id}.",
        "nothing": "No hay nada que deshacer.",
        "me": "Este mes: {n} actividades, {people} personas ayudadas. ¡Gracias!",
        "dup": "Ya tenemos esa nota (#{id}). Cuenta una sola vez.",
        "undo_hint": " DESHACER para borrar.",
        "name_hint": " Mande NOMBRE + su nombre para el crédito.",
        "logged": "Anotado #{id}: {bits}.",
        "people": "{n} {word}", "person": "persona", "persons": "personas", "youth": " ({y} jóvenes)",
        "ref": "ref {orgs}", "follow": "seguimiento pendiente",
        "link_many": "Demasiados códigos equivocados. Espere 10 minutos y vuelva a intentar.",
        "link_bad": "Ese código no sirvió. Pida uno nuevo en My notes y mande ENLAZAR + el código.",
        "linked": "Enlazado a {name}. Sus textos ahora salen en My notes.",
        "lang_set": "Listo, respuestas en español. Mande ENGLISH para inglés.",
    },
}

# command words -> (command, language of the word)
COMMANDS = {
    "HELP": ("help", "en"), "?": ("help", None), "INFO": ("help", None), "AYUDA": ("help", "es"),
    "UNDO": ("undo", "en"), "DESHACER": ("undo", "es"), "BORRAR": ("undo", "es"),
    "ME": ("me", "en"), "TOTAL": ("me", None), "STATS": ("me", "en"), "YO": ("me", "es"), "TOTALES": ("me", "es"),
    "ESPANOL": ("lang", "es"), "ESPAÑOL": ("lang", "es"), "SPANISH": ("lang", "es"),
    "ENGLISH": ("lang", "en"), "INGLES": ("lang", "en"), "INGLÉS": ("lang", "en"),
}

# short, common words: only a pile of them together reads as Spanish ("Sherman Park" alone doesn't)
_ES = set("""el la los las un una unos unas de del con para por que y en a al le les se su sus hoy ayer mañana
manana dimos di dio ayudé ayude ayudamos llevé lleve llevamos repartimos entregamos comida niños ninos familias
familia mamá mama papá papa señora señor vecinos jóvenes jovenes personas parque escuela llamar llamarla
llamarlo viernes lunes martes miércoles miercoles jueves sábado sabado domingo necesita necesitan""".split())
_EN = set("""the and with for to of at in on a an we i my our fed gave helped kids families people today
yesterday call back need needs mom dad""".split())
_WORD = re.compile(r"[a-záéíóúñü]+")


def looks_spanish(text: str) -> bool:
    words = _WORD.findall(text.lower())
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    return es >= 2 and es > en


def gsm(s: str) -> str:
    """á í ó ú (and capitals) -> a i o u; keeps é ñ ü ¿ ¡, which plain texts can carry."""
    keep = set("éÉñÑüÜ¿¡")
    return "".join(c if c in keep else unicodedata.normalize("NFKD", c)[0] if ord(c) > 127 and c.isalpha() else c
                   for c in s)


def t(lang: str, key: str, **kw) -> str:
    return gsm(TEXT.get(lang, TEXT["en"])[key].format(**kw))


def lang_for(phone: str, word_lang: str | None = None, body: str = "") -> str:
    saved = db.sms_lang(phone)
    if saved:
        return saved
    if word_lang:
        return word_lang
    return "es" if body and looks_spanish(body) else "en"


def confirmation(entry: dict, lang: str) -> str:
    """Same facts as extract.confirmation, in the reply language."""
    labels = LABELS_ES if lang == "es" else LABELS
    bits = [labels.get(entry["activity_type"], entry["activity_type"])]
    n, y = entry["people_served"], entry["youth_served"]
    if n:
        word = TEXT[lang]["person" if n == 1 else "persons"]
        bits.append(TEXT[lang]["people"].format(n=n, word=word) + (TEXT[lang]["youth"].format(y=y) if y else ""))
    if entry["neighborhood"]:
        bits.append(entry["neighborhood"])
    if entry["referred_to"]:
        bits.append(TEXT[lang]["ref"].format(orgs=", ".join(entry["referred_to"])))
    if entry["outcome"] == "needs_follow_up":
        bits.append(TEXT[lang]["follow"])
    return t(lang, "logged", id=entry["id"], bits=", ".join(bits))
