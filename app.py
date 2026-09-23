import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import notes as notes_mod

from flask import Flask, abort, jsonify, render_template, request, send_file

try:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.local.json"
DATA_DIR = BASE_DIR / "data"


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_code_dir(config: dict) -> Path:
    env_value = os.environ.get("GIT_VIEWER_CODE_DIR")
    if env_value:
        return Path(env_value)
    if config.get("code_dir"):
        return Path(config["code_dir"])
    return Path("/home/user/code")


def load_keep_awake_script(config: dict):
    if sys.platform != "win32":
        return None
    raw = config.get("keep_awake_script")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


CONFIG = load_config()
# Resolved once so every containment check compares against the same string.
ROOT = load_code_dir(CONFIG).resolve()
KEEP_AWAKE_SCRIPT = load_keep_awake_script(CONFIG)


# --- Paths -----------------------------------------------------------------

def contained(child: Path, root: Path) -> bool:
    """True if child is root itself or lies underneath it.

    A bare str.startswith() accepts a sibling whose name merely extends the
    root's -- 'C:/code/git-viewer-EVIL'.startswith('C:/code/git-viewer') is
    True -- so the separator has to take part in the comparison.
    """
    child_s, root_s = str(child), str(root)
    return child_s == root_s or child_s.startswith(root_s + os.sep)


def has_git_component(rel: Path) -> bool:
    """True if any path segment names the .git directory.

    Windows ignores trailing dots and spaces, so '.git.' reaches the same
    directory as '.git' and has to be caught here too.
    """
    return any(part.rstrip(". ").lower() == ".git" for part in rel.parts)


def resolve_rel(rel: str) -> Path:
    """Map a ROOT-relative request path onto disk. Aborts on escape attempts."""
    norm = rel.replace("\\", "/")
    if ".." in norm.split("/"):
        abort(400)
    full = (ROOT / norm).resolve() if norm else ROOT
    if not contained(full, ROOT):
        abort(403)
    return full


def to_rel(full: Path) -> str:
    return full.relative_to(ROOT).as_posix()


# --- JSON state (data/) ----------------------------------------------------

class JsonStore:
    """A small JSON document under data/, updated under a lock."""

    def __init__(self, name: str, default):
        self.path = DATA_DIR / name
        self.default = default
        self.lock = threading.Lock()

    def read(self):
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return self.default()

    def update(self, mutate):
        """Read, let mutate() change the value in place, write back."""
        with self.lock:
            value = self.read()
            mutate(value)
            DATA_DIR.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


favorites_store = JsonStore("favorites.json", list)      # [dir, ...]
bookmarks_store = JsonStore("bookmarks.json", dict)      # {file: {type, index}}
nav_dirs_store = JsonStore("nav_directions.json", dict)  # {"dir|ext": "reversed"}


def _flatten_bookmarks(old: dict) -> dict:
    # {repo: {path: entry}} -> {"repo/path": entry}
    return {f"{repo}/{path}": entry
            for repo, entries in old.items() for path, entry in entries.items()}


def _rekey_nav_directions(old: dict) -> dict:
    # "repo|dir|ext" -> "repo/dir|ext"
    out = {}
    for key, value in old.items():
        parts = key.split("|")
        if len(parts) == 3:
            repo, d, ext = parts
            key = f"{repo}/{d}|{ext}" if d else f"{repo}|{ext}"
        out[key] = value
    return out


