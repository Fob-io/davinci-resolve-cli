"""Agent ergonomics: `doctor`, `schema list|show`, and `--dry-run` on every command that changes things.

`--dry-run` is added here, once, to every command not listed in READ_ONLY (a new command is therefore
guarded by default). Commands that already have their own `--dry-run` (it checks real state, e.g.
color copy-grade) keep it. The generic preview only echoes the command and its parameters:
nothing is sent to Resolve and nothing is validated.
"""
import click

from cli_anything.davinci_resolve.core import doctor as core_doctor
from cli_anything.davinci_resolve.core import render

READ_ONLY = {
    "api describe", "api search", "analysis status", "analysis refresh-luts", "color export-lut", "color groups",
    "color nodes", "color versions", "item effects list", "effects search", "fusion comps", "fusion export-comp", "fusion get-input", "fusion inputs",
    "fusion tools", "item export-frame", "item markers", "item props", "item takes", "layout export", "layout list",
    "media clips", "media markers", "media metadata", "media props", "media transcript", "media tree",
    "project cd", "project databases", "project export", "project folders", "project get-setting", "project info",
    "project list", "project use-database", "render burnin-presets", "render busy", "render formats", "render jobs",
    "render preset-export", "render presets", "render wait", "settings get", "settings presets", "stills albums",
    "stills album-use", "stills export", "stills list", "storage ls", "storage reveal", "storage volumes",
    "subs check-voice", "subs export-voice", "subs generate-status", "subs list", "subs transcribe",
    "timeline dump", "timeline export", "timeline info", "timeline inout", "timeline items", "timeline list", "timeline playhead",
    "timeline set", "timeline tracks", "timeline versions", "marker list", "status", "launch", "repl", "doctor",
    "schema list", "schema show", "api call",  # api call has its own --yes gate for destructive methods
}


def _walk(group, prefix=""):
    for name, cmd in sorted(group.commands.items()):
        if isinstance(cmd, click.Group):
            yield from _walk(cmd, f"{prefix}{name} ")
        else:
            yield f"{prefix}{name}", cmd


def _dry_run_callback(path, callback):
    def run(*args, dry_run=False, **kwargs):
        if not dry_run:
            return callback(*args, **kwargs)
        ctx = click.get_current_context()
        from cli_anything.davinci_resolve.davinci_resolve_cli import _emit
        _emit(ctx.find_root(), {"dry_run": True, "command": path, "params": ctx.params | {"dry_run": True},
                                "note": "preview only: nothing was sent to Resolve or validated"})
    run.__name__ = getattr(callback, "__name__", "run")
    return run


def _add_dry_run(cli):
    for path, cmd in _walk(cli):
        if path in READ_ONLY or any(p.name == "dry_run" for p in cmd.params):
            continue
        cmd.params.append(click.Option(["--dry-run"], is_flag=True, help="Preview the command, change nothing."))
        cmd.callback = _dry_run_callback(path, cmd.callback)


def _describe(path, cmd):
    return {
        "command": path,
        "help": (cmd.help or "").strip(),
        "mutating": path not in READ_ONLY,
        "params": [
            {
                "name": p.name,
                "flags": list(p.opts) if isinstance(p, click.Option) else None,
                "kind": "option" if isinstance(p, click.Option) else "argument",
                "type": p.type.name,
                "required": p.required,
                "multiple": p.multiple or getattr(p, "nargs", 1) != 1,
                "default": p.default if isinstance(p.default, (str, int, float, bool)) else None,
                "choices": list(p.type.choices) if isinstance(p.type, click.Choice) else None,
                "help": getattr(p, "help", None),
            }
            for p in cmd.params if p.name != "help"
        ],
    }


def register(cli, run, emit):
    _add_dry_run(cli)  # before doctor/schema exist; they are read-only anyway

    @cli.command()
    @click.pass_context
    def doctor(ctx):
        """Check the scripting bridge, Resolve and the optional tools (ffmpeg, stable-ts). Exit 2 if Resolve is unreachable."""
        data = core_doctor.report()
        emit(ctx, data)
        ctx.exit(0 if data["ok"] else 2 if not data["reachable"] else 1)

    @cli.group()
    def schema():
        """Command discovery for agents: parameters of every command."""

    @schema.command("list")
    @click.pass_context
    def schema_list(ctx):
        """Every command with its one-line help and whether it changes anything."""
        emit(ctx, [{"command": p, "mutating": p not in READ_ONLY, "help": (c.help or "").strip().split("\n")[0]}
                   for p, c in _walk(cli)])

    @schema.command("show")
    @click.argument("command", nargs=-1, required=True)
    @click.pass_context
    def schema_show(ctx, command):
        """Parameters (name, type, required, default, choices) of COMMAND, e.g. schema show timeline delete-clips."""
        wanted = " ".join(command)
        for path, cmd in _walk(cli):
            if path == wanted:
                return emit(ctx, _describe(path, cmd))
        raise click.UsageError(f"No command '{wanted}'. Use: schema list")

    render_group = cli.commands["render"]

    @render_group.command("wait")
    @click.argument("job_id")
    @click.option("--interval", type=float, default=2.0, show_default=True, help="Seconds between polls.")
    @click.option("--timeout", type=float, help="Give up after this many seconds (job keeps running).")
    @click.pass_context
    def render_wait(ctx, job_id, interval, timeout):
        """Block until render job JOB_ID completes; progress goes to stderr. Fails if the job fails."""
        def progress(status):
            click.echo(f"{status.get('JobStatus')} {status.get('CompletionPercentage', 0)}%", err=True)
        run(ctx, render.wait, job_id, interval, timeout, progress)
