# Features in detail

The full reference behind the [README](../README.md): every page, command and safeguard.

## Logging: text, voice or web
| Channel | Who it's for | Cost |
|---|---|---|
| **Text a number** (Twilio SMS) | Anyone with any phone, even a flip phone with no data | about 1¢ per text |
| **Tap and talk** (web page, mic button) | Workers who'd rather talk than type. English or Spanish | free (browser speech recognition) |
| **Type it** (same web page) | Office or volunteer laptop | free |

SMS commands: just text what you did · `NAME Dee` · `UNDO` · `ME` (your totals this month) · `LINK 123456` (code from My notes: your texts then show there too) · `HELP`

**En español:** texts written in Spanish get Spanish replies. Commands: `AYUDA` · `NOMBRE Dee` · `DESHACER` · `YO` · `ENLAZAR 123456`. Text `ESPAÑOL` or `ENGLISH` to pick a language for good.

## What's on the dashboard
People and youth served, referrals, hours, resolved rate · people served per week · all open follow-ups, oldest due first (one tap to mark done; independent of the chart period) · by activity, neighborhood, referral partner and worker · recent entries · **Export CSV** · **Make grant report** (printable, Save as PDF).

**Monthly report by email:** set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and `REPORT_TO` (any mail account with SMTP, e.g. Gmail with an app password, so it's free) and in the first week of each month the server emails last month's report to ComForce, once. Admins can also press **✉ Email last month's report** on `/report`, or run `python -m app.monthly_report [--month 2026-10] [--send]`. The email has the same report as `/report`. No raw notes or phone numbers go out.

## Two sides: regular people and admins
| | Who | Pages | Sign-in |
|---|---|---|---|
| **Regular person** | Workers, volunteers | `/` log (type or talk) · `/phone` text simulator · `/me` My notes | **Optional.** Anyone can log and undo what they just saved. An account (name + PIN, made at `/login`) adds My notes: their own totals and follow-ups, and fixing or removing their own notes from any phone. |
| **Admin** | ComForce staff | `/dashboard` · `/report` · `/connect` (calendars, sheets, phone setup) · `/team` (accounts) · `/console` (testing) · CSV export | **Required, plus two-step sign-in.** Name `admin` + `ADMIN_PASSWORD` from `.env`, or any account an admin has promoted on `/team`, then a 6-digit code from an authenticator app. |

**Two-step sign-in** (`/security`):
- **Required for admins.** The first time an admin signs in, they scan a QR code with Google Authenticator, Microsoft Authenticator, Authy or similar. Until then, the only page they can open is that setup page.
- **Optional for everyone else.** Turning it on also signs out that person's other devices.
- Each code works once. Setup gives 10 one-time backup codes for a lost phone.
- Lost phone: an admin presses **Reset 2-step** on `/team`. For the built-in admin, run `python -m app.mfa reset admin` on the server.
- Standard TOTP, no SMS, no outside service, no cost.

- Texting (`/sms`) needs no account; texts are tied to the sender's phone.
- If `ADMIN_PASSWORD` isn't set, the server makes one up, saves it in `data/admin_password.txt` and prints it at startup, so the admin side is never left open by accident.
- Signed-in sessions are a signed cookie. Changing someone's role or removing their account on `/team` takes effect on their next click. PINs are stored as PBKDF2 hashes, and 5 wrong tries lock that name for 10 minutes.
- Tests: `python -m eval.auth_test` checks every route is either public on purpose or admin-only. Two separate reviews (a code read, and 361 attack requests against a running server) found no way for a regular person to reach admin data.

## On phones and in the apps people already use (`/connect`)
- **Phone app:** add ImpactText to the home screen (iPhone: Share → Add to Home Screen; Android: Install app). Notes saved with no signal stay on the phone and send themselves later, keeping the day they happened.
- **Calendars:** open follow-ups as a calendar that **Google Calendar, Outlook and Apple Calendar** can subscribe to, per worker or for everyone. The due day is read from the note ("call her back Friday"). Sensitive cases show only a number. Each follow-up on the dashboard also has a 📅 button that adds it to Google, Outlook or Apple.
- **Spreadsheets:** a live data link for **Google Sheets** (`=IMPORTDATA`), **Excel** (Data → From Web) and Numbers.
- **Siri** ("Hey Siri, log impact") via the Shortcuts app, and **Android share**: share text from any app to ImpactText.
- No developer accounts or OAuth: it all uses open formats (iCalendar, CSV, web app manifest).
- Phones and Google/Microsoft must be able to reach the server: run with `--host 0.0.0.0` for phones on the same Wi-Fi, and use an https address (a tunnel or hosting, then set `PUBLIC_URL`) to install the app and use the calendar and spreadsheet links.
- The calendar and spreadsheet links skip the admin sign-in (those apps can't sign in) and are protected by a secret token instead. **Anyone with a link can read it**; "Make new links" on `/connect` revokes the old ones. Tests: `python -m eval.integrations_test`.

## Trust and privacy
- **Personal details are scrubbed before anything is stored or sent to the AI:** phone numbers, emails, street addresses and titled names ("Ms. Johnson"). The AI summary also rewrites any other names ("a mother"). Limit: a bare first name in the raw note isn't caught offline. Entries about violence or injury are flagged `sensitive` and kept out of report stories, and the report only quotes AI-written summaries.
- A text sent twice (bad signal, double tap) is only counted once.
- The AI only fills in fields. Every number in the report comes from the database, and the report prompt forbids inventing figures.
- **Never fails in the field.** If the AI is down, the note is still saved by the keyword extractor.
- `UNDO` only removes your own text from the last 24 hours. Texts are tied to the sender's phone, so two workers who pick the same `NAME` can't undo or merge each other's entries. Phone numbers only ever leave the server as the last 4 digits.
- **Only admins see everyone's data**, and admins need two-step sign-in. A regular person sees only their own notes, and only if they make an account. Set your own `ADMIN_PASSWORD` in `.env` before real data goes in.
- **Texts:** once online, `/sms` only accepts texts signed by Twilio. The exception is the `/phone` simulator's fictional 555-01xx numbers. Nobody can pretend to be a worker's phone to undo their text or read their totals.
- Someone retyping another person's note gets "already saved", not the note or a way to undo it.
- A note resent by a phone (lost reply, offline queue) carries the same random id, so it counts once even hours later.

## Cost to run (estimates; check current prices)
| Item | Monthly |
|---|---|
| AI note reading, about 300 notes/month on Claude Opus 5.5 (~750 input + ~250 output tokens each) | about $2.50 (about $0.10 on Claude Haiku 5.5: set `MODEL=claude-haiku-5-5` and the prices in .env) |
| Twilio number + texts | a few dollars, plus one-time US A2P 10DLC registration (see [SMS_SETUP.md](SMS_SETUP.md)) |
| Hosting (small Render/Railway/Fly instance with a disk) | $0–7 |