def migrate_legacy_state():
    """Move pre-data/ state files from the project root, converting repo keys."""
    for name, convert in (
        ("favorites.json", lambda v: v),  # already ROOT-relative
        ("bookmarks.json", _flatten_bookmarks),
        ("nav_directions.json", _rekey_nav_directions),
    ):
        old, new = BASE_DIR / name, DATA_DIR / name
        if not old.is_file() or new.exists():
            continue
        try:
            value = convert(json.loads(old.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError, AttributeError):
            app.logger.warning("skipping unreadable legacy %s", name)
            continue
        DATA_DIR.mkdir(exist_ok=True)
        new.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        old.unlink()


migrate_legacy_state()


@app.route("/")
def index():
    return render_template("index.html", code_dir=str(ROOT), root_name=ROOT.name or str(ROOT))


# --- Files -----------------------------------------------------------------

@app.route("/api/tree")
def tree():
    target_dir = resolve_rel(request.args.get("path", ""))
    if not target_dir.is_dir():
        abort(404)

    entries = []
    for item in target_dir.iterdir():
        try:
            mtime = item.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append({
            "name": item.name,
            "path": to_rel(item),
            "type": "tree" if item.is_dir() else "blob",
            "mtime": mtime,
        })
    entries.sort(key=lambda e: (0 if e["type"] == "tree" else 1, e["name"].lower()))
    return jsonify(entries)


TEXT_EXTS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.sh', '.bash', '.ps1', '.psm1', '.psd1',
    '.json', '.yml', '.yaml', '.xml', '.html', '.htm', '.css', '.toml', '.md', '.txt',
    '.cfg', '.ini', '.conf', '.env', '.service', '.timer', '.csv', '.sql',
    '.rb', '.go', '.rs', '.java', '.c', '.h', '.cpp', '.hpp', '.vue', '.svelte',
    '.gitignore', '.dockerignore', '.dockerfile', '.makefile', '.srt', '',
}
HEIF_EXTS = {'.heic', '.heif'}


@app.route("/api/blob")
def blob():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    file_full = resolve_rel(path)
    if not file_full.is_file():
        abort(404)

    filename = file_full.name
    ext = ('.' + filename.rsplit('.', 1)[1]).lower() if '.' in filename else ''

    # Text files: return JSON with content and ext
    if ext in TEXT_EXTS:
        try:
            content = file_full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            abort(500)
        return jsonify({"content": content, "path": path, "ext": ext})

    # HEIC/HEIF: convert to JPEG since most browsers can't render natively
    if ext in HEIF_EXTS:
        if not HEIF_SUPPORT:
            abort(500, description="HEIC support not installed (pip install pillow-heif)")
        try:
            img = Image.open(file_full)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return send_file(buf, mimetype='image/jpeg')
        except Exception:
            abort(500)

    # Binary files (images, PDFs, audio, office docs, archives, etc.): return raw bytes
    mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return send_file(file_full, mimetype=mime, download_name=filename)


@app.route("/raw/<path:relpath>")
def raw(relpath):
    """Serve a file inline with its real content type.

    Unlike /api/blob (which wraps text in JSON), this streams the raw bytes so
    the browser renders HTML as a page. The URL mirrors the on-disk layout
    (ROOT/<relpath>), so a page's relative CSS/JS/image references resolve
    against sibling /raw/ URLs.
    """
    file_full = resolve_rel(relpath)
    if not file_full.is_file():
        abort(404)
    if has_git_component(file_full.relative_to(ROOT)):
        abort(404)
    mime = mimetypes.guess_type(str(file_full))[0] or "application/octet-stream"
    return send_file(file_full, mimetype=mime)


@app.route("/api/blob", methods=["PUT"])
def blob_write():
    data = request.get_json()
    if not data:
        abort(400)
    path = data.get("path", "")
    file_content = data.get("content")
    if file_content is None or not path:
        abort(400)

    file_full = resolve_rel(path)
    if not file_full.is_file():
        abort(404)

    # Git metadata is off limits: hooks and config would run the next time
    # the user invokes git in that repository.
    if has_git_component(file_full.relative_to(ROOT)):
        abort(403)

    try:
        file_full.write_text(file_content, encoding="utf-8")
    except Exception:
        abort(500)
    return jsonify({"ok": True})


# --- Favorites / bookmarks / nav direction --------------------------------

@app.route("/api/favorites")
def favorites():
    return jsonify(favorites_store.read())


@app.route("/api/favorites", methods=["POST"])
def add_favorite():
    data = request.get_json()
    if not data or "path" not in data:
        abort(400)
    path = data["path"]
    favorites_store.update(lambda favs: path in favs or favs.append(path))
    return jsonify({"ok": True})


@app.route("/api/favorites", methods=["DELETE"])
def remove_favorite():
    data = request.get_json()
    if not data or "path" not in data:
        abort(400)
    path = data["path"]
    favorites_store.update(lambda favs: path in favs and favs.remove(path))
    return jsonify({"ok": True})


def _bookmark_path(path: str) -> str:
    if not path:
        abort(400)
    resolve_rel(path)
    return path


