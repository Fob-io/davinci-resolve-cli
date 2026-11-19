"""Unit tests: core modules against a fake Resolve object (no app needed)."""
from unittest.mock import MagicMock

import pytest

from cli_anything.davinci_resolve.core import media, project, render, timeline
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError


def fake_resolve(project_name="P", projects=("P", "Q")):
    resolve = MagicMock()
    pm = resolve.GetProjectManager.return_value
    proj = pm.GetCurrentProject.return_value
    proj.GetName.return_value = project_name
    pm.GetProjectListInCurrentFolder.return_value = list(projects)
    return resolve, pm, proj


class TestProject:
    def test_open_unknown_lists_available(self):
        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="Available"):
            project.open_project(resolve, "nope")

    def test_open_saves_only_saved_projects(self):
        resolve, pm, _ = fake_resolve(project_name="Untitled Project")
        project.open_project(resolve, "Q")
        pm.SaveProject.assert_not_called()
        pm.LoadProject.assert_called_with("Q")

    def test_open_saves_current_first(self):
        resolve, pm, _ = fake_resolve()
        project.open_project(resolve, "Q")
        pm.SaveProject.assert_called_once()

    def test_new_rejects_duplicate(self):
        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="already exists"):
            project.create_project(resolve, "P")

    def test_no_project_open(self):
        resolve, pm, _ = fake_resolve()
        pm.GetCurrentProject.return_value = None
        with pytest.raises(ResolveError, match="No project"):
            project.info(resolve)


class TestMedia:
    def test_import_missing_file(self, tmp_path):
        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="not found"):
            media.import_media(resolve, [str(tmp_path / "x.mov")])

    def test_unknown_bin(self):
        resolve, _, proj = fake_resolve()
        root = proj.GetMediaPool.return_value.GetRootFolder.return_value
        root.GetSubFolderList.return_value = []
        with pytest.raises(ResolveError, match="Bin 'A'"):
            media.list_clips(resolve, "A")


class TestTimeline:
    def test_marker_invalid_color(self):
        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="Invalid color"):
            timeline.add_marker(resolve, 10, "Orange", "x")

    def test_marker_add_failure_reported(self):
        resolve, _, proj = fake_resolve()
        proj.GetCurrentTimeline.return_value.AddMarker.return_value = False
        with pytest.raises(ResolveError, match="AddMarker"):
            timeline.add_marker(resolve, 10, "Red", "x")

    def test_items_bad_track(self):
        resolve, _, proj = fake_resolve()
        proj.GetCurrentTimeline.return_value.GetTrackCount.return_value = 1
        with pytest.raises(ResolveError, match="No video track 3"):
            timeline.items(resolve, "video", 3)

    def test_export_unknown_format(self):
        resolve = MagicMock(spec=["GetProjectManager", "EXPORT_DRT"])
        pm = resolve.GetProjectManager.return_value
        pm.GetCurrentProject.return_value.GetCurrentTimeline.return_value = MagicMock()
        with pytest.raises(ResolveError, match="Unknown format"):
            timeline.export(resolve, "/tmp/x", "bogus")


class TestRender:
    def test_missing_target_dir(self):
        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="Target directory"):
            render.add_job(resolve, "/definitely/not/here")

    def test_unknown_preset(self, tmp_path):
        resolve, _, proj = fake_resolve()
        proj.GetRenderPresetList.return_value = ["H.264 Master"]
        with pytest.raises(ResolveError, match="Preset"):
            render.add_job(resolve, str(tmp_path), preset="Nope")

    def test_empty_job_id_is_error(self, tmp_path):
        resolve, _, proj = fake_resolve()
        proj.AddRenderJob.return_value = ""
        with pytest.raises(ResolveError, match="AddRenderJob"):
            render.add_job(resolve, str(tmp_path))


