// Files tab: directory listing, breadcrumb and per-folder sort settings.

// --- Files tab ---

// --- File sort: per-directory + global default ---
const SORT_DEFAULT_KEY = 'explorer_tree_sort_default';
const SORT_BY_DIR_KEY = 'explorer_tree_sort_by_dir';
const SORT_KEY_LABELS = { name: 'Name', mtime: 'Modified', type: 'Type' };
const SORT_KEY_ORDER = ['name', 'mtime', 'type'];
const DEFAULT_SORT_CONFIG = { keys: [{ key: 'name', dir: 'asc' }], foldersFirst: true };

function _readSortDefault() {
  try {
    const v = JSON.parse(localStorage.getItem(SORT_DEFAULT_KEY) || 'null');
    if (v && Array.isArray(v.keys) && v.keys.length > 0) return v;
  } catch {}
  return JSON.parse(JSON.stringify(DEFAULT_SORT_CONFIG));
}
function _readSortByDir() {
  try { return JSON.parse(localStorage.getItem(SORT_BY_DIR_KEY) || '{}') || {}; } catch { return {}; }
}
function _writeSortByDir(map) {
  localStorage.setItem(SORT_BY_DIR_KEY, JSON.stringify(map));
}

function getSortConfig(dir) {
  const map = _readSortByDir();
  if (map[dir] && Array.isArray(map[dir].keys) && map[dir].keys.length > 0) return map[dir];
  return _readSortDefault();
}
function isDirSortOverridden(dir) {
  const map = _readSortByDir();
  return !!map[dir];
}
function saveSortConfigForDir(dir, cfg) {
  const map = _readSortByDir();
  map[dir] = cfg;
  _writeSortByDir(map);
}
function clearSortConfigForDir(dir) {
  const map = _readSortByDir();
  delete map[dir];
  _writeSortByDir(map);
}
function saveSortDefault(cfg) {
  localStorage.setItem(SORT_DEFAULT_KEY, JSON.stringify(cfg));
}

const _sortCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
function _entryExt(e) {
  if (e.type === 'tree') return '';
  const dot = e.name.lastIndexOf('.');
  return dot > 0 ? e.name.substring(dot + 1).toLowerCase() : '';
}
function _compareByKey(a, b, key) {
  if (key === 'name') return _sortCollator.compare(a.name, b.name);
  if (key === 'mtime') {
    const ax = +a.mtime || 0, bx = +b.mtime || 0;
    return ax < bx ? -1 : ax > bx ? 1 : 0;
  }
  if (key === 'type') return _sortCollator.compare(_entryExt(a), _entryExt(b));
  return 0;
}
function sortEntries(entries, cfg) {
  const arr = entries.slice();
  const keys = (cfg.keys || []).filter(k => SORT_KEY_LABELS[k.key]);
  arr.sort((a, b) => {
    if (cfg.foldersFirst) {
      const ax = a.type === 'tree' ? 0 : 1;
      const bx = b.type === 'tree' ? 0 : 1;
      if (ax !== bx) return ax - bx;
    }
    for (const { key, dir } of keys) {
      const c = _compareByKey(a, b, key);
      if (c !== 0) return dir === 'desc' ? -c : c;
    }
    return _sortCollator.compare(a.name, b.name);
  });
  return arr;
}

// --- Sort popover UI ---
let _sortPopoverEditing = null; // working copy of cfg while popover is open

function toggleSortPopover(ev) {
  ev && ev.stopPropagation && ev.stopPropagation();
  const existing = document.getElementById('sort-popover');
  if (existing) { closeSortPopover(); return; }
  openSortPopover(ev && ev.currentTarget);
}

function openSortPopover(anchorEl) {
  _sortPopoverEditing = JSON.parse(JSON.stringify(getSortConfig(currentPath)));
  const pop = document.createElement('div');
  pop.id = 'sort-popover';
  pop.className = 'sort-popover';
  pop.onclick = e => e.stopPropagation();
  document.body.appendChild(pop);
  renderSortPopover();

  if (anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    const top = window.scrollY + r.bottom + 4;
    pop.style.top = top + 'px';
    // Anchor right edge to button right edge, then clamp into viewport
    pop.style.left = '0px';
    const pw = pop.offsetWidth;
    let left = window.scrollX + r.right - pw;
    if (left < 8) left = 8;
    pop.style.left = left + 'px';
  }

  setTimeout(() => {
    document.addEventListener('click', _sortPopoverOutsideClick, true);
    document.addEventListener('keydown', _sortPopoverKeydown, true);
  }, 0);
}

