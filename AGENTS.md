# Working on ImpactText

Start with [the contributor guide](docs/DEVELOPMENT.md) for local setup, the code map, and the exact checks CI runs. See [README.md](README.md) for product behavior and [the launch checklist](docs/LAUNCH_CHECKLIST.md) for live-service setup.

- Run test and demo commands with synthetic data. Point `IMPACT_DB` at a disposable database before using `python -m app.seed --reset` or the demo reset controls.
- Check route access in `app/main.py` and the endpoint's own authorization before changing a route. Keep redaction, ownership checks, and retry behavior together when changing note creation or edits.
- Match the verification command to the code you touch using the contributor guide. Use `.github/workflows/ci.yml` as the source of truth for the full suite.