class TestApi:
    """Generic API access: catalogue comes from the .pyi shipped with Resolve."""

    def test_catalogue_covers_main_classes(self):
        from cli_anything.davinci_resolve.core import api

        cat = api.catalogue()
        assert {"Resolve", "Project", "Timeline", "TimelineItem", "MediaPool"} <= set(cat)
        assert sum(len(m) for m in cat.values()) > 400

    def test_search_and_describe(self):
        from cli_anything.davinci_resolve.core import api

        assert any(r.startswith("Timeline.AddMarker(") for r in api.search("addmarker"))
        d = api.describe("Timeline.AddMarker")
        assert d["returns"] == "bool" and not d["destructive"]
        assert api.describe("Project.DeleteRenderJob")["destructive"]

    def test_unknown_symbol(self):
        from cli_anything.davinci_resolve.core import api

        with pytest.raises(ResolveError, match="Unknown class"):
            api.describe("Nope.X")
        with pytest.raises(ResolveError, match="no method"):
            api.describe("Timeline.Nope")

    def test_destructive_needs_yes(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, proj = fake_resolve()
        with pytest.raises(ResolveError, match="--yes"):
            api.call(resolve, "Project.DeleteRenderJob", ["j1"], {})
        proj.DeleteRenderJob.assert_not_called()
        api.call(resolve, "Project.DeleteRenderJob", ["j1"], {}, yes=True)
        proj.DeleteRenderJob.assert_called_once_with("j1")

    def test_args_must_be_list(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="JSON array"):
            api.call(resolve, "Project.GetName", {"a": 1}, {})

    def test_const_and_object_results(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, proj = fake_resolve()
        resolve.EXPORT_DRT = 7
        timeline = proj.GetCurrentTimeline.return_value
        timeline.Export.return_value = True
        out = api.call(resolve, "Timeline.Export", ["/tmp/x.drt", {"$const": "EXPORT_DRT"}], {})
        timeline.Export.assert_called_once_with("/tmp/x.drt", 7)
        assert out["result"] is True
        bare = MagicMock(spec=["GetProjectManager"])
        bare.GetProjectManager.return_value = resolve.GetProjectManager.return_value
        with pytest.raises(ResolveError, match="no constant"):
            api.call(bare, "Timeline.Export", ["/tmp/x", {"$const": "EXPORT_NOPE"}], {})

    def test_method_outside_stub_allowed_only_if_live_object_has_it(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, proj = fake_resolve()
        proj.GetSetting.return_value = "30"
        out = api.call(resolve, "Project.GetSetting", ["timelineFrameRate"], {})
        assert out["result"] == "30" and out["in_stub"] is False
        proj.mock_add_spec(["GetName"])
        with pytest.raises(ResolveError, match="no method"):
            api.call(resolve, "Project.GetNothing", [], {})

    def test_timeline_item_needs_selectors(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="--track"):
            api.call(resolve, "TimelineItem.GetName", [], {})

    def test_unaddressable_class(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, _ = fake_resolve()
        with pytest.raises(ResolveError, match="cannot be addressed"):
            api.call(resolve, "ColorGroup.GetName", [], {})


def fake_clip(name, color="", metadata=""):
    clip = MagicMock()
    clip.GetName.return_value = name
    state = {"color": color, "meta": metadata}
    clip.GetClipColor.side_effect = lambda: state["color"]
    clip.SetClipColor.side_effect = lambda v: state.update(color=v) or True
    clip.GetMetadata.side_effect = lambda k=None: state["meta"]
    clip.SetMetadata.side_effect = lambda d: state.update(meta=next(iter(d.values()))) or True
    return clip


def resolve_with_clips(*clips):
    resolve, _, proj = fake_resolve()
    root = proj.GetMediaPool.return_value.GetRootFolder.return_value
    root.GetClipList.return_value = list(clips)
    root.GetSubFolderList.return_value = []
    return resolve


class TestBulkClipOps:
    def test_selector_needs_exactly_one(self):
        resolve = resolve_with_clips()
        with pytest.raises(ResolveError, match="exactly one"):
            media.select_clips(resolve)
        with pytest.raises(ResolveError, match="exactly one"):
            media.select_clips(resolve, clip="a", bin_path="/")

    def test_duplicate_clip_name_is_ambiguous(self):
        resolve = resolve_with_clips(fake_clip("a"), fake_clip("a"))
        with pytest.raises(ResolveError, match="2 clips"):
            media.select_clips(resolve, clip="a")

    def test_color_never_overwrites_without_flag(self):
        mine, empty = fake_clip("mine", color="Green"), fake_clip("empty")
        resolve = resolve_with_clips(mine, empty)
        out = media.set_clip_color(resolve, "Blue", bin_path="/")
        assert {r["clip"]: r["status"] for r in out["results"]} == {"mine": "skipped", "empty": "set"}
        assert mine.GetClipColor() == "Green" and empty.GetClipColor() == "Blue"
        mine.SetClipColor.assert_not_called()

    def test_overwrite_flag_replaces(self):
        mine = fake_clip("mine", color="Green")
        out = media.set_clip_color(resolve_with_clips(mine), "Blue", overwrite=True, bin_path="/")
        assert out["counts"]["set"] == 1 and mine.GetClipColor() == "Blue"

    def test_same_value_is_unchanged(self):
        out = media.set_clip_color(resolve_with_clips(fake_clip("c", color="Blue")), "Blue", bin_path="/")
        assert out["counts"]["unchanged"] == 1

    def test_ignored_write_reported_as_failed(self):
        clip = fake_clip("c")
        clip.SetMetadata.side_effect = lambda d: True  # Resolve says True but keeps the old value
        out = media.set_clip_metadata(resolve_with_clips(clip), "Comments", "x", bin_path="/")
        assert out["counts"]["failed"] == 1

    def test_invalid_color(self):
        with pytest.raises(ResolveError, match="Invalid color"):
            media.set_clip_color(resolve_with_clips(), "Orange2", bin_path="/")


class TestPlayheadAndItems:
    def test_playhead_set_failure_restores_position(self):
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        tl.GetCurrentTimecode.return_value = "01:00:05:00"
        tl.SetCurrentTimecode.return_value = False
        with pytest.raises(ResolveError, match="restored to 01:00:05:00"):
            timeline.playhead(resolve, "99:99:99:99")
        tl.SetCurrentTimecode.assert_called_with("01:00:05:00")

    def test_playhead_silently_normalized_value_is_an_error(self):
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        reads = iter(["01:00:00:00", "100:40:42:09"])  # before, after the (ignored) request
        tl.GetCurrentTimecode.side_effect = lambda: next(reads, "01:00:00:00")
        tl.SetCurrentTimecode.return_value = True
        with pytest.raises(ResolveError, match="100:40:42:09"):
            timeline.playhead(resolve, "99:99:99:99")

    def test_playhead_accepts_drop_frame_separator(self):
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        reads = iter(["01:00:00;00", "01:00:10;00", "01:00:10;00"])
        tl.GetCurrentTimecode.side_effect = lambda: next(reads)
        tl.SetCurrentTimecode.return_value = True
        assert timeline.playhead(resolve, "01:00:10:00") == {"timecode": "01:00:10;00"}

    def test_playhead_read_only(self):
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        tl.GetCurrentTimecode.return_value = "01:00:00:00"
        assert timeline.playhead(resolve) == {"timecode": "01:00:00:00"}
        tl.SetCurrentTimecode.assert_not_called()

    def test_items_full_adds_source_fields(self):
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        tl.GetTrackCount.return_value = 1
        item = MagicMock()
        item.GetName.return_value = "n"
        item.GetMediaPoolItem.return_value.GetName.return_value = "src.mov"
        item.GetLeftOffset.return_value = 12
        tl.GetItemListInTrack.return_value = [item]
        assert "source" not in timeline.items(resolve)[0]
        row = timeline.items(resolve, full=True)[0]
        assert row["source"] == "src.mov" and row["left_offset"] == 12


def album_with(labels):
    album = MagicMock()
    stills = [MagicMock(name=f"still{i}") for i in range(len(labels))]
    album.GetStills.return_value = stills
    album.GetLabel.side_effect = lambda s: labels[stills.index(s)]
    return album, stills


class TestStills:
    def _resolve(self, album):
        resolve, _, proj = fake_resolve()
        proj.GetGallery.return_value.GetCurrentStillAlbum.return_value = album
        return resolve

    def test_pick_needs_exactly_one_selector(self, tmp_path):
        from cli_anything.davinci_resolve.core import stills

        resolve = self._resolve(album_with(["a"])[0])
        with pytest.raises(ResolveError, match="exactly one"):
            stills.export(resolve, str(tmp_path))
        with pytest.raises(ResolveError, match="exactly one"):
            stills.export(resolve, str(tmp_path), labels=("a",), all_stills=True)

    def test_unknown_label_and_index(self, tmp_path):
        from cli_anything.davinci_resolve.core import stills

        resolve = self._resolve(album_with(["a"])[0])
        with pytest.raises(ResolveError, match="No still labelled"):
            stills.export(resolve, str(tmp_path), labels=("zzz",))
        with pytest.raises(ResolveError, match="No still at index"):
            stills.export(resolve, str(tmp_path), indexes=(5,))

    def test_export_creates_folder_and_reports_new_files(self, tmp_path):
        from cli_anything.davinci_resolve.core import stills

        album, _ = album_with(["a", "b"])
        target = tmp_path / "new" / "dir"

        def fake_export(chosen, folder, prefix, fmt):
            (target / f"{prefix}_1.png").write_bytes(b"x")
            return True

        album.ExportStills.side_effect = fake_export
        out = stills.export(self._resolve(album), str(target), prefix="p", all_stills=True)
        assert out["files"] == ["p_1.png"] and out["exported"] == 2

    def test_export_success_without_files_is_an_error(self, tmp_path):
        from cli_anything.davinci_resolve.core import stills

        album, _ = album_with(["a"])
        album.ExportStills.return_value = True
        with pytest.raises(ResolveError, match="wrote no files"):
            stills.export(self._resolve(album), str(tmp_path), all_stills=True)

    def test_delete_needs_yes_and_touches_nothing_without_it(self):
        from cli_anything.davinci_resolve.core import stills

        album, _ = album_with(["a"])
        resolve = self._resolve(album)
        with pytest.raises(ResolveError, match="--yes"):
            stills.delete(resolve, all_stills=True)
        album.DeleteStills.assert_not_called()

    def test_grab_retries_then_gives_up(self, monkeypatch):
        from cli_anything.davinci_resolve.core import stills

        monkeypatch.setattr(stills.time, "sleep", lambda s: None)
        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        tl.GrabStill.side_effect = [None, None, MagicMock()]
        assert stills.grab(resolve)["grabbed"] is True
        tl.GrabStill.side_effect = [None] * stills._GRAB_ATTEMPTS
        with pytest.raises(ResolveError, match=f"GrabStill failed {stills._GRAB_ATTEMPTS} times"):
            stills.grab(resolve)

    def test_label_goes_on_the_returned_still(self, monkeypatch):
        from cli_anything.davinci_resolve.core import stills

        monkeypatch.setattr(stills.time, "sleep", lambda s: None)
        resolve, _, proj = fake_resolve()
        new = MagicMock()
        proj.GetCurrentTimeline.return_value.GrabStill.return_value = new
        album = proj.GetGallery.return_value.GetCurrentStillAlbum.return_value
        album.SetLabel.return_value = True
        stills.grab(resolve, label="v1")
        album.SetLabel.assert_called_once_with(new, "v1")


class TestFusion:
    def _setup(self, names=("Composition 1",), tool_value=None, accepts=True):
        from cli_anything.davinci_resolve.core import fusion  # noqa: F401

        resolve, _, proj = fake_resolve()
        tl = proj.GetCurrentTimeline.return_value
        item = MagicMock()
        item.GetFusionCompNameList.return_value = list(names)
        tl.GetItemListInTrack.return_value = [item]
        comp = item.GetFusionCompByIndex.return_value
        tool = comp.FindTool.return_value
        state = {"v": tool_value}
        tool.GetInput.side_effect = lambda n: state["v"]
        tool.SetInput.side_effect = lambda n, v: state.update(v=v) if accepts else None
        inp = MagicMock()
        inp.GetAttrs.return_value = {"INPS_Name": "Size"}
        tool.GetInputList.return_value = {1: inp}
        return resolve, comp, tool

    def test_item_without_comp(self):
        from cli_anything.davinci_resolve.core import fusion

        resolve, _, _ = self._setup(names=())
        with pytest.raises(ResolveError, match="no Fusion comp"):
            fusion.tools(resolve, None, "video:1", 1, None)

    def test_unknown_tool_lists_available(self):
        from cli_anything.davinci_resolve.core import fusion

        resolve, comp, _ = self._setup()
        comp.FindTool.return_value = None
        t = MagicMock(); t.Name = "MediaIn1"
        comp.GetToolList.return_value = {1: t}
        with pytest.raises(ResolveError, match="MediaIn1"):
            fusion.get_input(resolve, None, "video:1", 1, None, "Nope", "Size")

    def test_unknown_input_name(self):
        from cli_anything.davinci_resolve.core import fusion

        resolve, _, _ = self._setup()
        with pytest.raises(ResolveError, match="no input 'Siz'"):
            fusion.set_input(resolve, None, "video:1", 1, None, "T", "Siz", 2)

    def test_set_input_read_back(self):
        from cli_anything.davinci_resolve.core import fusion

        resolve, _, _ = self._setup(tool_value=1.0)
        out = fusion.set_input(resolve, None, "video:1", 1, None, "T", "Size", 1.5)
        assert out["old"] == 1.0 and out["value"] == 1.5

    def test_ignored_write_is_an_error(self):
        from cli_anything.davinci_resolve.core import fusion

        resolve, _, _ = self._setup(tool_value=1.0, accepts=False)
        with pytest.raises(ResolveError, match="was ignored"):
            fusion.set_input(resolve, None, "video:1", 1, None, "T", "Size", 1.5)


class TestColorAndApiReceivers:
    def test_no_graph(self):
        from cli_anything.davinci_resolve.core import color

        resolve, _, proj = fake_resolve()
        item = MagicMock()
        item.GetNodeGraph.return_value = None
        proj.GetCurrentTimeline.return_value.GetItemListInTrack.return_value = [item]
        with pytest.raises(ResolveError, match="No node graph"):
            color.nodes(resolve, None, "video:1", 1)

    def test_nodes_rows(self):
        from cli_anything.davinci_resolve.core import color

        resolve, _, proj = fake_resolve()
        item = MagicMock()
        graph = item.GetNodeGraph.return_value
        graph.GetNumNodes.return_value = 2
        graph.GetNodeLabel.side_effect = ["Exp", ""]
        graph.GetToolsInNode.side_effect = [["HDR"], None]
        proj.GetCurrentTimeline.return_value.GetItemListInTrack.return_value = [item]
        rows = color.nodes(resolve, None, "video:1", 1)
        assert [r["label"] for r in rows] == ["Exp", ""] and rows[0]["tools"] == ["HDR"] and rows[1]["tools"] == []

    def test_api_call_graph_and_album_receivers(self):
        from cli_anything.davinci_resolve.core import api

        resolve, _, proj = fake_resolve()
        item = MagicMock()
        item.GetNodeGraph.return_value.GetNumNodes.return_value = 3
        proj.GetCurrentTimeline.return_value.GetItemListInTrack.return_value = [item]
        sel = {"track": "video:1", "item": 1, "layer": 2}
        assert api.call(resolve, "Graph.GetNumNodes", [], sel)["result"] == 3
        item.GetNodeGraph.assert_called_with(2)
        proj.GetGallery.return_value.GetCurrentStillAlbum.return_value.GetStills.return_value = []
        assert api.call(resolve, "GalleryStillAlbum.GetStills", [], {})["result"] == []