function closeSortPopover() {
  const pop = document.getElementById('sort-popover');
  if (pop) pop.remove();
  _sortPopoverEditing = null;
  document.removeEventListener('click', _sortPopoverOutsideClick, true);
  document.removeEventListener('keydown', _sortPopoverKeydown, true);
}

function _sortPopoverOutsideClick(e) {
  const pop = document.getElementById('sort-popover');
  if (!pop) return;
  if (pop.contains(e.target)) return;
  if (e.target.closest && e.target.closest('.sort-button')) return;
  closeSortPopover();
}
function _sortPopoverKeydown(e) {
  if (e.key === 'Escape') closeSortPopover();
}

function renderSortPopover() {
  const pop = document.getElementById('sort-popover');
  if (!pop || !_sortPopoverEditing) return;
  const cfg = _sortPopoverEditing;
  const used = new Set(cfg.keys.map(k => k.key));
  const unused = SORT_KEY_ORDER.filter(k => !used.has(k));
  const overridden = isDirSortOverridden(currentPath);

  const rows = cfg.keys.map((k, i) => `
    <div class="sort-priority-row">
      <span class="sort-priority-idx">${i + 1}.</span>
      <span class="sort-priority-label">${SORT_KEY_LABELS[k.key]}</span>
      <button class="sort-arrow${k.dir === 'asc' ? ' active' : ''}" onclick="setSortDir(${i}, 'asc')" title="昇順">↑</button>
      <button class="sort-arrow${k.dir === 'desc' ? ' active' : ''}" onclick="setSortDir(${i}, 'desc')" title="降順">↓</button>
      <button class="sort-move" onclick="moveSortKey(${i}, -1)" ${i === 0 ? 'disabled' : ''} title="上へ">▲</button>
      <button class="sort-move" onclick="moveSortKey(${i}, 1)" ${i === cfg.keys.length - 1 ? 'disabled' : ''} title="下へ">▼</button>
      <button class="sort-remove" onclick="removeSortKey(${i})" ${cfg.keys.length <= 1 ? 'disabled' : ''} title="削除">×</button>
    </div>
  `).join('');

  const addOptions = unused.length === 0
    ? `<span style="color:var(--text-muted);">（追加可能なキーなし）</span>`
    : `<select id="sort-add-select">${unused.map(k => `<option value="${k}">${SORT_KEY_LABELS[k]}</option>`).join('')}</select>
       <button class="sort-add-btn" onclick="addSortKey()">+ 追加</button>`;

  pop.innerHTML = `
    <div class="sort-popover-title">並び替え優先順位</div>
    <div class="sort-priority-list">${rows}</div>
    <div class="sort-add-row">${addOptions}</div>
    <label class="sort-folders-first">
      <input type="checkbox" ${cfg.foldersFirst ? 'checked' : ''} onchange="setFoldersFirst(this.checked)">
      フォルダを先頭に
    </label>
    <div class="sort-popover-actions">
      <button onclick="applySortToDir()" title="このディレクトリだけに適用">このフォルダのみ</button>
      <button onclick="applySortAsDefault()" title="全ディレクトリの既定値として保存">デフォルトに設定</button>
      <button onclick="resetSortForDir()" ${overridden ? '' : 'disabled'} title="このディレクトリの設定を削除してデフォルトに戻す">リセット</button>
      <button onclick="closeSortPopover()">閉じる</button>
    </div>
  `;
}

function _afterSortChange() {
  // Auto-save: while popover is open, any edit applies to current dir immediately.
  saveSortConfigForDir(currentPath, _sortPopoverEditing);
  renderSortPopover();
  rerenderFilesFromCache();
}

function setSortDir(idx, dir) {
  if (!_sortPopoverEditing) return;
  const k = _sortPopoverEditing.keys[idx];
  if (!k) return;
  k.dir = dir;
  _afterSortChange();
}
function moveSortKey(idx, delta) {
  if (!_sortPopoverEditing) return;
  const arr = _sortPopoverEditing.keys;
  const j = idx + delta;
  if (j < 0 || j >= arr.length) return;
  [arr[idx], arr[j]] = [arr[j], arr[idx]];
  _afterSortChange();
}
function removeSortKey(idx) {
  if (!_sortPopoverEditing) return;
  const arr = _sortPopoverEditing.keys;
  if (arr.length <= 1) return;
  arr.splice(idx, 1);
  _afterSortChange();
}
function addSortKey() {
  if (!_sortPopoverEditing) return;
  const sel = document.getElementById('sort-add-select');
  if (!sel || !sel.value) return;
  if (_sortPopoverEditing.keys.some(k => k.key === sel.value)) return;
  _sortPopoverEditing.keys.push({ key: sel.value, dir: 'asc' });
  _afterSortChange();
}
function setFoldersFirst(checked) {
  if (!_sortPopoverEditing) return;
  _sortPopoverEditing.foldersFirst = !!checked;
  _afterSortChange();
}
function applySortToDir() {
  if (!_sortPopoverEditing) return;
  saveSortConfigForDir(currentPath, _sortPopoverEditing);
  renderSortPopover();
  rerenderFilesFromCache();
}
function applySortAsDefault() {
  if (!_sortPopoverEditing) return;
  saveSortDefault(_sortPopoverEditing);
  clearSortConfigForDir(currentPath);
  renderSortPopover();
  rerenderFilesFromCache();
}
function resetSortForDir() {
  clearSortConfigForDir(currentPath);
  _sortPopoverEditing = JSON.parse(JSON.stringify(getSortConfig(currentPath)));
  renderSortPopover();
  rerenderFilesFromCache();
}

