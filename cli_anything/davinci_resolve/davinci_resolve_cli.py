"""cli-anything-davinci-resolve: command-line interface to a running DaVinci Resolve Studio."""
import json
import shlex

import click

from cli_anything.davinci_resolve.core import api as core_api
from cli_anything.davinci_resolve import agent, commands_extra
from cli_anything.davinci_resolve.core import color, fusion, media, project, render, stills, subtitles, timeline
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, connect
from cli_anything.davinci_resolve.utils.watchdog import DEFAULT_TIMEOUT, Guard, seconds_for

VERSION = "1.0.0"


def _pretty(data, indent=0):
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                click.echo(f"{pad}{k}:")
                _pretty(v, indent + 1)
            else:
                click.echo(f"{pad}{k}: {v}")
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, dict):
                click.echo(f"{pad}- " + ", ".join(f"{k}={x}" for k, x in v.items() if not isinstance(x, (dict, list))))
                for k, x in v.items():
                    if isinstance(x, (dict, list)) and x:
                        click.echo(f"{pad}  {k}:")
                        _pretty(x, indent + 2)
            else:
                click.echo(f"{pad}- {v}")
    else:
        click.echo(f"{pad}{data}")


def _emit(ctx, data):
    if ctx.obj["json"]:
        click.echo(json.dumps(data, default=str))
    else:
        _pretty(data)


def _run(ctx, fn, *args, offline=False, **kwargs):
    """Call core function (connecting to Resolve unless offline), print result as JSON or text."""
    command = " ".join(ctx.command_path.split()[1:])
    try:
        with Guard(seconds_for(command, ctx.obj.get("call_timeout", DEFAULT_TIMEOUT)), command, ctx.obj["json"]):
            data = fn(*args, **kwargs) if offline else fn(connect(), *args, **kwargs)
    except ResolveError as e:
        if ctx.obj["json"]:
            click.echo(json.dumps({"error": str(e), "error_code": e.code, **({"hint": e.hint} if e.hint else {})}))
        else:
            click.echo(f"error: {e}", err=True)
            if e.hint:
                click.echo(f"hint: {e.hint}", err=True)
        ctx.exit(e.exit_code)
    _emit(ctx, data)