@app.route("/api/bookmark")
def bookmark_get():
    path = _bookmark_path(request.args.get("path", ""))
    return jsonify(bookmarks_store.read().get(path) or {})


@app.route("/api/bookmark", methods=["PUT"])
def bookmark_set():
    data = request.get_json()
    if not data:
        abort(400)
    btype = data.get("type", "")
    index = data.get("index")
    if btype not in ("md", "text") or not isinstance(index, int):
        abort(400)
    path = _bookmark_path(data.get("path", ""))
    bookmarks_store.update(lambda bms: bms.__setitem__(path, {"type": btype, "index": index}))
    return jsonify({"ok": True})


@app.route("/api/bookmark", methods=["DELETE"])
def bookmark_remove():
    data = request.get_json()
    if not data:
        abort(400)
    path = _bookmark_path(data.get("path", ""))
    bookmarks_store.update(lambda bms: bms.pop(path, None))
    return jsonify({"ok": True})


@app.route("/api/nav_direction")
def nav_direction_get():
    return jsonify(nav_dirs_store.read())


@app.route("/api/nav_direction", methods=["POST"])
def nav_direction_set():
    data = request.get_json()
    if not data or "key" not in data:
        abort(400)
    key = data["key"]
    direction = data.get("direction", "normal")

    def apply(dirs):
        if direction == "normal":
            dirs.pop(key, None)
        else:
            dirs[key] = direction
    nav_dirs_store.update(apply)
    return jsonify({"ok": True})


# --- Playback --------------------------------------------------------------

PLAYBACK_MIN_SECONDS = 15


def _playback_log_path(rel_path: str) -> Path:
    if not rel_path:
        abort(400)
    audio_full = resolve_rel(rel_path)
    if not audio_full.is_file():
        abort(404)
    return audio_full.parent / (audio_full.name + ".playback.jsonl")


@app.route("/api/playback-log")
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


@app.route("/api/playback-log", methods=["POST"])
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


@app.route("/api/playback-overview")
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


@app.route("/api/keep-awake", methods=["POST"])
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
            app.logger.warning("keep-awake.ps1 hung, killed")
    except (OSError, BrokenPipeError):
        app.logger.warning("keep-awake.ps1 spawn failed", exc_info=True)
    return ("", 204)


# --- Notes -----------------------------------------------------------------

NOTES_SUFFIX = ".notes.md"
SNAPSHOT_TEXT_LIMIT = 50 * 1024  # 50 KB


def _notes_kind_for_path(path: str) -> str:
    ext = ('.' + path.rsplit('.', 1)[1]).lower() if '.' in path else ''
    if ext == '.md':
        return 'md_sentence'
    if ext == '.srt':
        return 'srt'
    return 'lines'


def _notes_paths(path: str):
    """(target file, its sidecar .notes.md) for a ROOT-relative target path."""
    if not path or path.endswith(NOTES_SUFFIX):
        abort(400)
    target_full = resolve_rel(path)
    return target_full, target_full.parent / (target_full.name + NOTES_SUFFIX)


def _promote_to_unresolved(sec, reason):
    body = sec.body or ""
    if "未解決理由:" not in body.split("\n", 1)[0]:
        sec.body = f"未解決理由: {reason}\n\n{body}".rstrip() + "\n"


