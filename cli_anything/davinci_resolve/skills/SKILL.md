---
name: "cli-anything-davinci-resolve"
description: "Drive DaVinci Resolve Studio from the shell: projects, media pool bins/import, timelines, markers, export, render queue. JSON output for agents."
---

# cli-anything-davinci-resolve

Thin CLI over the Resolve scripting API. Resolve holds all state; the CLI is stateless.

## Prerequisites
- DaVinci Resolve Studio running, Preferences > System > General > External scripting = Local.
- Python 3.10-3.13 venv (`pip install git+https://github.com/Fob-io/davinci-resolve-cli.git`). Python 3.14 cannot load Resolve's library.

## Usage
`cli-anything-davinci-resolve [--json] <group> <command> [args]` — `--json` goes BEFORE the group.
No subcommand = REPL. Errors: exit 1, `{"error": "..."}` with `--json`.

## Command groups
- `status` — version, page, project, timeline
- `project` — list, info, open, new, save, get-setting, set-setting
- `media` — tree, clips [--bin], new-bin [--parent], import PATHS [--bin]; bulk on a clip or bin: props, set-prop, metadata, set-metadata, set-color, transcript
- `timeline` — list, new, set, info, items [--type --track --full], playhead [TIMECODE], append CLIP, export PATH [--format drt|fcpxml_1_10|edl|aaf...]
- `marker` — list, add FRAME [--color --name --note --duration], delete FRAME
- `render` — presets, add DIR [--preset --name], jobs, start [IDS], delete ID, busy
- `stills` — list, grab [--label --at], export DIR [--prefix --format --label|--index|--all], delete [--yes]
- `color` — nodes --item N [--track --timeline --layer]
- `fusion` — comps, tools, inputs TOOL, get-input TOOL INPUT, set-input TOOL INPUT VALUE (all take --item N [--track video:1 --timeline --comp])
- `api` — the whole scripting API (~500 methods): `search REGEX`, `describe Class.Method` (both offline, read from the stub shipped with Resolve), `call Class.Method [--args JSON] [selectors] [--yes]`

## Example
```bash
cli-anything-davinci-resolve --json project new "Episode 5"
cli-anything-davinci-resolve media import ~/clips/a.mov --bin Footage
cli-anything-davinci-resolve timeline new "Cut v1" && cli-anything-davinci-resolve timeline set "Cut v1"
cli-anything-davinci-resolve timeline append a.mov
cli-anything-davinci-resolve render add ~/Exports --preset "H.264 Master" --name ep5
cli-anything-davinci-resolve render start && cli-anything-davinci-resolve render busy
```

## Generic API (`api`)
Use the dedicated commands above when one exists; use `api` for everything else.
```bash
cli-anything-davinci-resolve api search "^Timeline\.Export"
cli-anything-davinci-resolve api describe Timeline.AddMarker
cli-anything-davinci-resolve --json api call Timeline.AddMarker --args '[100, "Red", "fix", "", 1]'
cli-anything-davinci-resolve --json api call TimelineItem.GetName --track video:1 --item 1
cli-anything-davinci-resolve api call MediaPool.AppendToTimeline --args '[[{"$clip": "a.mov"}]]'
cli-anything-davinci-resolve api call Timeline.Export --args '["/tmp/t.drt", {"$const": "EXPORT_DRT"}]'
```
- Receiver selectors: `--timeline NAME` (Timeline, TimelineItem), `--track video:1 --item N` (TimelineItem, N 1-based), `--clip NAME` (MediaPoolItem), `--bin PATH` (Folder). Resolve, ProjectManager, Project, MediaPool, MediaStorage, Gallery need none.
- Argument refs inside `--args`: `{"$clip": name}`, `{"$timeline": name}`, `{"$bin": path}`, `{"$const": "EXPORT_DRT"}` (any Resolve constant).
- Results that are Resolve objects come back as `{"_object": name}`.
- Methods starting Delete/Remove/Close/Quit/Clear need `--yes`.
- The stub omits some legacy methods the app still accepts (e.g. `Project.GetSetting`): `call` allows them and adds `"in_stub": false`. Receivers also exist for `Graph` (`--track --item --layer`) and `GalleryStillAlbum` (`--album`). Not addressable: Effect, ColorGroup, Photo*, FusionComp/tools (use `fusion`).

