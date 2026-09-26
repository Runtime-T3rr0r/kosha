"""Cross-checks config/effects.yaml against the rubric and the seed table
(context doc §7, piece 6): every entry must classify to the level the table states.
Read-only entries go through classify_action(read_only=True); the rest through classify().
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kosha.pricing.rubric import SCOPES, classify, classify_action

ROOT = Path(__file__).resolve().parents[1]
EFFECTS = yaml.safe_load((ROOT / "config/effects.yaml").read_text())
BY_ID = {e["id"]: e for e in EFFECTS}

# Seed-table row -> level stated in the table, and the entry ids that implement that row.
TABLE = [
    ("ls, cat (non-secret), grep, find, git status/diff/log, SELECT", 0,
     ["ls", "cat_nonsecret", "grep", "find", "git_status", "git_diff", "git_log", "sql_select"]),
    ("edit/write tracked+committed file in repo", 2, ["file_write_tracked_clean"]),
    ("edit/write untracked file in repo", 3, ["file_write_untracked"]),
    ("write outside repo/workspace", 4, ["file_write_outside_workspace"]),
    ("rm tracked committed file", 2, ["rm_tracked_clean"]),
    ("rm untracked / rm -rf dir / git clean -fdx / git reset --hard", 3,
     ["rm_untracked", "rm_recursive", "git_clean_force", "git_reset_hard"]),
    ("git commit, git branch, git stash", 2, ["git_commit", "git_branch", "git_stash"]),
    ("git push (non-force)", 4, ["git_push"]),
    ("git push --force / delete remote branch", 4, ["git_push_force", "git_push_delete"]),
    ("SQL INSERT/UPDATE with WHERE, local dev DB", 3, ["sql_insert_local", "sql_update_where_local"]),
    ("SQL DROP/TRUNCATE/DELETE-no-WHERE, local dev DB", 3,
     ["sql_drop_local", "sql_truncate_local", "sql_delete_no_where_local"]),
    ("any write SQL on shared/prod DB", 4, ["sql_write_shared"]),
    ("run migrations local", 3, ["migrate_local"]),
    ("run migrations shared", 4, ["migrate_shared"]),
    ("npm/pip install", 2, ["npm_install", "pip_install"]),
    ("npm publish / docker push / twine upload", 4, ["npm_publish", "docker_push", "twine_upload"]),
    ("kubectl get/describe", 0, ["kubectl_get", "kubectl_describe"]),
    ("kubectl apply/delete/scale/rollout, helm upgrade", 4,
     ["kubectl_apply", "kubectl_delete", "kubectl_scale", "kubectl_rollout", "helm_upgrade"]),
    ("terraform apply/destroy", 4, ["terraform_apply", "terraform_destroy"]),
    ("curl GET", 0, ["http_get"]),
    ("curl POST/PUT/DELETE to external host", 4,
     ["http_post_external", "http_put_external", "http_delete_external"]),
    ("secret read into output", 4, ["secret_echo_var", "secret_cat_env", "secret_printenv"]),
    ("edit CI workflow files", 4, ["ci_github_workflow_edit", "ci_gitlab_edit"]),
    ("chmod 777, chown, sudo usermod, add SSH key, crontab/systemctl enable, kubectl create rolebinding", 5,
     ["chmod_777", "chown", "usermod", "ssh_key_add", "crontab", "systemctl_enable",
      "kubectl_create_rolebinding"]),
    ("deploy trigger", 4, ["deploy_trigger"]),
    ("unrecognized command", 4, ["unknown"]),
    # additions beyond the seed table
    ("git stash drop / clear", 3, ["git_stash_drop"]),
    ("git branch -D (unmerged)", 3, ["git_branch_delete_unmerged"]),
    ("git branch (list, no name)", 0, ["git_branch_list"]),
    ("kubectl rollout status/history", 0, ["kubectl_rollout_status"]),
    ("git checkout <branch>", 2, ["git_checkout"]),
    ("git checkout -- <path> (discard working tree)", 3, ["git_checkout_discard"]),
    ("docker build", 3, ["docker_build"]),
    ("docker run (conservative default)", 3, ["docker_run"]),
    ("docker ps", 0, ["docker_ps"]),
    ("docker exec", 4, ["docker_exec"]),
    ("cd, sort, head, echo (non-secret), bare xargs, pytest", 0,
     ["cd", "sort", "head", "echo_nonsecret", "xargs", "pytest"]),
    ("mkdir", 2, ["mkdir"]),
    ("chmod +x", 2, ["chmod_exec"]),
    ("opaque script (python/bash <file>, npm run), flat fallback", 3, ["opaque_script"]),
]

EXPECTED = {eid: (row, level) for row, level, ids in TABLE for eid in ids}

ACTION = SimpleNamespace(action_id="a1", agent_id="main", tool="run_command", argv=[], targets=[])


def level_of(e):
    if e["read_only"]:
        return classify_action(ACTION, executed=True, read_only=True)
    return classify(e["reversible"], e["scope"], e["privilege"])


def test_ids_unique():
    ids = [e["id"] for e in EFFECTS]
    assert len(ids) == len(set(ids))


def test_every_entry_maps_to_a_table_row_and_back():
    assert set(BY_ID) == set(EXPECTED)


@pytest.mark.parametrize("entry", EFFECTS, ids=lambda e: e["id"])
def test_schema_exact(entry):
    assert set(entry) == {"id", "match", "reversible", "scope", "privilege", "read_only", "note"}
    assert set(entry["match"]) == {"family", "sub", "flags_any"}
    assert isinstance(entry["match"]["family"], str)
    assert isinstance(entry["match"]["flags_any"], list)
    assert isinstance(entry["reversible"], bool)
    assert isinstance(entry["privilege"], bool)
    assert isinstance(entry["read_only"], bool)
    assert entry["scope"] in SCOPES + ("resolve",)


@pytest.mark.parametrize("eid", list(EXPECTED))
def test_entry_matches_table_level(eid):
    e = BY_ID[eid]
    row, level = EXPECTED[eid]
    got = level_of(e)
    assert got == level, f"{eid} ({row}): table says L{level}, rubric gives L{got}"


@pytest.mark.parametrize("entry", [e for e in EFFECTS if e["read_only"]], ids=lambda e: e["id"])
def test_read_only_entries_have_no_effect_axes(entry):
    # read-only means nothing changed: must not also claim irreversible/cross-scope/privilege
    assert (entry["reversible"], entry["scope"], entry["privilege"]) == (True, "local", False)


def test_stash_drop_and_clear_classify_above_plain_stash():
    stash, drop = BY_ID["git_stash"], BY_ID["git_stash_drop"]
    assert stash["match"]["sub"] == drop["match"]["sub"] == "stash"
    assert set(drop["match"]["flags_any"]) == {"drop", "clear"}
    assert classify(stash["reversible"], stash["scope"], stash["privilege"]) == 2
    assert classify(drop["reversible"], drop["scope"], drop["privilege"]) == 3


def test_branch_force_delete_classifies_above_plain_branch():
    branch, force_delete = BY_ID["git_branch"], BY_ID["git_branch_delete_unmerged"]
    assert branch["match"]["sub"] == force_delete["match"]["sub"] == "branch"
    assert force_delete["match"]["flags_any"] == ["-D"]
    assert classify(branch["reversible"], branch["scope"], branch["privilege"]) == 2
    assert classify(force_delete["reversible"], force_delete["scope"],
                    force_delete["privilege"]) == 3


def test_git_branch_split_list_create_force_delete():
    listing, create, force_delete = (BY_ID[i] for i in
                                     ("git_branch_list", "git_branch", "git_branch_delete_unmerged"))
    assert listing["match"]["sub"] == "branch_list" and listing["read_only"]
    assert create["match"] == {"family": "git", "sub": "branch", "flags_any": []}
    assert [level_of(e) for e in (listing, create, force_delete)] == [0, 2, 3]


def test_kubectl_rollout_split_read_vs_mutate():
    status, mutate = BY_ID["kubectl_rollout_status"], BY_ID["kubectl_rollout"]
    assert status["match"]["sub"] == mutate["match"]["sub"] == "rollout"
    assert set(status["match"]["flags_any"]) == {"status", "history"}
    assert mutate["match"]["flags_any"] == []      # undo/restart/pause/resume fall through here
    assert (level_of(status), level_of(mutate)) == (0, 4)


def test_checkout_discard_classifies_above_plain_checkout():
    checkout, discard = BY_ID["git_checkout"], BY_ID["git_checkout_discard"]
    assert checkout["match"]["sub"] == discard["match"]["sub"] == "checkout"
    assert discard["match"]["flags_any"] == ["--"]
    assert (level_of(checkout), level_of(discard)) == (2, 3)
    assert level_of(discard) == level_of(BY_ID["git_reset_hard"])


def test_force_push_is_same_level_but_pricier_cell_than_push():
    prices = json.loads((ROOT / "config/price_table.m1.json").read_text())["cells"]

    def cell(e):
        return f"{'rev' if e['reversible'] else 'irrev'}|{e['scope']}|nopriv"

    push, force = BY_ID["git_push"], BY_ID["git_push_force"]
    assert classify(push["reversible"], push["scope"], push["privilege"]) == \
        classify(force["reversible"], force["scope"], force["privilege"]) == 4
    assert prices[cell(force)]["price"] > prices[cell(push)]["price"]
