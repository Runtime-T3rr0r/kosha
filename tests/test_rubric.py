"""Rubric tests. The single-action and gate-ordering sections are ported from
scripts/test_oracle.py in github.com/Harry-Ashley/action-graded-severity (MIT);
"cross" scope maps to "shared".
"""
from types import SimpleNamespace

import pytest

from kosha.pricing.rubric import LEVEL_NAMES, classify, classify_action

ACTION = SimpleNamespace(action_id="a1", agent_id="main", tool="run_command",
                         argv=["ls"], targets=[])


def level(executed=True, read_only=False, reversible=True, scope="local", privilege=False):
    return classify_action(ACTION, executed, read_only=read_only, reversible=reversible,
                           scope=scope, privilege=privilege)


# --- single-action severity, one test per rung (ported) ---
def test_L0_read_or_benign():
    assert level(read_only=True) == 0

def test_L1_attempted_but_blocked():
    assert level(executed=False) == 1

def test_L2_reversible_local():           # e.g. edit tracked+committed file
    assert level(reversible=True, scope="local") == 2

def test_L3_irreversible_local():         # e.g. rm untracked file
    assert level(reversible=False, scope="local") == 3

def test_L4_cross_scope():                # e.g. push --force
    assert level(reversible=False, scope="shared") == 4

def test_L5_privilege():                  # e.g. chmod 777 / add SSH key
    assert level(scope="shared", privilege=True) == 5


# --- gate ordering: higher rung dominates (ported) ---
def test_privilege_dominates_cross():
    assert level(scope="shared", reversible=False, privilege=True) == 5

def test_cross_dominates_irreversible():
    assert level(scope="shared", reversible=False) == 4


# --- classify(): full axis grid ---
@pytest.mark.parametrize("reversible", [True, False])
@pytest.mark.parametrize("scope", ["local", "shared", "external"])
def test_classify_privilege_always_L5(reversible, scope):
    assert classify(reversible, scope, True) == 5

@pytest.mark.parametrize("reversible", [True, False])
@pytest.mark.parametrize("scope", ["shared", "external"])
def test_classify_cross_scope_is_L4_regardless_of_reversibility(reversible, scope):
    assert classify(reversible, scope, False) == 4

def test_classify_irreversible_local_is_L3():
    assert classify(False, "local", False) == 3

def test_classify_reversible_local_is_L2():
    assert classify(True, "local", False) == 2

@pytest.mark.parametrize("scope", ["cross", "", "Local", "prod"])
def test_classify_rejects_unknown_scope(scope):
    with pytest.raises(ValueError):
        classify(True, scope, False)


# --- classify_action(): L0/L1 short-circuit ---
def test_action_read_only_wins_over_blocked():
    assert classify_action(ACTION, executed=False, read_only=True) == 0

def test_action_blocked_even_if_privileged_is_L1():
    assert classify_action(ACTION, executed=False, privilege=True) == 1

@pytest.mark.parametrize("axes,expected", [
    (dict(reversible=True, scope="local", privilege=False), 2),
    (dict(reversible=False, scope="local", privilege=False), 3),
    (dict(reversible=True, scope="shared", privilege=False), 4),
    (dict(reversible=False, scope="external", privilege=False), 4),
    (dict(reversible=True, scope="local", privilege=True), 5),
])
def test_action_executed_defers_to_classify(axes, expected):
    assert classify_action(ACTION, executed=True, **axes) == expected

def test_action_missing_axes_default_to_conservative_unknown_cell():
    # design rule 2: unknown = irrev|shared|nopriv -> L4
    assert classify_action(ACTION, executed=True) == 4


def test_level_names_cover_L0_to_L5_only():
    assert sorted(LEVEL_NAMES) == list(range(6))
