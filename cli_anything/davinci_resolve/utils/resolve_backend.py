"""Backend: connects to the real, running DaVinci Resolve Studio via its scripting API."""
import os
import sys

_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


class ResolveError(RuntimeError):
    """Raised for any failure the agent should read and act on.

    `code` is a stable machine-readable label, `hint` the next step to try, `exit_code` the process
    exit status (1 = bad request or failed call, 2 = Resolve or its scripting API is unreachable).
    """

    def __init__(self, message, *, code="error", hint=None, exit_code=1):
        super().__init__(message)
        self.code, self.hint, self.exit_code = code, hint, exit_code


def script_api_dir():
    """Folder with Resolve's scripting modules and the DaVinciResolveScript.pyi API stub."""
    return os.environ.get("RESOLVE_SCRIPT_API", _SCRIPT_API)


def connect():
    """Return the live Resolve app object, or raise ResolveError with the fix."""
    if sys.version_info >= (3, 14):
        raise ResolveError(
            f"Python {sys.version_info.major}.{sys.version_info.minor} cannot load Resolve's "
            "fusionscript library. Install the CLI in a Python 3.10-3.13 environment.",
            code="python_unsupported", exit_code=2,
        )
    os.environ.setdefault("RESOLVE_SCRIPT_API", _SCRIPT_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", _SCRIPT_LIB)
    modules = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if modules not in sys.path:
        sys.path.append(modules)
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        raise ResolveError(f"DaVinciResolveScript not found in {modules}: {e}",
                           code="api_not_found", hint="Run: doctor", exit_code=2) from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError(
            "Cannot reach DaVinci Resolve Studio. Start it and enable "
            "Preferences > System > General > External scripting using: Local.",
            code="resolve_unavailable", hint="Run: launch (then: doctor)", exit_code=2,
        )
    return resolve


def project_manager(resolve):
    return resolve.GetProjectManager()


def current_project(resolve):
    project = project_manager(resolve).GetCurrentProject()
    if project is None:
        raise ResolveError("No project is open. Use: project open <name>", code="no_project")
    return project


def current_timeline(resolve):
    timeline = current_project(resolve).GetCurrentTimeline()
    if timeline is None:
        raise ResolveError("No current timeline. Use: timeline set <name>", code="no_timeline")
    return timeline
