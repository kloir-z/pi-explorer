from explorer.notes import encode_snapshot, decode_snapshot


def test_encode_snapshot_lines():
    snap = {"kind": "lines", "start": 10, "end": 15, "text": "foo\nbar"}
    s = encode_snapshot(snap)
    assert s.startswith("<!--snapshot:")
    assert s.endswith("-->")
    assert "\n" not in s  # must be single line


def test_encode_decode_roundtrip_lines():
    snap = {"kind": "lines", "start": 10, "end": 15, "text": "foo\nbar\n"}
    assert decode_snapshot(encode_snapshot(snap)) == snap


def test_decode_invalid_returns_none():
    assert decode_snapshot("<!--not a snapshot-->") is None
    assert decode_snapshot("plain text") is None
    assert decode_snapshot("<!--snapshot:{broken json-->") is None


def test_encode_unicode_and_quotes():
    snap = {"kind": "md_sentence", "index": 0, "text": 'これは"引用"を含む。'}
    decoded = decode_snapshot(encode_snapshot(snap))
    assert decoded == snap


def test_encode_multiline_text_is_serialized_as_escaped_newline():
    snap = {"kind": "lines", "start": 1, "end": 2, "text": "line1\nline2"}
    s = encode_snapshot(snap)
    assert "\\n" in s
    assert "\n" not in s


def test_encode_decode_roundtrip_with_timestamps():
    snap = {
        "kind": "lines", "start": 1, "end": 1, "text": "x",
        "created_at": "2026-04-30T10:00:00+09:00",
        "updated_at": "2026-04-30T11:30:00+09:00",
    }
    assert decode_snapshot(encode_snapshot(snap)) == snap


from explorer.notes import parse_anchor_heading, format_anchor_heading


def test_parse_lines_range():
    assert parse_anchor_heading("L10-15") == {"kind": "lines", "start": 10, "end": 15}


def test_parse_lines_single():
    assert parse_anchor_heading("L10") == {"kind": "lines", "start": 10, "end": 10}


def test_parse_md_sentence_with_text():
    assert parse_anchor_heading('S42 "hello world"') == {
        "kind": "md_sentence", "index": 42, "heading_text": "hello world",
    }


def test_parse_md_sentence_without_text():
    assert parse_anchor_heading("S42") == {
        "kind": "md_sentence", "index": 42, "heading_text": "",
    }


def test_parse_md_sentence_with_escaped_quote():
    assert parse_anchor_heading('S5 "say \\"hi\\""') == {
        "kind": "md_sentence", "index": 5, "heading_text": 'say "hi"',
    }


def test_parse_srt():
    assert parse_anchor_heading("00:01:23,456 --> 00:01:30,000") == {
        "kind": "srt", "start_ms": 83456, "end_ms": 90000,
    }


def test_put_notes_creates_file(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\nb\n", encoding="utf-8")
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": None,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
        "snapshot": {"kind": "lines", "start": 1, "end": 1, "text": "a"},
        "body": "ここはこう",
    }
    resp = client.put("/api/notes", json=payload)
    assert resp.status_code == 200
    assert resp.get_json()["mtime"] is not None
    notes_text = (repo / "foo.py.notes.md").read_text(encoding="utf-8")
    assert "## L1" in notes_text
    assert "ここはこう" in notes_text


def test_put_notes_overwrites_existing(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    initial = (
        "## L1\n"
        '<!--snapshot:{"kind":"lines","start":1,"end":1,"text":"a"}-->\n'
        "old body\n"
    )
    notes_path = repo / "foo.py.notes.md"
    notes_path.write_text(initial, encoding="utf-8")
    mtime = notes_path.stat().st_mtime
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": mtime,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
        "snapshot": {"kind": "lines", "start": 1, "end": 1, "text": "a"},
        "body": "new body",
    }
    resp = client.put("/api/notes", json=payload)
    assert resp.status_code == 200
    text = notes_path.read_text(encoding="utf-8")
    assert "new body" in text
    assert "old body" not in text


def test_put_notes_mtime_conflict(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    notes_path = repo / "foo.py.notes.md"
    notes_path.write_text("## L1\nbody\n", encoding="utf-8")
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": 0.0,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
        "snapshot": None,
        "body": "x",
    }
    resp = client.put("/api/notes", json=payload)
    assert resp.status_code == 409


