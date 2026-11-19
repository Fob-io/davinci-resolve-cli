# TEST.md — cli-anything-davinci-resolve

## Plan
- `test_core.py` (50 unit; project/media/timeline/render guards, `api` catalogue and receivers, bulk clip ops, playhead restore, stills (selection, mkdir, retry, --yes), Fusion (read-back), node graph), playhead restore): fake Resolve object. Project open/new guards (unknown, duplicate, no project, save-only-if-saved), media (missing file, unknown bin), timeline (bad marker color, failed AddMarker, bad track, unknown export format), render (missing dir, unknown preset, empty job id).
- `test_full_e2e.py` (18 E2E on real Resolve; incl. `api`, bulk clip ops/playhead, and a stills + Fusion + node-graph flow on a scratch project): CLI subprocess via `_resolve_cli`.
  - Workflow "ingest to delivery": new scratch project -> setting round-trip -> nested bins -> timeline -> marker add/list/delete -> .drt export -> ffmpeg test clip -> import -> append -> render queue -> real render -> verify file exists, size, ISO-media magic.
  - Scratch project `cliany_test_*` is created and deleted by the fixture.

## Results
`CLI_ANYTHING_FORCE_INSTALLED=1 pytest -v -s` : **74 passed** (100%).
Artifacts: `.drt` 17,155 bytes; render `rendered.mov` 410,253 bytes (H.264 Master preset writes .mov).

## Findings from E2E
- `Timeline.SetCurrentTimecode` returns True for impossible values (99:99:99:99 -> 100:40:42:09): CLI reads back and restores.
- Stub omits legacy methods that still work (`Project.GetSetting`): `api call` allows them, flags `in_stub: false`.

- `Fusion SetInput` returns None and ignores bad names silently: read-back added. `GrabStill` works from the Edit page; `ExportStills` writes image + .drx and needs the folder to exist (CLI creates it).

## Coverage notes / gaps
- Not covered: Fusion tool creation (fixture adds it directly), keyframes, `ApplyGradeFromDRX` (reachable via `api call Graph.ApplyGradeFromDRX`), timeline item properties, color, Fusion, audio, cloud/database projects, Python-3.14 error path, REPL loop.
- Fixes found by E2E: `project new` must save (else not listed); render needs non-empty timeline (added `timeline append`).
