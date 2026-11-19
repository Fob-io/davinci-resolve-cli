"""Timelines, track items, markers, interchange export."""
import re
import time

from cli_anything.davinci_resolve.core.media import walk_clips
from cli_anything.davinci_resolve.utils.resolve_backend import (
    ResolveError, current_project, current_timeline,
)

MARKER_COLORS = (
    "Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
    "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream",
)


def _all(resolve):
    project = current_project(resolve)
    return [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]


def find_timeline(project, name=None):
    """Timeline by name, or the current one when `name` is None."""
    if name is None:
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            raise ResolveError("No current timeline. Pass --timeline NAME")
        return timeline
    for i in range(1, project.GetTimelineCount() + 1):
        if project.GetTimelineByIndex(i).GetName() == name:
            return project.GetTimelineByIndex(i)
    raise ResolveError(f"Timeline '{name}' not found")


def find_item(project, timeline_name, track, index):
    """TimelineItem at 1-based position `index` on `track` ('video:1')."""
    if not track or not index:
        raise ResolveError("TimelineItem needs --track video:1 and --item N (1-based)")
    track_type, _, track_no = track.partition(":")
    items = find_timeline(project, timeline_name).GetItemListInTrack(track_type, int(track_no or 1)) or []
    if not 1 <= index <= len(items):
        raise ResolveError(f"Track {track} has {len(items)} items, no item {index}")
    return items[index - 1]


def list_timelines(resolve):
    current = current_project(resolve).GetCurrentTimeline()
    rows = []
    for t in _all(resolve):
        row = {"name": t.GetName(), "current": bool(current and t.GetName() == current.GetName()),
               "start_frame": t.GetStartFrame(), "end_frame": t.GetEndFrame()}
        row["duration_frames"] = (row["end_frame"] - row["start_frame"]) if None not in (row["start_frame"], row["end_frame"]) else None
        row["fps"] = t.GetSetting("timelineFrameRate")
        rows.append(row)
    return rows


def create(resolve, name):
    if name in [t.GetName() for t in _all(resolve)]:
        raise ResolveError(f"Timeline '{name}' already exists")
    pool = current_project(resolve).GetMediaPool()
    if pool.CreateEmptyTimeline(name) is None:
        raise ResolveError(f"CreateEmptyTimeline('{name}') failed")
    return {"timeline": name}


def set_current(resolve, name):
    match = [t for t in _all(resolve) if t.GetName() == name]
    if not match:
        raise ResolveError(f"Timeline '{name}' not found")
    if not current_project(resolve).SetCurrentTimeline(match[0]):
        raise ResolveError(f"SetCurrentTimeline('{name}') failed")
    return {"timeline": name}


def info(resolve):
    tl = current_timeline(resolve)
    return {
        "name": tl.GetName(),
        "start_frame": tl.GetStartFrame(),
        "end_frame": tl.GetEndFrame(),
        "start_timecode": tl.GetStartTimecode(),
        "fps": float(tl.GetSetting("timelineFrameRate")),
        "video_tracks": tl.GetTrackCount("video"),
        "audio_tracks": tl.GetTrackCount("audio"),
        "subtitle_tracks": tl.GetTrackCount("subtitle"),
        "mark_in_out": tl.GetMarkInOut() or {},
    }


def mark_in_out(resolve, mark_in=None, mark_out=None, clear=False):
    """Read (no args), set (frames relative to timeline start) or clear the In/Out range."""
    tl = current_timeline(resolve)
    if clear:
        tl.ClearMarkInOut()
    elif mark_in is not None or mark_out is not None:
        if mark_in is None or mark_out is None:
            raise ResolveError("Pass both IN and OUT")
        if not tl.SetMarkInOut(mark_in, mark_out):
            raise ResolveError(f"SetMarkInOut({mark_in}, {mark_out}) failed")
    return {"mark_in_out": tl.GetMarkInOut() or {}}


def tracks(resolve):
    tl = current_timeline(resolve)
    return [
        {"type": t, "index": i, "name": tl.GetTrackName(t, i), "enabled": tl.GetIsTrackEnabled(t, i), "locked": tl.GetIsTrackLocked(t, i)}
        for t in ("video", "audio", "subtitle") for i in range(1, tl.GetTrackCount(t) + 1)
    ]


