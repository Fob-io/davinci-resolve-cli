"""E2E tests: the installed CLI against the real, running DaVinci Resolve Studio.

Uses (and reuses) the project "00 - Personal / cli_test", emptied at start. Never touches
other projects. Resolve not running = failure, not skip.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest


def _resolve_cli(name):
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    sibling = os.path.join(os.path.dirname(sys.executable), name)  # venv not activated
    if os.path.exists(sibling):
        print(f"[_resolve_cli] Using installed command: {sibling}")
        return [sibling]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    return [sys.executable, "-m", "cli_anything.davinci_resolve"]


CLI = _resolve_cli("cli-anything-davinci-resolve")


def run(*args, check=True):
    result = subprocess.run(CLI + list(args), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{args} -> {result.returncode}: {result.stdout} {result.stderr}")
    return result


def jrun(*args):
    return json.loads(run("--json", *args).stdout)


TEST_FOLDER, TEST_PROJECT = "00 - Personal", "cli_test"


def _empty_project(project):
    """Fresh state for a reused test project: no timelines, no clips, no bins."""
    pool = project.GetMediaPool()
    timelines = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    if timelines:
        pool.DeleteTimelines(timelines)
    root = pool.GetRootFolder()
    pool.DeleteClips(list(root.GetClipList() or []))
    for sub in root.GetSubFolderList():
        pool.DeleteFolders([sub])


@pytest.fixture(scope="module")
def scratch():
    """Reusable project '00 - Personal / cli_test': emptied at start, kept afterwards.

    The project and project-manager folder open before the tests are restored at the end.
    """
    from cli_anything.davinci_resolve.utils.resolve_backend import connect

    pm = connect().GetProjectManager()
    prev = pm.GetCurrentProject()
    prev_name, prev_folder = (prev.GetName() if prev else None), pm.GetCurrentFolder()
    if prev is not None and prev_name != "Untitled Project":
        pm.SaveProject()
    pm.GotoRootFolder()
    assert pm.OpenFolder(TEST_FOLDER), f"project folder '{TEST_FOLDER}' not found"
    project = pm.LoadProject(TEST_PROJECT) if TEST_PROJECT in (pm.GetProjectListInCurrentFolder() or []) else pm.CreateProject(TEST_PROJECT)
    assert project is not None
    _empty_project(project)
    yield TEST_PROJECT
    pm.SaveProject()
    pm.GotoRootFolder()
    if prev_folder:
        pm.OpenFolder(prev_folder)
    if prev_name and prev_name != "Untitled Project":
        pm.LoadProject(prev_name)


class TestStatus:
    def test_help(self):
        assert run("--help").returncode == 0

    def test_status_json(self):
        data = jrun("status")
        assert data["product"] == "DaVinci Resolve Studio"
        assert data["version"].startswith("21")


class TestWorkflow:
    def test_project_created_and_current(self, scratch):
        assert jrun("status")["project"] == scratch
        assert scratch in jrun("project", "list")

    def test_duplicate_project_fails(self, scratch):
        r = run("--json", "project", "new", scratch, check=False)
        assert r.returncode == 1 and "already exists" in json.loads(r.stdout)["error"]

    def test_setting_roundtrip(self, scratch):
        out = jrun("project", "set-setting", "timelineFrameRate", "30")
        assert float(out["timelineFrameRate"]) == 30.0

    def test_bins_and_timeline(self, scratch):
        jrun("media", "new-bin", "Footage")
        jrun("media", "new-bin", "Day1", "--parent", "Footage")
        tree = jrun("media", "tree")
        assert tree["bins"][0]["name"] == "Footage"
        assert tree["bins"][0]["bins"][0]["name"] == "Day1"
        jrun("timeline", "new", "Cut v1")
        jrun("timeline", "set", "Cut v1")
        assert any(t["name"] == "Cut v1" and t["current"] for t in jrun("timeline", "list"))
        assert jrun("timeline", "info")["video_tracks"] >= 1

    def test_markers(self, scratch):
        start = jrun("timeline", "info")["start_frame"]
        jrun("marker", "add", str(start + 24), "--color", "Red", "--name", "fix audio", "--note", "pop")
        markers = jrun("marker", "list")
        assert markers[0]["name"] == "fix audio" and markers[0]["color"] == "Red"
        jrun("marker", "delete", str(markers[0]["frame"]))
        assert jrun("marker", "list") == []

    def test_export_timeline_drt(self, scratch, tmp_path):
        out = tmp_path / "cut.drt"
        jrun("timeline", "export", str(out), "--format", "drt")
        assert out.exists() and out.stat().st_size > 0
        print(f"\n  DRT: {out} ({out.stat().st_size:,} bytes)")

    def test_import_append_render(self, scratch, tmp_path):
        src = tmp_path / "testclip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=30",
             "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", str(src)],
            check=True,
        )
        jrun("media", "import", str(src), "--bin", "Footage")
        assert [c["name"] for c in jrun("media", "clips", "--bin", "Footage")] == ["testclip.mp4"]
        jrun("timeline", "append", "testclip.mp4")
        assert len(jrun("timeline", "items")) == 1

        assert "H.264 Master" in jrun("render", "presets")
        out = tmp_path / "out"
        out.mkdir()
        job = jrun("render", "add", str(out), "--preset", "H.264 Master", "--name", "rendered")
        assert job["job_id"] in [j["JobId"] for j in jrun("render", "jobs")]
        jrun("render", "start", job["job_id"])
        done = jrun("render", "wait", job["job_id"], "--interval", "1", "--timeout", "180")
        assert done["JobStatus"] == "Complete"
        outputs = list(out.glob("rendered.*"))
        assert len(outputs) == 1, outputs
        rendered = outputs[0]
        assert rendered.exists() and rendered.stat().st_size > 1000
        assert rendered.read_bytes()[4:8] == b"ftyp"  # ISO base media (mov/mp4) magic
        print(f"\n  Render: {rendered} ({rendered.stat().st_size:,} bytes)")
        jrun("render", "delete", job["job_id"])


class TestApiPassthrough:
    """Generic `api` group against real Resolve, inside the scratch project."""

    def test_search_and_describe_offline(self):
        assert any("Timeline.AddMarker" in r for r in jrun("api", "search", "^Timeline.AddMarker"))
        assert jrun("api", "describe", "Project.GetSettings")["returns"]

    def test_call_marker_roundtrip(self, scratch):
        jrun("timeline", "new", "ApiCut")
        jrun("timeline", "set", "ApiCut")
        start = jrun("timeline", "info")["start_frame"]
        assert jrun("api", "call", "Timeline.AddMarker", "--args", json.dumps([start + 5, "Green", "via api", "", 1]))["result"] is True
        markers = jrun("api", "call", "Timeline.GetMarkers", "--timeline", "ApiCut")["result"]
        assert any(m["name"] == "via api" for m in markers.values())

    def test_call_with_refs_and_item_selector(self, scratch, tmp_path):
        src = tmp_path / "apiclip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x180:rate=30",
             "-pix_fmt", "yuv420p", str(src)],
            check=True,
        )
        jrun("media", "import", str(src))
        assert jrun("api", "call", "MediaPoolItem.GetName", "--clip", "apiclip.mp4")["result"] == "apiclip.mp4"
        jrun("api", "call", "MediaPool.AppendToTimeline", "--args", json.dumps([[{"$clip": "apiclip.mp4"}]]))
        item = jrun("api", "call", "TimelineItem.GetName", "--track", "video:1", "--item", "1")
        assert item["result"] == "apiclip.mp4"
        out = tmp_path / "api.drt"
        jrun("api", "call", "Timeline.Export", "--args", json.dumps([str(out), {"$const": "EXPORT_DRT"}]))
        assert out.exists() and out.stat().st_size > 0

    def test_destructive_blocked_without_yes(self, scratch):
        r = run("--json", "api", "call", "Project.DeleteRenderJob", "--args", '["nope"]', check=False)
        assert r.returncode == 1 and "--yes" in json.loads(r.stdout)["error"]

    def test_legacy_method_outside_stub(self, scratch):
        out = jrun("api", "call", "Project.GetSetting", "--args", '["timelineFrameRate"]')
        assert out["in_stub"] is False and float(out["result"]) > 0


class TestBulkClipOpsE2E:
    """Bulk clip commands, read-back verified, inside the scratch project."""

    def test_color_metadata_props_items_playhead(self, scratch, tmp_path):
        for n in ("bulk_a.mp4", "bulk_b.mp4"):
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30",
                 "-pix_fmt", "yuv420p", str(tmp_path / n)],
                check=True,
            )
        jrun("media", "new-bin", "Bulk")
        jrun("media", "import", str(tmp_path / "bulk_a.mp4"), str(tmp_path / "bulk_b.mp4"), "--bin", "Bulk")

        # one clip gets its own color first: the bulk run must not touch it
        assert jrun("media", "set-color", "Green", "--clip", "bulk_a.mp4")["counts"]["set"] == 1
        out = jrun("media", "set-color", "Blue", "--bin", "Bulk")
        status = {r["clip"]: r["status"] for r in out["results"]}
        assert status == {"bulk_a.mp4": "skipped", "bulk_b.mp4": "set"}
        assert jrun("media", "set-color", "Blue", "--bin", "Bulk", "--overwrite")["counts"]["set"] == 1

        meta = jrun("media", "set-metadata", "Comments", "scene 7", "--bin", "Bulk")
        assert meta["counts"] == {"set": 2, "unchanged": 0, "skipped": 0, "failed": 0}
        again = jrun("media", "set-metadata", "Comments", "other", "--bin", "Bulk")
        assert again["counts"]["skipped"] == 2
        read = jrun("media", "metadata", "--key", "Comments", "--clip", "bulk_b.mp4")
        assert read[0]["metadata"]["Comments"] == "scene 7"

        props = jrun("media", "props", "--key", "Clip Name", "--key", "FPS", "--bin", "Bulk")
        assert {p["props"]["Clip Name"] for p in props} == {"bulk_a.mp4", "bulk_b.mp4"}
        assert float(props[0]["props"]["FPS"]) == 30.0

        jrun("timeline", "new", "BulkCut")
        jrun("timeline", "set", "BulkCut")
        jrun("timeline", "append", "bulk_a.mp4")
        row = jrun("timeline", "items", "--full")[0]
        assert row["source"] == "bulk_a.mp4" and row["enabled"] is True and row["source_end"] >= row["source_start"]

        tl_start = jrun("timeline", "info")["start_timecode"]
        assert jrun("timeline", "playhead", tl_start)["timecode"] == tl_start

    def test_bad_playhead_reports_error(self, scratch):
        before = jrun("timeline", "playhead")["timecode"]
        r = run("--json", "timeline", "playhead", "99:99:99:99", check=False)
        assert r.returncode == 1 and "restored" in json.loads(r.stdout)["error"]
        assert jrun("timeline", "playhead")["timecode"] == before


class TestStillsFusionColorE2E:
    """Stills, Fusion and node graph on real Resolve, inside the scratch project."""

    def test_flow(self, scratch, tmp_path):
        from cli_anything.davinci_resolve.utils.resolve_backend import connect

        src = tmp_path / "fx.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
             "-pix_fmt", "yuv420p", str(src)],
            check=True,
        )
        jrun("media", "import", str(src))
        jrun("timeline", "new", "FxCut")
        jrun("timeline", "set", "FxCut")
        jrun("timeline", "append", "fx.mp4")

        # --- color nodes
        nodes = jrun("color", "nodes", "--item", "1")
        assert len(nodes) >= 1 and nodes[0]["index"] == 1

        # --- fusion: item without comp, then with one
        r = run("--json", "fusion", "tools", "--item", "1", check=False)
        assert r.returncode == 1 and "no Fusion comp" in json.loads(r.stdout)["error"]
        jrun("api", "call", "TimelineItem.AddFusionComp", "--track", "video:1", "--item", "1")
        assert jrun("fusion", "comps", "--item", "1")["comps"] == ["Composition 1"]
        assert {t["name"] for t in jrun("fusion", "tools", "--item", "1")} >= {"MediaIn1", "MediaOut1"}
        comp = connect().GetProjectManager().GetCurrentProject().GetCurrentTimeline().GetItemListInTrack("video", 1)[0].GetFusionCompByIndex(1)
        comp.AddTool("Transform", 0, 0)  # adding tools is not a CLI command: set up the fixture directly
        assert "Size" in {i["id"] for i in jrun("fusion", "inputs", "Transform1", "--item", "1")}
        assert jrun("fusion", "get-input", "Transform1", "Size", "--item", "1")["value"] == 1.0
        out = jrun("fusion", "set-input", "Transform1", "Size", "1.5", "--item", "1")
        assert out["old"] == 1.0 and out["value"] == 1.5
        bad = run("--json", "fusion", "set-input", "Transform1", "NoSuchInput", "1", "--item", "1", check=False)
        assert bad.returncode == 1 and "no input" in json.loads(bad.stdout)["error"]

        # --- stills: grab, label, list, export (image + drx), delete guarded by --yes
        assert jrun("stills", "grab", "--label", "e2e_still")["stills_in_album"] >= 1
        assert "e2e_still" in [s["label"] for s in jrun("stills", "list")]
        out_dir = tmp_path / "stills_out" / "nested"
        exp = jrun("stills", "export", str(out_dir), "--prefix", "pre", "--label", "e2e_still")
        assert any(f.endswith(".png") for f in exp["files"]) and any(f.endswith(".drx") for f in exp["files"])
        assert all((out_dir / f).stat().st_size > 0 for f in exp["files"])
        print(f"\n  Stills: {exp['files']} in {out_dir}")
        blocked = run("--json", "stills", "delete", "--label", "e2e_still", check=False)
        assert blocked.returncode == 1 and "--yes" in json.loads(blocked.stdout)["error"]
        assert "e2e_still" in [s["label"] for s in jrun("stills", "list")]
        assert jrun("stills", "delete", "--label", "e2e_still", "--yes")["deleted"] == 1
        assert "e2e_still" not in [s["label"] for s in jrun("stills", "list")]

        # --- api receivers for Graph and album
        assert jrun("api", "call", "Graph.GetNumNodes", "--track", "video:1", "--item", "1")["result"] >= 1
        assert isinstance(jrun("api", "call", "GalleryStillAlbum.GetStills")["result"], list)


class TestSubtitlesE2E:
    """Subtitle workflow on real Resolve, inside the scratch project."""

    def test_flow(self, scratch, tmp_path):
        src = tmp_path / "voice.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x180:rate=30",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-pix_fmt", "yuv420p", "-shortest", str(src)],
            check=True,
        )
        jrun("media", "import", str(src))
        jrun("timeline", "new", "SubsCut")
        jrun("timeline", "set", "SubsCut")
        jrun("timeline", "append", "voice.mp4")
        info = jrun("timeline", "info")
        start, fps = info["start_frame"], info["fps"]

        # --- tracks, In/Out, markers
        assert any(t["type"] == "audio" for t in jrun("timeline", "tracks"))
        assert jrun("timeline", "track-enable", "audio", "1", "off")["enabled"] is False
        jrun("timeline", "track-enable", "audio", "1", "on")
        jrun("timeline", "inout", str(start + 10), str(start + 50))
        assert jrun("timeline", "inout")["mark_in_out"]["video"]["in"] == start + 10
        jrun("timeline", "inout", "--clear")
        assert jrun("marker", "add", str(start + 5), "--color", "Fuchsia", "--name", "Controlla sub")["frame"] == start + 5
        assert jrun("marker", "add", str(start + 5), "--color", "Fuchsia", "--name", "Controlla sub", "--shift")["frame"] == start + 6
        assert jrun("marker", "delete", "--name", "Controlla sub")["deleted"] == [start + 5, start + 6]

        # --- voice export + check (no subs yet: nothing measured)
        wav = str(tmp_path / "voce.wav")
        out = jrun("subs", "export-voice", "--out", wav)
        assert os.path.getsize(wav) > 1000 and out["clips"] >= 1
        assert out["method"] == "files"
        assert jrun("subs", "check-voice", wav)["measured"] == 0
        rendered = str(tmp_path / "voce-render.wav")  # multicam/compound clips take this path
        out = jrun("subs", "export-voice", "--method", "render", "--out", rendered)
        assert out["method"] == "render" and os.path.getsize(rendered) > 1000
        assert jrun("timeline", "tracks")[0]["enabled"] and jrun("status")["page"] != "deliver"
        assert jrun("render", "jobs") == []

        # --- apply: first build, then guarded rebuild
        words = [[w, start + 10 + 12 * i, start + 20 + 12 * i] for i, w in enumerate("uno due tre quattro cinque sei".split())]
        plan = {
            "timeline": "SubsCut", "maxc": 20, "replace": True, "confirmed": False, "ext_words": words,
            "ranges": [[start, start + 90, [["uno due tre", 3], ["quattro cinque sei", 3]]]],
        }
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(plan))
        result = jrun("subs", "apply", str(path))
        assert result["ok"] and result["count"] == 2 and result["long_lines"] == []
        assert [s["text"] for s in jrun("subs", "list")] == ["uno due tre", "quattro cinque sei"]

        blocked = run("--json", "subs", "apply", str(path), check=False)
        assert blocked.returncode == 1 and "confirmed" in json.loads(blocked.stdout)["error"]
        plan["confirmed"] = True
        plan["ranges"][0][2] = [["uno due tre quattro", 4], ["cinque sei", 2]]
        path.write_text(json.dumps(plan))
        assert jrun("subs", "apply", str(path))["ok"]
        bad = dict(plan, ranges=[[start, start + 90, [["solo", 1]]]])
        path.write_text(json.dumps(bad))
        mismatch = run("--json", "subs", "apply", str(path), check=False)
        assert mismatch.returncode == 1 and "mismatch" in json.loads(mismatch.stdout)["error"]
        assert [s["text"] for s in jrun("subs", "list")] == ["uno due tre quattro", "cinque sei"]  # untouched

        # --- cleanup is guarded, then removes only bsd-*.srt
        assert run("--json", "subs", "cleanup", check=False).returncode == 1
        assert len(jrun("subs", "cleanup", "--yes")["deleted"]) >= 2


class TestExtendedCommandsE2E:
    """Fusion building, item ops, grade write/versions/groups, timeline versions, presets, library."""

    def test_flow(self, scratch, tmp_path):
        src = tmp_path / "ext.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
             "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p", str(src)],
            check=True,
        )
        jrun("media", "import", str(src))
        jrun("timeline", "new", "Ext")
        jrun("timeline", "set", "Ext")
        jrun("timeline", "append", "ext.mp4")
        jrun("timeline", "append", "ext.mp4")

        # --- fusion: build a chain with CLI only
        assert jrun("fusion", "add-comp", "--item", "1")["comps"] == ["Composition 1"]
        assert jrun("fusion", "add-tool", "Background", "--item", "1")["tool"]
        jrun("fusion", "add-tool", "Merge", "--item", "1")
        link = jrun("fusion", "connect", "Background1", "Merge1", "Background", "--item", "1")
        assert link["connected_from"] == "Background1"
        jrun("fusion", "add-tool", "Transform", "--item", "1")
        assert jrun("fusion", "keyframe", "Transform1", "Size", "0", "1.0", "--item", "1")["value"] == 1.0
        assert jrun("fusion", "keyframe", "Transform1", "Size", "10", "2.0", "--item", "1")["value"] == 2.0
        comp_file = tmp_path / "c.comp"
        jrun("fusion", "export-comp", str(comp_file), "--item", "1")
        assert comp_file.stat().st_size > 0
        jrun("fusion", "rename-comp", "Composition 1", "Renamed", "--item", "1")
        assert "Renamed" in jrun("fusion", "comps", "--item", "1")["comps"]
        blocked = run("--json", "fusion", "delete-comp", "Renamed", "--item", "1", check=False)
        assert blocked.returncode == 1 and "--yes" in json.loads(blocked.stdout)["error"]

        # --- item properties, enable, markers
        assert jrun("item", "props", "--item", "1", "--set", "ZoomX=1.2")["ZoomX"] == 1.2
        assert jrun("item", "props", "--item", "1", "--key", "ZoomX")["ZoomX"] == 1.2
        assert jrun("item", "speed", "--item", "2", "--set", '{"Percentage": 50}')["Percentage"] == 50
        jrun("item", "speed", "--item", "2", "--set", '{"Percentage": 100}')
        assert jrun("item", "fades", "--item", "1", "--fade-in", "5")["FadeIn"] == 5
        assert run("--json", "item", "speed", "--item", "2", "--set", '{"Bogus": 1}', check=False).returncode == 1
        assert jrun("item", "enable", "--item", "2", "--off")["enabled"] is False
        jrun("item", "enable", "--item", "2")
        jrun("item", "marker-add", "--item", "1", "5", "--name", "m", "--color", "Red")
        assert [m["name"] for m in jrun("item", "markers", "--item", "1")] == ["m"]
        assert run("--json", "item", "marker-delete", "--item", "1", "--frame", "5", check=False).returncode != 0
        jrun("item", "marker-delete", "--item", "1", "--frame", "5", "--yes")
        jrun("media", "marker-add", "ext.mp4", "3", "--name", "pm")
        assert [m["name"] for m in jrun("media", "markers", "ext.mp4")] == ["pm"]
        jrun("media", "marker-delete", "ext.mp4", "--frame", "3", "--yes")
        jrun("item", "export-frame", str(tmp_path / "f.png"), "--item", "1", "--offset", "10")
        assert (tmp_path / "f.png").exists()

        # --- color: versions, node edit, copy-grade, groups
        jrun("color", "version-add", "--item", "1", "grade_a")
        assert "grade_a" in jrun("color", "versions", "--item", "1")["versions"]
        jrun("color", "version-rename", "--item", "1", "grade_a", "grade_b")
        assert "grade_b" in jrun("color", "versions", "--item", "1")["versions"]
        assert jrun("color", "node-set", "--item", "1", "1", "--label", "Exp")["label"] == "Exp"
        jrun("color", "set-cdl", "--item", "1", "--slope", "1.1 1.0 0.9")
        res = jrun("color", "copy-grade", "--src-track", "video:1", "--src-item", "1", "--dst-track", "video:1",
                   "--dst-item", "2", "--overwrite")
        assert res["counts"].get("copied") == 1, res
        dry = jrun("color", "copy-grade", "--src-track", "video:1", "--src-item", "1", "--dst-track", "video:1",
                   "--dst-item", "2", "--overwrite", "--dry-run")
        assert dry["counts"] == {"will_copy": 1}
        jrun("stills", "grab", "--label", "drx_src")
        exp = jrun("stills", "export", str(tmp_path / "drx"), "--label", "drx_src")
        drx = next(f for f in exp["files"] if f.endswith(".drx"))
        assert jrun("color", "apply-drx", str(tmp_path / "drx" / drx), "--track", "video:1", "--item", "2")["applied"] == 1
        assert run("--json", "api", "call", "Graph.ResetAllGrades", "--track", "video:1", "--item", "1", check=False).returncode == 1
        assert run("--json", "layout", "delete", "x", check=False).returncode == 1
        assert run("--json", "media", "analyze", "clear-transcript", "--clip", "ext.mp4", check=False).returncode == 1
        jrun("color", "group-add", "G1")
        assert jrun("color", "group-assign", "--item", "1", "G1")["group"] == "G1"
        assert jrun("color", "group-clips", "G1")
        jrun("color", "group-remove", "--item", "1")
        jrun("color", "group-delete", "G1", "--yes")
        assert "G1" not in jrun("color", "groups")
        assert run("--json", "color", "reset", "--item", "1", check=False).returncode == 1

        # --- timeline versions
        snap = jrun("timeline", "snapshot")
        assert snap["timeline"] == "Ext v2"
        assert jrun("timeline", "snapshot")["timeline"] == "Ext v3"
        assert jrun("timeline", "versions")["versions"] == ["Ext", "Ext v2", "Ext v3"]
        jrun("timeline", "rename", "Ext v3 old", "--timeline", "Ext v3")
        jrun("timeline", "delete", "Ext v3 old", "--yes")
        jrun("timeline", "set", "Ext")
        jrun("timeline", "track-name", "video", "1", "Main")
        jrun("timeline", "track-lock", "video", "1")
        jrun("timeline", "track-lock", "video", "1", "--off")

        # --- presets, settings, layouts
        jrun("render", "preset-save", "cliany_preset")
        assert "cliany_preset" in jrun("render", "presets")
        jrun("render", "preset-load", "cliany_preset")
        jrun("render", "preset-export", "cliany_preset", str(tmp_path / "p.xml"))
        assert run("--json", "render", "preset-delete", "cliany_preset", check=False).returncode == 1
        jrun("render", "preset-delete", "cliany_preset", "--yes")
        assert "mov" in str(jrun("render", "formats")).lower()
        assert "timelineFrameRate" in jrun("settings", "get", "--key", "timelineFrameRate")
        assert float(jrun("settings", "set", "timelineFrameRate=30")["timelineFrameRate"]) == 30.0
        bad = run("--json", "settings", "set", "noSuchSetting=1", check=False)
        assert bad.returncode == 1 and "not applied" in json.loads(bad.stdout)["error"]
        assert isinstance(jrun("settings", "presets"), list)
        assert isinstance(jrun("layout", "list"), list)

        # --- library
        assert "folders" in jrun("project", "folders")
        assert jrun("project", "databases")["databases"]
        assert jrun("storage", "volumes")
        assert "files" in jrun("storage", "ls", str(tmp_path))
        assert jrun("stills", "albums")["stills"]
        jrun("stills", "album-create", "cliany_album")
        assert "cliany_album" in jrun("stills", "albums")["stills"]
        assert "JobStatus" in str(jrun("analysis", "status", "detect-cuts")) or jrun("analysis", "status", "detect-cuts") is not None


class TestAgentErgonomics:
    def test_doctor_schema_dry_run_errors(self, scratch):
        report = jrun("doctor")
        assert report["ok"] and report["reachable"]
        assert {c["check"] for c in report["checks"]} >= {"python", "resolve", "ffmpeg", "stable-ts"}

        shown = jrun("schema", "show", "timeline", "delete-clips")
        assert shown["mutating"] and {p["name"] for p in shown["params"]} >= {"track", "items", "dry_run"}
        listed = {c["command"]: c["mutating"] for c in jrun("schema", "list")}
        assert listed["timeline delete-clips"] is True and listed["timeline list"] is False
        assert run("schema", "show", "no", "such", check=False).returncode == 2

        before = jrun("media", "tree")
        preview = jrun("media", "new-bin", "dryrun_bin", "--dry-run")
        assert preview["dry_run"] and preview["params"]["name"] == "dryrun_bin"
        assert jrun("media", "tree") == before  # nothing was created

        missing = run("--json", "render", "wait", "no-such-job", check=False)
        assert missing.returncode == 1 and json.loads(missing.stdout)["error_code"] == "not_found"

        env = {**os.environ, "RESOLVE_SCRIPT_API": "/nonexistent"}
        down = subprocess.run(CLI + ["--json", "status"], capture_output=True, text=True, env=env)
        assert down.returncode == 2 and json.loads(down.stdout)["error_code"] == "api_not_found"


class TestEditorRequestsE2E:
    """Gaps reported by a video-editing session: ranged append, bins on import, replace, pitch, Fusion save/dump."""

    def test_flow(self, scratch, tmp_path):
        v24, still = tmp_path / "v24.mp4", tmp_path / "still.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=24",
                        "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-pix_fmt", "yuv420p", str(v24)], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=1",
                        "-frames:v", "1", str(still)], check=True)
        jrun("timeline", "new", "Req")
        jrun("timeline", "set", "Req")
        fps = float(jrun("project", "get-setting", "timelineFrameRate")["timelineFrameRate"])

        # A3: import straight into a bin that does not exist yet
        assert run("--json", "media", "import", str(v24), "--bin", "Materiale/Video", check=False).returncode == 1
        jrun("media", "import", str(v24), str(still), "--bin", "Materiale/Video", "--create")
        assert {c["name"] for c in jrun("media", "clips", "--bin", "Materiale/Video")} == {"v24.mp4", "still.png"}

        # A2: missing track is created; exact length survives an fps mismatch; record position honoured
        first = jrun("timeline", "append", "v24.mp4", "--track", "video:3", "--start", "12", "--frames", "30", "--at", "0", "--kind", "video")
        assert first["items"][0]["track"] == "video:3" and first["items"][0]["duration"] == 30 and first["exact"]
        jrun("timeline", "append", "v24.mp4", "--track", "video:3", "--frames", "45", "--at", "60", "--kind", "video")
        # stills: exact duration through mark in/out
        s = jrun("timeline", "append", "still.png", "--track", "video:2", "--frames", "20", "--at", "0")
        assert s["items"][0]["duration"] == 20
        a = jrun("timeline", "append", "v24.mp4", "--track", "audio:2", "--frames", "30", "--at", "0", "--kind", "audio")
        assert a["items"][0]["track"].startswith("audio:2") and a["exact"], a
        # non-current timeline: switched to and back
        jrun("timeline", "new", "Other")
        jrun("timeline", "set", "Req")
        other = jrun("timeline", "append", "v24.mp4", "--timeline", "Other", "--frames", "24", "--kind", "video")
        assert abs(other["items"][0]["duration"] - 24) <= 1  # 24 tl frames is not a whole number of 24 fps source frames
        assert any(t["name"] == "Req" and t["current"] for t in jrun("timeline", "list"))

        # A1: several items at once, no ripple
        before = len(jrun("timeline", "items", "--type", "video", "--track", "3"))
        assert before == 2
        jrun("timeline", "delete-clips", "--track", "video:3", "--item", "1", "--item", "2", "--yes")
        assert jrun("timeline", "items", "--type", "video", "--track", "3") == []

        # A5: replace keeps the clip
        assert jrun("media", "replace", "v24.mp4", str(v24))["clip"] == "v24.mp4"

        # A6 / B pitch: audio volume and pitch through item props
        out = jrun("item", "props", "--track", "audio:2", "--item", "1", "--set", "AudioVolume=-18", "--set", "AudioPitchSemiTones=-3")
        assert out["AudioVolume"] == -18 and out["AudioPitchSemiTones"] == -3

        # A4: Fusion macro save and comp dump, Fusion clip
        jrun("fusion", "add-comp", "--track", "video:2", "--item", "1")
        added = jrun("fusion", "add-tool", "TextPlus", "--track", "video:2", "--item", "1")
        dumped = jrun("fusion", "dump", "--track", "video:2", "--item", "1")
        assert any(t["name"] == added["tool"] for t in dumped["tools"])
        jrun("fusion", "set-input", added["tool"], "StyledText", "Hello world", "--track", "video:2", "--item", "1")
        box = jrun("fusion", "bbox", added["tool"], "--track", "video:2", "--item", "1")
        assert box["width"] > 0 and box["height"] > 0 and box["width"] < box["canvas"][0]
        macro = tmp_path / "text.setting"
        jrun("fusion", "save-tool", added["tool"], str(macro), "--track", "video:2", "--item", "1")
        assert macro.stat().st_size > 0

        # Fusion clip from two contiguous items on a track
        jrun("timeline", "append", "v24.mp4", "--track", "video:4", "--frames", "30", "--at", "0", "--kind", "video")
        jrun("timeline", "append", "v24.mp4", "--track", "video:4", "--frames", "30", "--at", "30", "--kind", "video")
        assert jrun("timeline", "fusion-clip", "--track", "video:4", "--item", "1", "--item", "2")["from"] == 2


class TestClipWorkflowRequestsE2E:
    """Effects, track enable on other pages, export-voice track restore, titles without ripple."""

    def test_flow(self, scratch, tmp_path):
        from cli_anything.davinci_resolve.utils.resolve_backend import connect

        clip = tmp_path / "fx24.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
                        "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
        jrun("media", "import", str(clip))
        jrun("timeline", "new", "Fx")
        jrun("timeline", "set", "Fx")
        jrun("timeline", "append", "fx24.mp4", "--track", "video:1", "--frames", "60", "--at", "0")

        # effects: catalogue, add, list, disable, delete
        found = jrun("effects", "search", "Lens Blur", "--category", "ofx")
        assert found["matches"] >= 1, found
        effect_id = found["effects"][0]["id"]
        added = jrun("item", "effects", "add", "--category", "ofx", effect_id, "--track", "video:1", "--item", "1")
        assert added["category"] == "ofx" and added["enabled"] is True
        assert [e["index"] for e in jrun("item", "effects", "list", "--track", "video:1", "--item", "1")] == [1]
        assert jrun("item", "effects", "enable", "--category", "ofx", "1", "--off", "--track", "video:1", "--item", "1")["enabled"] is False
        assert jrun("item", "effects", "delete", "--category", "ofx", "1", "--yes", "--track", "video:1", "--item", "1")["remaining"] == 0

        # track enable works from another page, and the page is put back
        jrun("timeline", "add-track", "video")
        jrun("timeline", "track-enable", "video", "2", "off")
        resolve = connect()
        resolve.OpenPage("deliver")
        time.sleep(2)
        jrun("timeline", "track-enable", "video", "2", "on")
        assert resolve.GetCurrentPage() == "deliver"
        assert all(t["enabled"] for t in jrun("timeline", "tracks") if t["type"] == "video")
        resolve.OpenPage("edit")
        time.sleep(2)

        # export-voice by render leaves every audio track as it was
        jrun("timeline", "add-track", "audio")
        jrun("timeline", "add-track", "audio")
        before = [t["enabled"] for t in jrun("timeline", "tracks") if t["type"] == "audio"]
        jrun("subs", "export-voice", "--track", "1", "--method", "render", "--out", str(tmp_path / "voce.wav"))
        assert [t["enabled"] for t in jrun("timeline", "tracks") if t["type"] == "audio"] == before
        assert (tmp_path / "voce.wav").stat().st_size > 1000

        # a title: Resolve picks the destination track, so either it lands on video:3 or nothing changes
        layout_before = jrun("timeline", "items", "--type", "video", "--track", "1")
        landed = run("--json", "timeline", "insert-title", "Text+", "--mode", "ripple", "--track", "3", "--at", "10",
                     "--duration", "20", check=False)
        out = json.loads(landed.stdout)
        if landed.returncode == 0:
            assert out["start"] == 10 and out["duration"] == 20 and out["track"] == "video:3"
        else:
            assert out["error_code"] in ("insert_misplaced", "insert_failed", "insert_would_split", "not_topmost"), out
        assert jrun("timeline", "items", "--type", "video", "--track", "1") == layout_before

        # default mode never ripples: other tracks and the timeline end stay put, subtitle track included
        jrun("timeline", "add-track", "subtitle")
        info_before = jrun("timeline", "info")
        placed = jrun("timeline", "insert-title", "Text+", "--track", "4", "--at", "5", "--duration", "20", "--text", "Ciao")
        assert placed["track"] == "video:4" and placed["start"] == 5 and placed["result"]["exact"]
        assert jrun("timeline", "items", "--type", "video", "--track", "1") == layout_before
        assert jrun("timeline", "info")["end_frame"] == info_before["end_frame"]
        gen = jrun("timeline", "insert-title", "Solid Color", "--kind", "generator", "--track", "5", "--at", "0", "--duration", "10")
        assert gen["track"] == "video:5"
        busy = run("--json", "timeline", "insert-title", "Solid Color", "--kind", "generator", "--track", "5", "--at", "5", check=False)
        assert busy.returncode == 1 and json.loads(busy.stdout)["error_code"] == "track_occupied"
        assert all("type" in c for c in jrun("media", "clips"))


class TestStructureDumpE2E:
    """Peer request: one-call structure dump, item kinds, project open by path, hard timeout."""

    def test_dump_kinds_path_and_timeout(self, scratch):
        jrun("timeline", "new", "dumpcut")
        jrun("timeline", "set", "dumpcut")
        listed = jrun("timeline", "list")
        row = next(t for t in listed if t["name"] == "dumpcut")
        assert row["current"] and row["fps"] and row["duration_frames"] is not None

        dumped = jrun("timeline", "dump")
        assert dumped["project"] == TEST_PROJECT
        tl = dumped["timelines"][0]
        assert tl["name"] == "dumpcut" and tl["tracks"] and "markers" in tl
        everything = jrun("timeline", "dump", "--all", "--no-text", "--no-markers")
        assert len(everything["timelines"]) >= 2 and "markers" not in everything["timelines"][0]

        for r in (item for t in everything["timelines"] for tr in t["tracks"] for item in tr["items"]):
            assert r["kind"] in {"clip", "still", "compound", "fusion_clip", "nested_timeline", "title", "generator",
                                 "adjustment", "transition", "subtitle"}

        opened = jrun("project", "open", f"{TEST_FOLDER}/{TEST_PROJECT}")
        assert opened["project"] == TEST_PROJECT and opened["folder"] == TEST_FOLDER and opened["attempts"] >= 1
        missing = run("--json", "project", "open", "NoSuchFolder/x", check=False)
        assert missing.returncode == 1 and json.loads(missing.stdout)["error_code"] == "folder_not_found"
        jrun("project", "open", f"{TEST_FOLDER}/{TEST_PROJECT}")  # a failed path leaves the folder at the root

    def test_watchdog_ends_hung_call(self):
        code = ("import time\nfrom cli_anything.davinci_resolve.utils.watchdog import Guard\n"
                "with Guard(1, 'status', True):\n    time.sleep(10)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=8)
        assert out.returncode == 2 and json.loads(out.stdout)["error_code"] == "timeout"
