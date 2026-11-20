# ImpactText · Hacksgiving 2026 (Track 03: Data Collection & Impact Measurement)

**Text it, say it, or type it. We do the paperwork.**

ComForce MKE workers are out in the neighborhood, not at a desk. ImpactText lets them log what they did the way they'd tell a friend:

> *"fed 20 kids at sherman park, sent 1 mom to 414 life, need to call her back friday"*

It becomes a clean record (activity, people served, youth, neighborhood, referrals, follow-up), shows up on a dashboard, and rolls into a funder-ready impact report with one click. The records belong to ComForce: one SQLite file plus a one-click CSV export.

## Three ways to log (no app to install, no training)
| Channel | Who it's for | Cost |
|---|---|---|
| **Text a number** (Twilio SMS) | Anyone with any phone, even a flip phone with no data | about 1¢ per text |
| **Tap and talk** (web page, mic button) | Workers who'd rather talk than type. English or Spanish | free (browser speech recognition) |
| **Type it** (same web page) | Office or volunteer laptop | free |

SMS commands: just text what you did · `NAME Dee` · `UNDO` · `ME` (your totals this month) · `HELP`

## What's on the dashboard
People and youth served, referrals, hours, resolved rate · people served per week · follow-ups waiting (one tap to mark done) · by activity, neighborhood, referral partner and worker · recent entries · **Export CSV** · **Make grant report** (printable, Save as PDF).

## Run it (2 minutes)
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # add ANTHROPIC_API_KEY for AI; leave blank to use the free keyword extractor
python -m app.seed --reset      # ~155 SYNTHETIC demo entries over 90 days
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. Pages: `/` log · `/dashboard` · `/report` · `/phone` (SMS simulator).

## How it works
```
SMS (Twilio) ─┐
Voice (browser)┼─> POST /api/log or /sms ─> extract.py ─> SQLite (data/impact.db) ─> /dashboard, /report, CSV
Typed note ───┘         Claude + JSON schema (strict)
                        └─ falls back to free keyword rules if no key / offline
```
- `app/extract.py`: the prompt, the output schema, the keyword baseline, and normalization (e.g. "414 life" becomes `414 LIFE`, ZIP 53210 becomes Sherman Park)
- `app/impact.py`: the numbers and the report (AI-written from the numbers only, or a template)
- `app/main.py`: routes, including the Twilio webhook with signature check
- `app/db.py`: SQLite. `app/seed.py`: synthetic demo data. `eval/`: accuracy test

## Trust and privacy
- **No names of people helped are stored.** The prompt rewrites "Ms. Johnson" as "a mother". Entries about violence or injury are flagged `sensitive` and kept out of report stories.
- The AI only fills in fields. Every number in the report comes from the database, and the report prompt forbids inventing figures.
- **Never fails in the field.** If the AI is down, the note is still saved by the keyword extractor.
- `UNDO` only removes your own text from the last 24 hours.

## Measured
`python -m eval.run_eval` (free) or `python -m eval.run_eval --ai` scores 30 hand-labelled notes on 6 fields.

| Extractor | Field accuracy | Notes |
|---|---|---|
| Keyword baseline | **60.0%** | free, offline |
| Claude (`MODEL` in .env) | run `--ai` and fill in | |

`/api/stats` shows real latency, token use and cost per note while you demo.

## Cost to run (estimates; check current prices)
| Item | Monthly |
|---|---|
| AI note reading, about 300 notes/month on Claude Opus 5.5 (~750 input + ~250 output tokens each) | about $2.50 (about $0.10 on Claude Haiku 5.5: set `MODEL=claude-haiku-5-5` and the prices in .env) |
| Twilio number + texts | a few dollars, plus one-time US A2P 10DLC registration (see docs/SMS_SETUP.md) |
| Hosting (small Render/Railway/Fly instance with a disk) | $0–7 |

## Not done yet / next
Login or PIN on the dashboard · per-entry edit form · Spanish SMS replies · scheduled monthly report emailed to ComForce · Google Sheets sync.

*Demo data is synthetic. No real ComForce records are in this repo.*
