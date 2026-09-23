import io
import json
import mimetypes
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import notes as notes_mod

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

try:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

app = Flask(__name__)

CONFIG_FILE = Path(__file__).parent / "config.local.json"


def load_code_dir() -> Path:
    env_value = os.environ.get("GIT_VIEWER_CODE_DIR")
    if env_value:
        return Path(env_value)
    if CONFIG_FILE.is_file():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if config.get("code_dir"):
                return Path(config["code_dir"])
        except (json.JSONDecodeError, OSError):
            pass
    return Path("/home/user/code")


def load_keep_awake_script():
    if sys.platform != "win32" or not CONFIG_FILE.is_file():
        return None
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    raw = config.get("keep_awake_script")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


CODE_DIR = load_code_dir()
KEEP_AWAKE_SCRIPT = load_keep_awake_script()
FAVORITES_FILE = Path(__file__).parent / "favorites.json"


def read_favorites() -> list:
    if FAVORITES_FILE.is_file():
        try:
            return json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def write_favorites(favs: list):
    FAVORITES_FILE.write_text(json.dumps(favs, ensure_ascii=False), encoding="utf-8")


BOOKMARKS_FILE = Path(__file__).parent / "bookmarks.json"


def read_bookmarks() -> dict:
    if BOOKMARKS_FILE.is_file():
        try:
            return json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_bookmarks(bms: dict):
    BOOKMARKS_FILE.write_text(json.dumps(bms, ensure_ascii=False), encoding="utf-8")


NAV_DIRECTIONS_FILE = Path(__file__).parent / "nav_directions.json"


