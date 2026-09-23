"""Settings resolved once at import: which folder to serve, where state lives."""
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.local.json"
DATA_DIR = BASE_DIR / "data"


def load_config() -> dict:
    if not CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_root(config: dict) -> Path:
    """The folder to serve. The git-viewer era names still work as fallbacks."""
    for var in ("PI_EXPLORER_ROOT", "GIT_VIEWER_CODE_DIR"):
        if os.environ.get(var):
            return Path(os.environ[var])
    for key in ("root_dir", "code_dir"):
        if config.get(key):
            return Path(config[key])
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
ROOT = load_root(CONFIG).resolve()
KEEP_AWAKE_SCRIPT = load_keep_awake_script(CONFIG)
