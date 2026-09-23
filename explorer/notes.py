import json
import re

SNAPSHOT_RE = re.compile(r"^<!--snapshot:(.*)-->$")


def encode_snapshot(snap: dict) -> str:
    """Encode a snapshot dict as a single-line HTML comment."""
    body = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    if "\n" in body:
        raise ValueError("snapshot JSON must not contain literal newlines")
    return f"<!--snapshot:{body}-->"


def decode_snapshot(line: str) -> dict | None:
    """Decode a snapshot HTML comment line. Returns None if invalid."""
    m = SNAPSHOT_RE.match(line.strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


HEADING_LINES = re.compile(r"^L(\d+)(?:-(\d+))?$")
HEADING_MD = re.compile(r'^S(\d+)(?:\s+"((?:[^"\\]|\\.)*)")?$')
HEADING_SRT = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$"
)
HEADING_UNRESOLVED = re.compile(r"^Unresolved$")

WS_RE = re.compile(r"\s+")


def _ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def _format_ms(ms):
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_anchor_heading(text: str) -> dict | None:
    """Parse a heading body (without leading `## ` / `### `). Returns None if unrecognized."""
    text = text.strip()
    if HEADING_UNRESOLVED.match(text):
        return {"kind": "unresolved"}
    m = HEADING_LINES.match(text)
    if m:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        return {"kind": "lines", "start": start, "end": end}
    m = HEADING_MD.match(text)
    if m:
        idx = int(m.group(1))
        raw = m.group(2) or ""
        # un-escape \" and \\
        unescaped = re.sub(r"\\(.)", r"\1", raw)
        return {"kind": "md_sentence", "index": idx, "heading_text": unescaped}
    m = HEADING_SRT.match(text)
    if m:
        start_ms = _ms(*m.group(1, 2, 3, 4))
        end_ms = _ms(*m.group(5, 6, 7, 8))
        return {"kind": "srt", "start_ms": start_ms, "end_ms": end_ms}
    return None


def format_anchor_heading(anchor: dict) -> str:
    kind = anchor["kind"]
    if kind == "lines":
        start, end = anchor["start"], anchor["end"]
        return f"L{start}" if start == end else f"L{start}-{end}"
    if kind == "md_sentence":
        text = WS_RE.sub(" ", anchor.get("heading_text", "")).strip()
        if len(text) > 30:
            text = text[:30] + "..."
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'S{anchor["index"]} "{escaped}"'
    if kind == "srt":
        return f"{_format_ms(anchor['start_ms'])} --> {_format_ms(anchor['end_ms'])}"
    if kind == "unresolved":
        return "Unresolved"
    raise ValueError(f"unknown anchor kind: {kind}")


from dataclasses import dataclass, field


@dataclass
class NotesSection:
    anchor: dict
    snapshot: dict | None
    body: str


@dataclass
class NotesDoc:
    title: str | None = None
    resolved: list[NotesSection] = field(default_factory=list)
    unresolved: list[NotesSection] = field(default_factory=list)


def parse_notes_md(text: str) -> NotesDoc:
    doc = NotesDoc()
    lines = text.splitlines()
    i = 0
    in_unresolved = False
    seen_anchors: set[tuple] = set()

    # title
    if i < len(lines) and lines[i].startswith("# "):
        doc.title = lines[i][2:].strip()
        i += 1

    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].strip()
            anchor = parse_anchor_heading(heading)
            i += 1
            if anchor and anchor["kind"] == "unresolved":
                in_unresolved = True
                continue
            section, i = _consume_section_body(lines, i, level=2)
            if anchor is None:
                continue  # ignore unrecognized headings
            key = _anchor_key(anchor)
            if key in seen_anchors:
                continue
            seen_anchors.add(key)
            section.anchor = anchor
            target = doc.unresolved if in_unresolved else doc.resolved
            target.append(section)
        elif line.startswith("### ") and in_unresolved:
            heading = line[4:].strip()
            anchor = parse_anchor_heading(heading)
            i += 1
            section, i = _consume_section_body(lines, i, level=3)
            if anchor is None or anchor["kind"] == "unresolved":
                continue
            key = _anchor_key(anchor)
            if key in seen_anchors:
                continue
            seen_anchors.add(key)
            section.anchor = anchor
            doc.unresolved.append(section)
        else:
            i += 1
    return doc


def _consume_section_body(lines, start, level):
    """Consume body lines until next heading at same or higher level. Returns (section, new_index)."""
    body_lines = []
    snapshot = None
    i = start
    boundary_prefixes = ["## "] if level == 2 else ["## ", "### "]
    while i < len(lines):
        line = lines[i]
        if any(line.startswith(p) for p in boundary_prefixes):
            break
        if snapshot is None:
            decoded = decode_snapshot(line)
            if decoded is not None:
                snapshot = decoded
                i += 1
                continue
        body_lines.append(line)
        i += 1
    while body_lines and body_lines[0].strip() == "":
        body_lines.pop(0)
    while body_lines and body_lines[-1].strip() == "":
        body_lines.pop()
    body = "\n".join(body_lines)
    return NotesSection(anchor={}, snapshot=snapshot, body=body), i


