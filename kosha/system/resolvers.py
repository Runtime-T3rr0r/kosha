"""Context resolvers: facts about the world an argv alone can't tell you.

Each one answers conservatively when it can't be sure (untracked, outside the
workspace, shared DB, external host), so an unresolvable target is priced up, not down.
"""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml

KOSHA_YAML = Path(__file__).resolve().parents[2] / "config" / "kosha.yaml"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
SHARED_HINT = re.compile(r"prod|staging", re.I)
SQLITE_FILE = re.compile(r"\.(db|sqlite|sqlite3)$", re.I)
SECRET_NAME = re.compile(r"key|token|secret|passw|credential|auth", re.I)
GIT_TIMEOUT = 1.0


@lru_cache(maxsize=None)
def load_config(path: Path = KOSHA_YAML) -> dict:
    """config/kosha.yaml (gitignored; copy from config/kosha.example.yaml). {} if absent."""
    try:
        return yaml.safe_load(Path(path).read_text()) or {}
    except FileNotFoundError:
        return {}


def _git(cwd: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def workspace_of(cwd: str) -> Optional[str]:
    """KOSHA_WORKSPACE if set, else the git toplevel containing cwd, else None."""
    if os.environ.get("KOSHA_WORKSPACE"):
        return os.path.realpath(os.environ["KOSHA_WORKSPACE"])
    if not cwd or not os.path.isdir(cwd):
        return None
    r = _git(cwd, "rev-parse", "--show-toplevel")
    return os.path.realpath(r.stdout.strip()) if r and r.returncode == 0 else None


def abspath(path: str, cwd: str) -> Optional[str]:
    """Absolute, symlink-resolved path, or None if a relative path has no cwd to anchor it."""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        if not cwd:
            return None
        p = os.path.join(cwd, p)
    return os.path.realpath(p)


def in_workspace(path: str, cwd: str, workspace: Optional[str]) -> bool:
    p = abspath(path, cwd)
    if p is None or workspace is None:
        return False
    return p == workspace or p.startswith(workspace.rstrip(os.sep) + os.sep)


def is_tracked_clean(path: str, cwd: str) -> bool:
    """Tracked by git and identical to HEAD, so `git checkout -- path` restores it."""
    p = abspath(path, cwd)
    if p is None or not os.path.exists(p):
        return False
    d = os.path.dirname(p)
    tracked = _git(d, "ls-files", "--error-unmatch", "--", p)
    if tracked is None or tracked.returncode != 0:
        return False
    status = _git(d, "status", "--porcelain", "--", p)
    return status is not None and status.returncode == 0 and status.stdout.strip() == ""


def host_scope(target: str) -> str:
    """"local" for loopback / configured local hosts, else "external"."""
    host = urlparse(target if "://" in target else f"//{target}").hostname or ""
    if host in LOCAL_HOSTS or host in set(load_config().get("local_hosts") or []):
        return "local"
    try:
        return "local" if ipaddress.ip_address(host).is_loopback else "external"
    except ValueError:
        return "external"


def db_scope(target: str) -> str:
    """"local" or "shared" for a DB alias, URL, or sqlite file path.

    Order: alias in config/kosha.yaml `databases` -> prod/staging in the name -> sqlite
    file -> URL on a loopback host -> shared (conservative).
    """
    dbs = load_config().get("databases") or {}
    if target in dbs:
        return "shared" if dbs[target].get("scope") == "shared" else "local"
    for alias, spec in dbs.items():
        if spec.get("url") and spec["url"] == target:
            return "shared" if spec.get("scope") == "shared" else "local"
    if SHARED_HINT.search(target):
        return "shared"
    if target.startswith("sqlite:") or SQLITE_FILE.search(target):
        return "local"
    if "://" in target:
        return "local" if host_scope(target) == "local" else "shared"
    return "shared"


def shared_db_names() -> set[str]:
    """Aliases and URLs configured as shared, for scanning free text (migration env)."""
    out = set()
    for alias, spec in (load_config().get("databases") or {}).items():
        if spec.get("scope") == "shared":
            out.add(alias)
            if spec.get("url"):
                out.add(spec["url"])
    return out


def is_secret_var(name: str) -> bool:
    """Environment variable name that likely holds a credential."""
    return bool(SECRET_NAME.search(name.lstrip("$").strip("{}")))
