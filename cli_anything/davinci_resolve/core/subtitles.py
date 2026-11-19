"""Vertical-reel subtitles: voice export, generation, rebuild-the-whole-track insert, voice check.

Ported from the better-subs-davinci skill. Resolve cannot edit subtitle text through the API,
so `apply` always rebuilds the whole subtitle track from one SRT (with a backup and rollback).
"""
import array
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from cli_anything.davinci_resolve.core.media import walk_clips
from cli_anything.davinci_resolve.core.timeline import find_timeline, set_track_state
from cli_anything.davinci_resolve.utils.resolve_backend import ResolveError, current_project, current_timeline

SR = 16000
MIN_FRAMES = 8
SRT_PREFIX = "bsd-"

_TRANSCRIBE = """
import json, sys, stable_whisper
wav, fps, start, model = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
r = stable_whisper.load_model(model).transcribe(wav, language="it", fp16=False, verbose=None)
ws = [(w.word.strip(), round(w.start * fps) + start, round(w.end * fps) + start) for s in r.segments for w in s.words]
json.dump(ws, open(wav + ".words.json", "w"), ensure_ascii=False)
print(json.dumps({"words": len(ws), "json": wav + ".words.json", "text": r.text}, ensure_ascii=False))
"""


def _fps(tl):
    return float(tl.GetSetting("timelineFrameRate"))


def _subs(tl):
    if not tl.GetTrackCount("subtitle"):
        return []
    return sorted(tl.GetItemListInTrack("subtitle", 1) or [], key=lambda x: x.GetStart())


def list_subs(resolve):
    tl = current_timeline(resolve)
    start = tl.GetStartFrame()
    return [
        {"start": s.GetStart(), "end": s.GetEnd(), "rel_start": s.GetStart() - start, "text": s.GetName()}
        for s in _subs(tl)
    ]


def _ffmpeg():
    path = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(path):
        raise ResolveError("ffmpeg not found (brew install ffmpeg)")
    return path