def test_put_notes_rejects_huge_snapshot(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    huge = "x" * (50 * 1024 + 1)
    payload = {
        "path": "myrepo/foo.py", "if_match_mtime": None,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
        "snapshot": {"kind": "lines", "start": 1, "end": 1, "text": huge},
        "body": "x",
    }
    resp = client.put("/api/notes", json=payload)
    assert resp.status_code == 400


def test_parse_srt_with_dot_separator():
    assert parse_anchor_heading("00:00:01.500 --> 00:00:02.000") == {
        "kind": "srt", "start_ms": 1500, "end_ms": 2000,
    }


def test_parse_unresolved():
    assert parse_anchor_heading("Unresolved") == {"kind": "unresolved"}


def test_parse_unknown_returns_none():
    assert parse_anchor_heading("Random heading") is None
    assert parse_anchor_heading("L10-foo") is None


def test_format_lines_range():
    assert format_anchor_heading({"kind": "lines", "start": 10, "end": 15}) == "L10-15"


def test_format_lines_single():
    assert format_anchor_heading({"kind": "lines", "start": 10, "end": 10}) == "L10"


def test_format_md_sentence_truncates_to_30_chars():
    long = "a" * 50
    out = format_anchor_heading({"kind": "md_sentence", "index": 7, "heading_text": long})
    assert out == 'S7 "' + "a" * 30 + '..."'


def test_format_md_sentence_short_no_ellipsis():
    out = format_anchor_heading({"kind": "md_sentence", "index": 7, "heading_text": "short"})
    assert out == 'S7 "short"'


def test_format_md_sentence_normalizes_whitespace():
    out = format_anchor_heading({"kind": "md_sentence", "index": 1, "heading_text": "a  b\nc"})
    assert out == 'S1 "a b c"'


def test_format_md_sentence_escapes_quote():
    out = format_anchor_heading({"kind": "md_sentence", "index": 1, "heading_text": 'a"b'})
    assert out == 'S1 "a\\"b"'


def test_format_srt():
    out = format_anchor_heading({"kind": "srt", "start_ms": 83456, "end_ms": 90000})
    assert out == "00:01:23,456 --> 00:01:30,000"


from explorer.notes import parse_notes_md


def test_parse_empty_string():
    doc = parse_notes_md("")
    assert doc.title is None
    assert doc.resolved == []
    assert doc.unresolved == []


def test_parse_title_only():
    doc = parse_notes_md("# Notes for foo.py\n")
    assert doc.title == "Notes for foo.py"
    assert doc.resolved == []


def test_parse_single_lines_section():
    md = (
        "# Notes for foo.py\n"
        "\n"
        "## L10-15\n"
        "\n"
        '<!--snapshot:{"kind":"lines","start":10,"end":15,"text":"foo"}-->\n'
        "\n"
        "メモ本文1行目\n"
        "メモ本文2行目\n"
    )
    doc = parse_notes_md(md)
    assert len(doc.resolved) == 1
    sec = doc.resolved[0]
    assert sec.anchor == {"kind": "lines", "start": 10, "end": 15}
    assert sec.snapshot == {"kind": "lines", "start": 10, "end": 15, "text": "foo"}
    assert sec.body.strip() == "メモ本文1行目\nメモ本文2行目"


def test_parse_two_sections_and_unresolved():
    md = (
        "# Notes for foo.py\n"
        "## L10\n"
        '<!--snapshot:{"kind":"lines","start":10,"end":10,"text":"a"}-->\n'
        "first\n"
        "## L20\n"
        '<!--snapshot:{"kind":"lines","start":20,"end":20,"text":"b"}-->\n'
        "second\n"
        "## Unresolved\n"
        "### L99\n"
        '<!--snapshot:{"kind":"lines","start":99,"end":99,"text":"c"}-->\n'
        "未解決理由: ファイルの行数が不足\n"
        "third\n"
    )
    doc = parse_notes_md(md)
    assert len(doc.resolved) == 2
    assert len(doc.unresolved) == 1
    assert doc.unresolved[0].anchor == {"kind": "lines", "start": 99, "end": 99}
    assert "third" in doc.unresolved[0].body


def test_parse_unrecognized_heading_is_ignored():
    md = (
        "## NonsenseHeading\n"
        "ignored body\n"
        "## L1\n"
        '<!--snapshot:{"kind":"lines","start":1,"end":1,"text":"x"}-->\n'
        "real\n"
    )
    doc = parse_notes_md(md)
    assert len(doc.resolved) == 1
    assert "real" in doc.resolved[0].body


def test_parse_missing_snapshot_kept_as_none():
    md = (
        "## L1\n"
        "no snapshot here\n"
    )
    doc = parse_notes_md(md)
    assert len(doc.resolved) == 1
    assert doc.resolved[0].snapshot is None
    assert "no snapshot here" in doc.resolved[0].body


def test_parse_duplicate_anchor_first_wins():
    md = (
        "## L1\n"
        '<!--snapshot:{"kind":"lines","start":1,"end":1,"text":"a"}-->\n'
        "first\n"
        "## L1\n"
        '<!--snapshot:{"kind":"lines","start":1,"end":1,"text":"b"}-->\n'
        "second\n"
    )
    doc = parse_notes_md(md)
    assert len(doc.resolved) == 1
    assert "first" in doc.resolved[0].body


from explorer.notes import serialize_notes_md, NotesDoc, NotesSection


def test_serialize_empty():
    doc = NotesDoc(title="Notes for foo.py")
    out = serialize_notes_md(doc)
    assert out == "# Notes for foo.py\n"


def test_serialize_one_lines_section():
    doc = NotesDoc(
        title="Notes for foo.py",
        resolved=[NotesSection(
            anchor={"kind": "lines", "start": 10, "end": 15},
            snapshot={"kind": "lines", "start": 10, "end": 15, "text": "x"},
            body="メモ",
        )],
    )
    out = serialize_notes_md(doc)
    assert "# Notes for foo.py" in out
    assert "## L10-15" in out
    assert '<!--snapshot:{"kind":"lines"' in out
    assert "メモ" in out


def test_serialize_sort_order_lines():
    doc = NotesDoc(
        title="t",
        resolved=[
            NotesSection(anchor={"kind": "lines", "start": 30, "end": 30}, snapshot=None, body="c"),
            NotesSection(anchor={"kind": "lines", "start": 10, "end": 10}, snapshot=None, body="a"),
            NotesSection(anchor={"kind": "lines", "start": 20, "end": 20}, snapshot=None, body="b"),
        ],
    )
    out = serialize_notes_md(doc)
    pos_a = out.index("## L10")
    pos_b = out.index("## L20")
    pos_c = out.index("## L30")
    assert pos_a < pos_b < pos_c


def test_serialize_unresolved_section_after_resolved():
    doc = NotesDoc(
        title="t",
        resolved=[NotesSection(anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="r")],
        unresolved=[NotesSection(anchor={"kind": "lines", "start": 99, "end": 99}, snapshot=None, body="u")],
    )
    out = serialize_notes_md(doc)
    assert out.index("## L1") < out.index("## Unresolved")
    assert out.index("## Unresolved") < out.index("### L99")


def test_serialize_parse_roundtrip():
    original = NotesDoc(
        title="Notes for foo.py",
        resolved=[
            NotesSection(
                anchor={"kind": "lines", "start": 10, "end": 15},
                snapshot={"kind": "lines", "start": 10, "end": 15, "text": "x\ny"},
                body="本文1\n本文2",
            ),
            NotesSection(
                anchor={"kind": "srt", "start_ms": 1000, "end_ms": 2000},
                snapshot={"kind": "srt", "start_ms": 1000, "end_ms": 2000, "cue_index": 1, "text": "字幕"},
                body="srt メモ",
            ),
        ],
        unresolved=[
            NotesSection(
                anchor={"kind": "lines", "start": 99, "end": 99},
                snapshot={"kind": "lines", "start": 99, "end": 99, "text": "z"},
                body="未解決理由: foo\n本文",
            ),
        ],
    )
    text = serialize_notes_md(original)
    parsed = parse_notes_md(text)
    assert parsed.title == original.title
    assert len(parsed.resolved) == 2
    assert parsed.resolved[0].anchor == original.resolved[0].anchor
    assert parsed.resolved[0].snapshot == original.resolved[0].snapshot
    assert parsed.resolved[0].body == original.resolved[0].body
    assert len(parsed.unresolved) == 1
    assert parsed.unresolved[0].anchor == original.unresolved[0].anchor


from explorer.notes import atomic_write_text


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_overwrites(tmp_path):
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_creates_parent(tmp_path):
    target = tmp_path / "sub" / "out.md"
    atomic_write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_atomic_write_no_temp_left_behind(tmp_path):
    target = tmp_path / "out.md"
    atomic_write_text(target, "x")
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.md"]
    assert leftovers == []


from explorer.notes import resolve_lines_anchor


def _make_snap(start, end, text):
    return {"kind": "lines", "start": start, "end": end, "text": text}


def test_resolve_lines_exact_match():
    file_text = "a\nb\nc\nd\ne\n"
    snap = _make_snap(2, 3, "b\nc")
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 2, "end": 3}, snapshot=snap)
    assert result.resolved is True
    assert result.relocated is False
    assert result.start == 2
    assert result.end == 3


