// === File notes feature ===
let notesState = { path: null, mtime: null, kind: null, resolved: [], unresolved: [] };

async function fetchNotes(path) {
  const url = `/api/notes?path=${encodeURIComponent(path)}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    notesState = {
      path,
      mtime: data.mtime,
      kind: data.kind,
      resolved: data.resolved || [],
      unresolved: data.unresolved || [],
    };
    return notesState;
  } catch (e) {
    notesState = { path, mtime: null, kind: null, resolved: [], unresolved: [] };
    console.warn('fetchNotes failed', e);
    return notesState;
  }
}

async function putNote(anchor, snapshot, body) {
  const payload = {
    path: notesState.path,
    if_match_mtime: notesState.mtime,
    anchor, snapshot, body,
  };
  const resp = await fetch('/api/notes', {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (resp.status === 409) {
    alert('他で更新されたためメモを保存できませんでした。再読込してやり直してください。');
    return null;
  }
  if (!resp.ok) {
    alert('メモの保存に失敗しました');
    return null;
  }
  const data = await resp.json();
  notesState.mtime = data.mtime;
  return data;
}

async function deleteNote(anchor) {
  const payload = {
    path: notesState.path,
    if_match_mtime: notesState.mtime,
    anchor,
  };
  const resp = await fetch('/api/notes', {
    method: 'DELETE', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (resp.status === 409) {
    alert('他で更新されたためメモを削除できませんでした。');
    return null;
  }
  if (!resp.ok) {
    alert('メモの削除に失敗しました');
    return null;
  }
  const data = await resp.json();
  notesState.mtime = data.mtime;
  return data;
}

async function relocateNote(oldAnchor, newAnchor, headingText) {
  const payload = {
    path: notesState.path,
    if_match_mtime: notesState.mtime,
    old_anchor: oldAnchor, new_anchor: newAnchor, new_heading_text: headingText,
  };
  const resp = await fetch('/api/notes/relocate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!resp.ok) return null;
  const data = await resp.json();
  notesState.mtime = data.mtime;
  return data;
}

function findResolvedNote(anchorMatch) {
  return notesState.resolved.find(s => anchorMatch(s.anchor));
}

function decorateTextNotes(blobView, path) {
  const linesByStart = new Map();
  for (const sec of notesState.resolved) {
    if (sec.anchor.kind !== 'lines') continue;
    linesByStart.set(sec.anchor.start, sec);
  }
  blobView.querySelectorAll('.code-line[data-bm-line]').forEach(line => {
    const lineNo = Number(line.getAttribute('data-bm-line'));
    const sec = linesByStart.get(lineNo);
    const marker = makeNoteMarker(sec, () => {
      const anchor = { kind: 'lines', start: lineNo, end: lineNo };
      const snapshot = { kind: 'lines', start: lineNo, end: lineNo, text: line.textContent };
      openNoteModal({ kind: 'lines', anchor, snapshot });
    });
    line.appendChild(marker);
  });
}

function srtMsToTime(ms) {
  const total = Math.round(ms);
  const h = String(Math.floor(total / 3600000)).padStart(2, '0');
  const m = String(Math.floor((total % 3600000) / 60000)).padStart(2, '0');
  const s = String(Math.floor((total % 60000) / 1000)).padStart(2, '0');
  const r = String(total % 1000).padStart(3, '0');
  return `${h}:${m}:${s},${r}`;
}

function srtTimeToMs(s) {
  const m = /^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$/.exec(s.trim());
  if (!m) return null;
  return ((+m[1] * 60 + +m[2]) * 60 + +m[3]) * 1000 + +m[4];
}

function decorateSrtNotes(blobView, cues) {
  const byKey = new Map();
  for (const sec of notesState.resolved) {
    if (sec.anchor.kind !== 'srt') continue;
    byKey.set(`${sec.anchor.start_ms}-${sec.anchor.end_ms}`, sec);
  }
  blobView.querySelectorAll('.srt-cue').forEach(cueEl => {
    const idx = Number(cueEl.getAttribute('data-bm-idx'));
    const cue = cues[idx];
    if (!cue) return;
    const startMs = srtTimeToMs(cue.start);
    const endMs = srtTimeToMs(cue.end);
    if (startMs == null || endMs == null) return;
    const key = `${startMs}-${endMs}`;
    const sec = byKey.get(key);
    const marker = makeNoteMarker(sec, () => {
      const anchor = { kind: 'srt', start_ms: startMs, end_ms: endMs };
      const snapshot = {
        kind: 'srt', start_ms: startMs, end_ms: endMs,
        cue_index: cue.index ? Number(cue.index) : (idx + 1),
        text: cue.text,
      };
      openNoteModal({ kind: 'srt', anchor, snapshot });
    });
    const meta = cueEl.querySelector('.srt-meta');
    if (meta) meta.appendChild(marker);
  });
}

function decorateMdNotes(blobView) {
  const sentences = blobView.querySelectorAll('[data-bm-idx]');
  const indexByText = new Map();
  sentences.forEach(node => {
    const idx = Number(node.getAttribute('data-bm-idx'));
    const text = node.textContent.trim();
    if (!indexByText.has(text)) indexByText.set(text, []);
    indexByText.get(text).push({ idx, node });
  });

  const sectionsForRelocate = [];
  for (const sec of notesState.resolved) {
    if (sec.anchor.kind !== 'md_sentence') continue;
    let resolvedNode = null;
    let resolvedIdx = null;
    let relocated = false;

    const origNode = blobView.querySelector(`[data-bm-idx="${sec.anchor.index}"]`);
    if (origNode && sec.snapshot && origNode.textContent.trim() === sec.snapshot.text.trim()) {
      resolvedNode = origNode;
      resolvedIdx = sec.anchor.index;
    } else if (sec.snapshot) {
      const candidates = indexByText.get(sec.snapshot.text.trim()) || [];
      if (candidates.length === 0) {
        notesState.unresolved.push({ ...sec, reason: 'MD: 同一の文が見つからなかった' });
        continue;
      }
      const best = candidates.reduce((a, b) =>
        Math.abs(a.idx - sec.anchor.index) <= Math.abs(b.idx - sec.anchor.index) ? a : b
      );
      resolvedNode = best.node;
      resolvedIdx = best.idx;
      relocated = (resolvedIdx !== sec.anchor.index);
    } else if (origNode) {
      resolvedNode = origNode;
      resolvedIdx = sec.anchor.index;
    } else {
      notesState.unresolved.push({ ...sec, reason: 'MD: index がファイル外でスナップショットなし' });
      continue;
    }

    if (relocated) {
      sectionsForRelocate.push({
        old_anchor: sec.anchor,
        new_anchor: { kind: 'md_sentence', index: resolvedIdx },
        heading_text: (sec.snapshot?.text || '').slice(0, 30),
      });
    }
    sec.anchor = { ...sec.anchor, index: resolvedIdx };

    const marker = makeNoteMarker(sec, null);
    resolvedNode.appendChild(marker);
  }

  sentences.forEach(node => {
    if (node.querySelector('.note-marker')) return;
    const idx = Number(node.getAttribute('data-bm-idx'));
    const text = node.textContent.trim();
    const marker = makeNoteMarker(null, () => {
      const anchor = { kind: 'md_sentence', index: idx };
      const snapshot = { kind: 'md_sentence', index: idx, text };
      openNoteModal({ kind: 'md_sentence', anchor, snapshot });
    });
    node.appendChild(marker);
  });

  for (const r of sectionsForRelocate) {
    relocateNote(r.old_anchor, r.new_anchor, r.heading_text).catch(() => {});
  }
}

function openNoteModal(opts) {
  const isEdit = !!opts.existing;
  const anchor = isEdit ? opts.existing.anchor : opts.anchor;
  const snapshot = isEdit ? opts.existing.snapshot : opts.snapshot;
  const initialBody = isEdit ? opts.existing.body : '';
  const anchorLabel = formatAnchorLabel(anchor);

  const backdrop = document.createElement('div');
  backdrop.className = 'note-modal-backdrop';
  backdrop.innerHTML = `
    <div class="note-modal" onclick="event.stopPropagation()">
      <h3>${isEdit ? 'メモを編集' : 'メモを追加'}</h3>
      <div class="note-modal-anchor">${esc(anchorLabel)}</div>
      ${noteTimestampHtml(snapshot)}
      <textarea class="note-body"></textarea>
      <div class="note-modal-actions">
        ${isEdit ? '<button class="btn-delete">削除</button>' : ''}
        <button class="btn-cancel">キャンセル</button>
        <button class="btn-save">保存</button>
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  activeNoteModals++;
  const textarea = backdrop.querySelector('.note-body');
  textarea.value = initialBody;
  textarea.focus();

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    activeNoteModals = Math.max(0, activeNoteModals - 1);
    backdrop.remove();
  };
  backdrop.onclick = close;
  backdrop.querySelector('.btn-cancel').onclick = close;
  backdrop.querySelector('.btn-save').onclick = async () => {
    const body = textarea.value;
    const result = await putNote(anchor, snapshot, body);
    if (result) {
      close();
      await reloadCurrentBlob();
    }
  };
  if (isEdit) {
    backdrop.querySelector('.btn-delete').onclick = async () => {
      if (!confirm('このメモを削除しますか？')) return;
      const result = await deleteNote(anchor);
      if (result !== null) {
        close();
        await reloadCurrentBlob();
      }
    };
  }
}

