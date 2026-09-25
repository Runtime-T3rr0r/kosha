"""Cell pricing: "reversible|scope|privilege" -> price from config/price_table.*.json.

Priced per exact cell, not per level: force-push (irrev|shared|nopriv) costs more
than a normal push (rev|shared|nopriv) although both are L4.

Order: L0/L1 -> 0, privilege -> "*|*|priv", exact cell lookup, else conservative
unknown (irrev|shared|nopriv).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

M1_TABLE = Path(__file__).resolve().parents[2] / "config" / "price_table.m1.json"

PRIV_CELL = "*|*|priv"
UNKNOWN_CELL = "irrev|shared|nopriv"


@lru_cache(maxsize=None)
def load_table(path: Path = M1_TABLE) -> dict:
    return json.loads(Path(path).read_text())


def cell_of(reversible: bool, scope: str, privilege: bool) -> str:
    return f"{'rev' if reversible else 'irrev'}|{scope}|{'priv' if privilege else 'nopriv'}"


def price(cell: str, *, level: Optional[int] = None, table: Optional[dict] = None) -> float:
    """Price of one action in its cell.

    level 0/1 (read-only, or blocked before effect) prices at 0 without a cell lookup.
    """
    if level in (0, 1):
        return 0.0
    cells = (table or load_table())["cells"]
    parts = cell.split("|")
    if len(parts) == 3 and parts[2] == "priv":
        return float(cells[PRIV_CELL]["price"])
    if cell in cells:
        return float(cells[cell]["price"])
    return float(cells[UNKNOWN_CELL]["price"])