def add_track(resolve, track_type):
    tl = current_timeline(resolve)
    if not tl.AddTrack(track_type):
        raise ResolveError(f"AddTrack('{track_type}') failed")
    return {"type": track_type, "count": tl.GetTrackCount(track_type)}


def set_track_state(resolve, tl, track_type, index, enabled):
    """SetTrackEnable with read-back. Resolve ignores enabling a track (not disabling) unless the Edit page is
    open, so on another page the Edit page is opened for the write and the original page restored."""
    tl.SetTrackEnable(track_type, index, enabled)
    if tl.GetIsTrackEnabled(track_type, index) == enabled:
        return
    page = resolve.GetCurrentPage()
    if page != "edit":
        resolve.OpenPage("edit")
        try:
            for _ in range(4):  # the Edit page loads asynchronously
                time.sleep(1)
                tl.SetTrackEnable(track_type, index, enabled)
                if tl.GetIsTrackEnabled(track_type, index) == enabled:
                    return
        finally:
            if page:
                resolve.OpenPage(page)
    raise ResolveError(f"SetTrackEnable({track_type}:{index}, {enabled}) was ignored (current page: {page})",
                       code="track_enable_ignored", hint="Open the Edit page in Resolve and retry")


def set_track_enabled(resolve, track_type, index, enabled):
    set_track_state(resolve, current_timeline(resolve), track_type, index, enabled)
    return {"type": track_type, "index": index, "enabled": enabled}


_MEDIA_KINDS = {"still": "still", "fusion": "fusion_clip", "compound": "compound", "timeline": "nested_timeline"}
_TEXT_TOOLS = ("TextPlus", "Text3D")
_TRANSITION_WORDS = ("dissolve", "dip to", "push", "slide", "wipe", "iris", "fade", "smooth cut", "band", "barn", "clock",
                     "cross", "blur", "zoom", "spin", "flip", "mosaic", "page", "radial", "split")


def _text_of(item):
    """StyledText of a Text+/Text3D title, else None (a Fusion comp that cannot be read gives None, never an error)."""
    try:
        comp = item.GetFusionCompByIndex(1)
        for tool in (comp.GetToolList() or {}).values():
            if (tool.GetAttrs() or {}).get("TOOLS_RegID") in _TEXT_TOOLS:
                return tool.GetInput("StyledText")
    except Exception:
        pass
    return None


def _kind(item, track_type, neighbours):
    """What a timeline item is: clip, still, compound, fusion_clip, nested_timeline, title, generator,
    adjustment, transition or subtitle. Transitions and generators carry no media: told apart by
    overlap with the neighbouring items, then by name."""
    if track_type == "subtitle":
        return "subtitle"
    source = item.GetMediaPoolItem()
    if source:
        media_type = (source.GetClipProperty("Type") or "").lower()
        return next((k for w, k in _MEDIA_KINDS.items() if w in media_type), "clip")
    name = (item.GetName() or "").lower()
    if "adjustment" in name:
        return "adjustment"
    if item.GetFusionCompCount():
        return "title" if _text_of(item) is not None else "generator"
    start, end = item.GetStart(), item.GetEnd()
    if any(o.GetStart() < end and o.GetEnd() > start for o in neighbours if o is not item):
        return "transition"
    return "transition" if any(w in name for w in _TRANSITION_WORDS) else "generator"


def _item_row(item, full, track_type="video", neighbours=(), text=False):
    start, end = item.GetStart(), item.GetEnd()
    duration = item.GetDuration()
    row = {"name": item.GetName(), "kind": _kind(item, track_type, neighbours), "start": start, "end": end,
           "duration": duration if duration is not None else (end - start if None not in (start, end) else None)}
    if row["kind"] == "subtitle":
        row["text"] = row["name"]
    elif text and row["kind"] == "title":
        row["text"] = _text_of(item)
    if full:
        source = item.GetMediaPoolItem()
        row.update(
            source=source.GetName() if source else None,
            left_offset=item.GetLeftOffset(),
            right_offset=item.GetRightOffset(),
            source_start=item.GetSourceStartFrame(),
            source_end=item.GetSourceEndFrame(),
            enabled=item.GetClipEnabled(),
        )
    return row


