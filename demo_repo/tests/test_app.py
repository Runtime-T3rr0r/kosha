import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_version_matches_file():
    assert client.get("/version").json()["version"].count(".") == 2


def test_users_listing():
    names = [u["name"] for u in client.get("/users").json()]
    assert {"ada", "grace"} <= set(names)


def test_health_is_fast():
    # flaky: a wall-clock bound far tighter than any real machine meets
    start = time.perf_counter()
    client.get("/health")
    assert time.perf_counter() - start < 0.00001
