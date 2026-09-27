# Kosha for Bob

Opens Kosha's approval panel inside Bob the moment an agent's action is held.

- **Status bar:** `Kosha: 2 held · fleet 70/750`. Click it to open the panel.
- **On every new hold:** the panel opens beside your editor (without taking focus), plus a
  notification naming the agent, the command and the rule, with a **Review** button.
- **The panel** is koshad's own approval page (`/ui`): unlock with the approval passphrase you
  gave koshad at start-up, then **Approve once / Approve & reset window / Deny**.

The extension only reads from koshad (`/stream`, `/approvals`, `/sessions`). It can't approve
anything itself, and the passphrase never passes through it. Holds that happened before Bob
started are counted in the status bar but never pop up.

Settings: `kosha.koshadUrl` (default `http://127.0.0.1:8765`) and `kosha.autoOpen` (default on).

Build and install:

```sh
python kosha/adapters/bob_extension/build_vsix.py      # -> dist/kosha-bob-0.1.0.vsix
bob --install-extension dist/kosha-bob-0.1.0.vsix
```