async function renderFiles(path) {
  clearBlobUi();
  if (path !== undefined) currentPath = path;
  saveState();
  const entries = await fetchJson(`/api/tree?path=${encodeURIComponent(currentPath)}`);
  if (Array.isArray(entries)) lastDirFetch = { key: currentPath, entries };

  renderFilesView(entries);
}

function renderFilesView(entries) {
  const breadcrumb = buildBreadcrumb(currentPath);
  const sortCfg = getSortConfig(currentPath);
  const sorted = Array.isArray(entries) ? sortEntries(entries, sortCfg) : [];
  const sortLabel = formatSortLabel(sortCfg);
  const overridden = isDirSortOverridden(currentPath);

  content.innerHTML = `
    <div class="section-header" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
      <div style="display:flex;align-items:center;gap:4px;flex:1;min-width:0;">${breadcrumb}</div>
      <button class="sort-button${overridden ? ' overridden' : ''}" onclick="toggleSortPopover(event)" title="${overridden ? 'このフォルダで上書き中' : 'デフォルト適用中'}">
        Sort: ${esc(sortLabel)} ▾
      </button>
    </div>
    <div class="list-box">
      ${currentPath ? `<div class="list-row clickable" onclick="renderFiles('${parentPath(currentPath)}')"><span class="tree-icon">${ICON_FOLDER_OPEN}</span><span>..</span></div>` : ''}
      ${sorted.map(e => {
        const t = formatMtime(e.mtime);
        const time = t ? `<span class="file-mtime">${t}</span>` : '';
        if (e.type === 'tree') {
          const isFav = favorites.includes(e.path);
          return `<div class="list-row clickable" onclick="renderFiles('${esc(e.path)}')"><span class="tree-icon">${ICON_FOLDER}</span><span class="file-name">${esc(e.name)}</span>${time}<span class="fav-star ${isFav ? 'active' : ''}" onclick="toggleFavorite(event, '${esc(e.path)}')">${isFav ? '★' : '☆'}</span></div>`;
        }
        return `<div class="list-row clickable" data-blob-path="${esc(e.path)}" onclick="showBlob('${esc(e.path)}')"><span class="tree-icon">${ICON_FILE}</span><span class="file-name">${esc(e.name)}</span>${time}</div>`;
      }).join('')}
    </div>
    <div id="blob-view"></div>
  `;
  decorateNotesBadges();
}

function formatMtime(mtime) {
  if (!mtime || !isFinite(mtime)) return '';
  const d = new Date(mtime * 1000);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hms = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${mm}/${dd} ${hms}`;
  }
  return `${pad(d.getFullYear() % 100)}/${mm}/${dd} ${hms}`;
}

function formatSortLabel(cfg) {
  const arrow = d => d === 'desc' ? '↓' : '↑';
  const parts = (cfg.keys || []).map(k => `${SORT_KEY_LABELS[k.key] || k.key} ${arrow(k.dir)}`);
  if (parts.length === 0) return 'Name ↑';
  return parts.join(' → ');
}

function rerenderFilesFromCache() {
  const key = currentPath;
  if (lastDirFetch.key === key && Array.isArray(lastDirFetch.entries)) {
    renderFilesView(lastDirFetch.entries);
  } else {
    renderFiles(currentPath);
  }
}

function buildBreadcrumb(path) {
  let html = `<span class="clickable" onclick="renderFiles('')" style="cursor:pointer;color:var(--accent);">${esc(ROOT_NAME)}</span>`;
  if (path) {
    const parts = path.split('/');
    let acc = '';
    for (const p of parts) {
      acc = acc ? acc + '/' + p : p;
      html += ` / <span class="clickable" onclick="renderFiles('${acc}')" style="cursor:pointer;color:var(--accent);">${esc(p)}</span>`;
    }
  }
  return html;
}

function parentPath(path) {
  const i = path.lastIndexOf('/');
  return i > 0 ? path.substring(0, i) : '';
}
