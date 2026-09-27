"""Where the runtime config files (effects.yaml, price tables) are read from.

config/ at the repo root is the canonical copy. An installed wheel ships the runtime
files as the kosha.config package data (pyproject.toml maps config/ onto it), and an
editable install maps the same package onto the repo's config/. Scripts run from a
source checkout without installing (repo root on sys.path) have no kosha.config
package and read the repo-root config/ directly.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def config_file(name: str) -> Path:
    """Path of a runtime config file: the installed kosha.config copy if there is one,
    else the repo-root config/."""
    try:
        packaged = resources.files("kosha.config") / name
    except ModuleNotFoundError:
        packaged = None
    if packaged is not None and packaged.is_file():
        return Path(str(packaged))
    return REPO_CONFIG / name
