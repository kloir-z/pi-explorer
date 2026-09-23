"""Small JSON documents under data/ (favorites, bookmarks, nav directions)."""
import json
import logging
import threading

from .config import BASE_DIR, DATA_DIR

log = logging.getLogger(__name__)


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
            self.path.parent.mkdir(exist_ok=True)
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
            log.warning("skipping unreadable legacy %s", name)
            continue
        DATA_DIR.mkdir(exist_ok=True)
        new.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        old.unlink()
