"""Project-manager folders and databases, media storage (disk), gallery albums, AI/analysis."""
import os

from cli_anything.davinci_resolve.core.media import find_bin, select_clips
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project, project_manager


def _ok(result, what):
    if not result:
        raise ResolveError(f"{what} failed")


# ---- project folders (project manager) -------------------------------------------
def folders(resolve):
    pm = project_manager(resolve)
    return {"current": pm.GetCurrentFolder(), "folders": pm.GetFolderListInCurrentFolder() or [],
            "projects": pm.GetProjectListInCurrentFolder() or []}


def folder_go(resolve, target):
    """target: '/' root, '..' parent, or a sub-folder name."""
    pm = project_manager(resolve)
    ok = pm.GotoRootFolder() if target == "/" else pm.GotoParentFolder() if target == ".." else pm.OpenFolder(target)
    _ok(ok, f"open project folder '{target}'")
    return folders(resolve)


def folder_create(resolve, name):
    _ok(project_manager(resolve).CreateFolder(name), f"CreateFolder('{name}') (duplicate?)")
    return folders(resolve)


def folder_delete(resolve, name, yes=False):
    if not yes:
        raise ResolveError("project folder delete removes it for good. Re-run with --yes to confirm")
    _ok(project_manager(resolve).DeleteFolder(name), f"DeleteFolder('{name}') (not empty?)")
    return folders(resolve)


def project_delete(resolve, name, yes=False):
    if not yes:
        raise ResolveError("project delete removes the project for good. Re-run with --yes to confirm")
    _ok(project_manager(resolve).DeleteProject(name), f"DeleteProject('{name}') (open or missing?)")
    return folders(resolve)


def project_export(resolve, name, path, with_stills=True):
    _ok(project_manager(resolve).ExportProject(name, os.path.abspath(path), with_stills), f"ExportProject('{name}')")
    return {"exported": os.path.abspath(path)}


def project_import(resolve, path, name=None):
    if not os.path.isfile(path):
        raise ResolveError(f"Project file not found: {path}")
    pm = project_manager(resolve)
    _ok(pm.ImportProject(os.path.abspath(path), name) if name else pm.ImportProject(os.path.abspath(path)), "ImportProject")
    return folders(resolve)


def project_archive(resolve, name, path, with_media=False):
    _ok(project_manager(resolve).ArchiveProject(name, os.path.abspath(path), with_media), f"ArchiveProject('{name}')")
    return {"archived": os.path.abspath(path)}


def project_rename(resolve, new_name):
    project = current_project(resolve)
    old = project.GetName()
    _ok(project.SetName(new_name), f"SetName('{new_name}')")
    return {"old": old, "new": new_name}


# ---- databases ----------------------------------------------------------------------
def databases(resolve):
    pm = project_manager(resolve)
    return {"current": pm.GetCurrentDatabase(), "databases": pm.GetDatabaseList() or []}


def database_use(resolve, name):
    pm = project_manager(resolve)
    match = [d for d in pm.GetDatabaseList() or [] if d.get("DbName") == name]
    if not match:
        raise ResolveError(f"Database '{name}' not found. Available: {[d.get('DbName') for d in pm.GetDatabaseList() or []]}")
    _ok(pm.SetCurrentDatabase(match[0]), f"SetCurrentDatabase('{name}')")
    return databases(resolve)


# ---- media storage (disk) -----------------------------------------------------------
def storage_volumes(resolve):
    return resolve.GetMediaStorage().GetMountedVolumeList() or []


def storage_ls(resolve, folder):
    storage = resolve.GetMediaStorage()
    return {"folders": storage.GetSubFolderList(folder) or [], "files": storage.GetFileList(folder) or []}


def storage_import(resolve, paths, bin_path=""):
    """Import from disk into a bin (folders expand to their files)."""
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise ResolveError(f"Paths not found: {missing}")
    pool = current_project(resolve).GetMediaPool()
    pool.SetCurrentFolder(find_bin(pool.GetRootFolder(), bin_path))
    added = resolve.GetMediaStorage().AddItemListToMediaPool([os.path.abspath(p) for p in paths])
    if not added:
        raise ResolveError("AddItemListToMediaPool returned nothing (unsupported format?)")
    return {"imported": [c.GetName() for c in added]}


