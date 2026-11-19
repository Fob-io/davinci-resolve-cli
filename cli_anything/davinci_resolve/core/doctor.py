"""Environment diagnosis: is the scripting bridge usable, and are the optional tools there."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, connect, script_api_dir

_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
_WHISPER_MODEL = os.path.expanduser("~/.cache/whisper/large-v3-turbo.pt")
_SUBS_PYTHON = os.path.expanduser("~/venv-subs/bin/python")


def _check(name, ok, detail, hint=None, required=True):
    return {"check": name, "ok": bool(ok), "required": required, "detail": detail, **({"hint": hint} if hint and not ok else {})}


def _stable_ts():
    if importlib.util.find_spec("stable_whisper"):
        return sys.executable
    if os.path.exists(_SUBS_PYTHON):
        probe = subprocess.run([_SUBS_PYTHON, "-c", "import stable_whisper"], capture_output=True)
        if probe.returncode == 0:
            return _SUBS_PYTHON
    return None


def _probe(timeout=20):
    """Ask Resolve for its status in a child process: a hung Resolve blocks the caller, so it must be killable."""
    try:
        out = subprocess.run([sys.executable, "-m", "cli_anything.davinci_resolve", "--json", "--call-timeout", "0", "status"],
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode == 0:
        return ""
    try:
        err = json.loads(out.stdout)
        return f"{err['error']}" + (f" ({err['hint']})" if err.get("hint") else "")
    except (ValueError, KeyError):
        return out.stderr.strip() or "status failed"


def _resolve_checks():
    problem = _probe()
    if problem is None:
        return [_check("resolve", False, "Resolve gave no answer within 20s (hung, busy or a dialog is open)",
                       "Close any dialog in Resolve; if it stays stuck, quit and relaunch it (kill -9 as last resort)")], False
    if problem:
        return [_check("resolve", False, problem, "Run: launch")], False
    try:
        resolve = connect()
    except ResolveError as e:
        return [_check("resolve", False, str(e), e.hint)], False
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    timeline = project.GetCurrentTimeline() if project else None
    info = f"{resolve.GetProductName()} {resolve.GetVersionString()}, page={resolve.GetCurrentPage()}, " \
           f"project={project.GetName() if project else None}, timeline={timeline.GetName() if timeline else None}"
    checks = [_check("resolve", True, info),
              _check("responsive", resolve.GetCurrentPage() is not None, "current page readable",
                     "page is None: a dialog is probably open in Resolve (e.g. 'project was modified, reload'); close it", required=False),
              _check("studio edition", "studio" in (resolve.GetProductName() or "").lower(),
                     "external scripting needs Studio", "Free edition cannot be driven from outside", required=False)]
    return checks, True


def report():
    api = script_api_dir()
    py = sys.version_info
    checks = [
        _check("python", py < (3, 14), f"{py.major}.{py.minor}.{py.micro} at {sys.executable}",
               "fusionscript cannot load on Python 3.14+: use 3.10-3.13"),
        _check("scripting api", os.path.isdir(os.path.join(api, "Modules")), api,
               "Reinstall Resolve or set RESOLVE_SCRIPT_API"),
        _check("fusionscript lib", os.path.exists(os.environ.get("RESOLVE_SCRIPT_LIB", _SCRIPT_LIB)),
               os.environ.get("RESOLVE_SCRIPT_LIB", _SCRIPT_LIB), "Reinstall Resolve or set RESOLVE_SCRIPT_LIB"),
    ]
    resolve_checks, reachable = _resolve_checks()
    checks += resolve_checks
    ffmpeg = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.path.exists("/opt/homebrew/bin/ffmpeg") else None)
    checks.append(_check("ffmpeg", ffmpeg, ffmpeg or "not found", "brew install ffmpeg (needed by subs)", required=False))
    stable = _stable_ts()
    checks.append(_check("stable-ts", stable, stable or "not installed", "python3 -m venv ~/venv-subs && ~/venv-subs/bin/pip install stable-ts (needed by subs)", required=False))
    checks.append(_check("whisper model", os.path.exists(_WHISPER_MODEL), _WHISPER_MODEL,
                         "downloaded on first use of subs transcribe", required=False))
    ok = all(c["ok"] for c in checks if c["required"])
    return {"ok": ok, "reachable": reachable, "checks": checks}