def _resolve_doc_server_side(doc, target_path, kind: str) -> bool:
    """Resolve text/code or SRT anchors in place. Returns True if doc mutated."""
    if not target_path.is_file():
        if doc.resolved:
            for sec in doc.resolved:
                _promote_to_unresolved(sec, "対象ファイルが存在しない")
                doc.unresolved.append(sec)
            doc.resolved = []
            return True
        return False

    mutated = False
    file_text = target_path.read_text(encoding="utf-8", errors="replace")

    if kind == 'srt':
        cues = notes_mod.parse_srt_cues(file_text)
        new_resolved = []
        for sec in doc.resolved:
            if sec.anchor["kind"] != "srt":
                new_resolved.append(sec)
                continue
            r = notes_mod.resolve_srt_anchor(cues, sec.anchor)
            if r.resolved:
                new_resolved.append(sec)
            else:
                _promote_to_unresolved(sec, r.reason or "SRT タイムコード不一致")
                doc.unresolved.append(sec)
                mutated = True
        doc.resolved = new_resolved
    elif kind == 'lines':
        new_resolved = []
        for sec in doc.resolved:
            if sec.anchor["kind"] != "lines":
                new_resolved.append(sec)
                continue
            r = notes_mod.resolve_lines_anchor(file_text, sec.anchor, sec.snapshot)
            if not r.resolved:
                _promote_to_unresolved(sec, r.reason or "行範囲解決失敗")
                doc.unresolved.append(sec)
                mutated = True
                continue
            if r.relocated:
                sec.anchor = {"kind": "lines", "start": r.start, "end": r.end}
                mutated = True
            new_resolved.append(sec)
        doc.resolved = new_resolved
    elif kind == 'md_sentence':
        # 過去のクライアントバグで snapshot.text 末尾に marker の 💬 が混入していたケースを修復
        for sec in list(doc.resolved) + list(doc.unresolved):
            if sec.anchor.get("kind") != "md_sentence":
                continue
            if not isinstance(sec.snapshot, dict):
                continue
            text = sec.snapshot.get("text", "")
            if not isinstance(text, str):
                continue
            cleaned = text.rstrip()
            while cleaned.endswith("💬"):
                cleaned = cleaned[:-len("💬")].rstrip()
            if cleaned != text:
                sec.snapshot["text"] = cleaned
                mutated = True

    return mutated


def _validate_anchor(a):
    if not isinstance(a, dict) or "kind" not in a:
        return False
    k = a["kind"]
    if k == "lines":
        return all(isinstance(a.get(x), int) for x in ("start", "end")) and a["start"] >= 1 and a["end"] >= a["start"]
    if k == "md_sentence":
        return isinstance(a.get("index"), int) and a["index"] >= 0
    if k == "srt":
        return all(isinstance(a.get(x), int) for x in ("start_ms", "end_ms"))
    return False


def _validate_snapshot(s):
    if s is None:
        return True
    if not isinstance(s, dict):
        return False
    if isinstance(s.get("text"), str) and len(s["text"]) > SNAPSHOT_TEXT_LIMIT:
        return False
    for k in ("created_at", "updated_at"):
        if k in s and not isinstance(s[k], str):
            return False
    return True


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _find_section_by_anchor(doc, anchor):
    key = notes_mod._anchor_key(anchor)
    for sec in doc.resolved:
        if notes_mod._anchor_key(sec.anchor) == key:
            return sec
    for sec in doc.unresolved:
        if notes_mod._anchor_key(sec.anchor) == key:
            return sec
    return None


def _check_mtime(notes_full, expected):
    actual = notes_full.stat().st_mtime if notes_full.is_file() else None
    if expected is None:
        return actual is None
    if actual is None:
        return False
    return abs(actual - float(expected)) < 1e-6


@app.route("/api/notes")
def notes_get():
    path = request.args.get("path", "")
    target_full, notes_full = _notes_paths(path)
    kind = _notes_kind_for_path(path)

    if not notes_full.is_file():
        return jsonify({"mtime": None, "kind": kind, "resolved": [], "unresolved": []})

    doc = notes_mod.load_notes(notes_full)
    mutated = _resolve_doc_server_side(doc, target_full, kind)
    if mutated:
        notes_mod.save_notes(notes_full, doc)
    mtime = notes_full.stat().st_mtime

    def _extract_reason(body):
        first = (body or "").split("\n", 1)[0]
        if first.startswith("未解決理由:"):
            return first[len("未解決理由:"):].strip()
        return ""

    def section_to_dict(sec, *, unresolved=False, client_resolve=False):
        d = {
            "anchor": sec.anchor,
            "snapshot": sec.snapshot,
            "body": sec.body,
        }
        if not unresolved:
            d["relocated"] = False
            if client_resolve:
                d["client_resolve"] = True
        else:
            d["reason"] = _extract_reason(sec.body) or "アンカーが解決できない"
        return d

    is_md = (kind == 'md_sentence')
    resolved_out = [section_to_dict(s, client_resolve=is_md) for s in doc.resolved]
    unresolved_out = [section_to_dict(s, unresolved=True) for s in doc.unresolved]

    return jsonify({
        "mtime": mtime,
        "kind": kind,
        "resolved": resolved_out,
        "unresolved": unresolved_out,
    })


