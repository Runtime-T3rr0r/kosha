"""Ctrl+C stops the real koshad promptly and cleanly, even with /stream connections open
(the approval panel and the Bob extension keep one open all the time)."""
import os
import pty
import select
import signal
import socket
import subprocess
import sys
import time

import pytest

KOSHAD = os.path.join(os.path.dirname(sys.executable), "koshad")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.parametrize("open_streams", [0, 2])
def test_ctrl_c_stops_koshad_fast_and_clean(tmp_path, open_streams):
    port = free_port()
    env = {**os.environ, "KOSHA_DB": str(tmp_path / "k.db"), "KOSHAD_PORT": str(port)}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(KOSHAD, ["koshad"], env)
    buf = b""

    def until(token, t=20):
        nonlocal buf
        end = time.time() + t
        while token not in buf and time.time() < end:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    buf += os.read(fd, 4096)
                except OSError:
                    break
        return token in buf

    streams = []
    try:
        assert until(b"passphrase")
        os.write(fd, b"\n")                                   # no passphrase: fine for this test
        assert until(b"Uvicorn running"), buf
        streams = [subprocess.Popen(["curl", "-sN", f"http://127.0.0.1:{port}/stream"],
                                    stdout=subprocess.DEVNULL) for _ in range(open_streams)]
        time.sleep(0.8)
        start = time.time()
        os.kill(pid, signal.SIGINT)                           # what Ctrl+C sends
        while not os.waitpid(pid, os.WNOHANG)[0]:
            assert time.time() - start < 5, "koshad still running 5s after Ctrl+C"
            time.sleep(0.05)
        until(b"\x00", 0.3)
        out = buf.decode(errors="replace")
        assert "Traceback" not in out, out[-2000:]
        assert "Application shutdown complete" in out
    finally:
        for s in streams:
            s.kill()
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
