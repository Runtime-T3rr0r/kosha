import json
from pathlib import Path

import pytest

from kosha.pricing.pricing import cell_of, load_table, price
from kosha.pricing.rubric import classify

TABLE = json.loads((Path(__file__).resolve().parents[1] / "config/price_table.m1.json").read_text())


@pytest.mark.parametrize("cell,expected", [
    ("rev|local|nopriv", 2),
    ("irrev|local|nopriv", 10),
    ("rev|shared|nopriv", 30),
    ("irrev|shared|nopriv", 40),
    ("rev|external|nopriv", 30),
    ("irrev|external|nopriv", 45),
    ("*|*|priv", 60),
])
def test_every_defined_cell(cell, expected):
    assert price(cell) == expected


def test_all_table_cells_covered_by_test():
    assert set(load_table()["cells"]) == {
        "rev|local|nopriv", "irrev|local|nopriv", "rev|shared|nopriv", "irrev|shared|nopriv",
        "rev|external|nopriv", "irrev|external|nopriv", "*|*|priv",
    }


@pytest.mark.parametrize("reversible", [True, False])
@pytest.mark.parametrize("scope", ["local", "shared", "external"])
def test_privilege_overrides_reversibility_and_scope(reversible, scope):
    assert price(cell_of(reversible, scope, True)) == 60


@pytest.mark.parametrize("level", [0, 1])
@pytest.mark.parametrize("cell", [
    "rev|local|nopriv", "irrev|external|nopriv", "irrev|shared|priv", "*|*|priv", "garbage",
])
def test_L0_L1_price_zero_regardless_of_cell(level, cell):
    assert price(cell, level=level) == 0


@pytest.mark.parametrize("cell", ["garbage", "", "irrev|prod|nopriv", "rev|local", "maybe|local|nopriv"])
def test_unknown_cell_prices_as_irrev_shared(cell):
    assert price(cell) == 40


@pytest.mark.parametrize("cell", ["irrev|prod|priv", "?|?|priv"])
def test_unknown_cell_with_privilege_prices_as_priv(cell):
    assert price(cell) == 60


def test_regression_force_push_costs_more_than_push_at_same_level():
    # both L4; the flat per-level scheme would have priced them equally
    assert classify(False, "shared", False) == classify(True, "shared", False) == 4
    assert price("irrev|shared|nopriv") > price("rev|shared|nopriv")


@pytest.mark.parametrize("level", [2, 3, 4, 5, None])
def test_levels_above_L1_use_the_cell(level):
    assert price("irrev|local|nopriv", level=level) == 10


def test_explicit_table_is_used():
    t = {"cells": {**TABLE["cells"], "rev|local|nopriv": {"price": 7, "n": 12, "p_high": 0.1}}}
    assert price("rev|local|nopriv", table=t) == 7


def test_cell_of_format():
    assert cell_of(True, "local", False) == "rev|local|nopriv"
    assert cell_of(False, "external", True) == "irrev|external|priv"
