"""Deliver page: render presets and the render queue."""
import os
import time

from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def presets(resolve):
    return list(current_project(resolve).GetRenderPresetList() or [])


def add_job(resolve, target_dir, preset=None, name=None):
    project = current_project(resolve)
    if not os.path.isdir(target_dir):
        raise ResolveError(f"Target directory not found: {target_dir}")
    if preset and preset not in presets(resolve):
        raise ResolveError(f"Preset '{preset}' not found. Available: {presets(resolve)}")
    if preset:
        project.LoadRenderPreset(preset)
    settings = {"TargetDir": os.path.abspath(target_dir)}
    if name:
        settings["CustomName"] = name
    if not project.SetRenderSettings(settings):
        raise ResolveError(f"SetRenderSettings rejected {settings}")
    page = resolve.GetCurrentPage()
    if page != "deliver":  # AddRenderJob returns None unless the Deliver page is loaded
        resolve.OpenPage("deliver")
    try:
        job_id = None
        for _ in range(6):  # the Deliver page loads asynchronously; AddRenderJob returns None until it is ready
            job_id = project.AddRenderJob()
            if job_id:
                break
            time.sleep(1)
    finally:
        if page != "deliver":
            resolve.OpenPage(page)
    if not job_id:
        raise ResolveError("AddRenderJob failed (empty timeline or invalid settings)")
    return {"job_id": job_id}


def jobs(resolve):
    project = current_project(resolve)
    return [
        {**j, "status": project.GetRenderJobStatus(j["JobId"])}
        for j in project.GetRenderJobList() or []
    ]


def start(resolve, job_ids=None):
    project = current_project(resolve)
    ok = project.StartRendering(job_ids) if job_ids else project.StartRendering()
    if not ok:
        raise ResolveError("StartRendering failed (empty queue or render already running)")
    return {"started": job_ids or "all"}


def delete_job(resolve, job_id):
    if not current_project(resolve).DeleteRenderJob(job_id):
        raise ResolveError(f"DeleteRenderJob('{job_id}') failed")
    return {"deleted": job_id}


def rendering_in_progress(resolve):
    return {"rendering": bool(current_project(resolve).IsRenderingInProgress())}


_FINAL = ("Complete", "Failed", "Cancelled")


def wait(resolve, job_id, interval=1.0, timeout=None, progress=None):
    """Block until the job reaches a final status; `progress(status_dict)` is called on every poll."""
    project = current_project(resolve)
    if not any(j["JobId"] == job_id for j in project.GetRenderJobList() or []):
        raise ResolveError(f"Render job '{job_id}' not found. Use: render jobs", code="not_found")
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        status = project.GetRenderJobStatus(job_id) or {}
        if progress:
            progress(status)
        if status.get("JobStatus") in _FINAL:
            if status["JobStatus"] != "Complete":
                raise ResolveError(f"Render job '{job_id}' ended as {status['JobStatus']}: {status.get('Error', '')}".rstrip(": "),
                                   code="render_failed")
            return {"job_id": job_id, **status}
        if deadline is not None and time.monotonic() >= deadline:
            raise ResolveError(f"Timed out after {timeout}s; job is {status.get('JobStatus')} at "
                               f"{status.get('CompletionPercentage')}%", code="timeout")
        time.sleep(interval)