// --- Note markers + resolution status -------------------------------------

const NOTE_STATUS_META = {
  todo:    { label: '未反映' },
  done:    { label: '反映済み' },
  wontfix: { label: '反映不要' },
};

function noteStatusOf(sec) {
  const r = sec && sec.snapshot && sec.snapshot.resolution;
  const s = r && r.status;
  return (s === 'done' || s === 'wontfix') ? s : 'todo';
}

// Mirrors notes.py:_anchor_key — returns a string usable as Map key.
function anchorKeyJs(a) {
  if (!a || !a.kind) return '';
  if (a.kind === 'lines') return `lines:${a.start}:${a.end}`;
  if (a.kind === 'md_sentence') return `md_sentence:${a.index}`;
  if (a.kind === 'srt') return `srt:${a.start_ms}:${a.end_ms}`;
  return a.kind;
}

// Mirrors notes.py:parse_anchor_heading. Returns null if heading text doesn't
// match any known anchor pattern.
const _HD_LINES = /^L(\d+)(?:-(\d+))?$/;
const _HD_MD = /^S(\d+)(?:\s+"((?:[^"\\]|\\.)*)")?$/;
const _HD_SRT = /^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$/;
function parseAnchorHeadingText(text) {
  const t = (text || '').trim();
  if (!t || t === 'Unresolved') return null;
  let m = _HD_LINES.exec(t);
  if (m) {
    const start = +m[1];
    const end = m[2] ? +m[2] : start;
    return { kind: 'lines', start, end };
  }
  m = _HD_MD.exec(t);
  if (m) {
    const raw = m[2] || '';
    return {
      kind: 'md_sentence',
      index: +m[1],
      heading_text: raw.replace(/\\(.)/g, '$1'),
    };
  }
  m = _HD_SRT.exec(t);
  if (m) {
    const ms = (h, mn, s, msv) => ((+h * 60 + +mn) * 60 + +s) * 1000 + +msv;
    return {
      kind: 'srt',
      start_ms: ms(m[1], m[2], m[3], m[4]),
      end_ms: ms(m[5], m[6], m[7], m[8]),
    };
  }
  return null;
}

