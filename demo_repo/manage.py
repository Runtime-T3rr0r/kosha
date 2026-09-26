"""Demo management commands.

  python manage.py migrate --db dev|prod    apply pending migrations/*.sql
  python manage.py status  --db dev|prod    show applied migrations

dev is data/dev.db in this repo. prod is DEMO_PROD_DB, default ../prod.db: a
disposable sqlite file created by setup_demo.py, standing in for the shared prod DB.
"""
import argparse
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DBS = {"dev": ROOT / "data" / "dev.db",
       "prod": Path(os.environ.get("DEMO_PROD_DB", ROOT.parent / "prod.db"))}


def connect(alias: str) -> sqlite3.Connection:
    c = sqlite3.connect(DBS[alias])
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY)")
    return c


def migrate(alias: str) -> list[str]:
    applied = []
    with connect(alias) as c:
        done = {r[0] for r in c.execute("SELECT name FROM schema_migrations")}
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            if f.name not in done:
                c.executescript(f.read_text())
                c.execute("INSERT INTO schema_migrations VALUES (?)", (f.name,))
                applied.append(f.name)
    return applied


def status(alias: str) -> list[str]:
    with connect(alias) as c:
        return [r[0] for r in c.execute("SELECT name FROM schema_migrations ORDER BY name")]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["migrate", "status"])
    ap.add_argument("--db", choices=sorted(DBS), required=True)
    a = ap.parse_args()
    if a.command == "migrate":
        applied = migrate(a.db)
        print(f"{a.db}: applied {', '.join(applied)}" if applied else f"{a.db}: up to date")
    else:
        print(f"{a.db}: {', '.join(status(a.db)) or 'no migrations'}")