def storage_reveal(resolve, path):
    _ok(resolve.GetMediaStorage().RevealInStorage(os.path.abspath(path)), "RevealInStorage")
    return {"revealed": os.path.abspath(path)}


# ---- gallery albums -----------------------------------------------------------------
def albums(resolve):
    gallery = current_project(resolve).GetGallery()
    return {"stills": [gallery.GetAlbumName(a) for a in gallery.GetGalleryStillAlbums() or []],
            "powergrade": [gallery.GetAlbumName(a) for a in gallery.GetGalleryPowerGradeAlbums() or []],
            "current": gallery.GetAlbumName(gallery.GetCurrentStillAlbum())}


def album_create(resolve, name, powergrade=False):
    gallery = current_project(resolve).GetGallery()
    album = gallery.CreateGalleryPowerGradeAlbum() if powergrade else gallery.CreateGalleryStillAlbum()
    if album is None:
        raise ResolveError("Creating the album failed")
    _ok(gallery.SetAlbumName(album, name), f"SetAlbumName('{name}')")
    return albums(resolve)


def album_use(resolve, name):
    gallery = current_project(resolve).GetGallery()
    for album in (gallery.GetGalleryStillAlbums() or []) + (gallery.GetGalleryPowerGradeAlbums() or []):
        if gallery.GetAlbumName(album) == name:
            _ok(gallery.SetCurrentStillAlbum(album), f"SetCurrentStillAlbum('{name}')")
            return albums(resolve)
    raise ResolveError(f"Album '{name}' not found. Available: {albums(resolve)}")


def stills_import(resolve, paths, album=None):
    from cli_anything.davinci_resolve.core.stills import find_album
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise ResolveError(f"Files not found: {missing}")
    alb = find_album(current_project(resolve), album)
    _ok(alb.ImportStills([os.path.abspath(p) for p in paths]), "ImportStills")
    return {"stills_in_album": len(alb.GetStills() or [])}


# ---- AI / analysis -------------------------------------------------------------------
def detect_scene_cuts(resolve):
    from cli_anything.davinci_resolve.utils.resolve_backend import current_timeline
    _ok(current_timeline(resolve).DetectSceneCuts(), "DetectSceneCuts")
    return {"started": True, "check": "analysis status detect-cuts"}


def analysis_status(resolve, kind):
    project = current_project(resolve)
    fns = {"detect-cuts": "GetDetectSceneCutsStatus", "transcribe": "GetTranscribeAudioStatus",
           "slate": "GetAnalyzeForSlateStatus", "reframe": "GetSmartReframeStatus",
           "subtitles": "GetCreateSubtitlesFromAudioStatus"}
    if kind not in fns:
        raise ResolveError(f"kind must be one of {list(fns)}")
    return getattr(project, fns[kind])()


def _targets(resolve, clip, bin_path):
    if (clip is None) == (bin_path is None):
        raise ResolveError("Pass exactly one of --clip NAME or --bin PATH")
    if clip is not None:
        return select_clips(resolve, clip=clip)
    return [find_bin(current_project(resolve).GetMediaPool().GetRootFolder(), bin_path)]


def analyze(resolve, action, clip=None, bin_path=None, faces=False, better=False, color="Blue", speakers=False, nested=False, yes=False):
    """Run an analysis on a clip or a whole bin: transcribe, clear-transcript, classify-audio,
    clear-classification, intellisearch, slate."""
    if action in ("clear-transcript", "clear-classification") and not yes:
        raise ResolveError(f"{action} deletes existing analysis data. Re-run with --yes to confirm")
    for target in _targets(resolve, clip, bin_path):
        calls = {
            "transcribe": lambda t=target: t.TranscribeAudio(speakers, nested),
            "clear-transcript": lambda t=target: t.ClearTranscription(),
            "classify-audio": lambda t=target: t.PerformAudioClassification(),
            "clear-classification": lambda t=target: t.ClearAudioClassification(),
            "intellisearch": lambda t=target: t.AnalyzeForIntellisearch(faces, better),
            "slate": lambda t=target: t.AnalyzeForSlate(color),
        }
        if action not in calls:
            raise ResolveError(f"action must be one of {list(calls)}")
        _ok(calls[action](), f"{action} on '{target.GetName()}'")
    return {"action": action, "started": True, "check": "analysis status ..."}


def luts_refresh(resolve):
    _ok(current_project(resolve).RefreshLUTList(), "RefreshLUTList")
    return {"refreshed": True}