def read_nav_directions() -> dict:
    if NAV_DIRECTIONS_FILE.is_file():
        try:
            return json.loads(NAV_DIRECTIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_nav_directions(d: dict):
    NAV_DIRECTIONS_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def valid_repo(name: str) -> Path:
    """Validate repo name and return path. Abort 404 if not a git repo.

    Accepts 'repo' (direct child) or 'category/repo' (two-level).
    """
    if "\\" in name or name.startswith(".") or ".." in name:
        abort(400)
    parts = name.split("/")
    if len(parts) > 2 or any(p.startswith(".") for p in parts):
        abort(400)
    repo_path = (CODE_DIR / name).resolve()
    if not contained(repo_path, CODE_DIR.resolve()):
        abort(403)
    if not (repo_path / ".git").is_dir():
        abort(404)
    return repo_path


# A repo's own config must never make git run a program we did not choose.
# .git/config is reachable through PUT /api/blob, so diff.external would
# otherwise turn GET /api/diff into arbitrary code execution, and fsmonitor
# does the same for status. Command-line -c beats repo config. The diff call
# sites additionally pass --no-ext-diff/--no-textconv, because .gitattributes
# can name a textconv driver whose key cannot be enumerated here.
GIT_SAFE_CONFIG = ["-c", "diff.external=", "-c", "core.fsmonitor=false"]

# Belt and braces for the diff family, which is the only place that consults
# external diff drivers and .gitattributes textconv filters.
NO_EXT_DIFF = ["--no-ext-diff", "--no-textconv"]


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


def git(repo_path: Path, *args: str, default: str = "") -> str:
    """Run a git command and return stdout. Returns default on error."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + GIT_SAFE_CONFIG + list(args),
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            return default
        return result.stdout.decode("utf-8", errors="replace").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return default


def get_repo_info(repo_path: Path) -> dict:
    """Gather status info for a single repo."""
    name = repo_path.name

    # One porcelain v2 call yields branch, upstream, ahead/behind, and changes.
    # On Windows, each git invocation costs ~60-80ms, so fewer calls matters.
    status_raw = git(repo_path, "status", "--porcelain=v2", "--branch")
    branch = "unknown"
    changes = 0
    has_upstream = False
    ahead = behind = 0
    for line in status_raw.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):]
        elif line.startswith("# branch.upstream "):
            has_upstream = True
        elif line.startswith("# branch.ab "):
            parts = line[len("# branch.ab "):].split()
            if len(parts) == 2:
                try:
                    ahead = int(parts[0].lstrip("+"))
                    behind = int(parts[1].lstrip("-"))
                except ValueError:
                    pass
        elif line and not line.startswith("#"):
            changes += 1

    if has_upstream:
        if ahead > 0 and behind > 0:
            remote_status = f"ahead {ahead}, behind {behind}"
        elif ahead > 0:
            remote_status = f"ahead {ahead}"
        elif behind > 0:
            remote_status = f"behind {behind}"
        else:
            remote_status = "up to date"
    else:
        remote_status = "no remote"

    log_line = git(repo_path, "log", "-1", "--format=%h\t%s\t%aI")
    if log_line and "\t" in log_line:
        parts = log_line.split("\t", 2)
        latest_commit, latest_message, latest_time = parts[0], parts[1], parts[2]
    else:
        latest_commit, latest_message, latest_time = "", "", ""

    return {
        "name": name,
        "branch": branch,
        "changes": changes,
        "latest_commit": latest_commit,
        "latest_message": latest_message,
        "latest_time": latest_time,
        "remote_status": remote_status,
    }


@app.route("/")
def index():
    return render_template("index.html", code_dir=str(CODE_DIR.resolve()))


@app.route("/api/check")
def check():
    """Lightweight endpoint: return HEAD hash + change count for polling."""
    repo_name = request.args.get("repo", "")
    if not repo_name:
        return jsonify({"head": "", "changes": 0})
    repo_path = valid_repo(repo_name)
    head = git(repo_path, "rev-parse", "HEAD", default="")
    status = git(repo_path, "status", "--porcelain")
    changes = len(status.splitlines()) if status else 0
    return jsonify({"head": head, "changes": changes})


@app.route("/api/info")
def info():
    """Single-repo info (same shape as one entry in /api/repos)."""
    name = request.args.get("repo", "")
    repo_path = valid_repo(name)
    data = get_repo_info(repo_path)
    if "/" in name:
        data["name"] = name
        data["category"] = name.split("/", 1)[0]
    else:
        data["category"] = ""
    return jsonify(data)


@app.route("/api/repos")
def repos():
    # Collect all repo paths first, then fetch info in parallel
    repo_entries = []  # (path, category)
    for item in sorted(CODE_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not item.is_dir():
            continue
        if (item / ".git").is_dir():
            repo_entries.append((item, ""))
        else:
            category = item.name
            for sub in sorted(item.iterdir(), key=lambda p: p.name.lower()):
                if sub.is_dir() and (sub / ".git").is_dir():
                    repo_entries.append((sub, category))

    def fetch_info(entry):
        path, category = entry
        info = get_repo_info(path)
        if category:
            info["name"] = f"{category}/{path.name}"
        info["category"] = category
        return info

    with ThreadPoolExecutor(max_workers=8) as pool:
        repo_list = list(pool.map(fetch_info, repo_entries))
    return jsonify(repo_list)


@app.route("/api/log")
def log():
    name = request.args.get("repo", "")
    limit = min(int(request.args.get("limit", "50")), 200)
    repo_path = valid_repo(name)

    raw = git(repo_path, "log", f"-{limit}",
              "--format=%h\t%s\t%aI",
              "--shortstat")

    commits = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "\t" not in line:
            i += 1
            continue
        parts = line.split("\t", 2)
        entry = {
            "hash": parts[0],
            "message": parts[1],
            "time": parts[2],
            "files_changed": 0,
            "insertions": 0,
            "deletions": 0,
        }
        i += 1
        while i < len(lines) and lines[i] == "":
            i += 1
        if i < len(lines) and "file" in lines[i] and "\t" not in lines[i]:
            stat = lines[i]
            fc = re.search(r"(\d+) file", stat)
            ins = re.search(r"(\d+) insertion", stat)
            dels = re.search(r"(\d+) deletion", stat)
            entry["files_changed"] = int(fc.group(1)) if fc else 0
            entry["insertions"] = int(ins.group(1)) if ins else 0
            entry["deletions"] = int(dels.group(1)) if dels else 0
            i += 1
        commits.append(entry)

    return jsonify(commits)


@app.route("/api/diff")
def diff():
    name = request.args.get("repo", "")
    commit = request.args.get("commit", "")
    repo_path = valid_repo(name)

    file_path = request.args.get("file", "")
    if file_path and ("/" == file_path[0] or ".." in file_path):
        abort(400)

    if commit:
        if not commit.replace("-", "").isalnum() or len(commit) > 40:
            abort(400)
        parent = git(repo_path, "rev-parse", "--verify", f"{commit}^", default="")
        if parent:
            cmd = ["diff", *NO_EXT_DIFF, f"{commit}^..{commit}"]
            if file_path:
                cmd += ["--", file_path]
            diff_text = git(repo_path, *cmd)
            files_raw = git(repo_path, "diff", *NO_EXT_DIFF, "--name-only", f"{commit}^..{commit}")
        else:
            cmd = ["diff-tree", "-p", *NO_EXT_DIFF, "--root", commit]
            if file_path:
                cmd += ["--", file_path]
            raw = git(repo_path, *cmd)
            diff_text = raw.split("\n", 1)[1] if "\n" in raw else raw
            files_raw = git(repo_path, "diff-tree", "--no-commit-id", *NO_EXT_DIFF, "--name-only", "-r", "--root", commit)
    else:
        file_args = ["--", file_path] if file_path else []
        unstaged = git(repo_path, "diff", *NO_EXT_DIFF, *file_args)
        staged = git(repo_path, "diff", *NO_EXT_DIFF, "--cached", *file_args)
        diff_text = staged + ("\n" if staged and unstaged else "") + unstaged
        files_raw = git(repo_path, "diff", *NO_EXT_DIFF, "--name-only") + "\n" + git(repo_path, "diff", *NO_EXT_DIFF, "--name-only", "--cached")

    files = sorted(set(f for f in files_raw.splitlines() if f))

    return jsonify({"diff": diff_text, "files": files})


@app.route("/api/branches")
def branches():
    name = request.args.get("repo", "")
    repo_path = valid_repo(name)

    raw = git(repo_path, "branch", "-a", "--format=%(refname:short)\t%(HEAD)\t%(upstream:short)\t%(objectname:short)")
    result = []
    for line in raw.splitlines():
        if not line:
            continue
        parts = line.split("\t", 3)
        result.append({
            "name": parts[0],
            "current": parts[1].strip() == "*",
            "upstream": parts[2] if len(parts) > 2 else "",
            "hash": parts[3] if len(parts) > 3 else "",
        })
    return jsonify(result)


@app.route("/api/tree")
def tree():
    name = request.args.get("repo", "")
    path = request.args.get("path", "")
    repo_path = valid_repo(name)

    if ".." in path:
        abort(400)

    target_dir = (repo_path / path).resolve() if path else repo_path
    if not contained(target_dir, repo_path.resolve()):
        abort(403)
    if not target_dir.is_dir():
        abort(404)

    entries = []
    for item in target_dir.iterdir():
        rel = str(item.relative_to(repo_path)).replace("\\", "/")
        try:
            mtime = item.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append({
            "name": item.name,
            "path": rel,
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
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico', '.bmp', '.avif', '.heic', '.heif'}
HEIF_EXTS = {'.heic', '.heif'}
PDF_EXTS = {'.pdf'}


@app.route("/api/blob")
def blob():
    name = request.args.get("repo", "")
    path = request.args.get("path", "")
    repo_path = valid_repo(name)

    if not path or ".." in path:
        abort(400)

    file_full = (repo_path / path).resolve()
    if not contained(file_full, repo_path.resolve()):
        abort(403)
    if not file_full.is_file():
        abort(404)

    filename = path.replace("\\", "/").rsplit("/", 1)[-1]
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
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    return send_file(file_full, mimetype=mime, download_name=path.rsplit('/', 1)[-1])


def _in_repo(file_full: Path, code_root: Path) -> bool:
    """True if an ancestor of file_full (up to code_root) is a git work tree."""
    parent = file_full.parent
    while True:
        if (parent / ".git").is_dir():
            return True
        if parent == code_root or parent.parent == parent:
            return False
        parent = parent.parent


@app.route("/raw/<path:relpath>")
def raw(relpath):
    """Serve a repo file inline with its real content type.

    Unlike /api/blob (which wraps text in JSON), this streams the raw bytes so
    the browser renders HTML as a page. The URL mirrors the on-disk layout
    (CODE_DIR/<relpath>), so a page's relative CSS/JS/image references resolve
    against sibling /raw/ URLs. Read-only preview of local repos.
    """
    norm = relpath.replace("\\", "/")
    if ".." in norm.split("/"):
        abort(400)
    code_root = CODE_DIR.resolve()
    file_full = (CODE_DIR / norm).resolve()
    if not contained(file_full, code_root):
        abort(403)
    if not file_full.is_file():
        abort(404)
    if any(part == ".git" for part in file_full.relative_to(code_root).parts):
        abort(404)
    if not _in_repo(file_full, code_root):
        abort(404)

    mime = mimetypes.guess_type(str(file_full))[0] or "application/octet-stream"
    return send_file(file_full, mimetype=mime)


@app.route("/api/blob", methods=["PUT"])
def blob_write():
    data = request.get_json()
    if not data:
        abort(400)
    name = data.get("repo", "")
    path = data.get("path", "")
    file_content = data.get("content")
    if file_content is None:
        abort(400)

    repo_path = valid_repo(name)

    if not path or ".." in path:
        abort(400)

    file_full = (repo_path / path).resolve()
    if not contained(file_full, repo_path.resolve()):
        abort(403)
    if not file_full.is_file():
        abort(404)

    # Git metadata is off limits. Writing .git/config would let a caller set
    # diff.external and have the next /api/diff run a program of their choosing,
    # and hooks are an execution vector in their own right.
    if has_git_component(file_full.relative_to(repo_path.resolve())):
        abort(403)

    try:
        file_full.write_text(file_content, encoding="utf-8")
    except Exception:
        abort(500)
    return jsonify({"ok": True})


@app.route("/api/favorites")
def favorites():
    return jsonify(read_favorites())


@app.route("/api/favorites", methods=["POST"])
def add_favorite():
    data = request.get_json()
    if not data or "path" not in data:
        abort(400)
    path = data["path"]
    favs = read_favorites()
    if path not in favs:
        favs.append(path)
        write_favorites(favs)
    return jsonify({"ok": True})


@app.route("/api/favorites", methods=["DELETE"])
def remove_favorite():
    data = request.get_json()
    if not data or "path" not in data:
        abort(400)
    path = data["path"]
    favs = read_favorites()
    if path in favs:
        favs.remove(path)
        write_favorites(favs)
    return jsonify({"ok": True})


@app.route("/api/bookmark")
def bookmark_get():
    name = request.args.get("repo", "")
    path = request.args.get("path", "")
    valid_repo(name)
    if not path or ".." in path:
        abort(400)
    bms = read_bookmarks()
    entry = bms.get(name, {}).get(path)
    return jsonify(entry or {})


@app.route("/api/bookmark", methods=["PUT"])
def bookmark_set():
    data = request.get_json()
    if not data:
        abort(400)
    name = data.get("repo", "")
    path = data.get("path", "")
    btype = data.get("type", "")
    index = data.get("index")
    if btype not in ("md", "text") or not isinstance(index, int):
        abort(400)
    valid_repo(name)
    if not path or ".." in path:
        abort(400)
    bms = read_bookmarks()
    if name not in bms:
        bms[name] = {}
    bms[name][path] = {"type": btype, "index": index}
    write_bookmarks(bms)
    return jsonify({"ok": True})


@app.route("/api/bookmark", methods=["DELETE"])
def bookmark_remove():
    data = request.get_json()
    if not data:
        abort(400)
    name = data.get("repo", "")
    path = data.get("path", "")
    valid_repo(name)
    if not path or ".." in path:
        abort(400)
    bms = read_bookmarks()
    if name in bms and path in bms[name]:
        del bms[name][path]
        if not bms[name]:
            del bms[name]
        write_bookmarks(bms)
    return jsonify({"ok": True})


@app.route("/api/nav_direction")
def nav_direction_get():
    return jsonify(read_nav_directions())


@app.route("/api/nav_direction", methods=["POST"])
def nav_direction_set():
    data = request.get_json()
    if not data or "key" not in data:
        abort(400)
    key = data["key"]
    direction = data.get("direction", "normal")
    dirs = read_nav_directions()
    if direction == "normal":
        dirs.pop(key, None)
    else:
        dirs[key] = direction
    write_nav_directions(dirs)
    return jsonify({"ok": True})


PLAYBACK_MIN_SECONDS = 15


def _playback_log_path(repo_path: Path, rel_path: str) -> Path:
    if not rel_path or ".." in rel_path:
        abort(400)
    audio_full = (repo_path / rel_path).resolve()
    if not contained(audio_full, repo_path.resolve()):
        abort(403)
    if not audio_full.is_file():
        abort(404)
    return audio_full.parent / (audio_full.name + ".playback.jsonl")


@app.route("/api/playback-log")
def playback_log_list():
    name = request.args.get("repo", "")
    path = request.args.get("path", "")
    repo_path = valid_repo(name)
    log_file = _playback_log_path(repo_path, path)
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
    name = data.get("repo", "")
    path = data.get("path", "")
    start_sec = data.get("start_sec")
    end_sec = data.get("end_sec")
    started_at = data.get("started_at", "")
    ended_at = data.get("ended_at", "")
    if not isinstance(start_sec, (int, float)) or not isinstance(end_sec, (int, float)):
        abort(400)
    if end_sec - start_sec < PLAYBACK_MIN_SECONDS:
        return jsonify({"ok": True, "skipped": True})
    repo_path = valid_repo(name)
    log_file = _playback_log_path(repo_path, path)
    record = {
        "started_at": str(started_at),
        "ended_at": str(ended_at),
        "start_sec": round(float(start_sec), 2),
        "end_sec": round(float(end_sec), 2),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


def _iter_output_mp3(base_full: Path):
    """Yield every `output.mp3` under base_full, pruning .git/hidden/archived dirs."""
    for root, dirs, files in os.walk(base_full):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "archived"]
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
    """Aggregate listening progress for every output.mp3 under a repo (optionally
    scoped to ?path=). One row per audio: coverage/reach/last-played, so a caller
    can render a cross-project "what have I listened to, how far" dashboard."""
    name = request.args.get("repo", "")
    base = request.args.get("path", "") or ""
    repo_path = valid_repo(name)
    if ".." in base:
        abort(400)
    base_full = (repo_path / base).resolve() if base else repo_path.resolve()
    if not contained(base_full, repo_path.resolve()):
        abort(403)
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
                "dir": folder.relative_to(repo_path).as_posix(),
                "audio": mp3.relative_to(repo_path).as_posix(),
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


NOTES_SUFFIX = ".notes.md"
SNAPSHOT_TEXT_LIMIT = 50 * 1024  # 50 KB


def _notes_kind_for_path(path: str) -> str:
    ext = ('.' + path.rsplit('.', 1)[1]).lower() if '.' in path else ''
    if ext == '.md':
        return 'md_sentence'
    if ext == '.srt':
        return 'srt'
    return 'lines'


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
    name = request.args.get("repo", "")
    path = request.args.get("path", "")
    repo_path = valid_repo(name)
    if not path or ".." in path or path.endswith(NOTES_SUFFIX):
        abort(400)

    target_full = (repo_path / path).resolve()
    if not contained(target_full, repo_path.resolve()):
        abort(403)

    kind = _notes_kind_for_path(path)
    notes_full = target_full.parent / (target_full.name + NOTES_SUFFIX)

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
    name = data.get("repo", "")
    path = data.get("path", "")
    anchor = data.get("anchor")
    snapshot = data.get("snapshot")
    body = data.get("body", "")
    if_match = data.get("if_match_mtime", None)
    if not isinstance(body, str):
        abort(400)
    if not _validate_anchor(anchor) or not _validate_snapshot(snapshot):
        abort(400)
    repo_path = valid_repo(name)
    if not path or ".." in path or path.endswith(NOTES_SUFFIX):
        abort(400)
    target_full = (repo_path / path).resolve()
    if not contained(target_full, repo_path.resolve()):
        abort(403)
    notes_full = target_full.parent / (target_full.name + NOTES_SUFFIX)

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
    name = data.get("repo", "")
    path = data.get("path", "")
    anchor = data.get("anchor")
    if_match = data.get("if_match_mtime", None)
    if not _validate_anchor(anchor):
        abort(400)
    repo_path = valid_repo(name)
    if not path or ".." in path or path.endswith(NOTES_SUFFIX):
        abort(400)
    target_full = (repo_path / path).resolve()
    if not contained(target_full, repo_path.resolve()):
        abort(403)
    notes_full = target_full.parent / (target_full.name + NOTES_SUFFIX)
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
    name = data.get("repo", "")
    path = data.get("path", "")
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
    repo_path = valid_repo(name)
    if not path or ".." in path or path.endswith(NOTES_SUFFIX):
        abort(400)
    target_full = (repo_path / path).resolve()
    if not contained(target_full, repo_path.resolve()):
        abort(403)
    notes_full = target_full.parent / (target_full.name + NOTES_SUFFIX)
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
    name = request.args.get("repo", "")
    path = request.args.get("path", "") or ""
    repo_path = valid_repo(name)
    if ".." in path:
        abort(400)
    base_full = (repo_path / path).resolve() if path else repo_path
    if not contained(base_full, repo_path.resolve()):
        abort(403)
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
