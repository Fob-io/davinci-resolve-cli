"""Color page: the node graph of a timeline item, and copying/applying grades.

Verified quirk: TimelineItem.CopyGrades() returns True but applies nothing unless the Color
page is open, so writes switch to it and restore the previous page. It replaces the whole
destination grade and makes no version, hence the skip-graded default and --version.
"""
import os
import time

from cli_anything.davinci_resolve.core.timeline import find_item, find_timeline
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def nodes(resolve, timeline, track, item, layer=1):
    graph = find_item(current_project(resolve), timeline, track, item).GetNodeGraph(layer)
    if graph is None:
        raise ResolveError(f"No node graph on layer {layer} (not a video item with a grade?)")
    return [
        {
            "index": i,
            "label": graph.GetNodeLabel(i),
            "color": graph.GetNodeColor(i),
            "tools": graph.GetToolsInNode(i) or [],
            "lut": graph.GetLUT(i),
        }
        for i in range(1, (graph.GetNumNodes() or 0) + 1)
    ]


def _num_nodes(item):
    graph = item.GetNodeGraph(1)
    return (graph.GetNumNodes() or 0) if graph else 0


def _split(track):
    kind, _, no = track.partition(":")
    return kind, int(no or 1)


def _source_name(item):
    pool_item = item.GetMediaPoolItem()
    return (pool_item.GetClipProperty("File Name") or pool_item.GetName()) if pool_item else item.GetName()


def _on_color_page(resolve, fn):
    page = resolve.GetCurrentPage()
    if page != "color":
        if not resolve.OpenPage("color"):
            raise ResolveError("Could not open the Color page")
        time.sleep(1.5)  # the Color page loads asynchronously
    try:
        return fn()
    finally:
        if page != "color" and (not resolve.OpenPage(page) or resolve.GetCurrentPage() != page):
            raise ResolveError(f"Done, but could not return to the {page} page (Resolve is on Color)")


def copy_grade(resolve, timeline, src_track, dst_track, src_item=None, dst_items=(), clip_color=None,
               src_from=0, overwrite=False, version=None, dry_run=False):
    """Copy a grade onto the items of `dst_track`.

    Source: `src_item` (1-based, on `src_track`) for every destination, or - when omitted - the
    first item on `src_track` starting at/after frame `src_from` with the same media file name.
    Destinations with more than one node count as already graded and are skipped unless `overwrite`.
    A single-node destination may still carry a CDL/LUT: the API cannot tell, so it is replaced.
    Use --version to keep the old grade, and --dry-run first.
    """
    project = current_project(resolve)
    tl = find_timeline(project, timeline)
    src_kind, src_no = _split(src_track)
    dst_kind, dst_no = _split(dst_track)
    sources = [i for i in tl.GetItemListInTrack(src_kind, src_no) or [] if i.GetStart() >= src_from]
    if src_item is not None:
        listed = tl.GetItemListInTrack(src_kind, src_no) or []
        if not 1 <= src_item <= len(listed):
            raise ResolveError(f"Track {src_track} has {len(listed)} items, no item {src_item}")
        fixed = listed[src_item - 1]
    else:
        fixed, by_name = None, {}
        for i in sources:
            by_name.setdefault(_source_name(i), i)
    dests = tl.GetItemListInTrack(dst_kind, dst_no) or []
    if dst_items:
        bad = [n for n in dst_items if not 1 <= n <= len(dests)]
        if bad:
            raise ResolveError(f"Track {dst_track} has {len(dests)} items, no item {bad}")
        dests = [dests[n - 1] for n in dst_items]
    if clip_color:
        dests = [d for d in dests if d.GetClipColor() == clip_color]
    plan = []
    for d in dests:
        src = fixed or by_name.get(_source_name(d))
        if src is None:
            status = "no_source"
        elif d.GetUniqueId() == src.GetUniqueId():
            status = "same_item"
        elif _num_nodes(d) > 1 and not overwrite:
            status = "already_graded"
        else:
            status = "will_copy" if dry_run else "copy"
        plan.append((d, src, status))

    def run():
        for n, (d, src, status) in enumerate(plan):
            if status != "copy":
                continue
            if version and not d.AddVersion(version, 0):
                plan[n] = (d, src, "failed")
                continue
            ok = src.CopyGrades([d])
            plan[n] = (d, src, "copied" if ok and _num_nodes(d) == _num_nodes(src) else "failed")

    if not dry_run and any(s == "copy" for _, _, s in plan):
        _on_color_page(resolve, run)
    rows = [{"item": d.GetName(), "start": d.GetStart(), "status": s} for d, _, s in plan]
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if counts.get("failed"):
        bad = [r["item"] for r in rows if r["status"] == "failed"]
        raise ResolveError(f"Grade copy failed on {bad} (version backup refused, copy ignored, or node count differs). Counts: {counts}")
    return {"counts": counts, "results": rows}


