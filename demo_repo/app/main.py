"""Demo service for the Kosha "prepare release 1.3" scenario: a tiny users API."""
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="demo-users")


def db() -> sqlite3.Connection:
    return sqlite3.connect(os.environ.get("DEMO_DB", ROOT / "data" / "dev.db"))


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/version")
def version() -> dict:
    return {"version": (ROOT / "VERSION").read_text().strip()}


@app.get("/users")
def users() -> list[dict]:
    with db() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
        return [dict(zip(cols, r)) for r in c.execute("SELECT * FROM users ORDER BY id")]


@app.post("/users")
def create_user(name: str) -> dict:
    with db() as c:
        cur = c.execute("INSERT INTO users(name) VALUES (?)", (name,))
        return {"id": cur.lastrowid, "name": name}