// Decorate the rendered Markdown of a *.notes.md file with status badges per
// section heading. Each badge is clickable and opens the existing status
// popover (which reuses setNoteResolution → notesState.path = target file).
function decorateNotesMdView(mdBody) {
  if (!mdBody) return;
  const byKey = new Map();
  for (const sec of notesState.resolved || []) byKey.set(anchorKeyJs(sec.anchor), sec);
  for (const sec of notesState.unresolved || []) {
    const k = anchorKeyJs(sec.anchor);
    if (!byKey.has(k)) byKey.set(k, sec);
  }
  if (byKey.size === 0) return;

  const headingText = (h) => {
    // Concatenate direct text content only, skipping any child elements
    // (e.g. existing note markers or our own badge from a re-decoration).
    let s = '';
    for (const n of h.childNodes) {
      if (n.nodeType === 3) s += n.nodeValue;
    }
    return s.trim();
  };
  for (const h of mdBody.querySelectorAll('h2, h3')) {
    if (h.querySelector('.notes-md-status')) continue;
    const anchor = parseAnchorHeadingText(headingText(h));
    if (!anchor) continue;
    const sec = byKey.get(anchorKeyJs(anchor));
    if (!sec) continue;
    const status = noteStatusOf(sec);
    const badge = document.createElement('span');
    badge.className = `notes-md-status status-${status}`;
    badge.innerHTML = `<span class="dot">●</span><span>${NOTE_STATUS_META[status].label}</span>`;
    badge.title = `${NOTE_STATUS_META[status].label} · クリックで変更`;
    badge.onclick = (e) => { e.stopPropagation(); openNotePopover(badge, sec); };
    h.appendChild(badge);

    // Inline-display the snapshot text (what the note was attached to) so
    // users don't have to open the edit view to remember the original quote.
    if (sec.snapshot && typeof sec.snapshot.text === 'string' && sec.snapshot.text.trim()) {
      // Avoid duplicating if a previous decoration already inserted one.
      const next = h.nextElementSibling;
      if (!next || !next.classList.contains('notes-md-snippet')) {
        const snippet = document.createElement('blockquote');
        snippet.className = 'notes-md-snippet';
        snippet.textContent = sec.snapshot.text;
        h.insertAdjacentElement('afterend', snippet);
      }
    }
  }
}