def _track_rows(tl, track_type, track, full, text):
    listed = tl.GetItemListInTrack(track_type, track) or []
    return [_item_row(i, full, track_type, listed, text) for i in listed]


def items(resolve, track_type="video", track=1, full=False, text=False):
    tl = current_timeline(resolve)
    if not 1 <= track <= tl.GetTrackCount(track_type):
        raise ResolveError(f"No {track_type} track {track}")
    return _track_rows(tl, track_type, track, full, text)


def dump(resolve, timeline_name=None, every=False, text=True, markers=True):
    """Whole structure in one call: per timeline its tracks with items (kind, start, duration, title text) and markers.
    Works on non-current timelines without switching to them."""
    project = current_project(resolve)
    if every:
        timelines = _all(resolve)
    else:
        timelines = [find_timeline(project, timeline_name) if timeline_name else current_timeline(resolve)]
    current = project.GetCurrentTimeline()
    out = []
    for tl in timelines:
        row = {"name": tl.GetName(), "current": bool(current and tl.GetName() == current.GetName()),
               "fps": tl.GetSetting("timelineFrameRate"), "start_frame": tl.GetStartFrame(), "end_frame": tl.GetEndFrame(),
               "start_timecode": tl.GetStartTimecode(), "tracks": []}
        for track_type in ("video", "audio", "subtitle"):
            for index in range(1, tl.GetTrackCount(track_type) + 1):
                row["tracks"].append({"type": track_type, "index": index, "name": tl.GetTrackName(track_type, index),
                                      "enabled": tl.GetIsTrackEnabled(track_type, index),
                                      "locked": tl.GetIsTrackLocked(track_type, index),
                                      "items": _track_rows(tl, track_type, index, False, text)})
        if markers:
            row["markers"] = [{"frame": f, **m} for f, m in sorted((tl.GetMarkers() or {}).items())]
        out.append(row)
    return {"project": project.GetName(), "width": project.GetSetting("timelineResolutionWidth"),
            "height": project.GetSetting("timelineResolutionHeight"), "timelines": out}


def playhead(resolve, timecode=None):
    """Read the playhead timecode, or move it when `timecode` is given.

    Resolve returns True even for impossible values (it normalizes 99:99:99:99 to some
    other time), so the result is read back and the old position restored on mismatch.
    """
    tl = current_timeline(resolve)
    if timecode is not None:
        before = tl.GetCurrentTimecode()
        ok = tl.SetCurrentTimecode(timecode)
        after = tl.GetCurrentTimecode()
        if not ok or after.replace(";", ":") != timecode.replace(";", ":"):
            tl.SetCurrentTimecode(before)
            raise ResolveError(
                f"Cannot move playhead to '{timecode}' (Resolve gave '{after}': outside the timeline or invalid). "
                f"Playhead restored to {before}"
            )
    return {"timecode": tl.GetCurrentTimecode()}


def export(resolve, path, fmt):
    tl = current_timeline(resolve)
    const = getattr(resolve, f"EXPORT_{fmt.upper()}", None)
    if const is None:
        available = sorted(a[7:].lower() for a in dir(resolve) if a.startswith("EXPORT_") and a[7:].isupper())
        raise ResolveError(f"Unknown format '{fmt}'. Available: {available}")
    if not tl.Export(path, const):
        raise ResolveError(f"Export to '{path}' failed")
    return {"exported": path, "format": fmt}


def list_markers(resolve):
    markers = current_timeline(resolve).GetMarkers() or {}
    return [{"frame": f, **m} for f, m in sorted(markers.items())]


def add_marker(resolve, frame, color, name, note="", duration=1, shift=False):
    """With `shift`, an occupied frame moves the marker to the next free one instead of failing."""
    if color not in MARKER_COLORS:
        raise ResolveError(f"Invalid color '{color}'. Valid: {list(MARKER_COLORS)}")
    tl = current_timeline(resolve)
    if shift:
        taken = tl.GetMarkers() or {}
        while frame in taken:
            frame += 1
    if not tl.AddMarker(frame, color, name, note, duration, ""):
        raise ResolveError(f"AddMarker at frame {frame} failed (marker already there?)")
    return {"frame": frame, "color": color, "name": name}


