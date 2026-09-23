import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Flask test client whose ROOT is tmp_path; files live under tmp_path/myrepo."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    monkeypatch.setenv("GIT_VIEWER_CODE_DIR", str(tmp_path))
    # ROOT is resolved at import time, so drop cached modules to pick up the env.
    for name in [m for m in sys.modules if m == "explorer" or m.startswith("explorer.")]:
        del sys.modules[name]
    from explorer import create_app, store
    for s in (store.favorites_store, store.bookmarks_store, store.nav_dirs_store):
        monkeypatch.setattr(s, "path", tmp_path / "_data" / s.path.name)
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), repo