def _voice_from_files(tl, clips, fps, start):
    buf = bytearray(2 * int((max(x.GetEnd() for x in clips) - start) / fps * SR + SR))
    speed, errors = [], {}
    for x in clips:
        dur = (x.GetEnd() - x.GetStart()) / fps
        if abs((x.GetSourceEndTime() - x.GetSourceStartTime()) - dur) > 0.1:
            speed.append({"frame": x.GetStart(), "clip": x.GetName()})
        path = x.GetMediaPoolItem().GetClipProperty("File Path")
        r = subprocess.run(
            [_ffmpeg(), "-v", "error", "-ss", str(x.GetSourceStartTime()), "-t", str(dur), "-i", path,
             "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
            capture_output=True,
        )
        if r.returncode:
            errors.setdefault(r.stderr[-200:].decode(errors="ignore").strip(), []).append(x.GetName())
            continue
        i = 2 * int(round((x.GetStart() - start) / fps * SR))
        buf[i:i + len(r.stdout)] = r.stdout
    return bytes(buf), speed, [{"error": e, "clips": len(c), "example": c[0]} for e, c in errors.items()]


def _restore_tracks(resolve, tl, before):
    """Put audio tracks back as they were, verified; never lets one failure hide the others."""
    failed = []
    for i, state in before.items():
        try:
            set_track_state(resolve, tl, "audio", i, state)
        except ResolveError:
            failed.append(i)
    if failed:
        raise ResolveError(f"Audio tracks {failed} could not be restored to their previous state: re-enable them",
                           code="track_restore_failed")


def _render_track(resolve, tl, track, out):
    """Render only audio track `track` to a WAV (other audio tracks muted meanwhile), then convert."""
    project = current_project(resolve)
    count = tl.GetTrackCount("audio")
    before = {i: tl.GetIsTrackEnabled("audio", i) for i in range(1, count + 1)}
    page = resolve.GetCurrentPage()
    target = tempfile.mkdtemp(prefix="bsd-render-")
    job = None
    try:
        for i in before:
            tl.SetTrackEnable("audio", i, i == track)
        resolve.OpenPage("deliver")
        # Settings applied during the first Deliver visit do not stick: the first queued job is refused
        # at start. Applying them again and re-queuing works, so try the whole setup up to three times.
        started = False
        for _ in range(3):
            # Resolve lists no codec for the Wave format, so render audio-only into a QuickTime container.
            if not project.SetCurrentRenderFormatAndCodec("mov", "H264"):
                raise ResolveError(f"Cannot select the QuickTime render format; formats: {project.GetRenderFormats()}")
            if not project.SetRenderSettings({"TargetDir": target, "CustomName": "voce", "ExportVideo": False,
                                              "ExportAudio": True, "AudioCodec": "lpcm", "SelectAllFrames": True}):
                raise ResolveError("SetRenderSettings rejected the audio-only settings")
            job = None
            for _ in range(6):  # AddRenderJob returns None until the page has loaded
                job = project.AddRenderJob()
                if job:
                    break
                time.sleep(1)
            if not job:
                raise ResolveError("AddRenderJob returned nothing for the audio-only render")
            started = project.StartRendering([job])
            if started:
                break
            project.DeleteRenderJob(job)
            job = None
            time.sleep(1)
        if not started:
            raise ResolveError("StartRendering refused the audio render three times")
        while project.IsRenderingInProgress():
            time.sleep(1)
        status = project.GetRenderJobStatus(job)
        if status.get("JobStatus") != "Complete":
            raise ResolveError(f"Audio render did not complete: {status}")
        files = glob.glob(os.path.join(target, "voce.*"))
        if not files:
            raise ResolveError(f"Render finished but wrote no audio file in {target}")
        r = subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", files[0], "-ac", "1", "-ar", str(SR), out],
                           capture_output=True)
        if r.returncode:
            raise ResolveError(f"ffmpeg conversion failed: {r.stderr[-200:].decode(errors='ignore')}")
    finally:
        if job:
            project.DeleteRenderJob(job)
        resolve.OpenPage(page)
        _restore_tracks(resolve, tl, before)  # after leaving Deliver: enabling a track is ignored there
        shutil.rmtree(target, ignore_errors=True)


def export_voice(resolve, track=1, out=None, method="auto"):
    """Voice track as 16 kHz mono WAV aligned to the timeline start.

    files: rebuild from source clips with ffmpeg (fast; impossible for multicam/compound clips,
    whose File Path is empty). render: render the track from Resolve (always works, slower).
    auto: files when every clip has a path, else render.
    """
    tl = current_timeline(resolve)
    fps, start = _fps(tl), tl.GetStartFrame()
    items = tl.GetItemListInTrack("audio", track) or []
    if not items:
        raise ResolveError(f"No clips on audio track {track}")
    clips = [x for x in items if x.GetMediaPoolItem()]
    out = out or os.path.join(tempfile.gettempdir(), "bsd-voce.wav")
    pathless = [x for x in clips if not x.GetMediaPoolItem().GetClipProperty("File Path")]
    if method == "auto":
        method = "render" if pathless or not clips else "files"
    if method == "files":
        if pathless:
            raise ResolveError(f"{len(pathless)} of {len(clips)} clips have no source file (multicam/compound): use --method render")
        data, speed, errors = _voice_from_files(tl, clips, fps, start)
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data)
        if errors:
            raise ResolveError(f"ffmpeg failed on some clips: {errors}")
        return {"wav": out, "method": "files", "clips": len(clips), "speed_changed": speed}
    _render_track(resolve, tl, track, out)
    return {"wav": out, "method": "render", "clips": len(items),
            "note": "speed changes are already baked into the render"}