def delete_marker(resolve, frame=None, name=None):
    """Delete the marker at `frame`, or every marker called `name` (other markers stay)."""
    if (frame is None) == (name is None):
        raise ResolveError("Pass exactly one of FRAME or --name")
    tl = current_timeline(resolve)
    if frame is not None:
        if not tl.DeleteMarkerAtFrame(frame):
            raise ResolveError(f"No marker at frame {frame}")
        return {"deleted": [frame]}
    frames = [f for f, m in (tl.GetMarkers() or {}).items() if m.get("name") == name]
    gone = [f for f in frames if tl.DeleteMarkerAtFrame(f)]
    if len(gone) != len(frames):
        raise ResolveError(f"Deleted {gone}, failed on {sorted(set(frames) - set(gone))}")
    return {"deleted": sorted(gone)}


_RELOAD_HINT = "If Resolve shows a 'project was modified, reload' dialog, close it and retry. Run: doctor"
_DURATION_RETRY = (0, 1, -1, 2, -2, 3, -3, 4, -4)  # AppendToTimeline can be 1-2 frames off when the clip fps differs from the timeline's


def _parse_track(track):
    kind, _, number = track.partition(":")
    if kind not in ("video", "audio") or not (number or "1").isdigit() or int(number or 1) < 1:
        raise ResolveError(f"--track must look like video:2 or audio:1, got '{track}'")
    return kind, int(number or 1)


def append_clip(resolve, clip_name, track=None, start=None, end=None, frames=None, at=None, kind=None, timeline=None):
    """Append a media-pool clip (searched in all bins) to a timeline.

    start/end: source frames (end inclusive). frames: exact length on the timeline (end is derived from it
    and retried when the clip fps differs from the timeline's). at: timeline-relative record frame.
    kind: video, audio or both. Missing tracks up to --track are created first.
    """
    project = current_project(resolve)
    if end is not None and frames is not None:
        raise ResolveError("Pass --end or --frames, not both")
    tl = find_timeline(project, timeline)
    current = project.GetCurrentTimeline()
    pool = project.GetMediaPool()
    clips = [c for c in walk_clips(pool.GetRootFolder()) if c.GetName() == clip_name]
    if not clips:
        raise ResolveError(f"Clip '{clip_name}' not found in the media pool")
    clip = clips[0]
    if current is None or tl.GetName() != current.GetName():  # AppendToTimeline returns None on a timeline that is not current
        project.SetCurrentTimeline(tl)
    try:
        return _append(resolve, project, pool, tl, clip, clip_name, track, start, end, frames, at, kind)
    finally:
        if current is not None and tl.GetName() != current.GetName():
            project.SetCurrentTimeline(current)


def _append(resolve, project, pool, tl, clip, clip_name, track, start, end, frames, at, kind):
    info = {"mediaPoolItem": clip}
    track_type, track_no = _parse_track(track) if track else (None, None)
    if track_type:
        while tl.GetTrackCount(track_type) < track_no:  # a missing track would silently put the clip on track 1
            if not tl.AddTrack(track_type):
                raise ResolveError(f"AddTrack('{track_type}') failed while creating {track}")
        info["trackIndex"] = track_no
    if kind not in ("video", "audio", "both", None):
        raise ResolveError("--kind must be video, audio or both")
    media_type = {"video": 1, "audio": 2}.get(kind) or (2 if track_type == "audio" else None)
    if media_type:
        info["mediaType"] = media_type
    if at is not None:
        info["recordFrame"] = tl.GetStartFrame() + at
    tl_fps = float(tl.GetSetting("timelineFrameRate"))
    still = clip.GetClipProperty("Type") == "Still"
    if still:
        # AppendToTimeline ignores startFrame/endFrame for stills: set the length through mark in/out instead
        if frames:
            clip.SetMarkInOut(0, frames - 1)
        return _appended(_append_call(tl, pool, info), clip_name, frames, tl_fps)
    src_fps = float(clip.GetClipProperty("FPS") or tl_fps)
    first = start or 0
    if frames is None:
        if start is not None:
            info["startFrame"] = start
        if end is not None:
            info["endFrame"] = end
        return _appended(_append_call(tl, pool, info), clip_name, None, tl_fps)
    base = first + round(frames * src_fps / tl_fps) - 1
    # A source length rarely maps to an exact number of timeline frames, so probe one frame off at a time
    # and keep the closest (exact when the fps allow it).
    best = None
    for adjust in _DURATION_RETRY:
        added = _append_call(tl, pool, {**info, "startFrame": first, "endFrame": base + adjust})
        if not added:
            break
        off = max(abs(_dur(a) - frames) for a in added)
        if best is None or off < best[0]:
            best = (off, adjust)
        if off == 0:
            return _appended(added, clip_name, frames, tl_fps)
        tl.DeleteClips(added, False)  # wrong length: remove the attempt
    if best is None:
        raise ResolveError(f"AppendToTimeline('{clip_name}') returned nothing", code="append_failed", hint=_RELOAD_HINT)
    added = _append_call(tl, pool, {**info, "startFrame": first, "endFrame": base + best[1]})
    return _appended(added, clip_name, frames, tl_fps)


