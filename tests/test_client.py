"""client.py fails closed on every way koshad can fail to answer."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kosha.adapters import client
from kosha.system.action import Action

ACTION = Action("a1", "s1", "main", "replay", "run_command", {}, ["ls"], "/r", [], "t")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(handler_body):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            handler_body(self)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def reply(status, body):
    def h(req):
        req.send_response(status)
        req.end_headers()
        req.wfile.write(body.encode())
    return h


@pytest.fixture
def at(monkeypatch):
    def point(url):
        monkeypatch.setattr(client, "KOSHAD_URL", url)
    return point


def assert_fail_closed(d):
    assert (d.decision, d.rule, d.level) == ("deny", "fail_closed", -1)


def test_connection_refused(at):
    at(f"http://127.0.0.1:{free_port()}")
    assert_fail_closed(client.decide(ACTION))


def test_timeout(at):
    srv = serve(lambda req: time.sleep(1.5))
    at(f"http://127.0.0.1:{srv.server_port}")
    t = time.monotonic()
    assert_fail_closed(client.decide(ACTION, timeout=0.3))
    assert time.monotonic() - t < 1.4
    srv.shutdown()


@pytest.mark.parametrize("status,body", [
    (500, "boom"),
    (200, "not json"),
    (200, '{"decision": "allow"}'),                   # missing Decision fields
    (200, '{"decision": "allow", "bogus": 1}'),
])
def test_bad_responses(at, status, body):
    srv = serve(reply(status, body))
    at(f"http://127.0.0.1:{srv.server_port}")
    assert_fail_closed(client.decide(ACTION))
    srv.shutdown()


def test_good_response_passes_through(at):
    body = ('{"decision":"allow","reason":"ok","level":0,"cell":"rev|local|nopriv","price":0,'
            '"fleet_after":0,"agent_after":0,"rule":"ok","suggestion":null}')
    srv = serve(reply(200, body))
    at(f"http://127.0.0.1:{srv.server_port}")
    d = client.decide(ACTION)
    assert (d.decision, d.rule) == ("allow", "ok")
    srv.shutdown()


def test_settle_unreachable_returns_false(at):
    at(f"http://127.0.0.1:{free_port()}")
    assert client.settle("a1", "success") is False