def apply_drx(resolve, timeline, path, track, items, mode=0, overwrite=False):
    """Apply a .drx grade file to items of a track (mode 0 no keyframes, 1 source TC, 2 start frames).

    Items with more than one node count as graded and are skipped unless `overwrite`.
    """
    if not os.path.isfile(path):
        raise ResolveError(f"DRX file not found: {path}")
    tl = find_timeline(current_project(resolve), timeline)
    kind, no = _split(track)
    listed = tl.GetItemListInTrack(kind, no) or []
    if not items:
        chosen = listed
    else:
        bad = [n for n in items if not 1 <= n <= len(listed)]
        if bad:
            raise ResolveError(f"Track {track} has {len(listed)} items, no item {bad}")
        chosen = [listed[n - 1] for n in items]
    skipped = [c.GetName() for c in chosen if _num_nodes(c) > 1 and not overwrite]
    chosen = [c for c in chosen if c.GetName() not in skipped or overwrite]

    def run():
        failed = []
        for clip in chosen:
            graph = clip.GetNodeGraph(1)
            if graph is None or not graph.ApplyGradeFromDRX(os.path.abspath(path), mode):
                failed.append(clip.GetName())
        return failed

    failed = _on_color_page(resolve, run)
    if failed:
        raise ResolveError(f"ApplyGradeFromDRX failed on {failed} (bad file, or mode not 0/1/2?)")
    return {"applied": len(chosen), "skipped_graded": skipped, "nodes_after": [_num_nodes(i) for i in chosen]}


def set_cdl(resolve, timeline, track, item, slope, offset, power, saturation, node=1):
    """Set CDL values on one node; each of slope/offset/power is 'R G B'."""
    clip = find_item(current_project(resolve), timeline, track, item)
    cdl = {"NodeIndex": str(node), "Slope": slope, "Offset": offset, "Power": power, "Saturation": str(saturation)}
    if not _on_color_page(resolve, lambda: clip.SetCDL(cdl)):
        raise ResolveError(f"SetCDL failed on node {node} (does the node exist?)")
    return {"item": clip.GetName(), "cdl": cdl}


def _clip(resolve, timeline, track, item):
    return find_item(current_project(resolve), timeline, track, item)


def _ok(result, what):
    if not result:
        raise ResolveError(f"{what} failed")


# ---- grade versions (0 = local, 1 = remote) ---------------------------------
def versions(resolve, timeline, track, item, remote=False):
    clip = _clip(resolve, timeline, track, item)
    kind = 1 if remote else 0
    return {"current": clip.GetCurrentVersion(), "versions": clip.GetVersionNameList(kind) or []}


def version_add(resolve, timeline, track, item, name, remote=False):
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.AddVersion(name, 1 if remote else 0), f"AddVersion('{name}')")
    return versions(resolve, timeline, track, item, remote)


def version_load(resolve, timeline, track, item, name, remote=False):
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.LoadVersionByName(name, 1 if remote else 0), f"LoadVersionByName('{name}')")
    return versions(resolve, timeline, track, item, remote)


def version_rename(resolve, timeline, track, item, old, new, remote=False):
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.RenameVersionByName(old, new, 1 if remote else 0), f"RenameVersionByName('{old}')")
    return versions(resolve, timeline, track, item, remote)


def version_delete(resolve, timeline, track, item, name, remote=False, yes=False):
    if not yes:
        raise ResolveError("version-delete removes a grade version. Re-run with --yes to confirm")
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.DeleteVersionByName(name, 1 if remote else 0), f"DeleteVersionByName('{name}')")
    return versions(resolve, timeline, track, item, remote)


