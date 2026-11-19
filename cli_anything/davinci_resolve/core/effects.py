"""Effects on a timeline item (Fusion / OFX / audio) and the catalogue of available effects.

An effect is addressed by category and its 1-based index among that category's effects on the item.
Every write is read back: the API answers True for some calls it then ignores.
"""
from cli_anything.davinci_resolve.core.timeline import find_item
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project

CATEGORIES = ("fusion", "ofx", "audio")


def _item(resolve, timeline, track, item):
    return find_item(current_project(resolve), timeline, track, item)


def _category(category):
    if category not in CATEGORIES:
        raise ResolveError(f"category must be one of {list(CATEGORIES)}, got '{category}'")
    return category


def _row(effect, category):
    info = effect.GetInfo() or {}
    return {"category": category, "index": effect.GetIndex(), "name": info.get("name"), "id": info.get("id"),
            "enabled": effect.GetIsEnabled()}


def _pick(clip, category, index):
    effects = clip.GetEffects(_category(category)) or []
    if not 1 <= index <= len(effects):
        raise ResolveError(f"Item has {len(effects)} {category} effects, no effect {index}")
    return effects, effects[index - 1]


def search(resolve, text, media_type=None, category=None, limit=50):
    """Effects whose id or name contains `text` (case-insensitive), from Resolve.GetEffectsList."""
    found = resolve.GetEffectsList(media_type) if media_type else resolve.GetEffectsList()
    needle = text.lower()
    rows = [e for e in found or [] if (not category or e.get("category") == category)
            and (needle in (e.get("id") or "").lower() or needle in (e.get("name") or "").lower())]
    return {"total": len(found or []), "matches": len(rows), "effects": rows[:limit]}


def list_effects(resolve, timeline, track, item, category=None):
    clip = _item(resolve, timeline, track, item)
    cats = (_category(category),) if category else CATEGORIES
    return [_row(e, c) for c in cats for e in clip.GetEffects(c) or []]


def add(resolve, timeline, track, item, category, effect_id):
    clip = _item(resolve, timeline, track, item)
    before = len(clip.GetEffects(_category(category)) or [])
    made = clip.AddEffect(category, effect_id)
    if made is None or len(clip.GetEffects(category) or []) != before + 1:
        raise ResolveError(f"AddEffect('{category}', '{effect_id}') failed. Find the id with: effects search TEXT",
                           code="effect_failed")
    return _row(made, category)


def delete(resolve, timeline, track, item, category, index, yes=False):
    if not yes:
        raise ResolveError("item effects delete removes the effect. Re-run with --yes to confirm")
    clip = _item(resolve, timeline, track, item)
    effects, target = _pick(clip, category, index)
    if not clip.DeleteEffects([target]) or len(clip.GetEffects(category) or []) != len(effects) - 1:
        raise ResolveError(f"DeleteEffects failed for {category} effect {index}")
    return {"deleted": index, "remaining": len(effects) - 1}


def enable(resolve, timeline, track, item, category, index, enabled):
    clip = _item(resolve, timeline, track, item)
    _, target = _pick(clip, category, index)
    target.SetIsEnabled(enabled)
    if target.GetIsEnabled() != enabled:
        raise ResolveError(f"SetIsEnabled({enabled}) was ignored")
    return _row(target, category)


def move(resolve, timeline, track, item, category, index, to):
    clip = _item(resolve, timeline, track, item)
    effects, target = _pick(clip, category, index)
    if not 1 <= to <= len(effects):
        raise ResolveError(f"Target index {to} is outside 1..{len(effects)}")
    target.SetIndex(to)
    if target.GetIndex() != to:
        raise ResolveError(f"SetIndex({to}) was ignored: effect is at {target.GetIndex()}")
    return _row(target, category)