## Bulk clip commands
Select with `--clip NAME` (whole pool) or `--bin PATH` (`/` = root, `--recursive` for sub-bins); exactly one.
```bash
cli-anything-davinci-resolve --json media set-color Blue --bin "Footage/Day1"
cli-anything-davinci-resolve --json media set-metadata Comments "scene 7" --bin / --recursive
cli-anything-davinci-resolve --json media props --key "Clip Name" --key FPS --bin Footage
```
- Every `set-*` skips a clip that already has a different non-empty value, unless `--overwrite`. Output: `counts` {set, unchanged, skipped, failed} and a per-clip `results` list.
- Every write is read back; `failed` means Resolve said OK but kept the old value.
- `timeline playhead 01:00:10:00` verifies the result: Resolve accepts impossible timecodes (it turned 99:99:99:99 into 100:40:42:09), so the CLI restores the old position and errors.

## Stills, Fusion, color
```bash
cli-anything-davinci-resolve stills grab --at 01:00:10:00 --label v1
cli-anything-davinci-resolve stills export ~/stills --prefix v1 --label v1     # image + .drx grade, folder is created
cli-anything-davinci-resolve stills delete --label v1 --yes
cli-anything-davinci-resolve color nodes --item 3
# copy a grade by matching media file name; skips already graded (>1 node); --dry-run first
cli-anything-davinci-resolve color copy-grade --src-track video:3 --src-from 44009 --dst-track video:1 --clip-color Lime --dry-run
cli-anything-davinci-resolve color apply-drx grade.drx --track video:1 --item 3
cli-anything-davinci-resolve color set-cdl --track video:1 --item 3 --slope "1.1 1 0.9"
cli-anything-davinci-resolve fusion set-input Transform1 Size 1.5 --item 2
```
- `stills grab` retries (GrabStill fails silently right after a playhead move) and labels the still it got back, not an album position. `stills delete` needs `--yes`.
- Fusion `SetInput` returns None even when ignored: `fusion set-input` reads the value back and errors if it did not change. Values are JSON (`1.5`, `true`, `"text"`). Creating a comp: `api call TimelineItem.AddFusionComp --track video:1 --item N`. Adding tools to a comp is not a command.

## Agent guidance
- Inspect before mutating: `status`, `project list`, `timeline list`, `media tree`.
- `project open` saves the current saved project first (an unsaved-changes dialog would block scripts).
- `project new` also saves, otherwise the project is not listed.
- Marker frames are timeline-relative. `render add` needs a non-empty timeline.
- Preset "H.264 Master" writes `.mov`.

## Subtitles (`subs`) and timeline helpers

Covers every Resolve call the better-subs-davinci skill makes, so no `run_script` is needed.

| Need | Command |
|---|---|
| Open Resolve and wait | `launch` |
| fps, start frame, In/Out, subtitle track count | `timeline info` |
| Set/read/clear In/Out (frames relative to start) | `timeline inout [IN OUT] [--clear]` |
| Tracks / add subtitle track / enable | `timeline tracks`, `timeline add-track subtitle`, `timeline track-enable audio 2 off` |
| Subtitles now on the track | `subs list` |
| Voice WAV, 16 kHz mono | `subs export-voice [--track 1] [--out F] [--method auto\|files\|render]` (auto = ffmpeg on source files; multicam/compound clips have no file path, so it renders the track from Resolve, other audio tracks muted meanwhile) |
| Word timings (stable-ts, never installs) | `subs transcribe WAV` |
| Resolve auto-captions (fallback) | `subs generate`, then `subs generate-status` |
| Rebuild whole track from one SRT | `subs apply PLAN.json` |
| Sub-vs-voice offsets (report only) | `subs check-voice WAV [--threshold 4]` |
| Remove imported bsd-*.srt | `subs cleanup --yes` |
| Fuchsia review markers | `marker add FRAME --color Fuchsia --shift`, `marker delete --name "Controlla sub"` |