def _ids(tl):
    return {x.GetUniqueId() for t in ("video", "audio") for n in range(1, tl.GetTrackCount(t) + 1)
            for x in tl.GetItemListInTrack(t, n) or []}


def _append_call(tl, pool, info):
    """AppendToTimeline for one clip; returns the items that appeared on the timeline.

    The returned handles are not used: with a recordFrame, Resolve 21.2 returns dead handles (every getter
    gives None) although the clip is placed, so the new items are found by diffing the timeline.
    """
    before = _ids(tl)
    pool.AppendToTimeline([info])
    return [x for t in ("video", "audio") for n in range(1, tl.GetTrackCount(t) + 1)
            for x in tl.GetItemListInTrack(t, n) or [] if x.GetUniqueId() not in before]


def _dur(item):
    return item.GetDuration() if item.GetDuration() is not None else item.GetEnd() - item.GetStart()


def _items(added):
    """AppendToTimeline can return None entries (e.g. the audio half of a clip)."""
    return [a for a in added or [] if a]


def _appended(added, clip_name, frames, tl_fps):
    added = _items(added)
    if not added:
        raise ResolveError(f"AppendToTimeline('{clip_name}') failed", code="append_failed", hint=_RELOAD_HINT)
    return {"appended": clip_name, "exact": frames is None or all(_dur(a) == frames for a in added), "items": [
        {"track": ":".join(map(str, a.GetTrackTypeAndIndex())), "start": a.GetStart(), "end": a.GetEnd(), "duration": _dur(a)}
        for a in added]}


# ---- timeline versions: named duplicates "<base> v2", "<base> v3"... -------------
def _version_names(resolve, base):
    prefix = f"{base} v"
    return sorted(
        (t.GetName() for t in _all(resolve) if t.GetName() == base or (t.GetName().startswith(prefix) and t.GetName()[len(prefix):].isdigit())),
        key=lambda n: int(n[len(base) + 2:]) if n != base else 1,
    )


def versions(resolve, base=None):
    base = base or current_timeline(resolve).GetName()
    root = re.sub(r" v\d+$", "", base)  # "<base> v3" belongs to "<base>"
    return {"base": root, "versions": _version_names(resolve, root)}


def duplicate(resolve, new_name, source=None):
    project = current_project(resolve)
    src = find_timeline(project, source)
    copy = src.DuplicateTimeline(new_name)
    if copy is None:
        raise ResolveError(f"DuplicateTimeline('{new_name}') failed (name already used?)")
    return {"source": src.GetName(), "timeline": copy.GetName()}


def snapshot(resolve, source=None):
    """Duplicate `source` (default current) as the next free '<base> vN'."""
    project = current_project(resolve)
    src = find_timeline(project, source)
    info = versions(resolve, src.GetName())
    base = info["base"]
    last = max((int(n[len(base) + 2:]) if n != base else 1 for n in info["versions"]), default=1)
    return duplicate(resolve, f"{base} v{last + 1}", src.GetName())


def rename(resolve, new_name, source=None):
    tl = find_timeline(current_project(resolve), source)
    old = tl.GetName()
    if not tl.SetName(new_name):
        raise ResolveError(f"SetName('{new_name}') failed (name already used?)")
    return {"old": old, "new": new_name}


def delete_timeline(resolve, name, yes=False):
    if not yes:
        raise ResolveError("timeline delete removes the timeline (clips stay in the media pool). Re-run with --yes")
    project = current_project(resolve)
    tl = find_timeline(project, name)
    if not project.GetMediaPool().DeleteTimelines([tl]):
        raise ResolveError(f"DeleteTimelines('{name}') failed")
    return {"deleted": name}


