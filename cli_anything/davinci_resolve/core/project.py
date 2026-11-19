"""Project operations. Resolve holds the state; every call acts on the live app."""
from cli_anything.davinci_resolve.utils.resolve_backend import (
    ResolveError, current_project, project_manager,
)


def launch(timeout=60):
    """Start Resolve (macOS) and wait until the scripting API answers. No-op if already running."""
    import subprocess
    import time

    from cli_anything.davinci_resolve.utils.resolve_backend import connect

    last = None
    for attempt in range(timeout // 2):
        try:
            resolve = connect()
            return {"running": True, "version": resolve.GetVersionString()}
        except ResolveError as e:
            last = e
            if attempt == 0:
                subprocess.run(["open", "-a", "DaVinci Resolve"], check=False)
            time.sleep(2)
    raise ResolveError(f"Resolve did not answer within {timeout}s: {last}")


def status(resolve):
    project = project_manager(resolve).GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    return {
        "product": resolve.GetProductName(),
        "version": resolve.GetVersionString(),
        "page": resolve.GetCurrentPage(),
        "project": project.GetName() if project else None,
        "timeline": timeline.GetName() if timeline else None,
    }


def list_projects(resolve):
    return list(project_manager(resolve).GetProjectListInCurrentFolder() or [])


def info(resolve):
    project = current_project(resolve)
    return {
        "name": project.GetName(),
        "timeline_count": project.GetTimelineCount(),
        "fps": project.GetSetting("timelineFrameRate"),
        "width": project.GetSetting("timelineResolutionWidth"),
        "height": project.GetSetting("timelineResolutionHeight"),
    }


_LOAD_ATTEMPTS = 4
_LOAD_WAIT = 3  # seconds; Resolve needs a moment to finish closing the previous project


def _enter_folder(pm, path):
    """Go to the project-manager folder `path` ('A/B', from the root) and return it."""
    if not pm.GotoRootFolder():
        raise ResolveError("Cannot go to the root project folder", code="folder_not_found")
    for part in [p for p in path.split("/") if p]:
        if not pm.OpenFolder(part):
            raise ResolveError(f"Project folder '{part}' not found in '{pm.GetCurrentFolder()}'",
                               code="folder_not_found", hint=f"Available: {pm.GetFolderListInCurrentFolder() or []}")


def open_project(resolve, name):
    """Save the current project, then open it. NAME may be a path from the root ('02 - X/Mar-Giu/16 - Y'):
    the project-manager folder is then changed to match, so the call does not depend on earlier `project cd`.
    LoadProject is retried a few times because it fails right after a hang or while the old project closes."""
    import time

    pm = project_manager(resolve)
    folder, _, name = name.rpartition("/")
    if folder:
        _enter_folder(pm, folder)
    if name not in list_projects(resolve):
        raise ResolveError(f"Project '{name}' not found in '{pm.GetCurrentFolder()}'. Available: {list_projects(resolve)}",
                           code="project_not_found")
    current = pm.GetCurrentProject()
    if current is not None and current.GetName() != "Untitled Project":
        pm.SaveProject()  # avoid the unsaved-changes dialog no script can dismiss (also for projects in other folders)
    for attempt in range(_LOAD_ATTEMPTS):
        if pm.LoadProject(name) is not None:
            return {"project": name, "folder": pm.GetCurrentFolder(), "attempts": attempt + 1}
        time.sleep(_LOAD_WAIT)
    raise ResolveError(f"LoadProject('{name}') failed {_LOAD_ATTEMPTS} times", code="load_failed",
                       hint="Resolve may be hung or showing a dialog. Run: doctor")


def create_project(resolve, name):
    if name in list_projects(resolve):
        raise ResolveError(f"Project '{name}' already exists")
    if project_manager(resolve).CreateProject(name) is None:
        raise ResolveError(f"CreateProject('{name}') failed")
    project_manager(resolve).SaveProject()  # a new project is not listed until saved
    return {"project": name}


def save_project(resolve):
    if not project_manager(resolve).SaveProject():
        raise ResolveError("SaveProject failed")
    return {"saved": current_project(resolve).GetName()}


def get_setting(resolve, key):
    return {key: current_project(resolve).GetSetting(key)}


def set_setting(resolve, key, value):
    if not current_project(resolve).SetSetting(key, value):
        raise ResolveError(f"SetSetting({key}={value}) rejected")
    return {key: current_project(resolve).GetSetting(key)}
