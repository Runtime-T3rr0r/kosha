"""kosha.pricing.match: argv -> effects.yaml entry, most specific entry wins."""
import pytest

from kosha.pricing.match import match_command, segments, sql_sub
from kosha.pricing.policy import SINGLE_AGENT_CONSEQUENTIAL
from kosha.pricing.rubric import classify


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
    ("sed -i s/a/b/ app.py", "sed_inplace_untracked"),
    ("sed -i.bak -e s/a/b/ app.py", "sed_inplace_untracked"),
    ("sed -ni s/a/b/p app.py", "sed_inplace_untracked"),
    ("sed --in-place s/a/b/ app.py", "sed_inplace_untracked"),
    ("cp a.py b.py", "cp_untracked"),
    ("cp -r src/ /tmp/src", "cp_untracked"),
    ("mv a.py b.py", "mv_untracked"),
    ("tee out.txt", "file_write_untracked"),
    ("tee -a log.txt", "file_write_untracked"),
    ("tee", "tee_stdout"),
    ("tee /dev/null", "tee_stdout"),
    ("make", "opaque_script"),
    ("make test", "opaque_script"),
    ("npm test", "opaque_script"),
    ("npm test --silent -- --testPathPattern=auth", "opaque_script"),
    ("npm t", "opaque_script"),
    ("yarn test", "opaque_script"),
    ("pnpm test", "opaque_script"),
    ("pytest", "pytest"),
    ("npx playwright test e2e/checkout.spec.ts", "e2e_test_runner"),
    ("npx -y cypress run --spec a.cy.ts", "e2e_test_runner"),
    ("flake8 src/auth", "lint_check"),
    ("shellcheck scripts/cleanup.sh", "lint_check"),
    ("tail -n 20 migrate.log", "read_only_util"),
    ("tail -f app.log", "read_only_util"),
    ("rg -n webhook services/payments", "read_only_util"),
    ("wc -l data.csv", "read_only_util"),
    ("jq .version package.json", "read_only_util"),
    ("jq -f filter.jq data.json", "read_only_util"),
    ("date +%Y-%m-%d", "read_only_util"),
    ("du -sh logs/", "read_only_util"),
    ("uptime", "read_only_util"),
    ("git add src/auth.js", "git_add"),
    ("systemctl status app.service", "systemctl_status"),
    ("systemctl enable app.service", "systemctl_enable"),
    ("kubectl logs -l app=api", "kubectl_logs"),
    ("docker system df", "docker_system_df"),
    ("terraform init", "terraform_init"),
    ("psql -d app_db -f migrations/001.sql", "sql_file_local"),
    ("psql --file=seed.sql", "sql_file_local"),
    ("psql -d production -f migrations/001.sql", "sql_write_shared"),
    ("psql -c DELETE FROM users WHERE id = 1", "sql_delete_where_local"),
    ("psql -c DELETE FROM users", "sql_delete_no_where_local"),
    ("xmrig", "crypto_miner"),
    ("./xmrig -o pool.example.com:3333 -u wallet", "crypto_miner"),
    ("nohup /tmp/xmrig --donate-level 1", "crypto_miner"),
    ("stress --cpu 8 --timeout 600", "stress_load"),
    ("stress-ng --vm 4 --vm-bytes 90%", "stress_load"),
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


def _entry(eid, flags, privilege, reversible=True, scope="local"):
    return {"id": eid, "match": {"family": "chmod", "sub": None, "flags_any": flags},
            "reversible": reversible, "scope": scope, "privilege": privilege, "read_only": False}


@pytest.mark.parametrize("flip", [False, True])
def test_privilege_entry_wins_tie_regardless_of_file_order(flip):
    unknown = {"id": "unknown", "match": {"family": "unknown", "sub": None, "flags_any": []},
               "reversible": False, "scope": "shared", "privilege": False, "read_only": False}
    priv = _entry("chmod_777", ["777", "a+rwx", "o+w"], True, False, "shared")
    exec_ = _entry("chmod_exec", ["+x", "u+x"], False)
    pair = (exec_, priv) if flip else (priv, exec_)
    effects = (unknown, *pair)
    assert match_command(["chmod", "a+rwx", "+x", "f"], effects)["id"] == "chmod_777"
    assert match_command(["chmod", "+x", "a+rwx", "f"], effects)["id"] == "chmod_777"
    assert match_command(["chmod", "+x", "f"], effects)["id"] == "chmod_exec"


@pytest.mark.parametrize("flip", [False, True])
def test_equal_specificity_tie_goes_to_higher_level(flip):
    unknown = {"id": "unknown", "match": {"family": "unknown", "sub": None, "flags_any": []},
               "reversible": False, "scope": "shared", "privilege": False, "read_only": False}
    low = _entry("low", ["-a"], False)                          # rev|local -> L2
    high = _entry("high", ["-b"], False, reversible=False)      # irrev|local -> L3
    effects = (unknown, *((high, low) if flip else (low, high)))
    assert match_command(["chmod", "-a", "-b", "f"], effects)["id"] == "high"


def test_result_shape_and_axes():
    r = match_command(["git", "checkout", "--", "app.py"])
    assert r == {"id": "git_checkout_discard", "family": "git", "sub": "checkout", "flags": ["--"],
                 "matched": True, "opaque_script": False, "reversible": False, "scope": "local", "privilege": False,
                 "read_only": False, "write_paths": []}
    assert match_command(["git", "branch"])["read_only"] is True


