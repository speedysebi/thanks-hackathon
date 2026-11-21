# Launch checklist: what a person has to do (Sat Nov 22, due 11:00 AM CT)

The code is ready. Everything below needs a **person, an account, or a credit card**, and none of it can be done from the repo.
Items are in the order to do them. ⛔ = blocks the demo. ⚠️ = blocks real use by ComForce, but not the demo.

## 1. Decide who owns things (5 min, do first) ⛔
Write one name next to each:
| Thing | Who owns it | Why it matters |
|---|---|---|
| Hosting account (Render) | | Holds the database. Whoever owns it can read every note. |
| Domain (optional) | | Renews yearly; if it lapses the app's address dies. |
| Twilio account | | Pays for texts; the number belongs to this account. |
| Anthropic API key | | Pays for AI reading of notes. Set a monthly spend limit. |
| Built-in `admin` password + its authenticator phone | | Full access to everything. |

Best long-term answer for all of them: **ComForce's own email**, with a team member added. For the weekend, a team member's account works. Move it to ComForce afterwards.

## 2. Merge PR #5 ⛔
It includes #3 and #4. Then everyone runs `git pull` and `pip install -r requirements.txt` (adds `qrcode`).

## 3. Hosting (20 min) ⛔
The repo now has a `Dockerfile` and `render.yaml`.
1. render.com → sign up (owner from step 1) → **New → Blueprint** → pick `speedysebi/thanks-hackathon`.
2. It asks for `ADMIN_PASSWORD` (make a long one, 16+ characters, and put it in a password manager), `PUBLIC_URL` (fill in after the first deploy), and optionally `ANTHROPIC_API_KEY` and `TWILIO_AUTH_TOKEN`.
3. Plan: **Starter (about $7/month) + 1 GB disk (about $0.25/month)**. Check current prices. The **free plan won't work**: it has no disk, so every deploy or restart deletes all notes and accounts.
4. After it's up, copy the `https://impacttext-xxxx.onrender.com` address into `PUBLIC_URL` and redeploy.
5. Open `/api/health` and check it says `"ok": true`.
6. **Turn on automatic deploys through CI.** In Render, go to the service → Settings → **Deploy Hook** and copy the URL. In GitHub, go to repo → Settings → Secrets and variables → Actions → **New repository secret**, name it `RENDER_DEPLOY_HOOK_URL`, and paste. From then on, every push to `main` deploys only if all CI jobs pass. `render.yaml` turns off Render's own auto-deploy so a broken commit never goes live. Without the secret, CI still runs and just says "Not deployed".

CI (GitHub Actions) builds this exact Docker image on every push. It starts the image with a disk, signs in as admin with two-step, saves a note, restarts, and checks the note is still there. If a Render build ever fails anyway, the fallback is a native Python service:
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- Env: `IMPACT_DB=/data/impact.db`, with the disk mounted at `/data`

**Keep it to one instance.** SQLite and the sign-in rate limits live on one machine, so don't turn on autoscaling.

Emergency fallback for the demo: run it on a laptop with `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. It's free, but it dies if the laptop sleeps.

## 4. Domain (optional, 15 min plus DNS wait)
**You don't need one for the demo.** The `.onrender.com` address already has HTTPS, which is what phones need to install the app and what calendar and Sheets links need.
If you want `impact.comforcemke.org` (or a new domain, about $10–15/year at Cloudflare or Namecheap):
- Whoever controls ComForce's DNS adds a CNAME to the Render address.
- Then add the custom domain in Render and update `PUBLIC_URL`.
- DNS can take minutes to hours, so don't start this at 10 AM.

## 5. Admin setup (10 min, on the live site) ⛔
1. Go to `/login`, name `admin`, the `ADMIN_PASSWORD`.
2. You're sent to **Two-step sign-in**. On the admin's phone, open Google Authenticator or Microsoft Authenticator (free), tap +, and scan. Type the code.
3. **Save the 10 backup codes** in the password manager. Losing both the phone and the codes means running `python -m app.mfa reset admin` on the server (Render → Shell).
4. On **/team**, add a named account for each real ComForce admin (role Admin). Each one gets a QR code at their first sign-in. Use the built-in `admin` only as a break-glass account.
5. On **/connect**, press **Make new links** once, so no calendar or spreadsheet link from testing still works.
6. Decide: does anyone else besides admins get an account? Workers don't need one to log notes.

## 6. Texting (Twilio) ⚠️ for real use, optional for the demo
- **Demo:** use the built-in `/phone` simulator. It needs nothing.
- **Real texts tomorrow:** a Twilio **trial** account works, but it can only text phone numbers you verify first, and replies start with "Sent from your Twilio trial account". Buy a 414 number (about $1–2/month). Set the webhook to `https://<your address>/sms` (HTTP POST). Set `TWILIO_AUTH_TOKEN` in Render.
- **Real use by ComForce:** US carriers require **A2P 10DLC registration** (brand + campaign, small one-time and monthly fees). It takes **days to weeks**, so it **won't be done by tomorrow**. Start it now if ComForce wants texting soon. Toll-free verification is the alternative. Details: `docs/SMS_SETUP.md`.
- New since this PR: once the app is online, any text without a valid Twilio signature is refused, unless it comes from the simulator's fictional 555-01xx numbers. So set the Twilio token before testing real texts.

## 7. AI key (10 min) ⚠️ optional
- Without a key, the free keyword reader gets 91% of fields right on held-out notes.
- With a key: console.anthropic.com → create a key → **set a monthly spend limit** → put it in Render as `ANTHROPIC_API_KEY`.
- Then run `python -m eval.run_eval --ai` once locally to get the real AI accuracy for the pitch. It's about 60 notes, a few cents.

## 8. Before real ComForce data goes in ⚠️
- **The live database has demo data if you seeded it.** `python -m app.seed --reset` wipes everything, so run it only on purpose. Real use should start from an empty database.
- Get ComForce's OK on what gets stored: notes are scrubbed of phone numbers, emails, addresses and titled names, but a bare first name can slip through. Phone numbers from texts are stored, and are only shown as the last 4 digits.
- Backups: Render takes daily disk snapshots on paid plans. Also do a **CSV export** from the dashboard each week.

## 9. Demo-day checks (30 min, on real phones) ⛔
We tested in emulated phones (iPhone, Android, desktop: 43 checks, 90 page views, light and dark). **We haven't tested on a real phone.** Before 11:
- [ ] iPhone (Safari): log a note, **use the mic button** (can't be tested headless), Add to Home Screen.
- [ ] Android (Chrome): same, plus Install app.
- [ ] Airplane mode: save a note, turn the signal back on, and check it's sent once.
- [ ] Admin sign-in with the real authenticator phone, from a second device.
- [ ] Dashboard → Make grant report → Save as PDF.
- [ ] If Twilio is set up: text the number from a verified phone, then text `UNDO`.

## Known gaps (fine to say out loud at judging)
- Texting isn't linked to accounts yet: texts count per phone number and don't show on My notes.
- One server, one SQLite file. Fine for one nonprofit's team; not built to scale out.
- The first admin to sign in sets up 2FA by scanning a QR code. Whoever knows the password first can enroll their phone, so set the password privately and sign in right away. Or pre-load `ADMIN_TOTP_SECRET`.