// sec != null → has-note status dot (click opens the status popover).
// sec == null → faint add-note ＋ (click runs onAdd to create a new note).
function makeNoteMarker(sec, onAdd) {
  const marker = document.createElement('span');
  if (sec) {
    const status = noteStatusOf(sec);
    marker.className = `note-marker has-note status-${status}`;
    marker.textContent = '●';
    marker.title = `${NOTE_STATUS_META[status].label} · クリックで変更`;
    marker.onclick = (e) => { e.stopPropagation(); openNotePopover(marker, sec); };
  } else {
    marker.className = 'note-marker add-note';
    marker.textContent = '＋';
    marker.title = 'メモを追加';
    marker.onclick = (e) => { e.stopPropagation(); if (onAdd) onAdd(); };
  }
  return marker;
}

function nowIsoOffset() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? '+' : '-';
  const oh = p(Math.floor(Math.abs(off) / 60));
  const om = p(Math.abs(off) % 60);
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${oh}:${om}`;
}

// Build the updated snapshot and persist via the existing PUT /api/notes.
// status === 'todo' drops the resolution field (matches notes.py minimization).
async function setNoteResolution(sec, status) {
  const snap = Object.assign({}, sec.snapshot || {});
  if (status === 'todo') {
    delete snap.resolution;
  } else {
    const prevRef = sec.snapshot && sec.snapshot.resolution && sec.snapshot.resolution.ref;
    snap.resolution = { status, resolved_at: nowIsoOffset() };
    if (prevRef) snap.resolution.ref = prevRef;
  }
  const result = await putNote(sec.anchor, snap, sec.body);
  if (result) await reloadCurrentBlob();
}

let activeNotePopover = null;
function closeNotePopover() {
  if (activeNotePopover) { activeNotePopover.remove(); activeNotePopover = null; }
  document.removeEventListener('click', closeNotePopover, true);
}

function openNotePopover(markerEl, sec) {
  closeNotePopover();
  const current = noteStatusOf(sec);
  const pop = document.createElement('div');
  pop.className = 'note-popover';
  pop.onclick = (e) => e.stopPropagation();

  for (const st of ['todo', 'done', 'wontfix']) {
    const item = document.createElement('div');
    item.className = 'note-popover-item' + (st === current ? ' is-current' : '');
    item.innerHTML =
      `<span class="dot status-${st}">●</span>` +
      `<span>${NOTE_STATUS_META[st].label}</span>` +
      (st === current ? '<span class="check">✓</span>' : '');
    item.onclick = async () => {
      closeNotePopover();
      if (st !== current) await setNoteResolution(sec, st);
    };
    pop.appendChild(item);
  }

  const sep = document.createElement('div');
  sep.className = 'note-popover-sep';
  pop.appendChild(sep);

  const edit = document.createElement('div');
  edit.className = 'note-popover-item';
  edit.innerHTML = '<span>📝</span><span>メモを編集</span>';
  edit.onclick = () => {
    closeNotePopover();
    openNoteModal({ kind: sec.anchor.kind, existing: sec });
  };
  pop.appendChild(edit);

  document.body.appendChild(pop);
  const r = markerEl.getBoundingClientRect();
  const top = window.scrollY + r.bottom + 4;
  let left = window.scrollX + r.left;
  const maxLeft = window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 8;
  if (left > maxLeft) left = maxLeft;
  pop.style.top = `${top}px`;
  pop.style.left = `${Math.max(8, left)}px`;
  activeNotePopover = pop;
  setTimeout(() => document.addEventListener('click', closeNotePopover, true), 0);
}

function formatAnchorLabel(a) {
  if (a.kind === 'lines') {
    return a.start === a.end ? `L${a.start}` : `L${a.start}-${a.end}`;
  }
  if (a.kind === 'md_sentence') return `S${a.index}`;
  if (a.kind === 'srt') return `${srtMsToTime(a.start_ms)} --> ${srtMsToTime(a.end_ms)}`;
  return JSON.stringify(a);
}

function formatNoteTs(iso) {
  if (typeof iso !== 'string' || !iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function noteTimestampHtml(snapshot) {
  if (!snapshot) return '';
  const c = formatNoteTs(snapshot.created_at);
  const u = formatNoteTs(snapshot.updated_at);
  if (!c && !u) return '';
  if (c && u && c !== u) return `<div class="note-ts">作成 ${esc(c)} · 更新 ${esc(u)}</div>`;
  const t = c || u;
  return `<div class="note-ts">作成 ${esc(t)}</div>`;
}

async function reloadCurrentBlob() {
  if (notesState.embedMode) {
    await reloadEmbedNotes();
    return;
  }
  // When viewing a *.notes.md directly, notesState.path is the *target* file
  // (so PUT/DELETE go to the right place), but the user is still looking at
  // the notes.md — reload that instead.
  const displayPath = notesState.viewPath || notesState.path;
  if (displayPath) {
    await showBlob(displayPath);
  }
}

function decorateEmbeddedSrtNotes(container, cues) {
  const byKey = new Map();
  for (const sec of notesState.resolved) {
    if (sec.anchor.kind !== 'srt') continue;
    byKey.set(`${sec.anchor.start_ms}-${sec.anchor.end_ms}`, sec);
  }
  container.querySelectorAll('.srt-cue').forEach((cueEl, idx) => {
    const cue = cues[idx];
    if (!cue) return;
    const startMs = srtTimeToMs(cue.start);
    const endMs = srtTimeToMs(cue.end);
    if (startMs == null || endMs == null) return;
    const key = `${startMs}-${endMs}`;
    const sec = byKey.get(key);
    const marker = makeNoteMarker(sec, () => {
      const anchor = { kind: 'srt', start_ms: startMs, end_ms: endMs };
      const snapshot = {
        kind: 'srt', start_ms: startMs, end_ms: endMs,
        cue_index: idx + 1, text: cue.text,
      };
      openNoteModal({ kind: 'srt', anchor, snapshot });
    });
    const meta = cueEl.querySelector('.srt-meta');
    if (meta) meta.appendChild(marker);
  });
}

async function reloadEmbedNotes() {
  const container = document.getElementById('srt-embed');
  const cues = notesState.embedCues;
  const path = notesState.path;
  if (!container || !cues || !path) return;
  container.querySelectorAll('.note-marker').forEach(m => m.remove());
  await fetchNotes(path);
  notesState.embedMode = true;
  notesState.embedCues = cues;
  decorateEmbeddedSrtNotes(container, cues);
}

async function decorateNotesBadges() {
  const url = `/api/notes/index?path=${encodeURIComponent(currentPath)}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) return;
    const data = await resp.json();
    const counts = data.files || {};
    document.querySelectorAll('#content .list-row').forEach(row => {
      const nameSpan = row.querySelector('span:nth-child(2)');
      if (!nameSpan) return;
      const name = nameSpan.textContent;
      const count = counts[name];
      if (count) {
        const badge = document.createElement('span');
        badge.className = 'notes-badge';
        badge.textContent = `💬${count}`;
        nameSpan.appendChild(badge);
      }
    });
  } catch (e) {
    console.warn('decorateNotesBadges failed', e);
  }
}

