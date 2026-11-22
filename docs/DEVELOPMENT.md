# Developing ImpactText

ImpactText turns field notes from a web form, browser dictation, or SMS into structured activity records. Workers can log without an account or sign in to manage their own notes. Administrators use the records for follow-ups, exports, and impact reports.

## Run locally

Use Python 3.13 to match GitHub Actions and the Docker image. The browser uses plain HTML, CSS, and JavaScript served by FastAPI. Setup requires a Python environment and the packages in `requirements.txt`.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:IMPACT_DB = Join-Path ([System.IO.Path]::GetTempPath()) ("impacttext-dev-" + [guid]::NewGuid() + ".db")
.\.venv\Scripts\python.exe -m app.seed --reset
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

On macOS or Linux, use `.venv/bin/python` and set the database with `export IMPACT_DB="$(mktemp -d)/impact.db"` before running the seed and server commands. Keep `IMPACT_DB` set in the shell before starting either process. The default database is `data/impact.db` and `--reset` clears entries in whichever database is selected.

Open [the log page](http://127.0.0.1:8000), try `fed 12 kids at Sherman Park`, then use `/phone` to try the same flow through simulated SMS. To open `/dashboard`, sign in as `admin` using the password printed at startup when `ADMIN_PASSWORD` is unset, then enroll an authenticator. For a clean run, leave optional integrations unset. [.env.example](../.env.example) describes the AI, Twilio, and SMTP settings. Without an Anthropic key, extraction uses keyword rules and reports use a template.

## Where to make changes

| Area | Start here | What to check |
| --- | --- | --- |
| Note intake | `app/main.py` (`api_log`, `sms`, `log_note`), `static/index.html`, `static/phone.html` | Browser dictation submits to the web endpoint. The SMS simulator uses the same webhook and TwiML reply path as Twilio. |
| Parsing and privacy | `app/privacy.py`, `app/extract.py`, `app/rules.py`, `app/llm.py` | Scrub text before storage and extraction. Both the AI and keyword paths return normalized structured fields. |
| Storage and edits | `app/db.py`, `app/main.py` (`_own`, `api_update`, `api_delete`), `static/edit.js` | Schema setup happens on first connection. Validate partial count edits against persisted values and preserve the unique client ID used for retries. |
| Sign-in and worker identity | `app/main.py` (`require_admin`, `PUBLIC_PAGES`, `PUBLIC_PREFIXES`), `app/auth.py`, `app/mfa.py`, `app/db.py` | The middleware defaults to admin-only access. Public route families still enforce their own checks where needed. Web ownership uses the account ID. SMS commands identify the sender by phone, and linked texts appear in My notes. |
| Dashboard, exports, and reports | `app/impact.py`, `app/integrations.py`, `app/monthly_report.py`, `static/dashboard.html`, `static/report.html` | Report totals come from stored fields. Open follow-ups outlive the dashboard period. Scheduled email retries track accepted SMTP recipients in SQLite. |
| Phone install and offline logging | `static/index.html`, `static/pwa.js`, `static/sw.js`, `static/manifest.webmanifest` | The log page queues requests in browser local storage and resends the original client ID and logged date. The service worker excludes APIs and integration feeds from caching. |

The route-to-page map and router registration are in `app/main.py`. The admin console lives in `app/console.py` and `static/console.html`. See [Follow-ups, edits and retries](DATA_CORRECTNESS.md) for migration and data integrity details.

## Verify a change

Run these commands from the repository root in the activated environment, or invoke the environment's Python executable directly.

| Change | Local checks used in CI |
| --- | --- |
| Python syntax | `python -m compileall -q app eval` |
| Routes, roles, sign-in, or two-step | `python -m eval.auth_test` |
| Calendars, spreadsheets, or offline support | `python -m eval.integrations_test` |
| Privacy, ingestion, or parsing | `python -m eval.robustness` and `python -m eval.run_eval --min 88` |
| Edits, duplicates, or migrations | `python -m eval.data_correctness_test` |
| Monthly email | `python -m eval.monthly_report_test` |
| Linking a phone or changing SMS commands | `python -m eval.link_phone_test` and `python -m eval.spanish_sms_test` |
| SMS copy | `python -m eval.sms_cost` |

For browser checks, start a server with seeded synthetic data, `ADMIN_PASSWORD=sim-admin`, and `ADMIN_TOTP_SECRET=JBSWY3DPEHPK3PXP`. These values are fixtures for the test server. Run `python -m eval.browser_test http://127.0.0.1:8000 screenshots` with Chrome or Edge installed. If the script prints “No Edge/Chrome found; skipping,” set `CHROME_BIN` to the browser executable and rerun it.

## Read CI and deployment results

- GitHub Actions runs the workflow on pull requests, pushes to `main`, and manual dispatches. To observe a full run for each test commit, push each one after the preceding run completes. A newer run on the same Git ref can cancel an in-progress run. [CI workflow](../.github/workflows/ci.yml)
- The tests job gates the browser and Docker jobs, which run in parallel. Browser CI uploads screenshots. Docker CI starts the image with a volume at `/data`, runs smoke checks, restarts the container, and checks that its saved entry survives.
- The production deployment job runs only on a push to `main` after all three checks succeed. If `RENDER_DEPLOY_HOOK_URL` is missing, the job posts a notice and exits successfully. If the hook accepts the request, the job reports that deployment started. Verify the deployed revision and health separately in Render.
- [The production blueprint](../render.yaml) disables Render auto-deploy and mounts persistent storage. [The free demo blueprint](../render.free.yaml) uses Render auto-deploy, no persistent disk, and seeds synthetic data on startup. See [the launch checklist](LAUNCH_CHECKLIST.md) for service setup and [the SMS guide](SMS_SETUP.md) for Twilio configuration.
