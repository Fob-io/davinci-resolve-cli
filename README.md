# cli-anything-davinci-resolve

Agent-native CLI for **DaVinci Resolve Studio**, built on its Python scripting API and following the
[CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness layout (`cli_anything.davinci_resolve`).
Resolve holds all state; the CLI is a thin, stateless layer. Every command supports `--json`; the whole scripting API
(~500 methods) is reachable through `api search|describe|call`. See
[`SKILL.md`](cli_anything/davinci_resolve/skills/SKILL.md) for the full command reference (agent-readable).

## Requirements
- macOS with **DaVinci Resolve Studio** (external scripting is Studio-only).
- In Resolve: *Preferences > System > General > External scripting using: **Local***.
- **Python 3.10–3.13** (Resolve's `fusionscript` library does not load on 3.14).

## Install (any Mac)
```bash
python3.11 -m venv ~/.venvs/resolve-cli
~/.venvs/resolve-cli/bin/pip install "git+https://github.com/Fob-io/davinci-resolve-cli.git"
ln -s ~/.venvs/resolve-cli/bin/cli-anything-davinci-resolve ~/.local/bin/   # optional: put it on PATH
```
(with `pipx`: `pipx install --python python3.11 git+https://github.com/Fob-io/davinci-resolve-cli.git`)

## First run
```bash
cli-anything-davinci-resolve doctor        # checks Resolve, scripting bridge, optional tools
cli-anything-davinci-resolve --json status # version, page, project, timeline
cli-anything-davinci-resolve               # REPL
```
`--json` goes **before** the command group (`--json media tree`). Errors exit 1 with `{"error": ...}` in JSON mode.
Optional subtitle features need `stable_whisper` (checked by `doctor`).

## Development
```bash
git clone https://github.com/Fob-io/davinci-resolve-cli.git && cd davinci-resolve-cli
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest cli_anything/davinci_resolve/tests/test_core.py -v    # no Resolve needed
```
`test_full_e2e.py` needs a running Resolve; it only creates/deletes a scratch project `cliany_test_*`.
Run it only with Resolve idle: `CLI_ANYTHING_FORCE_INSTALLED=1 .venv/bin/python -m pytest cli_anything/davinci_resolve/tests -v -s`.

## License
MIT, see [LICENSE](LICENSE).