def test_resolve_lines_relocated_within_50():
    file_text = "a\nb\nc\nd\ne\nf\ng\nx\ny\nz\n"
    snap = _make_snap(5, 6, "x\ny")
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 5, "end": 6}, snapshot=snap)
    assert result.resolved is True
    assert result.relocated is True
    assert result.start == 8
    assert result.end == 9


def test_resolve_lines_unresolved_when_text_gone():
    file_text = "a\nb\nc\n"
    snap = _make_snap(1, 2, "x\ny")
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 1, "end": 2}, snapshot=snap)
    assert result.resolved is False


def test_resolve_lines_unresolved_when_outside_50():
    snap_text = "x\ny"
    file_text = "\n".join(["pad"] * 100 + snap_text.split("\n") + ["pad"] * 5) + "\n"
    snap = _make_snap(5, 6, snap_text)
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 5, "end": 6}, snapshot=snap)
    assert result.resolved is False


def test_resolve_lines_picks_nearest_when_multiple_candidates():
    snap_text = "TARGET"
    file_text = "TARGET\n" + "\n".join(["x"] * 9) + "\nTARGET\n" + "\n".join(["y"] * 5) + "\n"
    snap = _make_snap(11, 11, snap_text)
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 11, "end": 11}, snapshot=snap)
    assert result.resolved is True
    assert result.start == 11  # nearest


