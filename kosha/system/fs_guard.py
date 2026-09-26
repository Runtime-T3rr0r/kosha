"""fs_guard: backup bypass lock (layer 3). Watches a working tree and undoes any file
change that no Kosha reservation covers.

A gated write is expected: /decide records an expected_writes lease (file tools: the
exact path; commands: their cwd) in the same transaction as the allow, so the lease
exists before the write happens. Any create / modify / delete / move with no lease is
a bypass (a native tool, a script, a human in another shell). It is undone and logged
as a `bypass_detected` event:

  modified or deleted a known file   -> restored to its last accepted content
  created a new file                 -> moved to .kosha_quarantine/<timestamp>/<path>
  moved a -> b                       -> a restored; b restored if it was known, else quarantined

"Last accepted content" rather than `git checkout --`: agents make approved,
uncommitted edits through kosha-mcp, and resetting to HEAD would wipe those along
with the bypass. For a tracked, clean file the two are identical. Files over
MAX_SNAPSHOT are not snapshotted and fall back to `git checkout --` (tracked) or
quarantine (untracked).

This is a backup, not the primary lock. It acts after the write has happened, and
two windows are accepted by design: a write under a directory leased by a gated
command while that command runs, and a write to a leased path within LEASE_GRACE
(0.5s) after its gated action settles. The grace can't be dropped: one gated write
emits several events (truncate, then write), and without it the guard would "undo" an
approved write back to its half-written state. Layers 1 (mode tool groups) and 2 (kosha-hook) stop bypasses
before they happen.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from kosha.system.kosha_db import KoshaDB

QUARANTINE = ".kosha_quarantine"
IGNORED_DIRS = {".git", QUARANTINE, "__pycache__", ".bob", ".venv", "node_modules"}
# sqlite files change through db_exec, which leases nothing: the DB is kosha's to price,
# not fs_guard's to revert
IGNORED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3",
                    ".pyc", ".swp", ".swx", "~")
MAX_SNAPSHOT = 5 * 1024 * 1024

ABSENT = object()        # accepted state: the file does not exist
UNSNAPSHOTTED = None     # accepted state: the file exists, content too large or unreadable to hold


class FsGuard(FileSystemEventHandler):
    def __init__(self, root: str | Path, db: KoshaDB, session_id: Optional[str] = None):
        self.root = os.path.realpath(root)
        self.db = db
        self.session_id = session_id
        self.known: dict[str, object] = {}     # path -> last accepted state (bytes | ABSENT | UNSNAPSHOTTED)
        self.lock = threading.Lock()
        self.observer: Optional[Observer] = None
        self._snapshot_tree()

    # --- lifecycle ---

    def start(self) -> "FsGuard":
        self.observer = Observer()
        self.observer.schedule(self, self.root, recursive=True)
        self.observer.start()
        return self

    def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)

    # --- state ---

    def ignored(self, path: str) -> bool:
        rel = os.path.relpath(path, self.root)
        if rel == "." or rel.startswith(".."):
            return True
        return (any(p in IGNORED_DIRS for p in Path(rel).parts)
                or path.endswith(IGNORED_SUFFIXES) or path.endswith(".kosha_restore")
                or os.path.islink(path))

    def _state(self, path: str) -> object:
        if not os.path.isfile(path):
            return ABSENT
        try:
            if os.path.getsize(path) > MAX_SNAPSHOT:
                return UNSNAPSHOTTED
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return UNSNAPSHOTTED

    def _snapshot_tree(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for name in filenames:
                p = os.path.join(dirpath, name)
                if not self.ignored(p):
                    self.known[p] = self._state(p)

    # --- undo ---

    def _undo(self, path: str) -> str:
        """Put path back to its last accepted state."""
        accepted = self.known.get(path, ABSENT)
        if accepted is ABSENT:                      # it should not exist
            return self._quarantine(path) if os.path.lexists(path) else "nothing to undo"
        if accepted is UNSNAPSHOTTED:               # no copy held: git is the only source
            return self._git_checkout(path) if self._tracked(path) else "unrecoverable"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.kosha_restore"
        with open(tmp, "wb") as f:
            f.write(accepted)
        os.replace(tmp, path)
        return "restored"

    def _quarantine(self, path: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = os.path.join(self.root, QUARANTINE, stamp, os.path.relpath(path, self.root))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
        return f"quarantined to {os.path.relpath(dest, self.root)}"

    def _tracked(self, path: str) -> bool:
        r = subprocess.run(["git", "-C", self.root, "ls-files", "--error-unmatch", "--", path],
                           capture_output=True, timeout=5)
        return r.returncode == 0

    def _git_checkout(self, path: str) -> str:
        r = subprocess.run(["git", "-C", self.root, "checkout", "--", path],
                           capture_output=True, timeout=5)
        return "git checkout" if r.returncode == 0 else "unrecoverable"

    # --- events ---

    def check(self, path: str, kind: str) -> None:
        """One path changed (or may have). Accept it if leased, else undo and report."""
        if self.ignored(path):
            return
        current = self._state(path)
        accepted = self.known.get(path, ABSENT)
        if current is accepted or (isinstance(current, bytes) and current == accepted):
            return                                  # no change: repeat event, or our own undo
        if current is UNSNAPSHOTTED and accepted is UNSNAPSHOTTED:
            return                                  # can't compare large files (documented limit)
        if self.db.match_expected(path):
            self.known[path] = current
            return
        outcome = self._undo(path)
        self.db.log_event("bypass_detected", self.session_id,
                          {"event": kind, "path": os.path.relpath(path, self.root),
                           "outcome": outcome, "root": self.root})

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in ("created", "modified", "deleted", "moved"):
            return
        paths = [(os.path.realpath(event.src_path), event.event_type)]
        if event.event_type == "moved":             # source vanished, destination appeared
            paths = [(paths[0][0], "moved away"), (os.path.realpath(event.dest_path), "moved here")]
        with self.lock:
            for path, kind in paths:
                try:
                    self.check(path, kind)
                except Exception as e:              # a guard that dies silently guards nothing
                    self.db.log_event("fs_guard_error", self.session_id,
                                      {"event": kind, "path": path, "error": f"{type(e).__name__}: {e}"})


def main(argv: list[str] | None = None) -> None:
    import argparse
    import time
    ap = argparse.ArgumentParser(prog="kosha-fs-guard")
    ap.add_argument("root", help="working tree to guard (e.g. demo_repo)")
    ap.add_argument("--session", default=None, help="session id to tag bypass events with")
    a = ap.parse_args(argv)
    guard = FsGuard(a.root, KoshaDB(), a.session).start()
    print(f"fs_guard watching {guard.root}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        guard.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
