"""Render/burn-in/layout/project-settings presets and project settings."""
import os

from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project


def _ok(result, what):
    if not result:
        raise ResolveError(f"{what} failed")


# ---- render presets ------------------------------------------------------------
def render_presets(resolve):
    return current_project(resolve).GetRenderPresetList() or []


def render_preset_load(resolve, name):
    _ok(current_project(resolve).LoadRenderPreset(name), f"LoadRenderPreset('{name}')")
    return {"loaded": name}


def render_preset_save(resolve, name, update=False):
    project = current_project(resolve)
    _ok(project.UpdateRenderPreset(name) if update else project.SaveAsNewRenderPreset(name),
        f"{'Update' if update else 'SaveAsNew'}RenderPreset('{name}')")
    return render_presets(resolve)


def render_preset_delete(resolve, name, yes=False):
    if not yes:
        raise ResolveError("render-preset delete removes the preset. Re-run with --yes to confirm")
    _ok(current_project(resolve).DeleteRenderPreset(name), f"DeleteRenderPreset('{name}')")
    return render_presets(resolve)


def render_preset_import(resolve, path):
    if not os.path.isfile(path):
        raise ResolveError(f"Preset file not found: {path}")
    _ok(resolve.ImportRenderPreset(os.path.abspath(path)), "ImportRenderPreset")
    return render_presets(resolve)


def render_preset_export(resolve, name, path):
    _ok(resolve.ExportRenderPreset(name, os.path.abspath(path)), f"ExportRenderPreset('{name}')")
    return {"exported": os.path.abspath(path)}


def render_formats(resolve, fmt=None):
    project = current_project(resolve)
    if fmt:
        return project.GetRenderCodecs(fmt) or {}
    return project.GetRenderFormats() or {}


def burnin_presets(resolve):
    return {"presets": resolve.GetBurnInPresetList() or []}


def burnin_load(resolve, name):
    _ok(current_project(resolve).LoadBurnInPreset(name), f"LoadBurnInPreset('{name}')")
    return {"loaded": name}


# ---- project settings ------------------------------------------------------------
def _same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def settings_get(resolve, keys=()):
    settings = current_project(resolve).GetSettings() or {}
    return {k: settings.get(k) for k in keys} if keys else settings


def settings_set(resolve, values):
    """values: {setting: value}. Read back, because SetSetting can return True and ignore a value."""
    project = current_project(resolve)
    for key, value in values.items():
        project.SetSetting(key, value)
    now = project.GetSettings() or {}
    bad = {k: now.get(k) for k, v in values.items() if not _same(now.get(k), v)}
    if bad:
        raise ResolveError(f"Settings not applied (unknown key or invalid value): {bad}")
    return {k: now[k] for k in values}


def settings_presets(resolve):
    return current_project(resolve).GetProjectSettingsPresetList() or []


def settings_preset_apply(resolve, name):
    _ok(current_project(resolve).SetProjectSettingsPreset(name), f"SetProjectSettingsPreset('{name}')")
    return {"applied": name}


def settings_preset_save(resolve, name, update=False):
    project = current_project(resolve)
    _ok(project.UpdateProjectSettingsPreset(name) if update else project.SaveCurrentProjectSettingsAsNewPreset(name),
        f"save project settings preset '{name}'")
    return settings_presets(resolve)


def settings_preset_delete(resolve, name, yes=False):
    if not yes:
        raise ResolveError("settings-preset delete removes the preset. Re-run with --yes to confirm")
    _ok(current_project(resolve).DeleteProjectSettingsPreset(name), f"DeleteProjectSettingsPreset('{name}')")
    return settings_presets(resolve)


# ---- layout presets ------------------------------------------------------------
def layouts(resolve):
    return resolve.GetLayoutPresetList() or []


def layout_do(resolve, action, name, path=None, yes=False):
    if action == "delete" and not yes:
        raise ResolveError("layout delete removes the preset. Re-run with --yes to confirm")
    fn = {"load": lambda: resolve.LoadLayoutPreset(name), "save": lambda: resolve.SaveLayoutPreset(name),
          "update": lambda: resolve.UpdateLayoutPreset(name), "delete": lambda: resolve.DeleteLayoutPreset(name),
          "export": lambda: resolve.ExportLayoutPreset(name, os.path.abspath(path)),
          "import": lambda: resolve.ImportLayoutPreset(os.path.abspath(path), name)}[action]
    if action in ("export", "import") and not path:
        raise ResolveError(f"layout {action} needs --path")
    _ok(fn(), f"layout {action} '{name}'")
    return layouts(resolve)
