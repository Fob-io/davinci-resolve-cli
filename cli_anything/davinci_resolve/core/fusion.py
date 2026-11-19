"""Fusion compositions on a timeline item: tools and their inputs.

Fusion's SetInput returns None whether or not it worked (a wrong input name is silently
ignored), so every write is read back.
"""
import os

from cli_anything.davinci_resolve.core.timeline import find_item
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def _item(resolve, timeline, track, item):
    return find_item(current_project(resolve), timeline, track, item)


def _comp(resolve, timeline, track, item, comp):
    """Comp by name or 1-based index (default: the first)."""
    clip = _item(resolve, timeline, track, item)
    names = clip.GetFusionCompNameList() or []
    if not names:
        raise ResolveError(
            "This item has no Fusion comp. Create one: "
            "api call TimelineItem.AddFusionComp --track ... --item ..."
        )
    if comp is None:
        found = clip.GetFusionCompByIndex(1)
    elif str(comp).isdigit():
        found = clip.GetFusionCompByIndex(int(comp))
    else:
        found = clip.GetFusionCompByName(comp)
    if found is None:
        raise ResolveError(f"Comp '{comp}' not found. Comps on this item: {names}")
    return found


def _tool(comp, name):
    tool = comp.FindTool(name)
    if tool is None:
        known = [t.Name for t in (comp.GetToolList() or {}).values()]
        raise ResolveError(f"Tool '{name}' not found. Tools in this comp: {known}")
    return tool


def _input_names(tool):
    """Every name an input answers to: the display name ('Styled Text') and the scripting id ('StyledText')."""
    names = []
    for handle in (tool.GetInputList() or {}).values():
        attrs = handle.GetAttrs() or {}
        names += [n for n in (attrs.get("INPS_Name"), attrs.get("INPS_ID")) if n]
    return names


def comps(resolve, timeline, track, item):
    clip = _item(resolve, timeline, track, item)
    return {"item": clip.GetName(), "comps": clip.GetFusionCompNameList() or []}


def tools(resolve, timeline, track, item, comp):
    found = _comp(resolve, timeline, track, item, comp)
    return [{"name": t.Name, "id": t.ID} for t in (found.GetToolList() or {}).values()]


def inputs(resolve, timeline, track, item, comp, tool):
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    rows = ((h.GetAttrs() or {}) for h in (found.GetInputList() or {}).values())
    return [{"id": a.get("INPS_ID"), "name": a.get("INPS_Name")} for a in rows if a.get("INPS_ID") or a.get("INPS_Name")]


def get_input(resolve, timeline, track, item, comp, tool, input_name):
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    if input_name not in _input_names(found):
        raise ResolveError(f"'{tool}' has no input '{input_name}'. Try: fusion inputs {tool}")
    return {"tool": tool, "input": input_name, "value": found.GetInput(input_name)}


def _input_handle(tool, name):
    return next(i for i in (tool.GetInputList() or {}).values()
                if name in (i.GetAttrs().get("INPS_Name"), i.GetAttrs().get("INPS_ID")))


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-6
    return a == b


def set_input(resolve, timeline, track, item, comp, tool, input_name, value):
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    if input_name not in _input_names(found):
        raise ResolveError(f"'{tool}' has no input '{input_name}'. Try: fusion inputs {tool}")
    old = found.GetInput(input_name)
    found.SetInput(input_name, value)
    now = found.GetInput(input_name)
    if not _same(now, value):
        raise ResolveError(f"SetInput({tool}.{input_name}={value!r}) was ignored: value is still {now!r}")
    return {"tool": tool, "input": input_name, "old": old, "value": now}


def _ok(result, what):
    if not result:
        raise ResolveError(f"{what} failed")
    return result


def add_comp(resolve, timeline, track, item, path=None):
    """New empty comp, or import a .comp file."""
    clip = _item(resolve, timeline, track, item)
    if path:
        if not os.path.isfile(path):
            raise ResolveError(f"Comp file not found: {path}")
        _ok(clip.ImportFusionComp(os.path.abspath(path)), f"ImportFusionComp({path})")
    else:
        _ok(clip.AddFusionComp(), "AddFusionComp")
    return comps(resolve, timeline, track, item)


def export_comp(resolve, timeline, track, item, path, comp_index=1):
    clip = _item(resolve, timeline, track, item)
    _ok(clip.ExportFusionComp(os.path.abspath(path), comp_index), f"ExportFusionComp(index {comp_index})")
    return {"exported": os.path.abspath(path)}


def delete_comp(resolve, timeline, track, item, name, yes=False):
    if not yes:
        raise ResolveError("fusion delete-comp removes the comp for good. Re-run with --yes to confirm")
    clip = _item(resolve, timeline, track, item)
    _ok(clip.DeleteFusionCompByName(name), f"DeleteFusionCompByName('{name}')")
    return comps(resolve, timeline, track, item)


def rename_comp(resolve, timeline, track, item, old, new):
    clip = _item(resolve, timeline, track, item)
    _ok(clip.RenameFusionCompByName(old, new), f"RenameFusionCompByName('{old}')")
    return comps(resolve, timeline, track, item)


