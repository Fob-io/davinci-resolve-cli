"""Media pool: bins and clips."""
import os

from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def _pool(resolve):
    return current_project(resolve).GetMediaPool()


def find_bin(folder, path):
    """Resolve a '/'-separated bin path relative to `folder`."""
    for part in [p for p in path.split("/") if p]:
        match = [f for f in folder.GetSubFolderList() if f.GetName() == part]
        if not match:
            raise ResolveError(f"Bin '{part}' not found in '{folder.GetName()}'")
        folder = match[0]
    return folder


def tree(resolve, folder=None):
    folder = folder or _pool(resolve).GetRootFolder()
    return {
        "name": folder.GetName(),
        "clips": len(folder.GetClipList() or []),
        "bins": [tree(resolve, f) for f in folder.GetSubFolderList()],
    }


def list_clips(resolve, bin_path=""):
    folder = find_bin(_pool(resolve).GetRootFolder(), bin_path)
    return [
        {
            "name": c.GetName(),
            "duration": c.GetClipProperty("Duration"),
            "fps": c.GetClipProperty("FPS"),
            "type": c.GetClipProperty("Type"),
            "path": c.GetClipProperty("File Path"),
        }
        for c in folder.GetClipList() or []
    ]


def create_bin(resolve, name, parent=""):
    pool = _pool(resolve)
    parent_folder = find_bin(pool.GetRootFolder(), parent)
    if pool.AddSubFolder(parent_folder, name) is None:
        raise ResolveError(f"AddSubFolder('{name}') failed (duplicate name?)")
    return {"bin": f"{parent}/{name}".strip("/")}


def _ensure_bin(pool, path):
    """Bin at '/'-separated `path`, creating the missing levels."""
    folder = pool.GetRootFolder()
    for part in [p for p in path.split("/") if p]:
        match = [f for f in folder.GetSubFolderList() if f.GetName() == part]
        if not match:
            made = pool.AddSubFolder(folder, part)
            if made is None:
                raise ResolveError(f"AddSubFolder('{part}') failed in '{folder.GetName()}'")
            match = [made]
        folder = match[0]
    return folder


def import_media(resolve, paths, bin_path="", create=False):
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise ResolveError(f"Files not found: {missing}")
    pool = _pool(resolve)
    pool.SetCurrentFolder(_ensure_bin(pool, bin_path) if create else find_bin(pool.GetRootFolder(), bin_path))
    clips = pool.ImportMedia([os.path.abspath(p) for p in paths])
    if not clips:
        raise ResolveError("ImportMedia returned nothing (unsupported format?)")
    return {"imported": [c.GetName() for c in clips]}


CLIP_COLORS = (
    "Orange", "Apricot", "Yellow", "Lime", "Olive", "Green", "Teal", "Navy",
    "Blue", "Purple", "Violet", "Pink", "Tan", "Beige", "Brown", "Chocolate",
)


def walk_clips(folder):
    """Every clip in `folder` and its sub-bins."""
    yield from folder.GetClipList() or []
    for sub in folder.GetSubFolderList():
        yield from walk_clips(sub)


def select_clips(resolve, clip=None, bin_path=None, recursive=False):
    """Clips named by --clip NAME (whole pool) or --bin PATH ('/' = root); exactly one."""
    if (clip is None) == (bin_path is None):
        raise ResolveError("Pass exactly one of --clip NAME or --bin PATH")
    root = _pool(resolve).GetRootFolder()
    if clip is not None:
        found = [c for c in walk_clips(root) if c.GetName() == clip]
        if not found:
            raise ResolveError(f"Clip '{clip}' not found in the media pool")
        if len(found) > 1:
            raise ResolveError(f"{len(found)} clips are named '{clip}'. Select them with --bin PATH")
        return found
    folder = find_bin(root, bin_path)
    return list(walk_clips(folder)) if recursive else list(folder.GetClipList() or [])


def clip_props(resolve, keys, **selector):
    return [
        {"clip": c.GetName(), "props": {k: c.GetClipProperty(k) for k in keys} if keys else c.GetClipProperty()}
        for c in select_clips(resolve, **selector)
    ]


def clip_metadata(resolve, keys, **selector):
    return [
        {"clip": c.GetName(), "metadata": {k: c.GetMetadata(k) for k in keys} if keys else c.GetMetadata()}
        for c in select_clips(resolve, **selector)
    ]


def _bulk_set(clips, value, read, write, overwrite):
    """Write `value` per clip. Never replaces a different non-empty value unless `overwrite`.

    Each write is read back: Resolve returns True for some writes it then ignores.
    """
    results = []
    for c in clips:
        old = read(c)
        if old == value:
            status = "unchanged"
        elif old and not overwrite:
            status = "skipped"
        else:
            write(c, value)
            status = "set" if read(c) == value else "failed"
        results.append({"clip": c.GetName(), "status": status, "old": old})
    counts = {k: sum(r["status"] == k for r in results) for k in ("set", "unchanged", "skipped", "failed")}
    return {"counts": counts, "results": results}


def set_clip_property(resolve, key, value, overwrite=False, **selector):
    clips = select_clips(resolve, **selector)
    return _bulk_set(clips, value, lambda c: c.GetClipProperty(key), lambda c, v: c.SetClipProperty(key, v), overwrite)


def set_clip_metadata(resolve, key, value, overwrite=False, **selector):
    clips = select_clips(resolve, **selector)
    return _bulk_set(clips, value, lambda c: c.GetMetadata(key), lambda c, v: c.SetMetadata({key: v}), overwrite)


def set_clip_color(resolve, color, overwrite=False, **selector):
    if color not in CLIP_COLORS:
        raise ResolveError(f"Invalid color '{color}'. Valid: {list(CLIP_COLORS)}")
    clips = select_clips(resolve, **selector)
    return _bulk_set(clips, color, lambda c: c.GetClipColor(), lambda c, v: c.SetClipColor(v), overwrite)


def replace_clip(resolve, clip, path, keep_subclip=False):
    """Point media-pool clip `clip` at a new file; its timeline items stay. Same path = re-read the file."""
    if not os.path.isfile(path):
        raise ResolveError(f"File not found: {path}")
    (item,) = select_clips(resolve, clip=clip)
    call = item.ReplaceClipPreserveSubClip if keep_subclip else item.ReplaceClip
    if not call(os.path.abspath(path)):
        raise ResolveError(f"ReplaceClip('{clip}', '{path}') failed")
    return {"clip": clip, "file": item.GetClipProperty("File Path")}


def transcript(resolve, clip):
    (item,) = select_clips(resolve, clip=clip)
    return {"clip": clip, "transcription": item.GetTranscription()}