def _anchor_key(anchor: dict) -> tuple:
    kind = anchor["kind"]
    if kind == "lines":
        return ("lines", anchor["start"], anchor["end"])
    if kind == "md_sentence":
        return ("md_sentence", anchor["index"])
    if kind == "srt":
        return ("srt", anchor["start_ms"], anchor["end_ms"])
    return (kind,)


def _sort_key(anchor: dict):
    kind = anchor["kind"]
    if kind == "lines":
        return (0, anchor["start"], anchor["end"])
    if kind == "md_sentence":
        return (0, anchor["index"], 0)
    if kind == "srt":
        return (0, anchor["start_ms"], anchor["end_ms"])
    return (1, 0, 0)


def serialize_notes_md(doc: NotesDoc) -> str:
    parts = []
    if doc.title:
        parts.append(f"# {doc.title}\n")

    for sec in sorted(doc.resolved, key=lambda s: _sort_key(s.anchor)):
        parts.append(_serialize_section(sec, level=2))

    if doc.unresolved:
        parts.append("\n## Unresolved\n")
        for sec in sorted(doc.unresolved, key=lambda s: _sort_key(s.anchor)):
            parts.append(_serialize_section(sec, level=3))

    return "".join(parts)


def _serialize_section(sec: NotesSection, level: int) -> str:
    prefix = "##" if level == 2 else "###"
    out = [f"\n{prefix} {format_anchor_heading(sec.anchor)}\n"]
    if sec.snapshot is not None:
        out.append(f"\n{encode_snapshot(sec.snapshot)}\n")
    if sec.body:
        out.append(f"\n{sec.body.rstrip()}\n")
    return "".join(out)


