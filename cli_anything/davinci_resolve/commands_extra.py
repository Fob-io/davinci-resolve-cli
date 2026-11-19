"""CLI commands added on top of the core set: Fusion building, item properties/markers/takes/AI,
grade versions/nodes/groups, timeline versions, presets, settings, project library, media storage,
gallery albums and analysis. Registered onto the existing groups by `register`."""
import json

import click

from cli_anything.davinci_resolve.core import color, effects, fusion, library, media, presets, timeline, titem
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError


def _kv(pairs):
    """('Pan=10', 'Name=x') -> {'Pan': 10, 'Name': 'x'}; values parse as JSON when they can."""
    out = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise click.BadParameter(f"'{pair}' is not KEY=VALUE")
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def _yes(fn):
    return click.option("--yes", is_flag=True, help="Confirm the destructive action.")(fn)


def register(cli, run, item_selector, groups):
    sel = item_selector
    comp_opt = click.option("--comp", help="Comp name or 1-based index (default: first).")

    # ---- fusion -----------------------------------------------------------------
    fg = groups["fusion"]

    @fg.command("add-comp")
    @sel
    @click.option("--file", "path", type=click.Path(), help="Import this .comp instead of creating an empty one.")
    @click.pass_context
    def _(ctx, timeline, track, item, path):
        """Add a Fusion comp to the item (empty, or imported from a .comp file)."""
        run(ctx, fusion.add_comp, timeline, track, item, path)

    @fg.command("export-comp")
    @sel
    @click.argument("path", type=click.Path())
    @click.option("--index", type=int, default=1, show_default=True, help="1-based comp index.")
    @click.pass_context
    def _(ctx, timeline, track, item, path, index):
        """Export a comp to a .comp file."""
        run(ctx, fusion.export_comp, timeline, track, item, path, index)

    @fg.command("delete-comp")
    @sel
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, name, yes):
        """Delete comp NAME."""
        run(ctx, fusion.delete_comp, timeline, track, item, name, yes)

    @fg.command("rename-comp")
    @sel
    @click.argument("old")
    @click.argument("new")
    @click.pass_context
    def _(ctx, timeline, track, item, old, new):
        """Rename a comp."""
        run(ctx, fusion.rename_comp, timeline, track, item, old, new)

    @fg.command("load-comp")
    @sel
    @click.argument("name")
    @click.pass_context
    def _(ctx, timeline, track, item, name):
        """Make comp NAME the active one."""
        run(ctx, fusion.load_comp, timeline, track, item, name)

    @fg.command("add-tool")
    @sel
    @comp_opt
    @click.argument("tool_id")
    @click.option("--x", type=float, default=-32768, help="Flow X (default: auto).")
    @click.option("--y", type=float, default=-32768)
    @click.pass_context
    def _(ctx, timeline, track, item, comp, tool_id, x, y):
        """Add a tool by id (Merge, Background, TextPlus, Transform, ...)."""
        run(ctx, fusion.add_tool, timeline, track, item, comp, tool_id, x, y)

    @fg.command("delete-tool")
    @sel
    @comp_opt
    @click.argument("tool")
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, comp, tool, yes):
        """Delete a tool from the comp."""
        run(ctx, fusion.delete_tool, timeline, track, item, comp, tool, yes)

    @fg.command("connect")
    @sel
    @comp_opt
    @click.argument("src")
    @click.argument("dst")
    @click.argument("dst_input")
    @click.pass_context
    def _(ctx, timeline, track, item, comp, src, dst, dst_input):
        """Wire SRC's output into DST's input DST_INPUT (e.g. Merge1 Background)."""
        run(ctx, fusion.connect_tools, timeline, track, item, comp, src, dst, dst_input)

    @fg.command("keyframe")
    @sel
    @comp_opt
    @click.argument("tool")
    @click.argument("input_name")
    @click.argument("frame", type=float)
    @click.argument("value")
    @click.pass_context
    def _(ctx, timeline, track, item, comp, tool, input_name, frame, value):
        """Set VALUE at comp FRAME, animating the input (BezierSpline) if needed."""
        run(ctx, fusion.keyframe, timeline, track, item, comp, tool, input_name, frame, _kv([f"v={value}"])["v"])

    @fg.command("save-tool")
    @sel
    @comp_opt
    @click.argument("tool")
    @click.argument("path", type=click.Path())
    @click.pass_context
    def _(ctx, timeline, track, item, comp, tool, path):
        """Save TOOL with its settings as a .setting macro file at PATH."""
        run(ctx, fusion.save_tool, timeline, track, item, comp, tool, path)

    @fg.command("dump")
    @sel
    @comp_opt
    @click.option("--all", "show_all", is_flag=True, help="Every input, not only the changed or connected ones.")
    @click.pass_context
    def _(ctx, timeline, track, item, comp, show_all):
        """Whole comp in one go: each tool with inputs that differ from default, and its connections."""
        run(ctx, fusion.dump, timeline, track, item, comp, show_all)

    @fg.command("bbox")
    @sel
    @comp_opt
    @click.argument("tool")
    @click.option("--frame", type=float, help="Comp frame to evaluate (default: current).")
    @click.pass_context
    def _(ctx, timeline, track, item, comp, tool, frame):
        """Rendered extent of TOOL's output: canvas size and content box in pixels (e.g. width of a Text+)."""
        run(ctx, fusion.bbox, timeline, track, item, comp, tool, frame)

    # ---- timeline: versions, structure, item operations ----------------------------
    tg = groups["timeline"]

    @tg.command("insert-title")
    @click.argument("name")
    @click.option("--track", type=int, default=1, show_default=True, help="Video track number.")
    @click.option("--at", type=int, default=0, show_default=True, help="Timeline-relative start frame.")
    @click.option("--duration", type=int, help="Length in frames (default 150).")
    @click.option("--kind", type=click.Choice(["fusion-title", "title", "generator", "fusion-generator"]), default="fusion-title", show_default=True)
    @click.option("--text", help="Text+ text (compound mode only).")
    @click.option("--mode", type=click.Choice(["compound", "ripple"]), default="compound", show_default=True,
                  help="compound: made in a scratch timeline, wrapped in a compound clip, appended at an exact frame, never ripples. "
                       "ripple: native Resolve insert; it can shift or trim other tracks and subtitles even when locked.")
    @click.pass_context
    def _(ctx, name, track, at, duration, kind, text, mode):
        """Place a title or generator (e.g. --kind generator "Solid Color") on a track at a frame."""
        if mode == "ripple":
            run(ctx, timeline.insert_title, name, track, at, duration, kind)
        else:
            run(ctx, timeline.insert_compound, name, track, at, duration, kind, text)

    @tg.command("fusion-clip")
    @click.option("--track", default="video:1", show_default=True, help="Track like video:1.")
    @click.option("--item", "items", type=int, multiple=True, required=True, help="1-based item (repeatable).")
    @click.pass_context
    def _(ctx, track, items):
        """Turn the given items into one Fusion clip (CreateFusionClip)."""
        run(ctx, timeline.fusion_clip, track, list(items))

    @tg.command("versions")
    @click.argument("name", required=False)
    @click.pass_context
    def _(ctx, name):
        """Timelines named 'NAME', 'NAME v2', 'NAME v3'... (default: current timeline)."""
        run(ctx, timeline.versions, name)

    @tg.command("snapshot")
    @click.option("--timeline", "source", help="Timeline to copy (default: current).")
    @click.pass_context
    def _(ctx, source):
        """Duplicate the timeline as the next free 'NAME vN' (named version snapshot)."""
        run(ctx, timeline.snapshot, source)

    @tg.command("duplicate")
    @click.argument("new_name")
    @click.option("--timeline", "source", help="Timeline to copy (default: current).")
    @click.pass_context
    def _(ctx, new_name, source):
        """Duplicate a timeline under NEW_NAME."""
        run(ctx, timeline.duplicate, new_name, source)

    @tg.command("rename")
    @click.argument("new_name")
    @click.option("--timeline", "source", help="Timeline to rename (default: current).")
    @click.pass_context
    def _(ctx, new_name, source):
        """Rename a timeline."""
        run(ctx, timeline.rename, new_name, source)

    @tg.command("delete")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a timeline (its clips stay in the media pool)."""
        run(ctx, timeline.delete_timeline, name, yes)

    @tg.command("insert")
    @click.argument("kind", type=click.Choice(["generator", "fusion-generator", "title", "fusion-title", "ofx-generator"]))
    @click.argument("name")
    @click.pass_context
    def _(ctx, kind, name):
        """Insert a generator or title at the playhead."""
        run(ctx, timeline.insert, kind, name)

    @tg.command("delete-track")
    @click.argument("track_type", type=click.Choice(["video", "audio", "subtitle"]))
    @click.argument("index", type=int)
    @_yes
    @click.pass_context
    def _(ctx, track_type, index, yes):
        """Delete a track and its clips."""
        run(ctx, timeline.delete_track, track_type, index, yes)

    @tg.command("track-lock")
    @click.argument("track_type", type=click.Choice(["video", "audio", "subtitle"]))
    @click.argument("index", type=int)
    @click.option("--off", is_flag=True, help="Unlock instead of lock.")
    @click.pass_context
    def _(ctx, track_type, index, off):
        """Lock (or unlock) a track."""
        run(ctx, timeline.track_lock, track_type, index, not off)

    @tg.command("track-name")
    @click.argument("track_type", type=click.Choice(["video", "audio", "subtitle"]))
    @click.argument("index", type=int)
    @click.argument("name")
    @click.pass_context
    def _(ctx, track_type, index, name):
        """Rename a track."""
        run(ctx, timeline.track_name, track_type, index, name)

    @tg.command("delete-clips")
    @click.option("--track", required=True, help="Track like video:1.")
    @click.option("--item", "items", type=int, multiple=True, required=True, help="1-based item (repeatable).")
    @click.option("--ripple", is_flag=True)
    @_yes
    @click.pass_context
    def _(ctx, track, items, ripple, yes):
        """Delete clips from the current timeline."""
        run(ctx, timeline.delete_clips, track, items, ripple, yes)

    @cli.group("item")
    def ig():
        """Timeline item properties, speed, fades, flags, markers, takes and AI tools."""

    @ig.command("props")
    @sel
    @click.option("--key", "keys", multiple=True, help="Only these properties (repeatable).")
    @click.option("--set", "sets", multiple=True, help="KEY=VALUE to write (repeatable), e.g. --set ZoomX=1.2.")
    @click.pass_context
    def _(ctx, timeline, track, item, keys, sets):
        """Read (Pan, Tilt, ZoomX, Opacity, CropLeft...) or write properties."""
        if sets:
            run(ctx, titem.set_props, timeline, track, item, _kv(sets))
        else:
            run(ctx, titem.get_props, timeline, track, item, keys)

    @ig.command("speed")
    @sel
    @click.option("--set", "new", help='JSON, e.g. \'{"Percentage": 50}\' (also PitchCorrection, StretchKeyframesToFit, RippleTimeline).')
    @click.pass_context
    def _(ctx, timeline, track, item, new):
        """Read or set the retime speed."""
        run(ctx, titem.speed, timeline, track, item, json.loads(new) if new else None)

    @ig.command("enable")
    @sel
    @click.option("--off", is_flag=True, help="Disable the clip.")
    @click.pass_context
    def _(ctx, timeline, track, item, off):
        """Enable (or disable) a clip."""
        run(ctx, titem.enable, timeline, track, item, not off)

    @ig.group("effects")
    def effects_group():
        """Fusion / OFX / audio effects on a clip: list, add, delete, enable, move."""

    cat_opt = click.option("--category", type=click.Choice(list(effects.CATEGORIES)), required=True)
    idx_arg = click.argument("index", type=int)

    @effects_group.command("list")
    @sel
    @click.option("--category", type=click.Choice(list(effects.CATEGORIES)), help="Only this category (default: all).")
    @click.pass_context
    def _(ctx, timeline, track, item, category):
        """Effects applied to the clip with category, index (per category), name, id, enabled."""
        run(ctx, effects.list_effects, timeline, track, item, category)

    @effects_group.command("add")
    @sel
    @cat_opt
    @click.argument("effect_id")
    @click.pass_context
    def _(ctx, timeline, track, item, category, effect_id):
        """Apply EFFECT_ID (e.g. 'Resolve FX/Resolve FX Blur/Lens Blur'; find ids with: effects search)."""
        run(ctx, effects.add, timeline, track, item, category, effect_id)

    @effects_group.command("delete")
    @sel
    @cat_opt
    @idx_arg
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, category, index, yes):
        """Remove effect INDEX of the category."""
        run(ctx, effects.delete, timeline, track, item, category, index, yes)

    @effects_group.command("enable")
    @sel
    @cat_opt
    @idx_arg
    @click.option("--off", is_flag=True, help="Disable the effect.")
    @click.pass_context
    def _(ctx, timeline, track, item, category, index, off):
        """Enable (or disable) effect INDEX of the category."""
        run(ctx, effects.enable, timeline, track, item, category, index, not off)

    @effects_group.command("move")
    @sel
    @cat_opt
    @idx_arg
    @click.argument("to", type=int)
    @click.pass_context
    def _(ctx, timeline, track, item, category, index, to):
        """Move effect INDEX to position TO (1-based, same category)."""
        run(ctx, effects.move, timeline, track, item, category, index, to)

    @ig.command("fades")
    @sel
    @click.option("--fade-in", type=int, help="Frames.")
    @click.option("--fade-out", type=int, help="Frames.")
    @click.pass_context
    def _(ctx, timeline, track, item, fade_in, fade_out):
        """Read or set audio fades."""
        run(ctx, titem.fades, timeline, track, item, fade_in, fade_out)

    @ig.command("color")
    @sel
    @click.argument("color_name", required=False)
    @click.option("--clear", is_flag=True)
    @click.pass_context
    def _(ctx, timeline, track, item, color_name, clear):
        """Read, set or clear the timeline clip color."""
        run(ctx, titem.clip_color, timeline, track, item, color_name, clear)

    @ig.command("flags")
    @sel
    @click.option("--add", help="Flag color to add.")
    @click.option("--clear", "clear_flag", help="Flag color to clear, or All.")
    @click.pass_context
    def _(ctx, timeline, track, item, add, clear_flag):
        """List, add or clear flags."""
        run(ctx, titem.flags, timeline, track, item, add, clear_flag)

    @ig.command("export-frame")
    @sel
    @click.argument("path", type=click.Path())
    @click.option("--offset", type=int, default=0, show_default=True, help="Frame offset inside the item.")
    @click.pass_context
    def _(ctx, timeline, track, item, path, offset):
        """Export one frame of the item as an image."""
        run(ctx, titem.export_frame, timeline, track, item, offset, path)

    @ig.command("takes")
    @sel
    @click.pass_context
    def _(ctx, timeline, track, item):
        """List takes."""
        run(ctx, titem.takes, timeline, track, item)

    @ig.command("take-select")
    @sel
    @click.argument("index", type=int)
    @click.pass_context
    def _(ctx, timeline, track, item, index):
        """Select take INDEX."""
        run(ctx, titem.take_select, timeline, track, item, index)

    @ig.command("take-add")
    @sel
    @click.argument("clip")
    @click.option("--start", type=int)
    @click.option("--end", type=int)
    @click.pass_context
    def _(ctx, timeline, track, item, clip, start, end):
        """Add media pool clip CLIP as a take."""
        run(ctx, titem.take_add, timeline, track, item, clip, start, end)

    @ig.command("take-delete")
    @sel
    @click.argument("index", type=int)
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, index, yes):
        """Delete take INDEX."""
        run(ctx, titem.take_delete, timeline, track, item, index, yes)

    @ig.command("take-finalize")
    @sel
    @click.pass_context
    def _(ctx, timeline, track, item):
        """Finalize the selected take."""
        run(ctx, titem.take_finalize, timeline, track, item)

    @ig.command("ai")
    @sel
    @click.argument("action", type=click.Choice(["magic-mask", "regenerate-mask", "stabilize", "smart-reframe"]))
    @click.option("--mode", type=click.Choice(["F", "B", "BI"]), help="Magic mask direction.")
    @click.pass_context
    def _(ctx, timeline, track, item, action, mode):
        """Resolve AI tools on the item (Studio)."""
        run(ctx, titem.ai, timeline, track, item, action, mode)

    @ig.command("markers")
    @sel
    @click.pass_context
    def _(ctx, timeline, track, item):
        """Markers on the timeline item."""
        run(ctx, titem.list_markers, timeline, track, item)

    @ig.command("marker-add")
    @sel
    @click.argument("frame", type=int)
    @click.option("--color", default="Blue", show_default=True)
    @click.option("--name", default="")
    @click.option("--note", default="")
    @click.option("--duration", type=int, default=1, show_default=True)
    @click.option("--custom", help="Custom data string.")
    @click.pass_context
    def _(ctx, timeline, track, item, frame, color, name, note, duration, custom):
        """Add a marker at FRAME (offset inside the item)."""
        run(ctx, titem.add_marker, timeline, track, item, None, frame, color, name, note, duration, custom)

    @ig.command("marker-delete")
    @sel
    @click.option("--frame", type=int)
    @click.option("--color", help="Marker color, or All.")
    @click.option("--custom")
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, frame, color, custom, yes):
        """Delete markers on the item by frame, color or custom data."""
        if not yes:
            raise click.UsageError("marker-delete needs --yes")
        run(ctx, titem.delete_marker, timeline, track, item, None, frame, color, custom)

    # media pool clip markers
    mg = groups["media"]

    @mg.command("replace")
    @click.argument("clip")
    @click.argument("path", type=click.Path())
    @click.option("--keep-subclip", is_flag=True, help="ReplaceClipPreserveSubClip: keep the sub-clip extents.")
    @click.pass_context
    def _(ctx, clip, path, keep_subclip):
        """Point media-pool CLIP at the file PATH; timeline items keep working (same path = re-read it)."""
        run(ctx, media.replace_clip, clip, path, keep_subclip)

    @mg.command("markers")
    @click.argument("clip")
    @click.pass_context
    def _(ctx, clip):
        """Markers on a media pool clip."""
        run(ctx, titem.list_markers, None, None, None, clip)

    @mg.command("marker-add")
    @click.argument("clip")
    @click.argument("frame", type=int)
    @click.option("--color", default="Blue", show_default=True)
    @click.option("--name", default="")
    @click.option("--note", default="")
    @click.option("--duration", type=int, default=1, show_default=True)
    @click.option("--custom")
    @click.pass_context
    def _(ctx, clip, frame, color, name, note, duration, custom):
        """Add a marker on a media pool clip."""
        run(ctx, titem.add_marker, None, None, None, clip, frame, color, name, note, duration, custom)

    @mg.command("marker-delete")
    @click.argument("clip")
    @click.option("--frame", type=int)
    @click.option("--color")
    @click.option("--custom")
    @_yes
    @click.pass_context
    def _(ctx, clip, frame, color, custom, yes):
        """Delete markers on a media pool clip."""
        if not yes:
            raise click.UsageError("marker-delete needs --yes")
        run(ctx, titem.delete_marker, None, None, None, clip, frame, color, custom)

    @mg.command("analyze")
    @click.argument("action", type=click.Choice(
        ["transcribe", "clear-transcript", "classify-audio", "clear-classification", "intellisearch", "slate"]))
    @click.option("--clip")
    @click.option("--bin", "bin_path")
    @click.option("--faces", is_flag=True, help="intellisearch: identify faces.")
    @click.option("--better", is_flag=True, help="intellisearch: better (slower) mode.")
    @click.option("--slate-color", default="Blue", show_default=True)
    @click.option("--speakers", is_flag=True, help="transcribe: speaker detection.")
    @_yes
    @click.pass_context
    def _(ctx, action, clip, bin_path, faces, better, slate_color, speakers, yes):
        """Run an analysis on a clip or bin (async: poll with `analysis status`). clear-* needs --yes."""
        run(ctx, library.analyze, action, clip, bin_path, faces, better, slate_color, speakers, False, yes)

    # ---- color: versions, nodes, groups ---------------------------------------------
    cg = groups["color"]

    @cg.command("versions")
    @sel
    @click.option("--remote", is_flag=True)
    @click.pass_context
    def _(ctx, timeline, track, item, remote):
        """Grade versions of the item."""
        run(ctx, color.versions, timeline, track, item, remote)

    def version_cmd(name, fn, args, confirm=False):
        def cmd(**kw):
            extra = [kw["yes"]] if confirm else []
            run(click.get_current_context(), fn, kw["timeline"], kw["track"], kw["item"],
                *[kw[a] for a in args], kw["remote"], *extra)
        cmd.__doc__ = f"{name.replace('-', ' ').capitalize()} on the item's grade."
        if confirm:
            cmd = _yes(cmd)
        cmd = click.option("--remote", is_flag=True)(cmd)
        for a in reversed(args):
            cmd = click.argument(a)(cmd)
        cmd = sel(cmd)
        cg.command(name)(cmd)

    version_cmd("version-add", color.version_add, ("name",))
    version_cmd("version-load", color.version_load, ("name",))
    version_cmd("version-rename", color.version_rename, ("old", "new"))
    version_cmd("version-delete", color.version_delete, ("name",), confirm=True)

    @cg.command("node-set")
    @sel
    @click.argument("index", type=int)
    @click.option("--label")
    @click.option("--color", "node_color", help="Node color name ('' clears).")
    @click.option("--enable/--disable", "enabled", default=None)
    @click.option("--lut", help="LUT path (relative to Resolve's LUT folder, or absolute).")
    @click.option("--layer", type=int, default=1)
    @click.pass_context
    def _(ctx, timeline, track, item, index, label, node_color, enabled, lut, layer):
        """Edit node INDEX: label, color, enabled, LUT."""
        run(ctx, color.node_set, timeline, track, item, index, label, node_color, enabled, lut, layer)

    @cg.command("reset")
    @sel
    @_yes
    @click.pass_context
    def _(ctx, timeline, track, item, yes):
        """Wipe the item's whole grade (add a version first)."""
        run(ctx, color.reset_grades, timeline, track, item, yes)

    @cg.command("export-lut")
    @sel
    @click.argument("kind", type=click.Choice(["17pt", "33pt", "65pt", "panorama"]))
    @click.argument("path", type=click.Path())
    @click.pass_context
    def _(ctx, timeline, track, item, kind, path):
        """Export the item's grade as a LUT."""
        run(ctx, color.export_lut, timeline, track, item, kind, path)

    @cg.command("groups")
    @click.pass_context
    def _(ctx):
        """Color groups in the project."""
        run(ctx, color.groups)

    @cg.command("group-add")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Create a color group."""
        run(ctx, color.group_add, name)

    @cg.command("group-delete")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a color group."""
        run(ctx, color.group_delete, name, yes)

    @cg.command("group-assign")
    @sel
    @click.argument("name")
    @click.pass_context
    def _(ctx, timeline, track, item, name):
        """Put the item in color group NAME."""
        run(ctx, color.group_assign, timeline, track, item, name)

    @cg.command("group-remove")
    @sel
    @click.pass_context
    def _(ctx, timeline, track, item):
        """Take the item out of its color group."""
        run(ctx, color.group_remove, timeline, track, item)

    @cg.command("group-clips")
    @click.argument("name")
    @click.option("--timeline")
    @click.pass_context
    def _(ctx, name, timeline):
        """Clips of a color group in a timeline."""
        run(ctx, color.group_clips, name, timeline)

    # ---- render presets, burn-in, layouts, project settings -------------------------------
    rg = groups["render"]

    @rg.command("presets")
    @click.pass_context
    def _(ctx):
        """Render presets."""
        run(ctx, presets.render_presets)

    @rg.command("preset-load")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Load a render preset."""
        run(ctx, presets.render_preset_load, name)

    @rg.command("preset-save")
    @click.argument("name")
    @click.option("--update", is_flag=True, help="Overwrite the existing preset NAME.")
    @click.pass_context
    def _(ctx, name, update):
        """Save the current render settings as a preset."""
        run(ctx, presets.render_preset_save, name, update)

    @rg.command("preset-delete")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a render preset."""
        run(ctx, presets.render_preset_delete, name, yes)

    @rg.command("preset-import")
    @click.argument("path", type=click.Path())
    @click.pass_context
    def _(ctx, path):
        """Import a render preset file."""
        run(ctx, presets.render_preset_import, path)

    @rg.command("preset-export")
    @click.argument("name")
    @click.argument("path", type=click.Path())
    @click.pass_context
    def _(ctx, name, path):
        """Export a render preset to a file."""
        run(ctx, presets.render_preset_export, name, path)

    @rg.command("formats")
    @click.argument("fmt", required=False)
    @click.pass_context
    def _(ctx, fmt):
        """Render formats, or the codecs of FMT (e.g. mov)."""
        run(ctx, presets.render_formats, fmt)

    @rg.command("burnin-presets")
    @click.pass_context
    def _(ctx):
        """Burn-in presets."""
        run(ctx, presets.burnin_presets)

    @rg.command("burnin-load")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Load a burn-in preset."""
        run(ctx, presets.burnin_load, name)

    @cli.group("effects")
    def effects_catalogue():
        """Catalogue of effects Resolve can apply."""

    @effects_catalogue.command("search")
    @click.argument("text")
    @click.option("--media-type", type=click.Choice(["video", "photo", "audio"]), help="Restrict to effects for this media.")
    @click.option("--category", type=click.Choice(list(effects.CATEGORIES)))
    @click.option("--limit", type=int, default=50, show_default=True)
    @click.pass_context
    def _(ctx, text, media_type, category, limit):
        """Effects whose id or name contains TEXT (the id is what 'item effects add' takes)."""
        run(ctx, effects.search, text, media_type, category, limit)

    @cli.group("settings")
    def sg():
        """Project settings and project settings presets."""

    @sg.command("get")
    @click.option("--key", "keys", multiple=True)
    @click.pass_context
    def _(ctx, keys):
        """Project settings (all, or --key ...)."""
        run(ctx, presets.settings_get, keys)

    @sg.command("set")
    @click.argument("pairs", nargs=-1, required=True)
    @click.pass_context
    def _(ctx, pairs):
        """Set KEY=VALUE pairs (e.g. timelineFrameRate=30), each read back."""
        run(ctx, presets.settings_set, {k: str(v) for k, v in _kv(pairs).items()})

    @sg.command("presets")
    @click.pass_context
    def _(ctx):
        """Project settings presets."""
        run(ctx, presets.settings_presets)

    @sg.command("preset-apply")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Apply a project settings preset."""
        run(ctx, presets.settings_preset_apply, name)

    @sg.command("preset-save")
    @click.argument("name")
    @click.option("--update", is_flag=True)
    @click.pass_context
    def _(ctx, name, update):
        """Save current project settings as a preset."""
        run(ctx, presets.settings_preset_save, name, update)

    @sg.command("preset-delete")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a project settings preset."""
        run(ctx, presets.settings_preset_delete, name, yes)

    @cli.group("layout")
    def lg():
        """UI layout presets."""

    @lg.command("list")
    @click.pass_context
    def _(ctx):
        """Layout presets."""
        run(ctx, presets.layouts)

    for action in ("load", "save", "update", "delete", "export", "import"):
        def make_layout(action=action):
            @lg.command(action)
            @click.argument("name")
            @click.option("--path", type=click.Path(), help="File for export/import.")
            @click.option("--yes", is_flag=True, help="Confirm (needed for delete).")
            def cmd(name, path, yes):
                run(click.get_current_context(), presets.layout_do, action, name, path, yes)
            cmd.__doc__ = f"{action.capitalize()} layout preset NAME."
        make_layout()

    # ---- project library: folders, databases, import/export -------------------------------
    pg = groups["project"]

    @pg.command("folders")
    @click.pass_context
    def _(ctx):
        """Current project-manager folder: sub-folders and projects."""
        run(ctx, library.folders)

    @pg.command("cd")
    @click.argument("target")
    @click.pass_context
    def _(ctx, target):
        """Enter a project folder: NAME, '..' (parent) or '/' (root)."""
        run(ctx, library.folder_go, target)

    @pg.command("mkdir")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Create a project folder."""
        run(ctx, library.folder_create, name)

    @pg.command("rmdir")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a project folder."""
        run(ctx, library.folder_delete, name, yes)

    @pg.command("delete")
    @click.argument("name")
    @_yes
    @click.pass_context
    def _(ctx, name, yes):
        """Delete a project."""
        run(ctx, library.project_delete, name, yes)

    @pg.command("rename")
    @click.argument("new_name")
    @click.pass_context
    def _(ctx, new_name):
        """Rename the current project."""
        run(ctx, library.project_rename, new_name)

    @pg.command("export")
    @click.argument("name")
    @click.argument("path", type=click.Path())
    @click.option("--no-stills", is_flag=True, help="Leave out stills and LUTs.")
    @click.pass_context
    def _(ctx, name, path, no_stills):
        """Export project NAME to a .drp file."""
        run(ctx, library.project_export, name, path, not no_stills)

    @pg.command("import")
    @click.argument("path", type=click.Path())
    @click.option("--name")
    @click.pass_context
    def _(ctx, path, name):
        """Import a .drp project file."""
        run(ctx, library.project_import, path, name)

    @pg.command("archive")
    @click.argument("name")
    @click.argument("path", type=click.Path())
    @click.option("--with-media", is_flag=True)
    @click.pass_context
    def _(ctx, name, path, with_media):
        """Archive project NAME (optionally with its source media)."""
        run(ctx, library.project_archive, name, path, with_media)

    @pg.command("databases")
    @click.pass_context
    def _(ctx):
        """Databases and the current one."""
        run(ctx, library.databases)

    @pg.command("use-database")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Switch to database NAME."""
        run(ctx, library.database_use, name)

    # ---- media storage (disk), gallery albums, analysis -----------------------------------
    @cli.group("storage")
    def stg():
        """Browse the disk through Resolve's media storage and import from it."""

    @stg.command("volumes")
    @click.pass_context
    def _(ctx):
        """Mounted volumes."""
        run(ctx, library.storage_volumes)

    @stg.command("ls")
    @click.argument("folder", type=click.Path())
    @click.pass_context
    def _(ctx, folder):
        """Sub-folders and files of FOLDER."""
        run(ctx, library.storage_ls, folder)

    @stg.command("import")
    @click.argument("paths", nargs=-1, required=True, type=click.Path())
    @click.option("--bin", "bin_path", default="")
    @click.pass_context
    def _(ctx, paths, bin_path):
        """Import files/folders into a bin."""
        run(ctx, library.storage_import, list(paths), bin_path)

    @stg.command("reveal")
    @click.argument("path", type=click.Path())
    @click.pass_context
    def _(ctx, path):
        """Show PATH in Resolve's media storage."""
        run(ctx, library.storage_reveal, path)

    sg2 = groups["stills"]

    @sg2.command("albums")
    @click.pass_context
    def _(ctx):
        """Still and PowerGrade albums."""
        run(ctx, library.albums)

    @sg2.command("album-create")
    @click.argument("name")
    @click.option("--powergrade", is_flag=True)
    @click.pass_context
    def _(ctx, name, powergrade):
        """Create an album."""
        run(ctx, library.album_create, name, powergrade)

    @sg2.command("album-use")
    @click.argument("name")
    @click.pass_context
    def _(ctx, name):
        """Make album NAME current."""
        run(ctx, library.album_use, name)

    @sg2.command("import")
    @click.argument("paths", nargs=-1, required=True, type=click.Path())
    @click.option("--album")
    @click.pass_context
    def _(ctx, paths, album):
        """Import still image(s) (with .drx grades) into an album."""
        run(ctx, library.stills_import, list(paths), album)

    @cli.group("analysis")
    def ag():
        """Background AI jobs: scene cuts and progress of transcribe/slate/reframe."""

    @ag.command("detect-cuts")
    @click.pass_context
    def _(ctx):
        """Detect scene cuts on the current timeline (async)."""
        run(ctx, library.detect_scene_cuts)

    @ag.command("status")
    @click.argument("kind", type=click.Choice(["detect-cuts", "transcribe", "slate", "reframe", "subtitles"]))
    @click.pass_context
    def _(ctx, kind):
        """Progress of a background job."""
        run(ctx, library.analysis_status, kind)

    @ag.command("refresh-luts")
    @click.pass_context
    def _(ctx):
        """Re-scan Resolve's LUT folder."""
        run(ctx, library.luts_refresh)