# ---- node graph edits ---------------------------------------------------------
def _graph(resolve, timeline, track, item, layer=1):
    graph = _clip(resolve, timeline, track, item).GetNodeGraph(layer)
    if graph is None:
        raise ResolveError(f"No node graph on layer {layer}")
    return graph


def _node(graph, index):
    if not 1 <= index <= (graph.GetNumNodes() or 0):
        raise ResolveError(f"Graph has {graph.GetNumNodes()} nodes, no node {index}")


def node_set(resolve, timeline, track, item, index, label=None, color=None, enabled=None, lut=None, layer=1):
    """Edit a node: label, color ('' clears), enable, LUT path. Each change is read back."""
    graph = _graph(resolve, timeline, track, item, layer)
    _node(graph, index)
    if label is not None:
        _ok(graph.SetNodeLabel(index, label), "SetNodeLabel")
    if color is not None:
        _ok(graph.SetNodeColor(index, color), f"SetNodeColor('{color}')")
    if enabled is not None:
        _ok(graph.SetNodeEnabled(index, enabled), "SetNodeEnabled")
    if lut is not None:
        _ok(graph.SetLUT(index, lut), f"SetLUT('{lut}') (path not found in the LUT folder?)")
    graph = _graph(resolve, timeline, track, item, layer)  # re-read, do not reuse the old object
    return {"index": index, "label": graph.GetNodeLabel(index), "color": graph.GetNodeColor(index),
            "lut": graph.GetLUT(index)}


def reset_grades(resolve, timeline, track, item, yes=False):
    if not yes:
        raise ResolveError("reset wipes the whole grade of the item. Re-run with --yes (add a version first)")
    graph = _graph(resolve, timeline, track, item)
    _ok(_on_color_page(resolve, graph.ResetAllGrades), "ResetAllGrades")
    return {"nodes": _num_nodes(_clip(resolve, timeline, track, item))}


def export_lut(resolve, timeline, track, item, kind, path):
    """kind: 17pt|33pt|65pt|panorama (Resolve.EXPORT_LUT_*)."""
    names = {"17pt": "EXPORT_LUT_17PTCUBE", "33pt": "EXPORT_LUT_33PTCUBE",
             "65pt": "EXPORT_LUT_65PTCUBE", "panorama": "EXPORT_LUT_PANASONICVLUT"}
    if kind not in names:
        raise ResolveError(f"LUT kind must be one of {list(names)}")
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.ExportLUT(getattr(resolve, names[kind]), os.path.abspath(path)), "ExportLUT")
    return {"exported": os.path.abspath(path)}


# ---- color groups -------------------------------------------------------------
def groups(resolve):
    return [g.GetName() for g in current_project(resolve).GetColorGroupsList() or []]


def _group(project, name):
    for g in project.GetColorGroupsList() or []:
        if g.GetName() == name:
            return g
    raise ResolveError(f"Color group '{name}' not found. Existing: {[g.GetName() for g in project.GetColorGroupsList() or []]}")


def group_add(resolve, name):
    if current_project(resolve).AddColorGroup(name) is None:
        raise ResolveError(f"AddColorGroup('{name}') failed (duplicate?)")
    return groups(resolve)


def group_delete(resolve, name, yes=False):
    if not yes:
        raise ResolveError("group-delete removes the group. Re-run with --yes to confirm")
    project = current_project(resolve)
    _ok(project.DeleteColorGroup(_group(project, name)), f"DeleteColorGroup('{name}')")
    return groups(resolve)


def group_assign(resolve, timeline, track, item, name):
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.AssignToColorGroup(_group(current_project(resolve), name)), f"AssignToColorGroup('{name}')")
    return {"item": clip.GetName(), "group": clip.GetColorGroup().GetName()}


def group_remove(resolve, timeline, track, item):
    clip = _clip(resolve, timeline, track, item)
    _ok(clip.RemoveFromColorGroup(), "RemoveFromColorGroup")
    return {"item": clip.GetName(), "group": None}


def group_clips(resolve, name, timeline=None):
    project = current_project(resolve)
    tl = find_timeline(project, timeline)
    return [c.GetName() for c in _group(project, name).GetClipsInTimeline(tl) or []]