def insert(resolve, kind, name):
    """Insert a generator / title / fusion title at the playhead."""
    fns = {"generator": "InsertGeneratorIntoTimeline", "fusion-generator": "InsertFusionGeneratorIntoTimeline",
           "title": "InsertTitleIntoTimeline", "fusion-title": "InsertFusionTitleIntoTimeline",
           "ofx-generator": "InsertOFXGeneratorIntoTimeline"}
    if kind not in fns:
        raise ResolveError(f"kind must be one of {list(fns)}")
    if getattr(current_timeline(resolve), fns[kind])(name) is None:
        raise ResolveError(f"{fns[kind]}('{name}') failed (unknown name?)")
    return {"inserted": name, "kind": kind}


def delete_track(resolve, track_type, index, yes=False):
    if not yes:
        raise ResolveError("delete-track removes the track and its clips. Re-run with --yes to confirm")
    if not current_timeline(resolve).DeleteTrack(track_type, index):
        raise ResolveError(f"DeleteTrack({track_type}, {index}) failed")
    return tracks(resolve)


def track_lock(resolve, track_type, index, locked):
    if not current_timeline(resolve).SetTrackLock(track_type, index, locked):
        raise ResolveError(f"SetTrackLock({track_type}, {index}) failed")
    return {"track": f"{track_type}:{index}", "locked": locked}


def track_name(resolve, track_type, index, name):
    if not current_timeline(resolve).SetTrackName(track_type, index, name):
        raise ResolveError(f"SetTrackName({track_type}, {index}) failed")
    return {"track": f"{track_type}:{index}", "name": name}


def fusion_clip(resolve, track, items):
    project = current_project(resolve)
    chosen = [find_item(project, None, track, n) for n in items]
    made = current_timeline(resolve).CreateFusionClip(chosen)
    if not made:
        raise ResolveError("CreateFusionClip failed (items on the same track and contiguous?)")
    return {"fusion_clip": made.GetName(), "from": len(chosen)}


def delete_clips(resolve, track, items, ripple=False, yes=False):
    if not yes:
        raise ResolveError("delete-clips removes clips from the timeline. Re-run with --yes to confirm")
    project = current_project(resolve)
    chosen = [find_item(project, None, track, n) for n in items]
    if not current_timeline(resolve).DeleteClips(chosen, ripple):
        raise ResolveError("DeleteClips failed")
    return {"deleted": len(chosen)}


def _timecode(tl, frame):
    """Non-drop timecode of an absolute timeline frame."""
    fps = round(float(tl.GetSetting("timelineFrameRate")))
    h, rest = divmod(frame, fps * 3600)
    m, rest = divmod(rest, fps * 60)
    sec, ff = divmod(rest, fps)
    return f"{h:02d}:{m:02d}:{sec:02d}:{ff:02d}"


def _layout(tl):
    """Position of every item on every track, subtitles included, plus the timeline end."""
    rows = {(t, n): [(x.GetStart(), x.GetEnd(), x.GetName()) for x in tl.GetItemListInTrack(t, n) or []]
            for t in ("video", "audio", "subtitle") for n in range(1, tl.GetTrackCount(t) + 1)}
    rows["end"] = tl.GetEndFrame()
    return rows


_INSERTERS = {"fusion-title": "InsertFusionTitleIntoTimeline", "title": "InsertTitleIntoTimeline",
              "generator": "InsertGeneratorIntoTimeline", "fusion-generator": "InsertFusionGeneratorIntoTimeline"}