`subs apply` plan: `{"timeline","maxc","ranges":[[start,end,[[text,n_words],...]],...],"ext_words":[[w,a,e],...]|null,"replace":bool,"confirmed":bool}` (absolute frames). It refuses on wrong timeline, overlapping blocks, word-count mismatch, subs under 8 frames, or existing subs without `confirmed`; it backs up the old track to a temp SRT and rolls back on failure.

## More groups (all destructive commands need `--yes`)
- `fusion`: `add-comp [--file X.comp]`, `export-comp`, `rename-comp`, `load-comp`, `delete-comp`, `add-tool ID`, `delete-tool`, `connect SRC DST INPUT`, `keyframe TOOL INPUT FRAME VALUE`.
- `item` (timeline item): `props [--set K=V]`, `speed`, `enable [--off]`, `fades`, `color`, `flags`, `export-frame`, `takes`/`take-*`, `ai magic-mask|stabilize|smart-reframe`, `markers`/`marker-add`/`marker-delete`.
- `media markers|marker-add|marker-delete CLIP`, `media analyze transcribe|intellisearch|slate|classify-audio ...` (async; poll `analysis status KIND`).
- `timeline snapshot` (next free "NAME vN"), `versions`, `duplicate`, `rename`, `delete`, `insert title|generator ...`, `delete-track`, `track-lock`, `track-name`, `delete-clips`.
- `color`: `versions`, `version-add|load|rename|delete`, `node-set` (label/color/enable/LUT), `reset`, `export-lut`, `groups`, `group-add|assign|remove|delete|clips`, plus `copy-grade`, `apply-drx`, `set-cdl`.
- `render presets|preset-load|preset-save|preset-delete|preset-import|preset-export|formats|burnin-presets|burnin-load`; `settings get|set K=V|presets|preset-*`; `layout list|load|save|update|delete|export|import`.
- `project folders|cd|mkdir|rmdir|delete|rename|export|import|archive|databases|use-database`; `storage volumes|ls|import|reveal`; `stills albums|album-create|album-use|import`; `analysis detect-cuts|status|refresh-luts`.

## Agent helpers

- `doctor`: checks Python, scripting API, Resolve, ffmpeg, stable-ts. Exit 0 ok, 1 required check failed, 2 Resolve unreachable.
- `schema list` / `schema show <command...>`: parameters, types, defaults and `mutating` flag of every command.
- `--dry-run` on every command that changes something: prints the command and its parameters, sends nothing to Resolve (commands with their own check, like `color copy-grade`, keep theirs).
- Errors with `--json`: `{"error", "error_code", "hint"}`. Exit 1 = bad request or failed call, 2 = Resolve/API unreachable. Codes: `resolve_unavailable`, `api_not_found`, `python_unsupported`, `no_project`, `no_timeline`, `not_found`, `render_failed`, `timeout`, `error`.
- `render wait JOB_ID [--interval S] [--timeout S]`: blocks until the job completes; progress on stderr, exit 1 if it fails.
- `timeline insert-title NAME [--kind fusion-title|title|generator|fusion-generator] [--track N] [--at F] [--duration D] [--text T] [--mode compound|ripple]`: default `compound` builds the item in a scratch timeline, wraps it in a compound clip and appends it at an exact frame: never ripples (subs and other tracks untouched), refuses an occupied range (`track_occupied`). `--mode ripple` is the native insert: Resolve picks the destination itself and a ripple insert can shift or trim OTHER tracks even when locked (observed), so it is verified and rolled back, and refused mid-clip (`insert_would_split`). Pool compounds/Fusion clips (e.g. a "SfondoGiallo" template) go in with `timeline append NAME --track video:N --at F --frames D`; `media clips` now shows each clip's `type`.
- Hang protection: every command ends with `error_code: "timeout"` (exit 2) if Resolve does not answer in `--call-timeout` seconds (default 120, `status` 20, long commands like subs/project open/import get a multiple, `0` = off, env `RESOLVE_CLI_TIMEOUT`). `doctor` probes Resolve in a killable child process and reports it as hung instead of hanging itself.
- `timeline dump [--timeline NAME] [--all] [--no-text] [--no-markers]`: tracks, items and markers of one or all timelines in one call (works without switching timeline). Items have `kind`: clip, still, compound, fusion_clip, nested_timeline, title (with `text`), generator, adjustment, transition, subtitle. `timeline items --text` adds title text. Transition/adjustment detection is heuristic (no media + overlap/name), not verified on a real transition.
- `project open "Folder/Sub/Name"` opens by path from the root (no dependence on `project cd`); LoadProject is retried 4 times (3 s apart). `timeline list` now has start/end frame, duration_frames, fps.