def load_comp(resolve, timeline, track, item, name):
    """Make comp `name` the active one."""
    clip = _item(resolve, timeline, track, item)
    _ok(clip.LoadFusionCompByName(name), f"LoadFusionCompByName('{name}')")
    return {"active": name}


def add_tool(resolve, timeline, track, item, comp, tool_id, x=-32768, y=-32768):
    """Add a tool by registry id (Merge, Background, TextPlus, Transform...). -32768 = auto position."""
    found = _comp(resolve, timeline, track, item, comp)
    tool = found.AddTool(tool_id, x, y)
    if tool is None:
        raise ResolveError(f"AddTool('{tool_id}') failed (unknown tool id?)")
    return {"tool": tool.Name, "id": tool_id}


def delete_tool(resolve, timeline, track, item, comp, tool, yes=False):
    if not yes:
        raise ResolveError("fusion delete-tool removes the tool. Re-run with --yes to confirm")
    found = _comp(resolve, timeline, track, item, comp)
    _tool(found, tool).Delete()
    if found.FindTool(tool) is not None:
        raise ResolveError(f"Delete of '{tool}' was ignored")
    return {"deleted": tool}


def connect_tools(resolve, timeline, track, item, comp, src, dst, dst_input):
    """Wire the output of `src` into input `dst_input` of `dst`; read back to verify."""
    found = _comp(resolve, timeline, track, item, comp)
    source, target = _tool(found, src), _tool(found, dst)
    if dst_input not in _input_names(target):
        raise ResolveError(f"'{dst}' has no input '{dst_input}'. Try: fusion inputs {dst}")
    handle = _input_handle(target, dst_input)
    handle.ConnectTo(source.GetOutputList()[1] if hasattr(source, "GetOutputList") else source)
    out = handle.GetConnectedOutput()
    if not out or out.GetTool().Name != src:
        raise ResolveError(f"Connecting {src} -> {dst}.{dst_input} was ignored (input is connected to "
                           f"{out.GetTool().Name if out else 'nothing'})")
    return {"src": src, "dst": dst, "input": dst_input, "connected_from": out.GetTool().Name}


def keyframe(resolve, timeline, track, item, comp, tool, input_name, frame, value):
    """Set `value` at comp frame `frame`, animating the input with a BezierSpline if it is not yet."""
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    if input_name not in _input_names(found):
        raise ResolveError(f"'{tool}' has no input '{input_name}'. Try: fusion inputs {tool}")
    handle = _input_handle(found, input_name)
    if not handle.GetConnectedOutput():  # not animated yet
        found.AddModifier(input_name, "BezierSpline")
    found.SetInput(input_name, value, frame)
    now = found.GetInput(input_name, frame)
    if not _same(now, value):
        raise ResolveError(f"Keyframe {tool}.{input_name}@{frame}={value!r} was ignored: reads {now!r}")
    return {"tool": tool, "input": input_name, "frame": frame, "value": now}


def save_tool(resolve, timeline, track, item, comp, tool, path):
    """Save one tool (and its settings) as a .setting macro file."""
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    found.SaveSettings(target)
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise ResolveError(f"SaveSettings('{target}') wrote nothing")
    return {"tool": tool, "saved": target, "bytes": os.path.getsize(target)}


def dump(resolve, timeline, track, item, comp, show_all=False):
    """Every tool of a comp with the inputs that differ from their default or are connected."""
    found = _comp(resolve, timeline, track, item, comp)
    out = []
    for tool in (found.GetToolList() or {}).values():
        entry = {"name": tool.Name, "id": tool.ID, "inputs": {}, "connected": {}}
        for handle in (tool.GetInputList() or {}).values():
            attrs = handle.GetAttrs() or {}
            name = attrs.get("INPS_ID") or attrs.get("INPS_Name")
            if not name:
                continue
            link = handle.GetConnectedOutput()
            if link:
                entry["connected"][name] = link.GetTool().Name
            value, default = tool.GetInput(name), attrs.get("INPS_DefaultValue")
            if show_all or (value is not None and not link and not _same(value, default)):
                entry["inputs"][name] = value
        out.append(entry)
    return {"comp": comp, "tools": out}


def bbox(resolve, timeline, track, item, comp, tool, frame=None):
    """Rendered size of a tool's output: canvas size and the data window (pixels with content).

    For Text+ this is the real text extent, no empirical font factor needed. Units are comp pixels.
    """
    found = _tool(_comp(resolve, timeline, track, item, comp), tool)
    image = found.Output.GetValue(frame) if frame is not None else found.Output.GetValue()
    if image is None:
        raise ResolveError(f"'{tool}' produced no image at frame {frame}")
    window = dict(image.DataWindow or {})
    left, bottom, right, top = (window.get(i) for i in (1, 2, 3, 4))
    if None in (left, bottom, right, top):
        raise ResolveError(f"'{tool}' image has no data window: {window}")
    return {"tool": tool, "canvas": [image.Width, image.Height], "left": left, "bottom": bottom, "right": right,
            "top": top, "width": right - left, "height": top - bottom}