def _python_with_stable_ts():
    candidates = [os.path.expanduser("~/venv-subs/bin/python"), shutil.which("python3"), sys.executable,
                  "/opt/homebrew/bin/python3"] + glob.glob(os.path.expanduser("~/**/bin/python"), recursive=False)
    for py in dict.fromkeys(c for c in candidates if c and os.path.exists(c)):
        if subprocess.run([py, "-c", "import stable_whisper"], capture_output=True).returncode == 0:
            return py
    return None


def transcribe(resolve, wav, model="large-v3-turbo"):
    """Word timings (absolute timeline frames) with stable-ts. Never installs anything."""
    tl = current_timeline(resolve)
    py = _python_with_stable_ts()
    if py is None:
        raise ResolveError(
            "No Python with stable_whisper found. Install (ask first): "
            "python3 -m venv ~/venv-subs && ~/venv-subs/bin/pip install stable-ts"
        )
    r = subprocess.run([py, "-c", _TRANSCRIBE, wav, str(_fps(tl)), str(tl.GetStartFrame()), model],
                       capture_output=True, text=True)
    if r.returncode:
        raise ResolveError(f"stable-ts failed: {r.stderr[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def generate(resolve, max_chars=20, voice_track=1):
    """Resolve's own auto-captions (fallback engine). Mutes every audio track except the voice meanwhile."""
    tl = current_timeline(resolve)
    if not tl.GetTrackCount("subtitle"):
        tl.AddTrack("subtitle")
    need = ("SUBTITLE_LANGUAGE", "AUTO_CAPTION_ITALIAN", "SUBTITLE_CAPTION_PRESET", "AUTO_CAPTION_SUBTITLE_DEFAULT",
            "SUBTITLE_CHARS_PER_LINE", "SUBTITLE_LINE_BREAK", "AUTO_CAPTION_LINE_SINGLE", "SUBTITLE_GAP")
    missing = [n for n in need if not hasattr(resolve, n)]
    if missing:
        raise ResolveError(f"This Resolve lacks constants: {missing}")
    r = resolve
    settings = {
        r.SUBTITLE_LANGUAGE: r.AUTO_CAPTION_ITALIAN,
        r.SUBTITLE_CAPTION_PRESET: r.AUTO_CAPTION_SUBTITLE_DEFAULT,
        r.SUBTITLE_CHARS_PER_LINE: max_chars + 4,
        r.SUBTITLE_LINE_BREAK: r.AUTO_CAPTION_LINE_SINGLE,
        r.SUBTITLE_GAP: 0,
    }
    tracks = range(1, tl.GetTrackCount("audio") + 1)
    before = {i: tl.GetIsTrackEnabled("audio", i) for i in tracks}
    try:
        for i in tracks:
            tl.SetTrackEnable("audio", i, i == voice_track)
        started = tl.CreateSubtitlesFromAudio(settings)
    finally:
        _restore_tracks(resolve, tl, before)
    if not started:
        raise ResolveError("CreateSubtitlesFromAudio refused to start")
    return {"started": True, "next": "poll: subs generate-status"}


def generate_status(resolve):
    return current_project(resolve).GetCreateSubtitlesFromAudioStatus()


def check_voice(resolve, wav, threshold=4):
    """Offset between subtitle starts and voice onsets. Reports only; never changes anything."""
    tl = current_timeline(resolve)
    fps, start = _fps(tl), tl.GetStartFrame()
    with wave.open(wav) as w:
        sr = w.getframerate()
        x = array.array("h", w.readframes(w.getnframes()))
    h = sr // 100
    db = [10 * math.log10(sum(v * v for v in x[i:i + h]) / h / 32768 ** 2 + 1e-12) for i in range(0, len(x) - h, h)]
    thr = sorted(db)[int(len(db) * .98)] - 28
    act = [d > thr for d in db]
    onsets = [i * .01 * fps + start for i in range(15, len(act)) if act[i] and not any(act[i - 15:i])]
    starts = [s.GetStart() for s in _subs(tl)]

    def voice_now(s):
        i = int((s - start) / fps * 100)
        return any(act[i:i + int(3 / fps * 100) + 1])

    diffs, immediate = [], 0
    for o in onsets:
        near = [s for s in starts if abs(s - o) <= 15]
        if near:
            s = min(near, key=lambda v: abs(v - o))
            if s >= o or not voice_now(s):
                diffs.append((s, round(s - o, 1)))
            else:
                immediate += 1
    mags = [abs(v) for _, v in diffs] or [0]
    return {
        "onsets": len(onsets), "voice_immediate": immediate, "measured": len(diffs),
        "mean_abs": round(sum(mags) / len(mags), 2), "max": max(mags),
        "over_threshold": [{"frame": s, "offset": v} for s, v in diffs if abs(v) > threshold],
    }


def _timecode(frame, start, fps):
    ms = round((frame - start) * 1000 / fps)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _srt(rows, start, fps):
    return "\n".join(
        f"{k + 1}\n{_timecode(a, start, fps)} --> {_timecode(e, start, fps)}\n{t}\n" for k, (a, e, t) in enumerate(rows)
    )


def _words_from_subs(zone, bs, be):
    """Fallback word timing (internal engine): spread each sub's duration over its words by length."""
    W, names = [], []
    for it in [x for x in zone if bs <= x.GetStart() < be]:
        words, s, e = it.GetName().split(), it.GetStart(), it.GetEnd()
        total, acc = sum(len(w) + 1 for w in words), 0
        for w in words:
            a = s + (e - s) * acc / total
            acc += len(w) + 1
            W.append((a, s + (e - s) * acc / total))
            names.append(w)
    return W, names


def apply_plan(resolve, plan):
    """Rebuild the subtitle track. plan: timeline, maxc, ranges [[start,end,[[text,n_words],..]],..],
    ext_words [[word,start,end],..]|None, replace, confirmed."""
    tl = current_timeline(resolve)
    project = current_project(resolve)
    pool = project.GetMediaPool()
    declared = plan.get("timeline")
    if declared and tl.GetName() != declared:
        raise ResolveError(f"Open timeline is '{tl.GetName()}', plan is for '{declared}'")
    maxc, replace, ext = plan.get("maxc", 20), plan.get("replace", False), plan.get("ext_words")
    fps, S = _fps(tl), tl.GetStartFrame()
    subs = _subs(tl)
    if subs and not plan.get("confirmed"):
        raise ResolveError(f"The track holds {len(subs)} subtitles; set confirmed=true to rebuild it")
    blocks = sorted(plan["ranges"], key=lambda b: b[0])
    if any(blocks[k][1] > blocks[k + 1][0] for k in range(len(blocks) - 1)):
        raise ResolveError("Overlapping blocks")
    inside = lambda f: any(b[0] <= f < b[1] for b in blocks)
    zone = list(subs) if replace else [x for x in subs if inside(x.GetStart())]
    keep = [(x.GetStart(), x.GetEnd(), x.GetName()) for x in subs if x not in zone]
    if not replace:
        edge = [t for a, e, t in keep if any(a < b[0] < e for b in blocks)] + [
            x.GetName() for x in zone if not any(b[0] <= x.GetStart() and x.GetEnd() <= b[1] for b in blocks)
        ]
        if edge:
            raise ResolveError(f"A block cuts existing subtitles in half; move its edges: {edge}")
    cuts = sorted(it.GetStart() for it in (tl.GetItemListInTrack("video", 1) or []))[1:]
    spans, align = [], []
    for bs, be, lines in blocks:
        if ext:
            words = [x for x in ext if bs - 10 <= x[1] < be]
            W, names = [(a, e) for _, a, e in words], [w for w, _, _ in words]
        else:
            W, names = _words_from_subs(zone, bs, be)
        i = 0
        for t, n in lines:
            align.append(f"{t} <= {' '.join(names[i:i + n])}")
            i += n
        need = sum(n for _, n in lines)
        if need != len(W) or any(n < 1 for _, n in lines):
            raise ResolveError(f"Word count mismatch in block {bs}-{be}: plan {need}, transcript {len(W)}")
        starts, i = [], 0
        for k, (_, n) in enumerate(lines):
            if k == 0:
                starts.append(bs)
            else:
                a, prev_end = W[i][0], W[i - 1][1]
                near = [c for c in cuts if prev_end - 5 <= c <= a + 5 and bs < c < be]
                starts.append(min(near, key=lambda c: abs(c - a)) if near else round(a))
            i += n
        spans += [(starts[k], starts[k + 1] if k + 1 < len(lines) else be, lines[k][0]) for k in range(len(lines))]
    short = [{"text": t, "start": a, "frames": e - a} for a, e, t in spans if e - a < MIN_FRAMES]
    if short:
        raise ResolveError(f"Subtitles under {MIN_FRAMES} frames, merge them with a neighbour: {short}")
    data = sorted(keep + spans)
    if any(data[k][1] > data[k + 1][0] for k in range(len(data) - 1)):
        raise ResolveError("Resulting subtitles overlap")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tl.GetName())
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"  # unique name: Resolve reuses a pool item of the same name
    path = os.path.join(tempfile.gettempdir(), f"{SRT_PREFIX}{safe}-{stamp}.srt")
    bak = os.path.join(tempfile.gettempdir(), f"{SRT_PREFIX}{safe}-{stamp}-backup.srt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_srt(data, S, fps))
    with open(bak, "w", encoding="utf-8") as f:
        f.write(_srt([(x.GetStart(), x.GetEnd(), x.GetName()) for x in subs], S, fps))
    if not tl.GetTrackCount("subtitle"):
        tl.AddTrack("subtitle")
    inout, fail = tl.GetMarkInOut(), None
    try:
        if subs:
            tl.DeleteClips(subs, False)
        tl.SetTrackEnable("subtitle", 1, True)
        tl.SetCurrentTimecode(tl.GetStartTimecode())
        imported = pool.ImportMedia([path])
        if not imported:
            raise ResolveError("Importing the SRT returned nothing")
        pool.AppendToTimeline([imported[0]])
    except Exception as err:  # noqa: BLE001 - roll back to the backup whatever went wrong
        fail = str(err)
        if subs:
            tl.SetCurrentTimecode(tl.GetStartTimecode())
            restored = pool.ImportMedia([bak])
            if restored:
                pool.AppendToTimeline([restored[0]])
    now = [(x.GetStart(), x.GetEnd(), x.GetName()) for x in _subs(tl)]
    video = (inout or {}).get("video", {})
    if "in" in video:
        tl.SetMarkInOut(video["in"], video["out"])
    result = {
        "ok": fail is None and now == data, "fail": fail, "backup": bak, "srt": path, "count": len(now),
        "gaps": [(now[k][1], now[k + 1][0]) for k in range(len(now) - 1) if now[k][1] != now[k + 1][0]],
        "cut_crossings": [(t, c) for a, e, t in now for c in cuts if a < c < e],
        "long_lines": [t for _, _, t in data if len(t) > maxc],
        "end_frame": tl.GetEndFrame(), "align": align,
        "saved": resolve.GetProjectManager().SaveProject(),
    }
    return result


def cleanup(resolve, yes=False):
    """Delete only the SRT items this tool imported (bsd-*.srt) from the media pool."""
    if not yes:
        raise ResolveError("subs cleanup removes media-pool items. Re-run with --yes")
    pool = current_project(resolve).GetMediaPool()
    mine = [c for c in walk_clips(pool.GetRootFolder()) if c.GetName().startswith(SRT_PREFIX) and (c.GetClipProperty("File Name") or "").endswith(".srt")]
    if mine and not pool.DeleteClips(mine):
        raise ResolveError("DeleteClips failed")
    return {"deleted": [c.GetName() for c in mine]}
