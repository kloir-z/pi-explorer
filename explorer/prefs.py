"""Per-user preferences persisted server-side: favorites, bookmarks, nav direction."""
from flask import Blueprint, abort, jsonify, request

from .paths import resolve_rel
from .store import bookmarks_store, favorites_store, nav_dirs_store

bp = Blueprint("prefs", __name__)


@bp.route("/api/favorites")
def favorites():
    return jsonify(favorites_store.read())


@bp.route("/api/favorites", methods=["POST"])
def add_favorite():
    data = request.get_json()
    if not data or "path" not in data:
        abort(400)
    path = data["path"]
    favorites_store.update(lambda favs: path in favs or favs.append(path))
    return jsonify({"ok": True})


@bp.route("/api/favorites", methods=["DELETE"])
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


@bp.route("/api/bookmark")
def bookmark_get():
    path = _bookmark_path(request.args.get("path", ""))
    return jsonify(bookmarks_store.read().get(path) or {})


@bp.route("/api/bookmark", methods=["PUT"])
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


@bp.route("/api/bookmark", methods=["DELETE"])
def bookmark_remove():
    data = request.get_json()
    if not data:
        abort(400)
    path = _bookmark_path(data.get("path", ""))
    bookmarks_store.update(lambda bms: bms.pop(path, None))
    return jsonify({"ok": True})


@bp.route("/api/nav_direction")
def nav_direction_get():
    return jsonify(nav_dirs_store.read())


@bp.route("/api/nav_direction", methods=["POST"])
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