def test_resolve_lines_no_snapshot_uses_anchor_range():
    file_text = "a\nb\nc\n"
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 2, "end": 3}, snapshot=None)
    assert result.resolved is True
    assert result.relocated is False


def test_resolve_lines_no_snapshot_out_of_range():
    file_text = "a\nb\n"
    result = resolve_lines_anchor(file_text, anchor={"kind": "lines", "start": 5, "end": 6}, snapshot=None)
    assert result.resolved is False


from explorer.notes import parse_srt_cues, resolve_srt_anchor


SRT_SAMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:02,500\n"
    "Hello\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "World\n"
)


def test_parse_srt_cues_basic():
    cues = parse_srt_cues(SRT_SAMPLE)
    assert len(cues) == 2
    assert cues[0]["start_ms"] == 1000
    assert cues[0]["end_ms"] == 2500
    assert cues[0]["text"] == "Hello"
    assert cues[0]["cue_index"] == 1
    assert cues[1]["text"] == "World"


def test_parse_srt_cues_handles_dot_separator():
    src = "1\n00:00:01.000 --> 00:00:02.000\nHi\n"
    cues = parse_srt_cues(src)
    assert cues[0]["start_ms"] == 1000


def test_resolve_srt_exact_match():
    cues = parse_srt_cues(SRT_SAMPLE)
    anchor = {"kind": "srt", "start_ms": 3000, "end_ms": 4000}
    result = resolve_srt_anchor(cues, anchor)
    assert result.resolved is True
    assert result.cue_index == 2


def test_resolve_srt_no_match():
    cues = parse_srt_cues(SRT_SAMPLE)
    anchor = {"kind": "srt", "start_ms": 9999, "end_ms": 10000}
    result = resolve_srt_anchor(cues, anchor)
    assert result.resolved is False


from explorer.notes import notes_path_for, load_notes, save_notes, upsert_section, delete_section


def test_notes_path_for_appends_suffix():
    from pathlib import PurePosixPath
    assert notes_path_for(PurePosixPath("foo/bar.srt")).as_posix() == "foo/bar.srt.notes.md"
    assert notes_path_for(PurePosixPath("README.md")).as_posix() == "README.md.notes.md"


def test_notes_path_for_rejects_already_notes():
    from pathlib import PurePosixPath
    import pytest
    with pytest.raises(ValueError):
        notes_path_for(PurePosixPath("foo.notes.md"))