def insert_title(resolve, name, track=1, at=0, duration=None, kind="fusion-title"):
    """Put a title or generator on video track `track` at timeline-relative frame `at` for `duration` frames, no ripple.

    Resolve, not the API caller, chooses the destination track of an insert, and an insert is a ripple edit.
    All other tracks (subtitles too) are locked meanwhile, and the result is checked against the layout and
    end frame from before: if anything but the new item changed, the new item is removed again (ripple delete)
    and the layout verified, so a wrong landing never stays on the timeline. An insert in the middle of an
    existing clip would split it, so that is refused up front.
    """
    if kind not in _INSERTERS:
        raise ResolveError(f"kind must be one of {sorted(_INSERTERS)}")
    tl = current_timeline(resolve)
    while tl.GetTrackCount("video") < track:
        if not tl.AddTrack("video"):
            raise ResolveError(f"AddTrack('video') failed while creating video:{track}")
    if track != tl.GetTrackCount("video"):
        # measured (Resolve 21.2): a native insert always goes to the TOPMOST video track and fails if that one is locked
        raise ResolveError(f"A native insert can only land on the topmost video track (V{tl.GetTrackCount('video')}), not V{track}",
                           code="not_topmost", hint="Use --mode compound (compound clip, any track), or drag the title by hand")
    start = tl.GetStartFrame()
    layout, ids = _layout(tl), _ids(tl)
    for key, rows in layout.items():
        # other tracks are locked during the insert and stay untouched (measured), so only the target track can be split
        if key == ("video", track) and any(a < start + at < b for a, b, _ in rows):
            raise ResolveError(f"Frame {at} falls inside a clip on video:{track}: a ripple insert would split it",
                               code="insert_would_split", hint="Insert at a cut, or on an empty stretch")
    others = [(t, i) for t in ("video", "audio", "subtitle") for i in range(1, tl.GetTrackCount(t) + 1) if (t, i) != ("video", track)]
    locks = {k: tl.GetIsTrackLocked(*k) for k in others + [("video", track)]}
    playhead_before, inout_before = tl.GetCurrentTimecode(), tl.GetMarkInOut() or {}
    inserted = None
    try:
        for k in others:
            tl.SetTrackLock(*k, True)
        tl.SetTrackLock("video", track, False)
        if duration:
            tl.SetMarkInOut(at, at + duration - 1)
        tl.SetCurrentTimecode(_timecode(tl, start + at))
        fn = getattr(tl, _INSERTERS[kind])
        for _ in range(4):  # right after a page switch the Edit page is not ready and the insert returns None
            inserted = fn(name)
            if inserted is not None:
                break
            time.sleep(1)
    finally:
        for k, state in locks.items():
            tl.SetTrackLock(*k, state)
        tl.ClearMarkInOut()
        video = inout_before.get("video") or inout_before.get("all") or {}
        if "in" in video and "out" in video:
            tl.SetMarkInOut(video["in"], video["out"])
        tl.SetCurrentTimecode(playhead_before)
    if inserted is None:
        raise ResolveError(f"Insert of '{name}' failed: unknown name, or Resolve's destination track is locked/unusable",
                           code="insert_failed", hint="Resolve picks the destination itself; for a pool item (compound, "
                           "fusion clip) use: timeline append NAME --track video:N --at F --frames D")
    new_id = inserted.GetUniqueId()
    new = [(t, n, x) for t in ("video", "audio") for n in range(1, tl.GetTrackCount(t) + 1)
           for x in tl.GetItemListInTrack(t, n) or [] if x.GetUniqueId() not in ids]
    mine = [(t, n, x) for t, n, x in new if x.GetUniqueId() == new_id]
    right = len(mine) == 1 and mine[0][:2] == ("video", track) and mine[0][2].GetStart() == start + at \
        and (not duration or _dur(mine[0][2]) == duration)
    after = _layout(tl)
    untouched = lambda d: {k: v for k, v in d.items() if k not in (("video", track), "end")}  # noqa: E731
    if right and untouched(after) == untouched(layout) and after["end"] >= layout["end"] and \
            after[("video", track)] == sorted(layout[("video", track)] + [(start + at, mine[0][2].GetEnd(), mine[0][2].GetName())]):
        return {"title": mine[0][2].GetName(), "track": f"video:{track}", "start": at, "duration": _dur(mine[0][2])}
    where = [(f"{t}:{n}", x.GetStart(), _dur(x)) for t, n, x in mine or new]
    if mine:
        # ripple delete closes the gap the insert opened; keep every other track locked so only the gap closes
        keep = [(t, n) for t in ("video", "audio", "subtitle") for n in range(1, tl.GetTrackCount(t) + 1) if (t, n) != mine[0][:2]]
        try:
            for k in keep:
                tl.SetTrackLock(*k, True)
            tl.DeleteClips([x for _, _, x in mine], True)
        finally:
            for k in keep:
                tl.SetTrackLock(*k, locks.get(k, False))
    restored = _layout(tl) == layout
    raise ResolveError(f"Insert landed on {where}, not video:{track} at {at}, or moved other items. "
                       + ("Removed again, timeline restored." if restored else "Removal did NOT restore the timeline: check it."),
                       code="insert_misplaced" if restored else "insert_misplaced_unrestored")


