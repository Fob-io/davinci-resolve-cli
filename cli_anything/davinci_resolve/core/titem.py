"""TimelineItem properties, speed, fades, flags, takes, AI tools, and item/media-pool markers."""
import os

from cli_anything.davinci_resolve.core.media import select_clips
from cli_anything.davinci_resolve.core.timeline import find_item
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def _item(resolve, timeline, track, item):
    return find_item(current_project(resolve), timeline, track, item)


def _ok(result, what):
    if not result:
        raise ResolveError(f"{what} failed")


def get_props(resolve, timeline, track, item, keys=()):
    props = _item(resolve, timeline, track, item).GetProperties() or {}
    return {k: props.get(k) for k in keys} if keys else props


def set_props(resolve, timeline, track, item, values):
    """values: {Pan: 10, ZoomX: 1.2, ...}. Each is read back (Resolve can return True and ignore a write)."""
    clip = _item(resolve, timeline, track, item)
    props = clip.GetProperties() or {}
    unknown = [k for k in values if k not in props]
    if unknown:
        raise ResolveError(f"Unknown properties {unknown}. Known: {sorted(props)}")
    _ok(clip.SetProperties(values), f"SetProperties({values})")
    now = clip.GetProperties() or {}
    bad = {k: now.get(k) for k, v in values.items() if abs(float(now.get(k, 0)) - float(v)) > 1e-4}
    if bad:
        raise ResolveError(f"SetProperties was ignored for {bad}")
    return {k: now[k] for k in values}


def speed(resolve, timeline, track, item, new=None):
    clip = _item(resolve, timeline, track, item)
    if new is not None:
        known = {"Percentage", "PitchCorrection", "StretchKeyframesToFit", "RippleTimeline"}
        if not isinstance(new, dict) or set(new) - known:
            raise ResolveError(f"--set takes a JSON object with keys from {sorted(known)}")
        _ok(clip.SetSpeed(new), f"SetSpeed({new})")
        now = clip.GetSpeed()
        now = now or {}
        bad = {k: now[k] for k, v in new.items() if k in now and (abs(float(now[k]) - float(v)) > 1e-3 if k == "Percentage" else now[k] != v)}
        if bad or "Percentage" in new and "Percentage" not in now:
            raise ResolveError(f"SetSpeed was ignored for {bad or 'Percentage'}: speed is {now}")
    return clip.GetSpeed()


def enable(resolve, timeline, track, item, enabled):
    clip = _item(resolve, timeline, track, item)
    _ok(clip.SetClipEnabled(enabled), "SetClipEnabled")
    return {"item": clip.GetName(), "enabled": clip.GetClipEnabled()}


def fades(resolve, timeline, track, item, fade_in=None, fade_out=None):
    clip = _item(resolve, timeline, track, item)
    new = {k: v for k, v in (("FadeIn", fade_in), ("FadeOut", fade_out)) if v is not None}
    if new:
        _ok(clip.SetFades(new), f"SetFades({new})")
        now = clip.GetFades() or {}
        bad = {k: now.get(k) for k, v in new.items() if now.get(k) != v}
        if bad:
            raise ResolveError(f"SetFades was ignored for {bad}")
    return clip.GetFades()


def clip_color(resolve, timeline, track, item, color=None, clear=False):
    clip = _item(resolve, timeline, track, item)
    if clear:
        _ok(clip.ClearClipColor(), "ClearClipColor")
    elif color:
        _ok(clip.SetClipColor(color), f"SetClipColor('{color}')")
    return {"item": clip.GetName(), "color": clip.GetClipColor()}


def flags(resolve, timeline, track, item, add=None, clear=None):
    clip = _item(resolve, timeline, track, item)
    if add:
        _ok(clip.AddFlag(add), f"AddFlag('{add}')")
    if clear:
        _ok(clip.ClearFlags(clear), f"ClearFlags('{clear}')")
    return {"flags": clip.GetFlagList()}


def export_frame(resolve, timeline, track, item, offset, path):
    clip = _item(resolve, timeline, track, item)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    result = clip.ExportFrame(offset, {"outputType": "file", "filePath": os.path.abspath(path)})
    if not result or not result.get("success") or not os.path.exists(os.path.abspath(path)):
        raise ResolveError(f"ExportFrame(offset {offset}) wrote nothing: {(result or {}).get('error')}")
    return {"exported": os.path.abspath(path)}


