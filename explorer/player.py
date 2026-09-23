"""Audio playback logs, the listening-progress overview, and keep-awake pings."""
import json
import os
import re
import subprocess
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request

from . import notes as notes_mod
from .config import KEEP_AWAKE_SCRIPT
from .paths import resolve_rel, to_rel

bp = Blueprint("player", __name__)


PLAYBACK_MIN_SECONDS = 15


def _playback_log_path(rel_path: str) -> Path:
    if not rel_path:
        abort(400)
    audio_full = resolve_rel(rel_path)
    if not audio_full.is_file():
        abort(404)
    return audio_full.parent / (audio_full.name + ".playback.jsonl")


@bp.route("/api/playback-log")
def playback_log_list():
    log_file = _playback_log_path(request.args.get("path", ""))
    if not log_file.is_file():
        return jsonify([])
    records = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return jsonify(records)


@bp.route("/api/playback-log", methods=["POST"])
def playback_log_add():
    data = request.get_json()
    if not data:
        abort(400)
    start_sec = data.get("start_sec")
    end_sec = data.get("end_sec")
    started_at = data.get("started_at", "")
    ended_at = data.get("ended_at", "")
    if not isinstance(start_sec, (int, float)) or not isinstance(end_sec, (int, float)):
        abort(400)
    if end_sec - start_sec < PLAYBACK_MIN_SECONDS:
        return jsonify({"ok": True, "skipped": True})
    log_file = _playback_log_path(data.get("path", ""))
    record = {
        "started_at": str(started_at),
        "ended_at": str(ended_at),
        "start_sec": round(float(start_sec), 2),
        "end_sec": round(float(end_sec), 2),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


# Directories never worth descending into when hunting for audio.
SCAN_SKIP_DIRS = {"archived", "node_modules", "__pycache__", "venv", "site-packages"}


def _iter_output_mp3(base_full: Path):
    """Yield every `output.mp3` under base_full, pruning hidden/archived/vendor dirs."""
    for root, dirs, files in os.walk(base_full):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
        if "output.mp3" in files:
            yield Path(root) / "output.mp3"


def _srt_duration_sec(srt_path: Path):
    """Total length (seconds) from the last cue of a sibling SRT, or None."""
    if not srt_path.is_file():
        return None
    try:
        text = srt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cues = notes_mod.parse_srt_cues(text)
    if not cues:
        return None
    return max(c["end_ms"] for c in cues) / 1000.0


def _coverage_from_log(log_file: Path):
    """Aggregate a `.playback.jsonl`: (covered_sec, reach_sec, sessions, last_played).

    covered_sec = length of the union of played [start,end] intervals (dedupes
    re-listens and skips); reach_sec = furthest position reached.
    """
    if not log_file.is_file():
        return 0.0, 0.0, 0, ""
    intervals = []
    reach = 0.0
    last = ""
    sessions = 0
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = rec.get("start_sec")
        e = rec.get("end_sec")
        if not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            continue
        if e < s:
            s, e = e, s
        intervals.append((float(s), float(e)))
        reach = max(reach, float(e))
        sessions += 1
        ended = rec.get("ended_at") or rec.get("started_at") or ""
        if isinstance(ended, str) and ended > last:
            last = ended
    covered = 0.0
    cur_s = cur_e = None
    for s, e in sorted(intervals):
        if cur_e is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            covered += cur_e - cur_s
            cur_s, cur_e = s, e
    if cur_e is not None:
        covered += cur_e - cur_s
    return covered, reach, sessions, last


@bp.route("/api/playback-overview")
def playback_overview():
    """Aggregate listening progress for every output.mp3 under ?path= (default:
    the whole root). One row per audio: coverage/reach/last-played, so a caller
    can render a cross-project "what have I listened to, how far" dashboard."""
    base_full = resolve_rel(request.args.get("path", ""))
    items = []
    if base_full.is_dir():
        for mp3 in _iter_output_mp3(base_full):
            folder = mp3.parent
            log_file = folder / (mp3.name + ".playback.jsonl")
            covered, reach, sessions, last = _coverage_from_log(log_file)
            duration = _srt_duration_sec(folder / "output.srt")
            if duration is None and reach > 0:
                duration = reach
            try:
                mtime = mp3.stat().st_mtime
            except OSError:
                mtime = 0.0
            items.append({
                "dir": to_rel(folder),
                "audio": to_rel(mp3),
                "name": folder.name,
                "duration_sec": round(duration, 1) if duration else None,
                "covered_sec": round(covered, 1),
                "reach_sec": round(reach, 1),
                "sessions": sessions,
                "last_played": last,
                "mtime": mtime,
            })
    return jsonify(items)


@bp.route("/api/keep-awake", methods=["POST"])
def keep_awake():
    if KEEP_AWAKE_SCRIPT is None:
        return ("", 204)
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", client_id):
        abort(400)
    session_id = f"git-viewer-{client_id}"
    payload = json.dumps({"session_id": session_id}).encode("utf-8")
    creationflags = (
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    try:
        proc = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(KEEP_AWAKE_SCRIPT),
                "-Minutes",
                "2",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            proc.communicate(input=payload, timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            current_app.logger.warning("keep-awake.ps1 hung, killed")
    except (OSError, BrokenPipeError):
        current_app.logger.warning("keep-awake.ps1 spawn failed", exc_info=True)
    return ("", 204)
