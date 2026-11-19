"""Generic access to the whole Resolve scripting API.

The method catalogue comes from DaVinciResolveScript.pyi, shipped with Resolve, so
`search`/`describe` work offline and always match the installed version. `call`
resolves the receiver object from selector options, because the CLI keeps no state.
"""
import os
import re
from functools import lru_cache

from cli_anything.davinci_resolve.core.media import find_bin, walk_clips
from cli_anything.davinci_resolve.core.timeline import find_item, find_timeline
from cli_anything.davinci_resolve.utils.resolve_backend import (
    ResolveError, current_project, script_api_dir,
)

_CLASS = re.compile(r"^class (\w+):\s*$")
_DEF = re.compile(r"^\tdef (\w+)\(self(?:, )?(.*?)\)(?: -> (.*))?:\s*$")
# Method-name prefixes that destroy or end something: need --yes.
COLOR_PAGE_CALLS = {("Graph", "ApplyGradeFromDRX"), ("Graph", "ResetAllGrades"), ("Graph", "ApplyArriCdlLut"),
                    ("TimelineItem", "CopyGrades"), ("TimelineItem", "SetCDL")}
DESTRUCTIVE = ("Delete", "Remove", "Close", "Quit", "Clear", "Reset", "CopyGrades", "ApplyGradeFromDRX")


@lru_cache(maxsize=1)
def catalogue():
    """{ClassName: {MethodName: {"params", "returns", "doc"}}} for the object classes."""
    with open(os.path.join(script_api_dir(), "DaVinciResolveScript.pyi"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    classes, current = {}, None
    for i, line in enumerate(lines):
        if m := _CLASS.match(line):
            current = classes.setdefault(m.group(1), {})
        elif current is not None and (m := _DEF.match(line)):
            doc = lines[i + 1].strip().strip('"') if i + 1 < len(lines) else ""
            current[m.group(1)] = {"params": m.group(2), "returns": m.group(3) or "", "doc": doc}
    return {c: m for c, m in classes.items() if m}


def search(pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    return [
        f"{cls}.{name}({m['params']}) -> {m['returns']} :: {m['doc']}"
        for cls, methods in catalogue().items()
        for name, m in methods.items()
        if rx.search(f"{cls}.{name} {m['doc']}")
    ]


def _lookup(symbol):
    cls, _, name = symbol.partition(".")
    methods = catalogue().get(cls)
    if methods is None:
        raise ResolveError(f"Unknown class '{cls}'. Classes: {sorted(catalogue())}")
    if name not in methods:
        raise ResolveError(f"'{cls}' has no method '{name}'. Try: api search {name or cls}")
    return cls, name, methods[name]


def describe(symbol):
    cls, name, m = _lookup(symbol)
    return {
        "symbol": f"{cls}.{name}", "params": m["params"], "returns": m["returns"], "doc": m["doc"],
        "destructive": name.startswith(DESTRUCTIVE),
    }


# ---- receiver and argument resolution ---------------------------------------
def _find_clip(project, name):
    clips = [c for c in walk_clips(project.GetMediaPool().GetRootFolder()) if c.GetName() == name]
    if not clips:
        raise ResolveError(f"Clip '{name}' not found in the media pool")
    return clips[0]


def receiver(resolve, cls, sel):
    """Object that owns the method, chosen by class and selector options."""
    if cls == "Resolve":
        return resolve
    if cls == "ProjectManager":
        return resolve.GetProjectManager()
    if cls == "MediaStorage":
        return resolve.GetMediaStorage()
    project = current_project(resolve)
    if cls == "Project":
        return project
    if cls == "MediaPool":
        return project.GetMediaPool()
    if cls == "Folder":
        return find_bin(project.GetMediaPool().GetRootFolder(), sel.get("bin") or "")
    if cls == "MediaPoolItem":
        return _find_clip(project, sel.get("clip") or "")
    if cls == "Timeline":
        return find_timeline(project, sel.get("timeline"))
    if cls == "TimelineItem":
        return find_item(project, sel.get("timeline"), sel.get("track"), sel.get("item"))
    if cls == "Gallery":
        return project.GetGallery()
    if cls == "GalleryStillAlbum":
        from cli_anything.davinci_resolve.core.stills import find_album
        return find_album(project, sel.get("album"))
    if cls == "Graph":
        graph = find_item(project, sel.get("timeline"), sel.get("track"), sel.get("item")).GetNodeGraph(sel.get("layer") or 1)
        if graph is None:
            raise ResolveError("This item has no node graph (not a video item with a grade)")
        return graph
    raise ResolveError(f"Class '{cls}' cannot be addressed from the CLI (no selector for it)")


def _resolve_arg(resolve, value):
    """Turn {"$clip": name} / {"$timeline": name} / {"$bin": path} / {"$const": NAME} into live objects."""
    if isinstance(value, list):
        return [_resolve_arg(resolve, v) for v in value]
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)).startswith("$"):
        key, arg = next(iter(value.items()))
        project = current_project(resolve)
        if key == "$clip":
            return _find_clip(project, arg)
        if key == "$timeline":
            return find_timeline(project, arg)
        if key == "$bin":
            return find_bin(project.GetMediaPool().GetRootFolder(), arg)
        if key == "$const":
            if not hasattr(resolve, arg):
                raise ResolveError(f"Resolve has no constant '{arg}'")
            return getattr(resolve, arg)
        raise ResolveError(f"Unknown reference '{key}'. Use $clip, $timeline, $bin or $const")
    if isinstance(value, dict):
        return {k: _resolve_arg(resolve, v) for k, v in value.items()}
    return value


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    getter = getattr(value, "GetName", None)
    name = None
    try:
        name = getter() if getter else None
    except Exception:  # noqa: BLE001 - any Resolve object may refuse GetName
        pass
    return {"_object": name if name is not None else str(value)}


def call(resolve, symbol, args, sel, yes=False):
    cls, _, name = symbol.partition(".")
    if cls not in catalogue():
        raise ResolveError(f"Unknown class '{cls}'. Classes: {sorted(catalogue())}")
    if name.startswith(DESTRUCTIVE) and not yes:
        raise ResolveError(f"{symbol} is destructive. Re-run with --yes to confirm")
    if not isinstance(args, list):
        raise ResolveError('--args must be a JSON array, e.g. \'["Blue", 10]\'')
    target = receiver(resolve, cls, sel)
    # The shipped stub omits some legacy methods the app still accepts (e.g. Project.GetSetting).
    in_stub = name in catalogue()[cls]
    if not in_stub and not hasattr(target, name):
        raise ResolveError(f"'{cls}' has no method '{name}'. Try: api search {name or cls}")
    resolved = [_resolve_arg(resolve, a) for a in args]
    if (cls, name) in COLOR_PAGE_CALLS:  # these silently do nothing unless the Color page is open
        from cli_anything.davinci_resolve.core.color import _on_color_page
        result = _on_color_page(resolve, lambda: getattr(target, name)(*resolved))
    else:
        result = getattr(target, name)(*resolved)
    out = {"symbol": symbol, "result": _jsonable(result)}
    if not in_stub:
        out["in_stub"] = False
    return out