@click.group(invoke_without_command=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option("--call-timeout", type=float, default=DEFAULT_TIMEOUT, show_default=True, envvar="RESOLVE_CLI_TIMEOUT",
              help="Seconds before a command that Resolve does not answer fails with error_code=timeout (0 = never).")
@click.pass_context
def cli(ctx, as_json, call_timeout):
    """Drive DaVinci Resolve Studio (must be running, external scripting = Local)."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json
    ctx.obj["call_timeout"] = call_timeout
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command()
@click.pass_context
def status(ctx):
    """Resolve version, current page, project and timeline."""
    _run(ctx, project.status)


@cli.command()
@click.option("--timeout", type=int, default=60, show_default=True)
@click.pass_context
def launch(ctx, timeout):
    """Open DaVinci Resolve if needed and wait until it answers."""
    _run(ctx, project.launch, timeout, offline=True)


# ---- project ---------------------------------------------------------------
@cli.group("project")
def project_group():
    """Open, create, save and configure projects."""


@project_group.command("list")
@click.pass_context
def project_list(ctx):
    """Projects in the current database folder."""
    _run(ctx, project.list_projects)


@project_group.command("info")
@click.pass_context
def project_info(ctx):
    """Current project summary."""
    _run(ctx, project.info)


@project_group.command("open")
@click.argument("name")
@click.pass_context
def project_open(ctx, name):
    """Save the current project, then open NAME, or a path from the root ("Folder/Sub/Name"). Retries LoadProject."""
    _run(ctx, project.open_project, name)


@project_group.command("new")
@click.argument("name")
@click.pass_context
def project_new(ctx, name):
    """Create project NAME."""
    _run(ctx, project.create_project, name)


@project_group.command("save")
@click.pass_context
def project_save(ctx):
    """Save the current project."""
    _run(ctx, project.save_project)


@project_group.command("get-setting")
@click.argument("key")
@click.pass_context
def project_get_setting(ctx, key):
    """Read a project setting (e.g. timelineFrameRate)."""
    _run(ctx, project.get_setting, key)


@project_group.command("set-setting")
@click.argument("key")
@click.argument("value")
@click.pass_context
def project_set_setting(ctx, key, value):
    """Write a project setting; prints the value read back."""
    _run(ctx, project.set_setting, key, value)


# ---- media -----------------------------------------------------------------
@cli.group("media")
def media_group():
    """Media pool bins and clips."""


@media_group.command("tree")
@click.pass_context
def media_tree(ctx):
    """Bin tree with clip counts."""
    _run(ctx, media.tree)


@media_group.command("clips")
@click.option("--bin", "bin_path", default="", help="Bin path like 'Footage/Day1' (default: root).")
@click.pass_context
def media_clips(ctx, bin_path):
    """Clips in a bin."""
    _run(ctx, media.list_clips, bin_path)


@media_group.command("new-bin")
@click.argument("name")
@click.option("--parent", default="", help="Parent bin path (default: root).")
@click.pass_context
def media_new_bin(ctx, name, parent):
    """Create a bin."""
    _run(ctx, media.create_bin, name, parent)


@media_group.command("import")
@click.argument("paths", nargs=-1, required=True)
@click.option("--bin", "bin_path", default="", help="Destination bin path (default: root).")
@click.option("--create", is_flag=True, help="Create the bin (and parents) if missing.")
@click.pass_context
def media_import(ctx, paths, bin_path, create):
    """Import files into the media pool."""
    _run(ctx, media.import_media, list(paths), bin_path, create)


def _clip_selector(fn):
    """--clip / --bin / --recursive options shared by the bulk clip commands."""
    for opt in reversed((
        click.option("--clip", help="One media-pool clip by name."),
        click.option("--bin", "bin_path", help="Every clip in a bin ('/' = root)."),
        click.option("--recursive", is_flag=True, help="With --bin: include sub-bins."),
    )):
        fn = opt(fn)
    return fn


@media_group.command("props")
@click.option("--key", "keys", multiple=True, help="Property name (repeatable). Default: all.")
@_clip_selector
@click.pass_context
def media_props(ctx, keys, clip, bin_path, recursive):
    """Read clip properties (GetClipProperty) for a clip or a bin."""
    _run(ctx, media.clip_props, list(keys), clip=clip, bin_path=bin_path, recursive=recursive)


@media_group.command("set-prop")
@click.argument("key")
@click.argument("value")
@click.option("--overwrite", is_flag=True, help="Replace existing different values (default: skip them).")
@_clip_selector
@click.pass_context
def media_set_prop(ctx, key, value, overwrite, clip, bin_path, recursive):
    """Set a clip property; non-empty existing values are kept unless --overwrite."""
    _run(ctx, media.set_clip_property, key, value, overwrite, clip=clip, bin_path=bin_path, recursive=recursive)


@media_group.command("metadata")
@click.option("--key", "keys", multiple=True, help="Metadata name (repeatable). Default: all.")
@_clip_selector
@click.pass_context
def media_metadata(ctx, keys, clip, bin_path, recursive):
    """Read clip metadata (GetMetadata) for a clip or a bin."""
    _run(ctx, media.clip_metadata, list(keys), clip=clip, bin_path=bin_path, recursive=recursive)


@media_group.command("set-metadata")
@click.argument("key")
@click.argument("value")
@click.option("--overwrite", is_flag=True, help="Replace existing different values (default: skip them).")
@_clip_selector
@click.pass_context
def media_set_metadata(ctx, key, value, overwrite, clip, bin_path, recursive):
    """Set a metadata field; non-empty existing values are kept unless --overwrite."""
    _run(ctx, media.set_clip_metadata, key, value, overwrite, clip=clip, bin_path=bin_path, recursive=recursive)


@media_group.command("set-color")
@click.argument("color")
@click.option("--overwrite", is_flag=True, help="Replace an existing different color (default: skip).")
@_clip_selector
@click.pass_context
def media_set_color(ctx, color, overwrite, clip, bin_path, recursive):
    """Set the clip color; an existing different color is kept unless --overwrite."""
    _run(ctx, media.set_clip_color, color, overwrite, clip=clip, bin_path=bin_path, recursive=recursive)


@media_group.command("transcript")
@click.argument("clip")
@click.pass_context
def media_transcript(ctx, clip):
    """Native transcription of one clip."""
    _run(ctx, media.transcript, clip)


# ---- timeline --------------------------------------------------------------
@cli.group("timeline")
def timeline_group():
    """Timelines, track items, interchange export."""


@timeline_group.command("list")
@click.pass_context
def timeline_list(ctx):
    """All timelines in the project."""
    _run(ctx, timeline.list_timelines)


@timeline_group.command("new")
@click.argument("name")
@click.pass_context
def timeline_new(ctx, name):
    """Create an empty timeline."""
    _run(ctx, timeline.create, name)


@timeline_group.command("set")
@click.argument("name")
@click.pass_context
def timeline_set(ctx, name):
    """Make NAME the current timeline."""
    _run(ctx, timeline.set_current, name)


@timeline_group.command("info")
@click.pass_context
def timeline_info(ctx):
    """Current timeline frame range and track counts."""
    _run(ctx, timeline.info)


@timeline_group.command("items")
@click.option("--type", "track_type", type=click.Choice(["video", "audio", "subtitle"]), default="video")
@click.option("--track", default=1, show_default=True)
@click.option("--full", is_flag=True, help="Add source clip, left/right offset, source frames, enabled.")
@click.option("--text", is_flag=True, help="Add the text of titles (Text+).")
@click.pass_context
def timeline_items(ctx, track_type, track, full, text):
    """Items on a track, each with a kind: clip, still, compound, fusion_clip, nested_timeline, title, generator, adjustment, transition, subtitle."""
    _run(ctx, timeline.items, track_type, track, full, text)


@timeline_group.command("dump")
@click.option("--timeline", "timeline_name", help="Timeline to dump (default: current).")
@click.option("--all", "every", is_flag=True, help="Every timeline of the project.")
@click.option("--no-text", is_flag=True, help="Skip reading title text (faster, avoids Fusion calls).")
@click.option("--no-markers", is_flag=True)
@click.pass_context
def timeline_dump(ctx, timeline_name, every, no_text, no_markers):
    """Whole structure in one call: tracks, items (kind, start, duration, title text) and markers."""
    _run(ctx, timeline.dump, timeline_name, every, not no_text, not no_markers)


@timeline_group.command("inout")
@click.argument("mark_in", type=int, required=False)
@click.argument("mark_out", type=int, required=False)
@click.option("--clear", is_flag=True, help="Clear the In/Out range.")
@click.pass_context
def timeline_inout(ctx, mark_in, mark_out, clear):
    """Show, set (frames relative to timeline start) or clear In/Out."""
    _run(ctx, timeline.mark_in_out, mark_in, mark_out, clear)


@timeline_group.command("tracks")
@click.pass_context
def timeline_tracks(ctx):
    """All tracks with name, enabled and locked state."""
    _run(ctx, timeline.tracks)


@timeline_group.command("add-track")
@click.argument("track_type", type=click.Choice(["video", "audio", "subtitle"]))
@click.pass_context
def timeline_add_track(ctx, track_type):
    """Add a track (a subtitle track must exist before generating or inserting subs)."""
    _run(ctx, timeline.add_track, track_type)


@timeline_group.command("track-enable")
@click.argument("track_type", type=click.Choice(["video", "audio", "subtitle"]))
@click.argument("index", type=int)
@click.argument("state", type=click.Choice(["on", "off"]))
@click.pass_context
def timeline_track_enable(ctx, track_type, index, state):
    """Enable or disable a track."""
    _run(ctx, timeline.set_track_enabled, track_type, index, state == "on")


@timeline_group.command("playhead")
@click.argument("timecode", required=False)
@click.pass_context
def timeline_playhead(ctx, timecode):
    """Show the playhead timecode, or move it to TIMECODE (e.g. 01:00:10:00)."""
    _run(ctx, timeline.playhead, timecode)


@timeline_group.command("append")
@click.argument("clip_name")
@click.option("--track", help="Destination like video:2 or audio:1; missing tracks are created.")
@click.option("--start", type=int, help="First source frame (source fps).")
@click.option("--end", type=int, help="Last source frame, inclusive (source fps).")
@click.option("--frames", type=int, help="Exact length on the timeline, in timeline frames (stills too; retried if the clip fps differs).")
@click.option("--at", type=int, help="Timeline-relative record frame (default: end of track).")
@click.option("--kind", type=click.Choice(["video", "audio", "both"]), help="Which streams to append (default: both, or audio on an audio track).")
@click.option("--timeline", "timeline_name", help="Timeline NAME (default: current). A non-current timeline is switched to and back.")
@click.pass_context
def timeline_append(ctx, clip_name, track, start, end, frames, at, kind, timeline_name):
    """Append a media-pool clip (by name) to a timeline, optionally a range, on a track, at a position."""
    _run(ctx, timeline.append_clip, clip_name, track, start, end, frames, at, kind, timeline_name)


@timeline_group.command("export")
@click.argument("path")
@click.option("--format", "fmt", default="drt", show_default=True, help="drt, fcpxml_1_10, edl, aaf, ...")
@click.pass_context
def timeline_export(ctx, path, fmt):
    """Export the current timeline."""
    _run(ctx, timeline.export, path, fmt)


# ---- markers ---------------------------------------------------------------
@cli.group("marker")
def marker_group():
    """Markers on the current timeline (frames are timeline-relative)."""


@marker_group.command("list")
@click.pass_context
def marker_list(ctx):
    """All timeline markers."""
    _run(ctx, timeline.list_markers)


@marker_group.command("add")
@click.argument("frame", type=int)
@click.option("--shift", is_flag=True, help="If the frame is taken, use the next free one.")
@click.option("--color", default="Blue", show_default=True)
@click.option("--name", default="")
@click.option("--note", default="")
@click.option("--duration", type=int, default=1, show_default=True)
@click.pass_context
def marker_add(ctx, frame, shift, color, name, note, duration):
    """Add a marker at FRAME."""
    _run(ctx, timeline.add_marker, frame, color, name, note, duration, shift)


@marker_group.command("delete")
@click.argument("frame", type=int, required=False)
@click.option("--name", help="Delete every marker with this name instead.")
@click.pass_context
def marker_delete(ctx, frame, name):
    """Delete the marker at FRAME, or all markers named --name."""
    _run(ctx, timeline.delete_marker, frame, name)


# ---- subtitles -------------------------------------------------------------
@cli.group("subs")
def subs_group():
    """Reel subtitles: voice export, transcription, generate, rebuild track, voice check."""


@subs_group.command("list")
@click.pass_context
def subs_list(ctx):
    """Subtitles on the subtitle track."""
    _run(ctx, subtitles.list_subs)


@subs_group.command("export-voice")
@click.option("--track", default=1, show_default=True, help="Audio track with the voice.")
@click.option("--out", help="WAV path (default: temp bsd-voce.wav).")
@click.option("--method", type=click.Choice(["auto", "files", "render"]), default="auto", show_default=True,
              help="files = ffmpeg on source clips; render = render the track from Resolve (multicam/compound).")
@click.pass_context
def subs_export_voice(ctx, track, out, method):
    """Voice track as 16 kHz mono WAV, aligned to the timeline start (needs ffmpeg)."""
    _run(ctx, subtitles.export_voice, track, out, method)


@subs_group.command("transcribe")
@click.argument("wav")
@click.option("--model", default="large-v3-turbo", show_default=True)
@click.pass_context
def subs_transcribe(ctx, wav, model):
    """Word timings in absolute frames with stable-ts; writes WAV.words.json. Never installs."""
    _run(ctx, subtitles.transcribe, wav, model)


@subs_group.command("generate")
@click.option("--max-chars", type=int, default=20, show_default=True)
@click.option("--voice-track", type=int, default=1, show_default=True)
@click.pass_context
def subs_generate(ctx, max_chars, voice_track):
    """Resolve's internal auto-captions (fallback). Other audio tracks are muted meanwhile."""
    _run(ctx, subtitles.generate, max_chars, voice_track)


@subs_group.command("generate-status")
@click.pass_context
def subs_generate_status(ctx):
    """Progress of the auto-caption job."""
    _run(ctx, subtitles.generate_status)


@subs_group.command("apply")
@click.argument("plan", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def subs_apply(ctx, plan):
    """Rebuild the whole subtitle track from a JSON PLAN (timeline, maxc, ranges, ext_words, replace, confirmed)."""
    with open(plan, encoding="utf-8") as f:
        data = json.load(f)
    _run(ctx, subtitles.apply_plan, data)


@subs_group.command("check-voice")
@click.argument("wav")
@click.option("--threshold", type=int, default=4, show_default=True, help="Frames of offset to report.")
@click.pass_context
def subs_check_voice(ctx, wav, threshold):
    """Offset between subtitle starts and voice onsets. Reports only."""
    _run(ctx, subtitles.check_voice, wav, threshold)


@subs_group.command("cleanup")
@click.option("--yes", is_flag=True, help="Confirm removal of the bsd-*.srt items imported by this tool.")
@click.pass_context
def subs_cleanup(ctx, yes):
    """Remove from the media pool only the bsd-*.srt files this tool imported."""
    _run(ctx, subtitles.cleanup, yes)


# ---- render ----------------------------------------------------------------
@cli.group("render")
def render_group():
    """Render presets and queue."""


@render_group.command("presets")
@click.pass_context
def render_presets(ctx):
    """Available render presets."""
    _run(ctx, render.presets)


@render_group.command("add")
@click.argument("target_dir", type=click.Path())
@click.option("--preset")
@click.option("--name", help="Output file name (no extension).")
@click.pass_context
def render_add(ctx, target_dir, preset, name):
    """Queue the current timeline for rendering."""
    _run(ctx, render.add_job, target_dir, preset, name)


@render_group.command("jobs")
@click.pass_context
def render_jobs(ctx):
    """Render queue with status."""
    _run(ctx, render.jobs)


@render_group.command("start")
@click.argument("job_ids", nargs=-1)
@click.pass_context
def render_start(ctx, job_ids):
    """Start rendering (all queued jobs, or the given ids)."""
    _run(ctx, render.start, list(job_ids) or None)


@render_group.command("delete")
@click.argument("job_id")
@click.pass_context
def render_delete(ctx, job_id):
    """Remove a job from the queue."""
    _run(ctx, render.delete_job, job_id)


@render_group.command("busy")
@click.pass_context
def render_busy(ctx):
    """Is a render running?"""
    _run(ctx, render.rendering_in_progress)


def _item_selector(fn):
    """--timeline / --track / --item: the TimelineItem a command acts on."""
    for opt in reversed((
        click.option("--timeline", help="Timeline name (default: current)."),
        click.option("--track", default="video:1", show_default=True, help="Track like video:1."),
        click.option("--item", type=int, required=True, help="1-based item position on the track."),
    )):
        fn = opt(fn)
    return fn


# ---- stills ------------------------------------------------------------------
@cli.group("stills")
def stills_group():
    """Gallery stills: list, grab, export (image + .drx grade), delete."""


def _still_pick(fn):
    for opt in reversed((
        click.option("--label", "labels", multiple=True, help="Still label (repeatable)."),
        click.option("--index", "indexes", type=int, multiple=True, help="1-based album position (repeatable)."),
        click.option("--all", "all_stills", is_flag=True, help="Every still in the album."),
        click.option("--album", help="Album name (default: current)."),
    )):
        fn = opt(fn)
    return fn


@stills_group.command("list")
@click.option("--album", help="Album name (default: current).")
@click.pass_context
def stills_list(ctx, album):
    """Stills in the album with their labels."""
    _run(ctx, stills.list_stills, album)


@stills_group.command("grab")
@click.option("--label", help="Label for the new still.")
@click.option("--at", help="Move the playhead to this timecode first (waits, retries).")
@click.pass_context
def stills_grab(ctx, label, at):
    """Grab a still of the clip under the playhead."""
    _run(ctx, stills.grab, label, at)


@stills_group.command("export")
@click.argument("directory")
@click.option("--prefix", default="still", show_default=True)
@click.option("--format", "fmt", default="png", show_default=True, help="png, jpg, tif, drx, ...")
@_still_pick
@click.pass_context
def stills_export(ctx, directory, prefix, fmt, labels, indexes, all_stills, album):
    """Export stills; the folder is created, a .drx grade is written next to each image."""
    _run(ctx, stills.export, directory, prefix, fmt, labels, indexes, all_stills, album)


@stills_group.command("delete")
@click.option("--yes", is_flag=True, help="Confirm: deleted stills cannot be recovered.")
@_still_pick
@click.pass_context
def stills_delete(ctx, yes, labels, indexes, all_stills, album):
    """Delete stills from the album."""
    _run(ctx, stills.delete, labels, indexes, all_stills, album, yes)


# ---- color -------------------------------------------------------------------
@cli.group("color")
def color_group():
    """Color page: node graph of a timeline item."""


@color_group.command("nodes")
@_item_selector
@click.option("--layer", type=int, default=1, show_default=True)
@click.pass_context
def color_nodes(ctx, timeline, track, item, layer):
    """Nodes of an item's graph: label, color, tools, LUT."""
    _run(ctx, color.nodes, timeline, track, item, layer)


@color_group.command("copy-grade")
@click.option("--timeline", default=None, help="Timeline name (default: current).")
@click.option("--src-track", required=True, help="Source track, e.g. video:3.")
@click.option("--dst-track", required=True, help="Destination track, e.g. video:1.")
@click.option("--src-item", type=int, default=None, help="1-based source item; omit to match by media file name.")
@click.option("--dst-item", "dst_items", type=int, multiple=True, help="Destination item (repeatable); default all.")
@click.option("--clip-color", default=None, help="Only destinations with this clip color (e.g. Lime).")
@click.option("--src-from", type=int, default=0, show_default=True, help="Match sources starting at/after this frame.")
@click.option("--overwrite", is_flag=True, help="Also replace destinations that already have a grade (>1 node).")
@click.option("--version", default=None, help="Save the current destination grade as this version first.")
@click.option("--dry-run", is_flag=True, help="Show what would be copied, change nothing.")
@click.pass_context
def color_copy_grade(ctx, timeline, src_track, dst_track, src_item, dst_items, clip_color, src_from, overwrite, version, dry_run):
    """Copy a grade (Color page opened for the copy, then restored); verified by node count."""
    _run(ctx, color.copy_grade, timeline, src_track, dst_track, src_item, dst_items, clip_color,
         src_from, overwrite, version, dry_run)


@color_group.command("apply-drx")
@click.argument("path", type=click.Path())
@click.option("--timeline", default=None)
@click.option("--track", required=True, help="Track, e.g. video:1.")
@click.option("--item", "items", type=int, multiple=True, help="Item (repeatable); default whole track.")
@click.option("--mode", type=click.IntRange(0, 2), default=0, show_default=True, help="0 no keyframes, 1 source TC, 2 start frames.")
@click.option("--overwrite", is_flag=True, help="Also replace items that already have a grade (>1 node).")
@click.pass_context
def color_apply_drx(ctx, path, timeline, track, items, mode, overwrite):
    """Apply a .drx grade file to items of a track; graded items are skipped unless --overwrite."""
    _run(ctx, color.apply_drx, timeline, path, track, items, mode, overwrite)


@color_group.command("set-cdl")
@_item_selector
@click.option("--node", type=int, default=1, show_default=True)
@click.option("--slope", default="1 1 1", show_default=True, help="'R G B'.")
@click.option("--offset", default="0 0 0", show_default=True)
@click.option("--power", default="1 1 1", show_default=True)
@click.option("--saturation", type=float, default=1.0, show_default=True)
@click.pass_context
def color_set_cdl(ctx, timeline, track, item, node, slope, offset, power, saturation):
    """Set CDL (slope/offset/power/saturation) on a node of an item."""
    _run(ctx, color.set_cdl, timeline, track, item, slope, offset, power, saturation, node)


# ---- fusion ------------------------------------------------------------------
@cli.group("fusion")
def fusion_group():
    """Fusion comps on a timeline item: tools and inputs."""


def _comp_option(fn):
    return click.option("--comp", help="Comp name or 1-based index (default: first).")(fn)


@fusion_group.command("comps")
@_item_selector
@click.pass_context
def fusion_comps(ctx, timeline, track, item):
    """Fusion comps on the item."""
    _run(ctx, fusion.comps, timeline, track, item)


@fusion_group.command("tools")
@_item_selector
@_comp_option
@click.pass_context
def fusion_tools(ctx, timeline, track, item, comp):
    """Tools in a comp."""
    _run(ctx, fusion.tools, timeline, track, item, comp)


@fusion_group.command("inputs")
@click.argument("tool")
@_item_selector
@_comp_option
@click.pass_context
def fusion_inputs(ctx, tool, timeline, track, item, comp):
    """Input names of TOOL."""
    _run(ctx, fusion.inputs, timeline, track, item, comp, tool)


@fusion_group.command("get-input")
@click.argument("tool")
@click.argument("input_name")
@_item_selector
@_comp_option
@click.pass_context
def fusion_get_input(ctx, tool, input_name, timeline, track, item, comp):
    """Current value of TOOL's input."""
    _run(ctx, fusion.get_input, timeline, track, item, comp, tool, input_name)


@fusion_group.command("set-input")
@click.argument("tool")
@click.argument("input_name")
@click.argument("value")
@_item_selector
@_comp_option
@click.pass_context
def fusion_set_input(ctx, tool, input_name, value, timeline, track, item, comp):
    """Set TOOL's input to VALUE (JSON: 1.5, true, "text"). Verified by read-back."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value  # bare words are plain strings
    _run(ctx, fusion.set_input, timeline, track, item, comp, tool, input_name, parsed)


# ---- api (the whole scripting API) -------------------------------------------
@cli.group("api")
def api_group():
    """Any Resolve scripting API method (search, describe, call)."""


@api_group.command("search")
@click.argument("pattern")
@click.pass_context
def api_search(ctx, pattern):
    """Regex over 'Class.Method' and docs, e.g. 'marker' or '^Timeline\\.Export'. Works offline."""
    _run(ctx, core_api.search, pattern, offline=True)


@api_group.command("describe")
@click.argument("symbol")
@click.pass_context
def api_describe(ctx, symbol):
    """Signature and doc of Class.Method, e.g. Timeline.AddMarker. Works offline."""
    _run(ctx, core_api.describe, symbol, offline=True)


@api_group.command("call")
@click.argument("symbol")
@click.option("--args", "args_json", default="[]", help='JSON array of arguments. Refs: {"$clip": name}, {"$timeline": name}, {"$bin": path}, {"$const": "EXPORT_DRT"}.')
@click.option("--timeline", help="Timeline name for Timeline/TimelineItem (default: current).")
@click.option("--track", help="Track like video:1 (TimelineItem).")
@click.option("--item", type=int, help="1-based item position on the track (TimelineItem).")
@click.option("--clip", help="Media-pool clip name (MediaPoolItem).")
@click.option("--bin", "bin_path", help="Bin path like Footage/Day1 (Folder).")
@click.option("--album", help="Still album name (GalleryStillAlbum; default: current).")
@click.option("--layer", type=int, help="Node-graph layer (Graph, default 1; uses --timeline/--track/--item).")
@click.option("--yes", is_flag=True, help="Confirm a destructive method (Delete*, Remove*, Close*, Quit, Clear*).")
@click.pass_context
def api_call(ctx, symbol, args_json, timeline, track, item, clip, bin_path, album, layer, yes):
    """Call Class.Method on the object chosen by the selector options.

    Example: api call Timeline.AddMarker --args '[100, "Red", "fix", "", 1]'
    """
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        ctx.fail(f"--args is not valid JSON: {e}")
    sel = {"timeline": timeline, "track": track, "item": item, "clip": clip, "bin": bin_path, "album": album, "layer": layer}
    _run(ctx, core_api.call, symbol, args, sel, yes=yes)


# ---- repl ------------------------------------------------------------------
commands_extra.register(cli, _run, _item_selector, {
    "fusion": fusion_group, "timeline": timeline_group, "media": media_group, "color": color_group,
    "render": render_group, "project": project_group, "stills": stills_group,
})
agent.register(cli, _run, _emit)


@cli.command(hidden=True)
@click.pass_context
def repl(ctx):
    """Interactive session (default when no subcommand is given)."""
    from cli_anything.davinci_resolve.utils.repl_skin import ReplSkin

    from cli_anything.davinci_resolve.utils.logo import print_logo

    print_logo()
    skin = ReplSkin("davinci_resolve", version=VERSION)
    skin.print_banner()
    session = skin.create_prompt_session()
    while True:
        try:
            line = skin.get_input(session).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("exit", "quit"):
            break
        if line in ("", "help"):
            skin.help({g: (cli.commands[g].help or "") for g in cli.commands if g != "repl"})
            continue
        try:
            cli.main(shlex.split(line), standalone_mode=False, obj=dict(ctx.obj))
        except click.ClickException as e:
            e.show()
        except (click.exceptions.Exit, click.exceptions.Abort, SystemExit):
            pass
    skin.print_goodbye()


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
