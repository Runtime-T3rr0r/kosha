#!/bin/sh
# End-to-end installer check with a fake Bob executable. It never reads or writes the
# caller's HOME or workspace. The fixture is deliberately retained under /tmp for review.
set -eu

pybin=${PYTHON:-.venv/bin/python}
test_dir=$(mktemp -d /tmp/kosha-installer-e2e.XXXXXX)
mkdir -p "$test_dir/work/.bob" "$test_dir/bin"

printf '%s\n' '#!/bin/sh' 'printf "%s\n" "$@" > "$KOSHA_FAKE_BOB_LOG"' > "$test_dir/bin/bob"
chmod +x "$test_dir/bin/bob"
printf '%s\n' '{"mcpServers":{"unrelated":{"command":"keep-me"}}}' > "$test_dir/work/.bob/mcp.json"
printf '%s\n' 'customModes:' '  - slug: unrelated' '    name: Keep me' > "$test_dir/work/.bob/custom_modes.yaml"

HOME="$test_dir/home" PATH="$test_dir/bin:$PATH" KOSHA_FAKE_BOB_LOG="$test_dir/bob.log" \
  "$pybin" -m kosha.adapters.install --workspace "$test_dir/work" --session isolated-fleet

"$pybin" - "$test_dir" <<'PY'
import json
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
settings = json.loads((root / "home/.bob/settings/settings.json").read_text())
mcp = json.loads((root / "work/.bob/mcp.json").read_text())["mcpServers"]
modes = yaml.safe_load((root / "work/.bob/custom_modes.yaml").read_text())["customModes"]

assert settings["approval"]["allowed_permissions"] == ["mcp", "todo"]
assert settings["hooks"]["PreToolUse"][0]["matcher"] == "^mcp__kosha__"
assert mcp["unrelated"] == {"command": "keep-me"}
assert mcp["kosha"]["args"][3] == "isolated-fleet"
assert [mode["slug"] for mode in modes] == ["unrelated", "kosha"]
assert (root / "bob.log").read_text().endswith(".vsix\n")
print("isolated installer E2E OK")
PY

printf 'Fixture retained for inspection: %s\n' "$test_dir"
