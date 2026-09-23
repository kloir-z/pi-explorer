"""Directory listing, file preview/download and in-place text editing."""
import io
import mimetypes

from flask import Blueprint, abort, jsonify, request, send_file

from .config import ROOT
from .paths import has_git_component, resolve_rel, to_rel

try:
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

bp = Blueprint("files", __name__)


@bp.route("/api/tree")
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


@bp.route("/api/blob")
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


@bp.route("/raw/<path:relpath>")
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


@bp.route("/api/blob", methods=["PUT"])
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
