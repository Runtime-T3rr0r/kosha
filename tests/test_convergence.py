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