## Editing helpers (added for the Ermanno editing session)

- `timeline append CLIP [--track video:3|audio:2] [--start F] [--end F | --frames N] [--at REL_FRAME] [--kind video|audio|both] [--timeline NAME]`: creates missing tracks, exact still length (mark in/out), exact timeline length even when the clip fps differs (closest wins, `exact` in the result), switches to a non-current timeline and back. Frames: `--start/--end` are source frames, `--frames/--at` timeline frames (`--at` relative to timeline start).
- `media import FILES --bin A/B --create`: creates the nested bins first. `media replace CLIP PATH`: ReplaceClip, timeline items keep working.
- `item props --set AudioVolume=-18 --set AudioPitchSemiTones=-3`: volume in dB and pitch in semitones.
- `timeline delete-clips --track video:3 --item 1 --item 2 --yes` (several at once, `--ripple` optional); `timeline fusion-clip --track video:1 --item 1 --item 2`.
- Fusion: input names accept both the scripting id (`StyledText`) and the display name (`Styled Text`); `fusion inputs` lists `{id, name}`. `fusion dump` (inputs that differ from default + connections), `fusion save-tool TOOL PATH` (.setting macro), `fusion bbox TOOL` (real content box of a tool's output, e.g. Text+ width in pixels).
- `doctor` flags `page=None`: usually a dialog is open in Resolve (e.g. "project was modified, reload"); appends then return nothing until it is closed.
- Tests run in the reusable project "00 - Personal / cli_test" (emptied at the start, kept afterwards); the previously open project and folder are reopened at the end.

## Effects, tracks, titles

- `effects search TEXT [--category ofx|fusion|audio] [--media-type video|photo|audio]` searches the catalogue (id or name); `item effects list|add|delete|enable|move --category ofx ... --track video:1 --item N` (index is per category; `add` takes the id from `effects search`; `delete` needs `--yes`).
- `timeline track-enable` re-enables a track on any page (Resolve ignores enabling unless the Edit page is open; the CLI switches to Edit for the write and goes back). `subs export-voice --method render` now puts every audio track back as it was, after leaving Deliver.
- `timeline insert-title NAME --track N --at FRAME --duration F`: best effort. Resolve chooses the destination track of a title insert (V1 on a fresh timeline, and it fails if that track is locked), so the title either lands on the requested track or the command changes nothing (`insert_failed`) / removes it again (`insert_misplaced`). Use the In/Out + playhead method yourself when the title must go to a specific track.
- Resolve 21.2: `AppendToTimeline` with a `recordFrame` returns dead handles (all getters None) though the clip is placed; `timeline append` finds the new items by diffing the timeline.
- Nests cannot be decomposed through the API: do it by hand in Resolve.