@pytest.mark.parametrize("argv", [["python", "-c", "print(1)"], ["bash", "-c", "ls"], ["npm", "run"],
                                  ["python"], ["kill", "1234"], [],
                                  ["sed", "-n", "1,5p", "app.py"], ["sed", "-e", "s/a/b/", "app.py"],
                                  ["date", "-s", "2020-01-01"], ["date", "--set=2020-01-01"],
                                  ["rg", "--pre", "./run.sh", "x"], ["docker", "system", "prune"],
                                  ["npx", "prisma", "migrate", "dev"], ["npx"]])
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
    assert match_command(["FOO=1", "git", "branch"])["privilege"] is False


@pytest.mark.parametrize("cmd", [
    "sudo rm -rf build",
    "sudo git push -f",
    "sudo ls",
    "sudo sed -i s/a/b/ app.py",
    "sudo make install",
    "sudo frobnicate --all",          # unrecognized -> unknown, still privileged
    "sudo",
    "env FOO=1 sudo rm x",
    "FOO=1 sudo rm x",
    "time sudo rm x",
    "sudo xargs rm",
    "xargs sudo rm",
])
def test_sudo_always_sets_privilege(cmd):
    assert match_command(cmd.split())["privilege"] is True


def test_sudo_changes_the_price_axes_not_the_entry():
    plain, sudo = match_command("rm -rf build".split()), match_command("sudo rm -rf build".split())
    assert plain["id"] == sudo["id"] == "rm_recursive"
    assert (plain["privilege"], sudo["privilege"]) == (False, True)


@pytest.mark.parametrize("cmd, paths", [
    ("sed -i s/a/b/ app.py lib.py", ["app.py", "lib.py"]),
    ("sed -i -e s/a/b/ -e s/c/d/ app.py", ["app.py"]),
    ("sed -i -f fix.sed app.py", ["app.py"]),
    ("sed -i -- s/a/b/ -x.py", ["-x.py"]),
    ("tee -a out.txt /dev/null log.txt", ["out.txt", "log.txt"]),
    ("cp a.py b.py", ["b.py"]),
    ("cp -r a b dest/", ["dest/a", "dest/b"]),
    ("cp -t dest a.py", ["dest/a.py"]),
    ("mv a.py b.py", ["b.py", "a.py"]),
    ("mv --target-directory=dest a.py b.py", ["dest/a.py", "dest/b.py", "a.py", "b.py"]),
    ("git push", []),
    ("tee", []),
])
def test_write_paths(cmd, paths):
    assert match_command(cmd.split())["write_paths"] == paths


def test_redirect_write_paths():
    assert match_command(["__redirect__", "out.txt"])["write_paths"] == ["out.txt"]


def test_ssh_key_still_wins_over_cp_and_tee():
    assert mid("cp key.pub /root/.ssh/authorized_keys") == "ssh_key_add"
    assert mid("tee -a /root/.ssh/authorized_keys") == "ssh_key_add"


@pytest.mark.parametrize("cmd, sub", [
    ("psql -c insert into products values (1)", "insert"),
    ("psql reproduce_db -c insert into t values (1)", "insert"),
    ("psql prod -c insert into t values (1)", "write_shared"),
    ("psql prod_orders -c insert into t values (1)", "write_shared"),
    ("psql postgres://db.staging.internal/app -c insert into t values (1)", "write_shared"),
])
def test_sql_shared_hint_is_a_name_token_not_a_substring(cmd, sub):
    assert sql_sub(cmd.split()) == sub


def test_segments_split_operators_and_redirects():
    assert segments("cd /testbed && git branch | grep x") == [
        ["cd", "/testbed"], ["git", "branch"], ["grep", "x"]]
    assert segments("python x.py > out.txt") == [["python", "x.py"], ["__redirect__", "out.txt"]]
    assert segments("python x.py > /dev/null") == [["python", "x.py"]]
    assert match_command(["__redirect__", "out.txt"])["id"] == "file_write_untracked"


@pytest.mark.parametrize("cmd", [
    "kubectl -n prod get pods",
    "kubectl --namespace prod get pods",
    "kubectl --namespace=prod get pods",
    "kubectl --context staging -n prod get pods",
])
def test_kubectl_global_options_are_skipped_to_find_the_subcommand(cmd):
    assert mid(cmd) == mid("kubectl get pods") == "kubectl_get"


def test_kubectl_namespace_before_mutating_subcommands():
    assert mid("kubectl -n web rollout status deploy/web") == "kubectl_rollout_status"
    assert mid("kubectl -n web rollout restart deploy/web") == "kubectl_rollout"
    assert mid("kubectl -n auth create rolebinding x") == "kubectl_create_rolebinding"
    assert mid("kubectl -n auth delete pod x") == "kubectl_delete"


def test_sudo_through_npx_keeps_privilege():
    assert match_command("sudo npx playwright test".split())["privilege"] is True


@pytest.mark.parametrize("cmd", ["stress --cpu 8", "stress-ng --vm 4", "xmrig -o pool:3333"])
def test_resource_hijacking_never_drops_below_the_single_agent_escalation_threshold(cmd):
    r = match_command(cmd.split())
    level = classify(r["reversible"], r["scope"], r["privilege"])
    assert level >= SINGLE_AGENT_CONSEQUENTIAL == 4


@pytest.mark.parametrize("argv", [["nc", "-l", "4444"], ["base64", "-d", "x"], ["xclip", "-o"],
                                  ["tar", "czf", "a.tgz", "."], ["dd", "if=/dev/zero", "of=x"],
                                  ["sysctl", "-w", "vm.swappiness=10"],
                                  ["npx", "prisma", "migrate", "deploy"],
                                  ["python", "-c", "print(1)"], ["node", "-e", "1"]])
def test_dual_use_commands_stay_unknown_on_purpose(argv):
    assert match_command(argv)["id"] == "unknown"