def test_load_notes_missing_returns_empty(tmp_path):
    p = tmp_path / "x.notes.md"
    doc = load_notes(p)
    assert doc.title is None
    assert doc.resolved == []


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "x.notes.md"
    doc = NotesDoc(title="Notes for x.py", resolved=[NotesSection(
        anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="hello")])
    save_notes(p, doc)
    loaded = load_notes(p)
    assert loaded.title == doc.title
    assert loaded.resolved[0].body == "hello"


def test_upsert_inserts_new():
    doc = NotesDoc()
    sec = NotesSection(anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="a")
    upsert_section(doc, sec)
    assert len(doc.resolved) == 1


def test_upsert_overwrites_existing():
    doc = NotesDoc(resolved=[NotesSection(
        anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="old")])
    upsert_section(doc, NotesSection(
        anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="new"))
    assert len(doc.resolved) == 1
    assert doc.resolved[0].body == "new"


def test_delete_removes_existing():
    doc = NotesDoc(resolved=[NotesSection(
        anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="x")])
    deleted = delete_section(doc, {"kind": "lines", "start": 1, "end": 1})
    assert deleted is True
    assert doc.resolved == []


def test_delete_not_found():
    doc = NotesDoc()
    deleted = delete_section(doc, {"kind": "lines", "start": 1, "end": 1})
    assert deleted is False


import pytest


