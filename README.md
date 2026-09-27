# Kosha

Kosha is a deterministic shared-budget gateway for AI coding agents. It classifies tool calls by reversibility, scope, and privilege, then allows, holds for human approval, or denies them. It is a guardrail for cooperative agents, not a security sandbox.

## Install for Bob

Install the Python package, start the daemon, then run the one-command Bob installer in the workspace you want to govern:

```sh
python -m venv .venv && .venv/bin/pip install .
.venv/bin/koshad
# in another terminal, from the target workspace
/absolute/path/to/.venv/bin/kosha-install --workspace .
```

`kosha-install` backs up and updates only Kosha's entries: it installs the Bob approval extension, adds the global Kosha identity hook, registers one workspace MCP server, and creates the **Kosha** mode. It needs the `bob` CLI on `PATH`; use `--no-extension` for a headless configuration run. Reload Bob and open a new task in the Kosha mode.

The installer gives Kosha MCP tools auto-approval inside that mode because Kosha makes the policy decision. Other Bob tools and configurations are preserved. `make bob-uninstall` removes the global identity hook; workspace `.bob` files have timestamped backups beside them.

The extension is a read-only monitor. It watches `koshad` at `http://127.0.0.1:8765`, shows `Kosha: <held> held · fleet <spent>/<budget>` in Bob’s status bar, opens the approval page on new holds, and offers a Review button. Human approval still happens only in koshad’s page with the terminal-supplied passphrase; the extension never receives it.

## Docker deployment

The container packages `koshad`, its policy table, UI, and a persistent SQLite ledger. It is intentionally local-only by default:

```sh
docker compose up --build
```

It prompts for the approval passphrase on the attached terminal and persists the ledger in the `kosha-data` volume. The published port is bound to `127.0.0.1`, so the local Bob extension and local MCP process use their default URL unchanged. For database aliases, copy `config/kosha.example.yaml` to the ignored `config/kosha.yaml` and uncomment the read-only mount in `compose.yaml`.

Run `make test` for the test suite, `make extension` to build and install only the VSIX, and `make package` to build the image.

To exercise the installer safely without Bob or changing any real settings, run `make test-installer`. It uses a temporary home, workspace, and fake Bob executable, then verifies the hook, VSIX invocation, MCP registration, and mode configuration.
