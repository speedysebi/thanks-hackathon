# ImpactText website setup and launch checklist

Choose the setup that matches what you need.

| Goal | Where to start |
| --- | --- |
| Try ImpactText on this computer | Follow [Try it in the README](../README.md#try-it-about-2-minutes) or the [isolated database setup](DEVELOPMENT.md#run-locally). Open http://127.0.0.1:8000 and use `/phone` to simulate SMS. |
| Share a temporary demo URL | Follow [Temporary hosted demo](#temporary-hosted-demo). Use synthetic data. |
| Launch for ComForce | Follow the numbered steps below. Use a persistent database and verify the service before entering real notes. |

## 1. Assign service owners
Write one name next to each:
| Thing | Who owns it | Why it matters |
|---|---|---|
| Hosting account (Render) | | Holds the database. Whoever owns it can read every note. |
| GitHub repository | | Controls the branch and deploy hook secret. |
| Domain (optional) | | Renews yearly; if it lapses the app's address dies. |
| Twilio account | | Pays for texts; the number belongs to this account. |
| Anthropic API key | | Pays for AI reading of notes. Set a monthly spend limit. |
| Built-in `admin` password + its authenticator phone | | Full access to everything. |

Use organization-owned accounts for the live service and grant team members the access they need.

## 2. Choose the revision
Use the repository's `main` branch for the hosted service. If you want to test locally first, follow the [contributor guide](DEVELOPMENT.md).

## 3. Create the production service
The repo now has a `Dockerfile` and `render.yaml`.
1. render.com → sign up (owner from step 1) → **New → Blueprint** → pick `speedysebi/thanks-hackathon`.
2. Choose `main` and the default Blueprint file, [`render.yaml`](../render.yaml). Set a strong `ADMIN_PASSWORD` and save it in the organization's password manager. The blueprint generates `SESSION_SECRET`. Leave AI and Twilio settings blank until you need them.
3. Confirm the service uses a paid Starter instance and a 1 GB persistent disk mounted at `/data`. The container stores its SQLite database at `/data/impact.db`. [Render persistent disks](https://render.com/docs/disks).
4. After the initial deployment, set `PUBLIC_URL` to the service's HTTPS origin, such as `https://impacttext-example.onrender.com`, with no path. Save and deploy the updated setting.
5. Open `/` and confirm the log page loads. Open `/api/health` and check for `"ok": true`.
6. **Turn on automatic deploys through CI.** In Render, go to the service → Settings → **Deploy Hook** and copy the URL. In GitHub, go to repo → Settings → Secrets and variables → Actions → **New repository secret**, name it `RENDER_DEPLOY_HOOK_URL`, and paste. The production blueprint disables Render auto-deploy. GitHub Actions calls the hook after a push to `main` passes all three check jobs. A missing secret produces a “Not deployed” notice in a successful job. After a hook call succeeds, verify the commit and rollout status in Render, then check `/api/health`.

On PRs and pushes to `main`, CI builds this exact Docker image after the test job passes. It starts the image with a disk, signs in as admin with two-step, saves a note, restarts, and checks the note is still there. If a Render build fails, the fallback is a native Python service:
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- Env: `IMPACT_DB=/data/impact.db`, with the disk mounted at `/data`

**Keep it to one instance.** SQLite and the sign-in rate limits live on one machine, so don't turn on autoscaling.

Manual dispatch runs the CI checks. Use a push or merge to `main` to exercise the production hook. Verify the expected revision in Render and repeat the health check. [Render deploy hooks](https://render.com/docs/deploy-hooks) · [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

## 4. Connect a custom domain (optional)
**You don't need one for the demo.** The `.onrender.com` address already has HTTPS, which is what phones need to install the app and what calendar and Sheets links need.
If you want a custom domain such as `impact.comforcemke.org`:
- Whoever controls ComForce's DNS adds a CNAME to the Render address.
- Then add the custom domain in Render and update `PUBLIC_URL`.
- If SMS is enabled, update the Twilio webhook to match the new HTTPS origin.

## 5. Set up administrator access
1. Go to `/login`, name `admin`, the `ADMIN_PASSWORD`.
2. You're sent to **Two-step sign-in**. On the admin's phone, open Google Authenticator or Microsoft Authenticator (free), tap +, and scan. Type the code.
3. **Save the 10 backup codes** in the password manager. Losing both the phone and the codes means running `python -m app.mfa reset admin` on the server (Render → Shell).
4. On **/team**, add a named account for each real ComForce admin (role Admin). Each one gets a QR code at their first sign-in. Use the built-in `admin` only as a break-glass account.
5. On **/connect**, press **Make new links** once, so no calendar or spreadsheet link from testing still works.
6. Decide: does anyone else besides admins get an account? Workers don't need one to log notes.

## 6. Connect a Twilio number (optional)
- **Demo:** use the built-in `/phone` simulator. It needs nothing.
- Follow the [Twilio setup guide](SMS_SETUP.md) to get a number and complete the applicable carrier registration. Set `TWILIO_AUTH_TOKEN` and `SMS_NUMBER` in Render, and configure the number's webhook to send POST requests to `https://<your address>/sms`. Use `HELP` and a synthetic note to verify replies.

## 7. Enable AI extraction (optional)
- Without a key, the app uses keyword extraction. The [evaluation results](EXPERIMENTS.md) describe how it performed on synthetic notes.
- With a key: console.anthropic.com → create a key → **set a monthly spend limit** → put it in Render as `ANTHROPIC_API_KEY`.
- Test extraction with a synthetic note. You can also run `python -m eval.run_eval --ai` locally with your key.

## 7b. Configure monthly report email (optional)
- Pick the account it's sent from (best: a ComForce mailbox). For Gmail, turn on 2-Step Verification, then Google Account → Security → **App passwords**, and make one for "ImpactText".
- In Render, set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<that address>`, `SMTP_PASSWORD=<the app password>`, and `REPORT_TO=<who gets it, comma-separated>`.
- Set `REPORT_TO` to a test mailbox first. On `/report`, press **Email last month's report** and confirm it arrives before switching to the intended recipients.
- Automatic attempts retry hourly during the first week. If SMTP accepts only some recipients,
  those addresses are saved in SQLite and retries send only to the remaining addresses, including
  after a restart. The month is marked complete only after every configured recipient is accepted.
  Manual sends deliberately resend to everyone. SMTP acceptance does not guarantee inbox delivery;
  an interrupted connection after acceptance can still leave delivery uncertain.

## 8. Prepare for real ComForce data
- Start real use with an empty production database. Test with a separate disposable database when possible. `python -m app.seed --reset` clears existing notes from the selected database and replaces them with synthetic notes.
- Get ComForce's OK on what gets stored: notes are scrubbed of phone numbers, emails, addresses and titled names, but a bare first name can slip through. Phone numbers from texts are stored, and are only shown as the last 4 digits.
- Backups: Render takes daily disk snapshots on paid plans. Also do a **CSV export** from the dashboard each week.

## 9. Verify on real devices before launch
Use synthetic notes for these checks.
- [ ] iPhone (Safari): log a note, **use the mic button** (can't be tested headless), Add to Home Screen.
- [ ] Android (Chrome): same, plus Install app.
- [ ] Airplane mode: save a note, turn the signal back on, and check it's sent once.
- [ ] Admin sign-in with the real authenticator phone, from a second device.
- [ ] Dashboard → Make grant report → Save as PDF.
- [ ] Redeploy or restart and confirm a synthetic note on the persistent disk is still available.
- [ ] If Twilio is set up: text the number from a verified phone, then text `UNDO`.

## Temporary hosted demo

Create a separate Render Blueprint and set the Blueprint Path to [`render.free.yaml`](../render.free.yaml). Supply a demo-only `ADMIN_PASSWORD`. Generate a private key with `python -c "import base64, os; print(base64.b32encode(os.urandom(20)).decode())"` and enter the same key as `ADMIN_TOTP_SECRET` in Render and as a time-based key in your authenticator app. After deployment, set `PUBLIC_URL` to the demo's HTTPS origin and try `/api/health`, `/phone`, and `/dashboard`.

This service auto-deploys and seeds synthetic data on startup. Its filesystem is ephemeral, so new notes and accounts can disappear on restart, redeploy, or idle spin-down. Use synthetic data only. [Render free service limits](https://render.com/docs/free).

## Operational notes
- Texts only show on My notes after the worker links their phone (My notes → Link my phone, then text `LINK` + the code). Set `SMS_NUMBER` so the page shows which number to text.
- One server, one SQLite file. Fine for one nonprofit's team; not built to scale out.
- The first admin to sign in sets up 2FA by scanning a QR code. Whoever knows the password first can enroll their phone, so set the password privately and sign in right away. Or pre-load `ADMIN_TOTP_SECRET`.