def insert_compound(resolve, name, track=1, at=0, duration=None, kind="fusion-title", text=None):
    """Title or generator on video track `track` at frame `at`, placed WITHOUT any ripple.

    A ripple insert (insert_title) can shift or trim other tracks and subtitles even when they are locked. Here
    the item is made in a scratch timeline, wrapped in a compound clip and appended at an exact record frame
    (an append never ripples). The result is a compound clip, not a native title: open it to edit the text,
    or pass `text` to set the Text+ StyledText beforehand. The scratch timeline is deleted afterwards.
    """
    import uuid

    if kind not in _INSERTERS:
        raise ResolveError(f"kind must be one of {sorted(_INSERTERS)}")
    project = current_project(resolve)
    target = current_timeline(resolve)
    pool = project.GetMediaPool()
    start = target.GetStartFrame()
    length = duration or 150
    busy = [x for x in target.GetItemListInTrack("video", track) or [] if x.GetStart() < start + at + length and x.GetEnd() > start + at]
    if busy:
        raise ResolveError(f"video:{track} is not empty between frames {at} and {at + length} ({len(busy)} item(s))",
                           code="track_occupied", hint="Pick another track or range")
    layout = _layout(target)
    tag = uuid.uuid4().hex[:6]
    scratch = pool.CreateEmptyTimeline(f"zz_cli_scratch_{tag}")
    if scratch is None:
        raise ResolveError("Cannot create the scratch timeline", code="scratch_failed")
    cc_item = None
    try:
        for setting in ("timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight"):
            scratch.SetSetting(setting, str(target.GetSetting(setting)))
        project.SetCurrentTimeline(scratch)
        scratch.SetMarkInOut(0, length - 1)
        scratch.SetCurrentTimecode(scratch.GetStartTimecode())
        made = None
        for _ in range(4):
            made = getattr(scratch, _INSERTERS[kind])(name)
            if made is not None:
                break
            time.sleep(1)
        if made is None:
            raise ResolveError(f"Insert of '{name}' into the scratch timeline failed: unknown name?", code="insert_failed")
        if text is not None:
            done = _set_text(made, text)
            if done is None:
                raise ResolveError(f"'{name}' has no Text+/Text3D tool to set text on", code="no_text_tool")
            if not done:
                raise ResolveError("The text read back differs from the one set", code="text_mismatch")
        cc = scratch.CreateCompoundClip(scratch.GetItemListInTrack("video", 1), {"name": f"{name} {tag}"})
        cc_item = cc.GetMediaPoolItem() if cc else None
        if cc_item is None:
            raise ResolveError("CreateCompoundClip failed", code="compound_failed")
        project.SetCurrentTimeline(target)
        result = _append(resolve, project, pool, target, cc_item, cc_item.GetName(), f"video:{track}", None, None,
                         duration, at, "video")
    finally:
        try:
            project.SetCurrentTimeline(target)
            pool.DeleteTimelines([scratch])
        except Exception:  # cleanup must not hide the real error
            pass
    after = _layout(target)
    key = ("video", track)
    # the end may grow when the item goes past the last clip; anything else changing on other tracks is a ripple
    if {k: v for k, v in after.items() if k not in (key, "end") and k[0] != "audio"} != \
            {k: v for k, v in layout.items() if k not in (key, "end") and k[0] != "audio"} or after["end"] < layout["end"]:
        raise ResolveError("Other tracks changed during the append: check the timeline", code="layout_changed")
    return {"clip": cc_item.GetName(), "track": f"video:{track}", "start": at, "result": result}


def _set_text(item, text):
    comp = item.GetFusionCompByIndex(1)
    for tool in (comp.GetToolList() or {}).values() if comp else ():
        if (tool.GetAttrs() or {}).get("TOOLS_RegID") in _TEXT_TOOLS:
            tool.SetInput("StyledText", text)
            return tool.GetInput("StyledText") == text
    return None
