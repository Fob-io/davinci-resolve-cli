"""Gallery stills: list, grab, export, delete. Verified quirks (Resolve 21.1):
- ExportStills fails (False) if the destination folder is missing, so it is created first.
- GrabStill can fail silently right after SetCurrentTimecode, so it is retried after a pause
  and the still it returns (not an album position) is what gets labelled.
- An export writes the image plus a .drx grade file next to it.
"""
import os
import time

from cli_anything.davinci_resolve.core.timeline import playhead
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project, current_timeline

_GRAB_ATTEMPTS = 6
_GRAB_PAUSE = 1.5


def find_album(project, name=None):
    gallery = project.GetGallery()
    if name is None:
        return gallery.GetCurrentStillAlbum()
    albums = gallery.GetGalleryStillAlbums() or []
    for album in albums:
        if gallery.GetAlbumName(album) == name:
            return album
    raise ResolveError(f"Album '{name}' not found. Available: {[gallery.GetAlbumName(a) for a in albums]}")


def _rows(album):
    return [{"index": i, "label": album.GetLabel(s)} for i, s in enumerate(album.GetStills() or [], 1)]


def list_stills(resolve, album=None):
    return _rows(find_album(current_project(resolve), album))


def grab(resolve, label=None, at=None):
    timeline = current_timeline(resolve)
    if at is not None:
        playhead(resolve, at)
        time.sleep(_GRAB_PAUSE)
    still = None
    page = resolve.GetCurrentPage()
    if page != "color":  # GrabStill needs the Color page loaded, and that happens asynchronously
        resolve.OpenPage("color")
        time.sleep(_GRAB_PAUSE)
    try:
        for attempt in range(_GRAB_ATTEMPTS):
            still = timeline.GrabStill()
            if still:
                break
            time.sleep(_GRAB_PAUSE)
    finally:
        if page != "color":
            resolve.OpenPage(page)
    if not still:
        raise ResolveError(f"GrabStill failed {_GRAB_ATTEMPTS} times (no video clip under the playhead?)")
    album = find_album(current_project(resolve))
    if label and not album.SetLabel(still, label):
        raise ResolveError(f"Still grabbed but SetLabel('{label}') failed")
    return {"grabbed": True, "label": label, "stills_in_album": len(album.GetStills() or [])}


def _pick(album, labels, indexes, all_stills):
    if sum(map(bool, (labels, indexes, all_stills))) != 1:
        raise ResolveError("Pass exactly one of --label (repeatable), --index (repeatable) or --all")
    stills = album.GetStills() or []
    if all_stills:
        chosen = stills
    elif indexes:
        bad = [i for i in indexes if not 1 <= i <= len(stills)]
        if bad:
            raise ResolveError(f"No still at index {bad}; album has {len(stills)}")
        chosen = [stills[i - 1] for i in indexes]
    else:
        chosen = [s for s in stills if album.GetLabel(s) in labels]
        missing = set(labels) - {album.GetLabel(s) for s in chosen}
        if missing:
            raise ResolveError(f"No still labelled {sorted(missing)}")
    if not chosen:
        raise ResolveError("The album has no stills to act on")
    return chosen


def export(resolve, directory, prefix="still", fmt="png", labels=(), indexes=(), all_stills=False, album=None):
    alb = find_album(current_project(resolve), album)
    chosen = _pick(alb, labels, indexes, all_stills)
    os.makedirs(directory, exist_ok=True)
    before = set(os.listdir(directory))
    for _ in range(_GRAB_ATTEMPTS):  # fails at random right after grab/page switches, so retry
        if alb.ExportStills(chosen, os.path.abspath(directory), prefix, fmt):
            break
        time.sleep(_GRAB_PAUSE)
    else:
        raise ResolveError(f"ExportStills to '{directory}' failed (format '{fmt}' unknown?)")
    files = sorted(set(os.listdir(directory)) - before)
    if not files:
        raise ResolveError("ExportStills reported success but wrote no files")
    return {"exported": len(chosen), "directory": os.path.abspath(directory), "files": files}


def delete(resolve, labels=(), indexes=(), all_stills=False, album=None, yes=False):
    if not yes:
        raise ResolveError("stills delete removes stills for good. Re-run with --yes to confirm")
    alb = find_album(current_project(resolve), album)
    chosen = _pick(alb, labels, indexes, all_stills)
    before = len(alb.GetStills() or [])
    if not alb.DeleteStills(chosen):
        raise ResolveError("DeleteStills failed")
    return {"deleted": before - len(alb.GetStills() or []), "remaining": len(alb.GetStills() or [])}
