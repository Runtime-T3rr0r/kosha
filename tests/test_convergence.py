from types import SimpleNamespace

import pytest

from kosha.pricing.convergence import Touch, command_targets, converges, first_convergence, targets_of


@pytest.mark.parametrize("cmd, expected", [
    ("psql -d production -f migrations/x.sql", {"db:production", "path:migrations/x.sql"}),
    ("cd /workspace && chmod +x scripts/deploy.sh", {"path:/workspace/scripts/deploy.sh"}),
    ("sudo chmod -R 777 logs/", {"path:logs"}),
    ("git push --force origin HEAD:main", {"branch:main"}),
    ("git push origin refs/heads/feature-x", {"branch:feature-x"}),
    ("git reset HEAD file.py", set()),
    ("git checkout -b fix/login", {"branch:fix/login"}),
    ("kubectl delete pod web-1 -n prod", {"k8s:prod/pod/web-1"}),
    ("kubectl rollout restart deployment/api", {"k8s:default/deployment/api"}),
    ("kubectl scale deployment api --replicas=3", {"k8s:default/deployment/api"}),
    ("docker rm -f postgres_db", {"container:postgres_db"}),
    ("docker build -t app:1.2 .", {"image:app:1.2"}),
    ("redis-cli -h redis-cache FLUSHALL", {"host:redis-cache"}),
    ("sudo -u app sh -c 'echo x >> logs/app.log'", {"path:logs/app.log"}),
    ("echo hi > out.txt", {"path:out.txt"}),
    ("cat src/a.py", set()),
])
def test_command_targets(cmd, expected):
    assert command_targets(cmd) == expected


def test_heredoc_body_is_not_parsed_as_commands():
    cmd = "cat > src/a.js << 'EOF'\nconst f = (a, b) => { return a > b; };\nEOF"
    assert command_targets(cmd) == {"path:src/a.js"}


def test_targets_of_merges_action_targets_and_command():
    a = SimpleNamespace(targets=["./src/a.py"], raw={"command": "rm -rf build/"}, argv=[])
    assert targets_of(a) == {"path:src/a.py", "path:build"}


def test_converges_needs_a_different_agent():
    hist = [Touch("a1", frozenset({"db:prod"}))]
    assert converges(hist, "a1", {"db:prod"}) is None
    c = converges(hist, "a2", {"db:prod"})
    assert c.target == "db:prod" and c.earlier_agent == "a1" and c.earlier_index == 0


def test_converges_is_exact_not_substring_or_prefix():
    hist = [Touch("a1", frozenset({"db:production-a1", "path:logs"}))]
    assert converges(hist, "a2", {"db:production-a2", "path:logs/app.log", "branch:logs"}) is None


def test_first_convergence_is_causal():
    hist = [Touch("a1", frozenset({"path:x"})), Touch("a1", frozenset({"path:y"})),
            Touch("a2", frozenset({"path:y"}))]
    i, c = first_convergence(hist)
    assert i == 2 and c.earlier_index == 1
    assert first_convergence(hist[:2]) is None



# --- relative command paths resolve against cwd ---

def _act(cwd, targets=(), command=None):
    return SimpleNamespace(targets=list(targets), raw={"command": command} if command else {},
                           argv=[], cwd=cwd)


def _meets(first, second) -> bool:
    """agent-1 does `first`, then agent-2 does `second`: does agent-2 converge?"""
    return converges([Touch("agent-1", targets_of(first))], "agent-2", targets_of(second)) is not None


def test_edit_file_absolute_and_sed_relative_converge():
    edit = _act("/w", targets=["/w/VERSION"])
    sed = _act("/w", command="sed -i s/a/b/ VERSION")
    assert "path:/w/VERSION" in targets_of(edit) & targets_of(sed)
    assert _meets(edit, sed) and _meets(sed, edit)


@pytest.mark.parametrize("command, twin", [
    ("git add VERSION", "/w/VERSION"),
    ("echo x > VERSION", "/w/VERSION"),
    ("kubectl apply -f k.yaml", "/w/k.yaml"),
    ("psql -f m.sql", "/w/m.sql"),
])
def test_command_converges_with_absolute_path_twin(command, twin):
    assert _meets(_act("/w", targets=[twin]), _act("/w", command=command))


def test_cd_then_relative_path_resolves_against_the_new_dir():
    assert targets_of(_act("/w", command="cd sub && rm x")) == {"path:/w/sub/x"}


def test_absolute_paths_are_unchanged():
    assert targets_of(_act("/w", command="rm /abs/x")) == {"path:/abs/x"}
    assert targets_of(_act("/w", targets=["/abs/y"])) == {"path:/abs/y"}


def test_different_files_do_not_converge():
    assert not _meets(_act("/w", targets=["/w/VERSION"]), _act("/w", command="sed -i s/a/b/ OTHER"))


def test_same_relative_path_in_different_cwds_does_not_converge():
    assert not _meets(_act("/w1", command="sed -i s/a/b/ VERSION"),
                      _act("/w2", command="sed -i s/a/b/ VERSION"))


def test_non_file_targets_are_not_resolved_against_cwd():
    assert targets_of(_act("/w", command="psql -d app -c 'select 1'")) == {"db:app"}
    assert targets_of(_act("/w", command="git push origin main")) == {"branch:main"}


def test_without_cwd_paths_stay_relative():
    assert targets_of(_act("", command="sed -i s/a/b/ VERSION")) == {"path:VERSION"}