def test_get_notes_missing_file(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("x = 1\n", encoding="utf-8")
    resp = client.get("/api/notes?path=myrepo/foo.py")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mtime"] is None
    assert body["resolved"] == []
    assert body["unresolved"] == []
    assert body["kind"] == "lines"


def test_get_notes_resolved_lines(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    notes = (
        "# Notes for foo.py\n"
        "## L2-3\n"
        '<!--snapshot:{"kind":"lines","start":2,"end":3,"text":"b\\nc"}-->\n'
        "メモ\n"
    )
    (repo / "foo.py.notes.md").write_text(notes, encoding="utf-8")
    resp = client.get("/api/notes?path=myrepo/foo.py")
    body = resp.get_json()
    assert len(body["resolved"]) == 1
    sec = body["resolved"][0]
    assert sec["anchor"] == {"kind": "lines", "start": 2, "end": 3}
    assert sec["relocated"] is False


def test_get_notes_relocates_and_writes_back(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("\n\n\nb\nc\nd\n", encoding="utf-8")
    notes = (
        "# Notes for foo.py\n"
        "## L1-2\n"
        '<!--snapshot:{"kind":"lines","start":1,"end":2,"text":"b\\nc"}-->\n'
        "メモ\n"
    )
    notes_path = repo / "foo.py.notes.md"
    notes_path.write_text(notes, encoding="utf-8")
    resp = client.get("/api/notes?path=myrepo/foo.py")
    body = resp.get_json()
    assert body["resolved"][0]["anchor"] == {"kind": "lines", "start": 4, "end": 5}
    # File rewritten with new heading
    assert "## L4-5" in notes_path.read_text(encoding="utf-8")


def test_get_notes_unresolved_lines(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    notes = (
        "## L10\n"
        '<!--snapshot:{"kind":"lines","start":10,"end":10,"text":"missing"}-->\n'
        "メモ\n"
    )
    (repo / "foo.py.notes.md").write_text(notes, encoding="utf-8")
    resp = client.get("/api/notes?path=myrepo/foo.py")
    body = resp.get_json()
    assert body["resolved"] == []
    assert len(body["unresolved"]) == 1


def test_get_notes_md_returns_client_resolve_flag(app_client):
    client, repo = app_client
    (repo / "doc.md").write_text("hello\n", encoding="utf-8")
    notes = (
        "## S0 \"hello\"\n"
        '<!--snapshot:{"kind":"md_sentence","index":0,"text":"hello"}-->\n'
        "メモ\n"
    )
    (repo / "doc.md.notes.md").write_text(notes, encoding="utf-8")
    resp = client.get("/api/notes?path=myrepo/doc.md")
    body = resp.get_json()
    assert body["kind"] == "md_sentence"
    assert body["resolved"][0]["client_resolve"] is True


def test_delete_notes_removes_section(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\nb\n", encoding="utf-8")
    notes_path = repo / "foo.py.notes.md"
    initial = (
        "## L1\nfirst\n"
        "## L2\nsecond\n"
    )
    notes_path.write_text(initial, encoding="utf-8")
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": notes_path.stat().st_mtime,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
    }
    resp = client.delete("/api/notes", json=payload)
    assert resp.status_code == 200
    text = notes_path.read_text(encoding="utf-8")
    assert "first" not in text
    assert "second" in text


def test_delete_last_section_removes_file(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    notes_path = repo / "foo.py.notes.md"
    notes_path.write_text("## L1\nbody\n", encoding="utf-8")
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": notes_path.stat().st_mtime,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
    }
    resp = client.delete("/api/notes", json=payload)
    assert resp.status_code == 200
    assert resp.get_json()["mtime"] is None
    assert not notes_path.exists()


def test_delete_notes_mtime_conflict(app_client):
    client, repo = app_client
    (repo / "foo.py").write_text("a\n", encoding="utf-8")
    notes_path = repo / "foo.py.notes.md"
    notes_path.write_text("## L1\nbody\n", encoding="utf-8")
    payload = {
        "path": "myrepo/foo.py",
        "if_match_mtime": 0.0,
        "anchor": {"kind": "lines", "start": 1, "end": 1},
    }
    resp = client.delete("/api/notes", json=payload)
    assert resp.status_code == 409


def test_relocate_md_section(app_client):
    client, repo = app_client
    (repo / "doc.md").write_text("hello\n", encoding="utf-8")
    notes_path = repo / "doc.md.notes.md"
    initial = (
        "## S5 \"old text\"\n"
        '<!--snapshot:{"kind":"md_sentence","index":5,"text":"hello"}-->\n'
        "メモ\n"
    )
    notes_path.write_text(initial, encoding="utf-8")
    payload = {
        "path": "myrepo/doc.md",
        "if_match_mtime": notes_path.stat().st_mtime,
        "old_anchor": {"kind": "md_sentence", "index": 5},
        "new_anchor": {"kind": "md_sentence", "index": 3},
        "new_heading_text": "hello",
    }
    resp = client.post("/api/notes/relocate", json=payload)
    assert resp.status_code == 200
    text = notes_path.read_text(encoding="utf-8")
    assert "## S3 \"hello\"" in text
    assert "S5" not in text
    assert "メモ" in text


def test_relocate_section_not_found(app_client):
    client, repo = app_client
    (repo / "doc.md").write_text("x\n", encoding="utf-8")
    notes_path = repo / "doc.md.notes.md"
    notes_path.write_text("## S1\nbody\n", encoding="utf-8")
    payload = {
        "path": "myrepo/doc.md",
        "if_match_mtime": notes_path.stat().st_mtime,
        "old_anchor": {"kind": "md_sentence", "index": 99},
        "new_anchor": {"kind": "md_sentence", "index": 1},
        "new_heading_text": "x",
    }
    resp = client.post("/api/notes/relocate", json=payload)
    assert resp.status_code == 404


def test_notes_index_empty(app_client):
    client, repo = app_client
    resp = client.get("/api/notes/index?path=myrepo")
    assert resp.status_code == 200
    assert resp.get_json() == {"files": {}}


def test_notes_index_counts_sections(app_client):
    client, repo = app_client
    (repo / "a.py.notes.md").write_text(
        "## L1\nx\n## L2\ny\n", encoding="utf-8")
    (repo / "b.py.notes.md").write_text(
        "## L1\nx\n## Unresolved\n### L99\nz\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "c.py.notes.md").write_text("## L1\nx\n", encoding="utf-8")
    resp = client.get("/api/notes/index?path=myrepo")
    body = resp.get_json()
    assert body["files"] == {"a.py": 2, "b.py": 2}  # only top-level
    resp2 = client.get("/api/notes/index?path=myrepo/sub")
    assert resp2.get_json()["files"] == {"c.py": 1}


from explorer.notes import (
    RESOLUTION_STATUSES,
    count_sections_by_status,
    get_resolution,
    set_resolution,
)


def test_get_resolution_missing_returns_default_todo():
    assert get_resolution(None) == {"status": "todo"}
    assert get_resolution({}) == {"status": "todo"}
    assert get_resolution({"kind": "lines", "start": 1, "end": 1}) == {"status": "todo"}


def test_get_resolution_returns_stored_fields():
    snap = {
        "kind": "srt", "start_ms": 0, "end_ms": 1,
        "resolution": {
            "status": "done",
            "resolved_at": "2026-05-23T14:00:00+09:00",
            "ref": "16a1943",
        },
    }
    res = get_resolution(snap)
    assert res == {
        "status": "done",
        "resolved_at": "2026-05-23T14:00:00+09:00",
        "ref": "16a1943",
    }


def test_get_resolution_unknown_status_falls_back_to_todo():
    snap = {"resolution": {"status": "garbage"}}
    assert get_resolution(snap) == {"status": "todo"}


def test_get_resolution_returns_fresh_dict():
    """Callers must not be able to mutate the source snapshot via the result."""
    snap = {"resolution": {"status": "done", "resolved_at": "x", "ref": "r"}}
    out = get_resolution(snap)
    out["status"] = "todo"
    assert snap["resolution"]["status"] == "done"


def test_set_resolution_done_with_ref():
    snap = {"kind": "lines", "start": 1, "end": 1, "text": "x"}
    set_resolution(snap, "done", ref="16a1943")
    assert snap["resolution"]["status"] == "done"
    assert snap["resolution"]["ref"] == "16a1943"
    assert "resolved_at" in snap["resolution"]


def test_set_resolution_wontfix_without_ref():
    snap = {"kind": "srt", "start_ms": 0, "end_ms": 1}
    set_resolution(snap, "wontfix")
    assert snap["resolution"]["status"] == "wontfix"
    assert "ref" not in snap["resolution"]


def test_set_resolution_todo_removes_field():
    snap = {"resolution": {"status": "done", "resolved_at": "x", "ref": "r"}}
    set_resolution(snap, "todo")
    assert "resolution" not in snap


def test_set_resolution_rejects_unknown_status():
    import pytest
    with pytest.raises(ValueError):
        set_resolution({}, "garbage")


def test_set_resolution_explicit_timestamp_preserved():
    snap = {}
    set_resolution(snap, "done", ref="abc", resolved_at="2026-01-01T00:00:00+09:00")
    assert snap["resolution"]["resolved_at"] == "2026-01-01T00:00:00+09:00"


def test_resolution_survives_snapshot_roundtrip():
    """Existing encode/decode/parse should preserve the resolution field unchanged."""
    snap = {
        "kind": "srt", "start_ms": 1000, "end_ms": 2000, "cue_index": 5, "text": "字幕",
        "resolution": {
            "status": "done",
            "resolved_at": "2026-05-23T14:00:00+09:00",
            "ref": "16a1943",
        },
    }
    assert decode_snapshot(encode_snapshot(snap)) == snap


def test_resolution_survives_serialize_parse_roundtrip():
    original = NotesDoc(
        title="Notes for output.srt",
        resolved=[
            NotesSection(
                anchor={"kind": "srt", "start_ms": 0, "end_ms": 1},
                snapshot={
                    "kind": "srt", "start_ms": 0, "end_ms": 1, "text": "x",
                    "resolution": {"status": "wontfix", "resolved_at": "2026-05-23T14:00:00+09:00"},
                },
                body="個人感想",
            ),
        ],
    )
    text = serialize_notes_md(original)
    parsed = parse_notes_md(text)
    assert get_resolution(parsed.resolved[0].snapshot) == {
        "status": "wontfix",
        "resolved_at": "2026-05-23T14:00:00+09:00",
    }


def test_count_sections_by_status_empty():
    doc = NotesDoc()
    assert count_sections_by_status(doc) == {s: 0 for s in RESOLUTION_STATUSES}


def test_count_sections_by_status_mixed():
    def mk(status):
        snap = {"kind": "lines", "start": 1, "end": 1}
        if status is not None:
            set_resolution(snap, status, ref="x")
        return NotesSection(anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=snap, body="")

    doc = NotesDoc(
        resolved=[mk(None), mk("done"), mk("done"), mk("wontfix")],
        unresolved=[mk(None), mk("done")],
    )
    counts = count_sections_by_status(doc)
    assert counts == {"todo": 2, "done": 3, "wontfix": 1}


def test_count_sections_by_status_treats_missing_snapshot_as_todo():
    sec = NotesSection(anchor={"kind": "lines", "start": 1, "end": 1}, snapshot=None, body="")
    doc = NotesDoc(resolved=[sec])
    assert count_sections_by_status(doc) == {"todo": 1, "done": 0, "wontfix": 0}
