"""kosha.pricing.match: argv -> effects.yaml entry, most specific entry wins."""
import pytest

from kosha.pricing.match import match_command, segments


def mid(cmd: str) -> str:
    return match_command(cmd.split())["id"]


@pytest.mark.parametrize("cmd, expected", [
    ("git branch", "git_branch_list"),
    ("git branch feat", "git_branch"),
    ("git branch -d feat", "git_branch"),
    ("git branch -D feat", "git_branch_delete_unmerged"),
    ("git checkout main", "git_checkout"),
    ("git checkout -- app.py", "git_checkout_discard"),
    ("git checkout HEAD -- .", "git_checkout_discard"),
    ("kubectl rollout status deploy/web", "kubectl_rollout_status"),
    ("kubectl rollout history deploy/web", "kubectl_rollout_status"),
    ("kubectl rollout undo deploy/web", "kubectl_rollout"),
    ("kubectl rollout restart deploy/web", "kubectl_rollout"),
    ("git stash", "git_stash"),
    ("git stash drop", "git_stash_drop"),
    ("git push origin feat", "git_push"),
    ("git push --force origin feat", "git_push_force"),
    ("git reset --hard HEAD~1", "git_reset_hard"),
    ("rm notes.txt", "rm_untracked"),
    ("rm -rf build", "rm_recursive"),
    ("chmod +x run.sh", "chmod_exec"),
    ("chmod u+x run.sh", "chmod_exec"),
    ("chmod 777 run.sh", "chmod_777"),
    ("chmod o+w run.sh", "chmod_777"),
    ("chmod a+rwx +x run.sh", "chmod_777"),
    ("cd /testbed", "cd"),
    ("sort out.txt", "sort"),
    ("head -n 20 app.py", "head"),
    ("echo done", "echo_nonsecret"),
    ("echo $API_KEY", "secret_echo_var"),
    ("pytest tests/test_x.py -q", "pytest"),
    ("python -m pytest tests", "pytest"),
    ("mkdir -p out/logs", "mkdir"),
    ("xargs", "xargs"),
    ("xargs grep foo", "grep"),
    ("xargs -I {} rm {}", "rm_untracked"),
    ("xargs -n 1 -0 rm -rf", "rm_recursive"),
    ("python reproduce.py", "opaque_script"),
    ("python3 -u reproduce.py --fast", "opaque_script"),
    ("bash run_tests.sh", "opaque_script"),
    ("sh ./build.sh", "opaque_script"),
    ("npm run build", "opaque_script"),
    ("python manage.py migrate", "migrate_local"),
])
def test_specificity(cmd, expected):
    assert mid(cmd) == expected


def test_flag_entry_wins_regardless_of_file_order():
    effects = (
        {"id": "unknown", "match": {"family": "unknown", "sub": None, "flags_any": []},
         "reversible": False, "scope": "shared", "privilege": False, "read_only": False},
        {"id": "discard", "match": {"family": "git", "sub": "checkout", "flags_any": ["--"]},
         "reversible": False, "scope": "local", "privilege": False, "read_only": False},
        {"id": "plain", "match": {"family": "git", "sub": "checkout", "flags_any": []},
         "reversible": True, "scope": "local", "privilege": False, "read_only": False},
    )
    assert match_command(["git", "checkout", "--", "f"], effects)["id"] == "discard"
    assert match_command(["git", "checkout", "main"], effects)["id"] == "plain"
    assert match_command(["git", "checkout", "--", "f"], effects[::-1])["id"] == "discard"


def test_result_shape_and_axes():
    r = match_command(["git", "checkout", "--", "app.py"])
    assert r == {"id": "git_checkout_discard", "family": "git", "sub": "checkout", "flags": ["--"],
                 "matched": True, "opaque_script": False, "reversible": False, "scope": "local", "privilege": False,
                 "read_only": False}
    assert match_command(["git", "branch"])["read_only"] is True


@pytest.mark.parametrize("argv", [["python", "-c", "print(1)"], ["bash", "-c", "ls"], ["npm", "run"],
                                  ["python"], ["tail", "-f", "log"], []])
def test_unmatched_falls_back_to_unknown(argv):
    r = match_command(argv)
    assert (r["id"], r["matched"], r["opaque_script"]) == ("unknown", False, False)
    assert (r["reversible"], r["scope"], r["privilege"]) == (False, "shared", False)


def test_opaque_script_is_flagged_and_irrev_local():
    r = match_command(["python", "reproduce.py"])
    assert (r["id"], r["matched"], r["opaque_script"]) == ("opaque_script", True, True)
    assert (r["reversible"], r["scope"], r["privilege"], r["read_only"]) == (False, "local", False, False)
    assert not match_command(["python", "-m", "pytest"])["opaque_script"]


def test_prefixes_stripped():
    assert mid("sudo git push -f") == "git_push_force"
    assert match_command(["FOO=1", "git", "branch"])["id"] == "git_branch_list"


def test_segments_split_operators_and_redirects():
    assert segments("cd /testbed && git branch | grep x") == [
        ["cd", "/testbed"], ["git", "branch"], ["grep", "x"]]
    assert segments("python x.py > out.txt") == [["python", "x.py"], ["__redirect__", "out.txt"]]
    assert segments("python x.py > /dev/null") == [["python", "x.py"]]
    assert match_command(["__redirect__", "out.txt"])["id"] == "file_write_untracked"