import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically (tempfile + os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class LinesResolution:
    resolved: bool
    relocated: bool = False
    start: int = 0
    end: int = 0
    reason: str = ""


def _normalize_block(s: str) -> str:
    lines = s.split("\n")
    return "\n".join(line.rstrip() for line in lines).rstrip("\n")


def resolve_lines_anchor(file_text: str, anchor: dict, snapshot: dict | None) -> LinesResolution:
    file_lines = file_text.split("\n")
    if file_lines and file_lines[-1] == "":
        file_lines.pop()  # drop trailing empty from final newline

    anchor_start, anchor_end = anchor["start"], anchor["end"]
    n = len(file_lines)

    if snapshot is None:
        if 1 <= anchor_start <= anchor_end <= n:
            return LinesResolution(resolved=True, start=anchor_start, end=anchor_end)
        return LinesResolution(resolved=False, reason="行範囲がファイル外")

    snap_text = snapshot.get("text", "")
    snap_lines = snap_text.split("\n")
    if snap_lines and snap_lines[-1] == "":
        snap_lines.pop()
    block_size = len(snap_lines)
    if block_size == 0:
        if 1 <= anchor_start <= anchor_end <= n:
            return LinesResolution(resolved=True, start=anchor_start, end=anchor_end)
        return LinesResolution(resolved=False, reason="スナップショットが空")

    target = _normalize_block(snap_text)

    # exact-position check
    if 1 <= anchor_start and anchor_start - 1 + block_size <= n:
        chunk = "\n".join(file_lines[anchor_start - 1 : anchor_start - 1 + block_size])
        if _normalize_block(chunk) == target:
            return LinesResolution(
                resolved=True, start=anchor_start, end=anchor_start + block_size - 1
            )

    # search ±50
    search_min = max(1, anchor_start - 50)
    search_max = n - block_size + 1
    if anchor_end + 50 < search_max:
        search_max = anchor_end + 50

    candidates = []
    for i in range(search_min, search_max + 1):
        chunk = "\n".join(file_lines[i - 1 : i - 1 + block_size])
        if _normalize_block(chunk) == target:
            candidates.append(i)

    if not candidates:
        return LinesResolution(resolved=False, reason="行範囲のテキストが見つからなかった")

    best = min(candidates, key=lambda i: abs(i - anchor_start))
    return LinesResolution(
        resolved=True, relocated=(best != anchor_start),
        start=best, end=best + block_size - 1,
    )


SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_srt_cues(text: str) -> list[dict]:
    cues = []
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    for block in blocks:
        block_lines = block.splitlines()
        if len(block_lines) < 2:
            continue
        idx_line = block_lines[0].strip()
        if SRT_TIME_RE.match(idx_line):
            time_idx = 0
            cue_index = len(cues) + 1
        else:
            time_idx = 1
            try:
                cue_index = int(idx_line)
            except ValueError:
                cue_index = len(cues) + 1
        if time_idx >= len(block_lines):
            continue
        m = SRT_TIME_RE.match(block_lines[time_idx])
        if not m:
            continue
        start_ms = _ms(*m.group(1, 2, 3, 4))
        end_ms = _ms(*m.group(5, 6, 7, 8))
        text_body = "\n".join(block_lines[time_idx + 1 :]).strip()
        cues.append({
            "cue_index": cue_index, "start_ms": start_ms, "end_ms": end_ms, "text": text_body,
        })
    return cues


@dataclass
class SrtResolution:
    resolved: bool
    cue_index: int = 0
    reason: str = ""


def resolve_srt_anchor(cues: list[dict], anchor: dict) -> SrtResolution:
    target = (anchor["start_ms"], anchor["end_ms"])
    for cue in cues:
        if (cue["start_ms"], cue["end_ms"]) == target:
            return SrtResolution(resolved=True, cue_index=cue["cue_index"])
    return SrtResolution(resolved=False, reason="該当タイムコードの字幕が見つからなかった")


NOTES_SUFFIX = ".notes.md"


def notes_path_for(target_path) -> Path:
    """Given a relative target file path, return the corresponding notes path."""
    p = Path(target_path)
    if p.name.endswith(NOTES_SUFFIX):
        raise ValueError("cannot create notes for a notes file")
    return p.parent / (p.name + NOTES_SUFFIX)


def load_notes(path) -> NotesDoc:
    p = Path(path)
    if not p.is_file():
        return NotesDoc()
    text = p.read_text(encoding="utf-8")
    return parse_notes_md(text)


def save_notes(path, doc: NotesDoc) -> None:
    text = serialize_notes_md(doc)
    atomic_write_text(Path(path), text)


def upsert_section(doc: NotesDoc, section: NotesSection) -> None:
    key = _anchor_key(section.anchor)
    for i, existing in enumerate(doc.resolved):
        if _anchor_key(existing.anchor) == key:
            doc.resolved[i] = section
            return
    doc.resolved.append(section)


def delete_section(doc: NotesDoc, anchor: dict) -> bool:
    key = _anchor_key(anchor)
    for i, existing in enumerate(doc.resolved):
        if _anchor_key(existing.anchor) == key:
            doc.resolved.pop(i)
            return True
    for i, existing in enumerate(doc.unresolved):
        if _anchor_key(existing.anchor) == key:
            doc.unresolved.pop(i)
            return True
    return False


from datetime import datetime, timedelta, timezone

RESOLUTION_STATUSES = ("todo", "done", "wontfix")
DEFAULT_RESOLUTION_STATUS = "todo"

_JST = timezone(timedelta(hours=9))


def _now_jst_iso() -> str:
    s = datetime.now(_JST).strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


def get_resolution(snapshot: dict | None) -> dict:
    """Return the resolution dict for a snapshot.

    Snapshots without a `resolution` field are treated as `{"status": "todo"}`.
    Unknown status values fall back to the default. Always returns a fresh dict
    so callers can mutate it without affecting the source snapshot.
    """
    if not isinstance(snapshot, dict):
        return {"status": DEFAULT_RESOLUTION_STATUS}
    res = snapshot.get("resolution")
    if not isinstance(res, dict):
        return {"status": DEFAULT_RESOLUTION_STATUS}
    status = res.get("status")
    if status not in RESOLUTION_STATUSES:
        status = DEFAULT_RESOLUTION_STATUS
    out = {"status": status}
    resolved_at = res.get("resolved_at")
    if isinstance(resolved_at, str) and resolved_at:
        out["resolved_at"] = resolved_at
    ref = res.get("ref")
    if isinstance(ref, str) and ref:
        out["ref"] = ref
    return out


def set_resolution(
    snapshot: dict,
    status: str,
    ref: str | None = None,
    resolved_at: str | None = None,
) -> dict:
    """Set the resolution on a snapshot in-place.

    - `status` must be one of RESOLUTION_STATUSES.
    - Setting status back to "todo" removes the `resolution` field entirely
      (keeps snapshots minimal; missing == default todo).
    - `resolved_at` defaults to now in JST (ISO-8601 with `+09:00` offset).
    - `ref` is an optional free-form pointer (e.g. commit hash, PR url, note).

    Returns the (mutated) snapshot for chaining.
    """
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot must be a dict")
    if status not in RESOLUTION_STATUSES:
        raise ValueError(
            f"unknown status: {status!r} (expected one of {RESOLUTION_STATUSES})"
        )
    if status == DEFAULT_RESOLUTION_STATUS:
        snapshot.pop("resolution", None)
        return snapshot
    res: dict = {"status": status, "resolved_at": resolved_at or _now_jst_iso()}
    if ref:
        res["ref"] = ref
    snapshot["resolution"] = res
    return snapshot


def count_sections_by_status(doc: NotesDoc) -> dict[str, int]:
    """Count sections grouped by resolution status across resolved + unresolved.

    Sections without a snapshot are treated as `todo`.
    """
    counts = {s: 0 for s in RESOLUTION_STATUSES}
    for sec in list(doc.resolved) + list(doc.unresolved):
        status = get_resolution(sec.snapshot)["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts
