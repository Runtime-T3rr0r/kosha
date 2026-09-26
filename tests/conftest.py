"""Shared fixtures for adapter tests: a real koshad (uvicorn, temp DB) on a free port."""
import socket
import threading
import time

import pytest
import uvicorn

from kosha.adapters import client
from kosha.api.server import create_app
from kosha.system.kosha_db import KoshaDB


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LiveKoshad:
    def __init__(self, db: KoshaDB, port: int):
        self.db, self.port = db, port
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(create_app(db), host="127.0.0.1", port=port,
                                                    log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("koshad did not start")
            time.sleep(0.02)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def live_koshad(tmp_path, monkeypatch):
    """koshad on a free port; client.py and any child process (via KOSHAD_URL) point at it."""
    k = LiveKoshad(KoshaDB(tmp_path / "koshad.db"), free_port())
    k.start()
    monkeypatch.setattr(client, "KOSHAD_URL", k.url)
    monkeypatch.setenv("KOSHAD_URL", k.url)
    yield k
    k.stop()


@pytest.fixture
def dead_koshad(monkeypatch):
    """Nothing listening: every client call must fail closed."""
    url = f"http://127.0.0.1:{free_port()}"
    monkeypatch.setattr(client, "KOSHAD_URL", url)
    monkeypatch.setenv("KOSHAD_URL", url)
    return url
