"""Path handling, file endpoints, prefs and legacy-state migration."""


def test_tree_lists_root_relative_paths(app_client):
    client, repo = app_client
    (repo / "sub").mkdir()
    (repo / "a.txt").write_text("a", encoding="utf-8")
    resp = client.get("/api/tree?path=myrepo")
    assert resp.status_code == 200
    entries = resp.get_json()
    assert [(e["path"], e["type"]) for e in entries] == [
        ("myrepo/sub", "tree"), ("myrepo/a.txt", "blob")]


def test_tree_root_lists_top_level(app_client):
    client, _ = app_client
    paths = [e["path"] for e in client.get("/api/tree").get_json()]
    assert "myrepo" in paths


def test_folders_need_no_git(app_client):
    client, repo = app_client
    assert not (repo / ".git").exists()
    (repo / "x.md").write_text("# hi", encoding="utf-8")
    assert client.get("/api/blob?path=myrepo/x.md").get_json()["content"] == "# hi"


def test_traversal_rejected(app_client):
    client, _ = app_client
    assert client.get("/api/tree?path=myrepo/../..").status_code == 400
    assert client.get("/api/blob?path=..%5Csecret").status_code == 400


def test_escape_via_absolute_path_rejected(app_client, tmp_path):
    client, _ = app_client
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert client.get(f"/api/blob?path={outside.as_posix()}").status_code == 403


def test_sibling_prefix_is_not_contained(tmp_path):
    from explorer.paths import contained
    root = tmp_path / "code"
    assert contained(root / "a", root)
    assert not contained(tmp_path / "code-evil" / "a", root)


def test_blob_write_and_git_guard(app_client):
    client, repo = app_client
    (repo / "f.txt").write_text("old", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]", encoding="utf-8")

    ok = client.put("/api/blob", json={"path": "myrepo/f.txt", "content": "new"})
    assert ok.status_code == 200
    assert (repo / "f.txt").read_text(encoding="utf-8") == "new"

    blocked = client.put("/api/blob", json={"path": "myrepo/.git/config", "content": "x"})
    assert blocked.status_code == 403
    assert client.get("/raw/myrepo/.git/config").status_code == 404


def test_bookmark_roundtrip(app_client):
    client, repo = app_client
    (repo / "b.md").write_text("x", encoding="utf-8")
    client.put("/api/bookmark", json={"path": "myrepo/b.md", "type": "md", "index": 3})
    assert client.get("/api/bookmark?path=myrepo/b.md").get_json() == {"type": "md", "index": 3}
    client.delete("/api/bookmark", json={"path": "myrepo/b.md"})
    assert client.get("/api/bookmark?path=myrepo/b.md").get_json() == {}


def test_favorites_add_remove(app_client):
    client, _ = app_client
    client.post("/api/favorites", json={"path": "myrepo"})
    client.post("/api/favorites", json={"path": "myrepo"})
    assert client.get("/api/favorites").get_json() == ["myrepo"]
    client.delete("/api/favorites", json={"path": "myrepo"})
    assert client.get("/api/favorites").get_json() == []


def test_playback_overview_scoped_to_path(app_client):
    client, repo = app_client
    for d in ("p1", "p2", "archived/p3", ".hidden/p4"):
        (repo / d).mkdir(parents=True)
        (repo / d / "output.mp3").write_bytes(b"")
    items = client.get("/api/playback-overview?path=myrepo").get_json()
    assert sorted(i["audio"] for i in items) == ["myrepo/p1/output.mp3", "myrepo/p2/output.mp3"]
    assert len(client.get("/api/playback-overview?path=myrepo/p1").get_json()) == 1


def test_legacy_key_conversion():
    from explorer.store import _flatten_bookmarks, _rekey_nav_directions
    assert _flatten_bookmarks({"r": {"a/b.md": {"type": "md", "index": 1}}}) == {
        "r/a/b.md": {"type": "md", "index": 1}}
    assert _rekey_nav_directions({"r|d/e|jpg": "reversed", "r||png": "reversed", "x|jpg": "reversed"}) == {
        "r/d/e|jpg": "reversed", "r|png": "reversed", "x|jpg": "reversed"}
