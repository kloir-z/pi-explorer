"""HTTP API for per-file sidecar notes (<file>.notes.md)."""
from datetime import datetime

from flask import Blueprint, abort, jsonify, request

from . import notes as notes_mod
from .paths import resolve_rel

bp = Blueprint("notes", __name__)


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


@bp.route("/api/notes")
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


@bp.route("/api/notes", methods=["PUT"])
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


@bp.route("/api/notes", methods=["DELETE"])
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


@bp.route("/api/notes/relocate", methods=["POST"])
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


@bp.route("/api/notes/index")
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