def takes(resolve, timeline, track, item):
    clip = _item(resolve, timeline, track, item)
    count = clip.GetTakesCount() or 0
    return {"count": count, "selected": clip.GetSelectedTakeIndex(),
            "takes": [clip.GetTakeByIndex(i) for i in range(1, count + 1)]}


def take_select(resolve, timeline, track, item, index):
    clip = _item(resolve, timeline, track, item)
    _ok(clip.SelectTakeByIndex(index), f"SelectTakeByIndex({index})")
    return takes(resolve, timeline, track, item)


def take_delete(resolve, timeline, track, item, index, yes=False):
    if not yes:
        raise ResolveError("take-delete removes a take. Re-run with --yes to confirm")
    clip = _item(resolve, timeline, track, item)
    _ok(clip.DeleteTakeByIndex(index), f"DeleteTakeByIndex({index})")
    return takes(resolve, timeline, track, item)


def take_add(resolve, timeline, track, item, clip_name, start=None, end=None):
    clip = _item(resolve, timeline, track, item)
    (pool_item,) = select_clips(resolve, clip=clip_name)
    _ok(clip.AddTake(pool_item, start, end) if start is not None else clip.AddTake(pool_item), "AddTake")
    return takes(resolve, timeline, track, item)


def take_finalize(resolve, timeline, track, item):
    _ok(_item(resolve, timeline, track, item).FinalizeTake(), "FinalizeTake")
    return {"finalized": True}


def ai(resolve, timeline, track, item, action, mode=None):
    """magic-mask (mode F|B|BI), regenerate-mask, stabilize, smart-reframe."""
    clip = _item(resolve, timeline, track, item)
    if action == "magic-mask":
        if mode not in ("F", "B", "BI"):
            raise ResolveError("magic-mask needs --mode F (forward), B (backward) or BI (both)")
        _ok(clip.CreateMagicMask(mode), "CreateMagicMask")
    elif action == "regenerate-mask":
        _ok(clip.RegenerateMagicMask(), "RegenerateMagicMask")
    elif action == "stabilize":
        _ok(clip.Stabilize(), "Stabilize")
    elif action == "smart-reframe":
        _ok(clip.SmartReframe(), "SmartReframe")
    else:
        raise ResolveError(f"Unknown AI action '{action}'")
    return {"item": clip.GetName(), "action": action}


# ---- markers (timeline item or media-pool clip) ------------------------------
def _holder(resolve, timeline, track, item, clip):
    if clip:
        (pool_item,) = select_clips(resolve, clip=clip)
        return pool_item
    return _item(resolve, timeline, track, item)


def list_markers(resolve, timeline, track, item, clip=None):
    return [{"frame": f, **m} for f, m in sorted((_holder(resolve, timeline, track, item, clip).GetMarkers() or {}).items())]


def add_marker(resolve, timeline, track, item, clip, frame, color, name, note="", duration=1, custom=None):
    holder = _holder(resolve, timeline, track, item, clip)
    args = (frame, color, name, note, duration) + ((custom,) if custom else ())
    _ok(holder.AddMarker(*args), f"AddMarker(frame {frame}; occupied or bad color?)")
    return list_markers(resolve, timeline, track, item, clip)


def delete_marker(resolve, timeline, track, item, clip, frame=None, color=None, custom=None):
    holder = _holder(resolve, timeline, track, item, clip)
    if sum(x is not None for x in (frame, color, custom)) != 1:
        raise ResolveError("Pass exactly one of --frame, --color (or All) or --custom")
    if frame is not None:
        _ok(holder.DeleteMarkerAtFrame(frame), f"DeleteMarkerAtFrame({frame})")
    elif color is not None:
        _ok(holder.DeleteMarkersByColor(color), f"DeleteMarkersByColor('{color}')")
    else:
        _ok(holder.DeleteMarkerByCustomData(custom), "DeleteMarkerByCustomData")
    return list_markers(resolve, timeline, track, item, clip)
