# Kosha dev/demo commands. `make help` lists them.
PY    := .venv/bin/python
PORT  ?= 8765
.DEFAULT_GOAL := help

.PHONY: help start start-keep stop reset status rehearse test extension bob-install bob-uninstall install package test-installer

help:  ## list the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-11s %s\n", $$1, $$2}'

start: stop  ## fresh demo world + fresh ledger, then run koshad here (asks the approval passphrase; Ctrl+C stops it)
	@$(PY) demo_repo/setup_demo.py
	@echo "In Bob: Developer: Reload Window, wait a few seconds, then open new tabs."
	@.demo/start_koshad.sh

start-keep: stop  ## run koshad on the existing demo world and ledger (no reset)
	@test -x .demo/start_koshad.sh || { echo "no demo world yet: run make start"; exit 1; }
	@.demo/start_koshad.sh

stop:  ## stop koshad (from any terminal) and wait until the port is free
	@pid=$$(ss -ltnp 2>/dev/null | grep ':$(PORT) ' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); \
	if [ -z "$$pid" ]; then \
	  if ss -ltn | grep -q ':$(PORT) '; then echo "port $(PORT) is held by a process that isn't yours; not touching it"; exit 1; fi; \
	  echo "koshad not running"; exit 0; fi; \
	if ! tr '\0' ' ' < /proc/$$pid/cmdline | grep -q koshad; then \
	  echo "port $(PORT) is held by something else (pid $$pid): $$(tr '\0' ' ' < /proc/$$pid/cmdline)"; exit 1; fi; \
	kill -INT $$pid; \
	for i in $$(seq 1 50); do ss -ltn | grep -q ':$(PORT) ' || break; sleep 0.1; done; \
	if ss -ltn | grep -q ':$(PORT) '; then kill -9 $$pid; sleep 0.3; echo "koshad killed (pid $$pid)"; \
	else echo "koshad stopped (pid $$pid)"; fi

reset: stop  ## fresh demo world + fresh ledger, without starting koshad
	@$(PY) demo_repo/setup_demo.py

status:  ## is koshad up, and what's held right now
	@curl -s -m 2 localhost:$(PORT)/approvals >/dev/null 2>&1 || { echo "koshad: not running"; exit 0; }; \
	echo "koshad: running on $(PORT)"; curl -s localhost:$(PORT)/approvals | $(PY) demo_repo/show_bundle.py

rehearse:  ## play the demo story against current code, no Bob needed (must end REHEARSAL OK)
	@$(PY) demo_repo/rehearse.py

test:  ## run the full test suite
	@$(PY) -m pytest -q -p no:cacheprovider

extension:  ## build the Kosha extension and install it into Bob (then Reload Window)
	@$(PY) kosha/adapters/bob_extension/build_vsix.py
	@bob --install-extension dist/kosha-bob-$$($(PY) -c "import json;print(json.load(open('kosha/adapters/bob_extension/package.json'))['version'])").vsix

install:  ## install Bob hook, extension, MCP server and Kosha mode for this workspace
	@$(PY) -m kosha.adapters.install --workspace "$(CURDIR)"

package:  ## build the deployable Docker image (run `docker compose up` afterwards)
	@docker build -t kosha:local .

test-installer:  ## run the complete Bob installer against an isolated fake home and workspace
	@PYTHON="$(PY)" sh scripts/test_installer.sh

bob-install:  ## one-time: Bob global hook for per-tab identity on Kosha's tools + auto-approve them (backed up)
	@$(PY) -m kosha.adapters.bob_install install

bob-uninstall:  ## remove the Kosha hook from Bob's global settings (backed up)
	@$(PY) -m kosha.adapters.bob_install uninstall
