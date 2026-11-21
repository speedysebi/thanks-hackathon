# 4-minute pitch: ImpactText

Audience: ComForce leadership (non-technical) + MSOE AI faculty.

**0:00 Problem (30s).** ComForce does crisis response, violence prevention and youth work, mostly through a few people on their phones at all hours. Funders such as the $500k Coalition to Advance Public Safety grant judge programs *on data*. Writing that data down is the job nobody has time for. Track 03 asks to "simplify how community workers document their activities, maintain ownership of their records, measure outcomes, and communicate their impact."

**0:30 Live demo (2 min).**
1. Phone on screen (`/phone`): text *"shooting near washington park, sat w the family 3 hrs, connected them to 414 life, check on them friday"* → instant confirmation reply.
2. Laptop (`/`): tap the mic and *say* a note. The fields fill in.
3. `/dashboard`: both entries are there, and the follow-up is in "waiting". Tap Done.
4. "Make grant report" → printable report → Save as PDF. *"This took 5 seconds. Writing it by hand takes an afternoon."*

**2:30 Numbers (30s).** Fill in from `python -m eval.run_eval --ai` and `/api/stats`:
- AI field accuracy __% vs 91% for the free keyword fallback, on 30 held-out labelled notes (keywords v1 was 65%)
- __ seconds per note, about $__ per note → about $__/month at 300 notes
- 3 ways to log, 0 apps to install, works on a flip phone

**3:00 Why trust it (30s).** Phone numbers, addresses and names like "Ms. Johnson" are scrubbed before anything is stored, and the AI summary removes other names. Double-sent texts count once. The AI fills fields; every report number comes from the database. Sensitive entries stay out of stories. If the AI goes down, notes still save. ComForce owns the data: one file + CSV export.

**3:30 Adopt it Monday (30s).** One small server + one phone number, about $10/month. Nothing to train: "just text what you did." Workers don't need an account; admins sign in to see everything. Next: Spanish replies, monthly report emailed automatically.

Backup: screen-record the demo Saturday night.
