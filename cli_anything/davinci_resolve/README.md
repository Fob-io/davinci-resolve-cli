# cli-anything-davinci-resolve

Command-line interface to **DaVinci Resolve Studio** through its Python scripting API.
Resolve holds all state; the CLI is a thin, stateless layer over the live app.

## Requirements
- DaVinci Resolve Studio running, with Preferences > System > General > *External scripting using: Local*.
- Python 3.10-3.13 (Resolve's `fusionscript` does not load on 3.14).

## Install
```bash
python3.11 -m venv ~/.venvs/resolve-cli
~/.venvs/resolve-cli/bin/pip install "git+https://github.com/Fob-io/davinci-resolve-cli.git"
```
Binary: `~/.venvs/resolve-cli/bin/cli-anything-davinci-resolve`. Check the setup with `cli-anything-davinci-resolve doctor`.

## Usage
```bash
cli-anything-davinci-resolve --json status
cli-anything-davinci-resolve project list
cli-anything-davinci-resolve media import clip.mov --bin Footage
cli-anything-davinci-resolve timeline new "Cut v1"
cli-anything-davinci-resolve marker add 120 --color Red --name "fix audio"
cli-anything-davinci-resolve render add ~/Exports --preset "YouTube 1080p" --name final
cli-anything-davinci-resolve render start
```
Anything without a dedicated command: `api search|describe|call` (see SKILL.md). Example:
`cli-anything-davinci-resolve api call Timeline.GetMarkers`.
No subcommand starts the REPL. Every command supports `--json` (placed before the group: `--json media tree`).
Errors exit 1 and print `{"error": ...}` in JSON mode.

## Tests
```bash
CLI_ANYTHING_FORCE_INSTALLED=1 .venv/bin/python -m pytest cli_anything/davinci_resolve/tests -v -s
```
E2E tests need a running Resolve; they create and delete a scratch project `cliany_test_*` only.