@app.route("/api/notes", methods=["PUT"])
def notes_put():
    data = request.get_json(silent=True) or {}
    anchor = data.get("anchor")
    snapshot = data.get("snapshot")
    body = data.get("body", "")
    if_match = data.get("if_match_mtime", None)
    if not isinstance(body, str):
        abort(400)
    if not _validate_anchor(anchor) or not _validate_snapshot(snapshot):
        abort(400)
    target_full, notes_full = _notes_paths(data.get("path", ""))

    if not _check_mtime(notes_full, if_match):
        abort(409)

    if notes_full.is_file():
        doc = notes_mod.load_notes(notes_full)
    else:
        doc = notes_mod.NotesDoc(title=f"Notes for {target_full.name}")
    if doc.title is None:
        doc.title = f"Notes for {target_full.name}"

    if isinstance(snapshot, dict):
        now = _now_iso()
        existing = _find_section_by_anchor(doc, anchor)
        prior_created = None
        if existing is not None and isinstance(existing.snapshot, dict):
            prior_created = existing.snapshot.get("created_at")
        snapshot = dict(snapshot)
        snapshot["created_at"] = prior_created if isinstance(prior_created, str) else now
        snapshot["updated_at"] = now

    section = notes_mod.NotesSection(anchor=anchor, snapshot=snapshot, body=body)
    notes_mod.upsert_section(doc, section)
    notes_mod.save_notes(notes_full, doc)

    return jsonify({"mtime": notes_full.stat().st_mtime})


@app.route("/api/notes", methods=["DELETE"])
def notes_delete():
    data = request.get_json(silent=True) or {}
    anchor = data.get("anchor")
    if_match = data.get("if_match_mtime", None)
    if not _validate_anchor(anchor):
        abort(400)
    _, notes_full = _notes_paths(data.get("path", ""))
    if not notes_full.is_file():
        return jsonify({"mtime": None})
    if not _check_mtime(notes_full, if_match):
        abort(409)
    doc = notes_mod.load_notes(notes_full)
    notes_mod.delete_section(doc, anchor)
    if not doc.resolved and not doc.unresolved:
        try:
            notes_full.unlink()
        except OSError:
            pass
        return jsonify({"mtime": None})
    notes_mod.save_notes(notes_full, doc)
    return jsonify({"mtime": notes_full.stat().st_mtime})


@app.route("/api/notes/relocate", methods=["POST"])
def notes_relocate():
    data = request.get_json(silent=True) or {}
    old_anchor = data.get("old_anchor")
    new_anchor = data.get("new_anchor")
    new_heading_text = data.get("new_heading_text", "")
    if_match = data.get("if_match_mtime", None)
    if not _validate_anchor(old_anchor) or not _validate_anchor(new_anchor):
        abort(400)
    if old_anchor["kind"] != "md_sentence" or new_anchor["kind"] != "md_sentence":
        abort(400)
    if not isinstance(new_heading_text, str):
        abort(400)
    _, notes_full = _notes_paths(data.get("path", ""))
    if not notes_full.is_file():
        abort(404)
    if not _check_mtime(notes_full, if_match):
        abort(409)

    doc = notes_mod.load_notes(notes_full)
    new_anchor_full = dict(new_anchor)
    new_anchor_full["heading_text"] = new_heading_text
    target_key = notes_mod._anchor_key(old_anchor)
    found = False
    for sec in doc.resolved:
        if notes_mod._anchor_key(sec.anchor) == target_key:
            sec.anchor = new_anchor_full
            found = True
            break
    if not found:
        abort(404)
    notes_mod.save_notes(notes_full, doc)
    return jsonify({"mtime": notes_full.stat().st_mtime})


@app.route("/api/notes/index")
def notes_index():
    base_full = resolve_rel(request.args.get("path", ""))
    if not base_full.is_dir():
        return jsonify({"files": {}})
    out = {}
    for entry in base_full.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.endswith(NOTES_SUFFIX):
            continue
        target_name = entry.name[: -len(NOTES_SUFFIX)]
        try:
            doc = notes_mod.load_notes(entry)
        except OSError:
            continue
        count = len(doc.resolved) + len(doc.unresolved)
        if count > 0:
            out[target_name] = count
    return jsonify({"files": out})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5125, debug=False)
