# demo_repo: "prepare release 1.3"

A small FastAPI + SQLite service used to demo Kosha governing a fleet of concurrent Bob tasks.
Every Bob tab in the Kosha mode is its own Kosha agent, and all tabs share one fleet.
This directory is a **template**: `python demo_repo/setup_demo.py` copies it into a fresh
git working copy under `.demo/work`, with a local bare `origin`, dev and disposable prod
databases, Kosha config, a fresh ledger DB, and Bob config for the Kosha mode.
Re-run it to reset everything (do this right before recording).

- `app/`: the service; `manage.py migrate --db dev|prod`: migrations in `migrations/`
- `tests/`: `test_health_is_fast` is deliberately flaky
- `.github/workflows/ci.yml`: CI, with a "slow integration check" step
- `deploy.sh`: **stub**, never deploys anywhere
- prod is a disposable sqlite file (`.demo/prod.db`), one migration behind dev