function renderUnresolvedPanel(blobView) {
  blobView.querySelectorAll('.unresolved-panel').forEach(p => p.remove());
  if (!notesState.unresolved.length) return;

  const panel = document.createElement('details');
  panel.className = 'unresolved-panel';
  panel.open = true;
  const summary = document.createElement('summary');
  summary.textContent = `未解決メモ (${notesState.unresolved.length})`;
  panel.appendChild(summary);

  for (const sec of notesState.unresolved) {
    const item = document.createElement('div');
    item.className = 'unresolved-item';
    const anchorLabel = formatAnchorLabel(sec.anchor);
    const reason = sec.reason || '理由不明';
    item.innerHTML = `
      <div class="anchor">${esc(anchorLabel)}</div>
      <div class="reason">${esc(reason)}</div>
      ${noteTimestampHtml(sec.snapshot)}
      <div class="body">${esc(sec.body || '')}</div>
      <div class="actions">
        <button class="btn-delete-unresolved">削除</button>
      </div>`;
    item.querySelector('.btn-delete-unresolved').onclick = async () => {
      if (!confirm('この未解決メモを削除しますか？')) return;
      await deleteNote(sec.anchor);
      await reloadCurrentBlob();
    };
    panel.appendChild(item);
  }
  blobView.appendChild(panel);
}
