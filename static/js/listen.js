// --- Listen (playback overview dashboard) ---
// ファイルタブで開いているフォルダ以下の全 output.mp3 の再生ログ (.playback.jsonl) を
// サーバ側で集約し、どれをどこまで聞いたかを横断一覧する。カバレッジ% (聴いた区間の
// 合計) と到達% (最遠到達) を併記。
const LISTEN_COLLAPSED_KEY = 'explorer_listen_collapsed';
let listenItems = [];
let listenScope = '';
let listenFilter = 'all';
let listenSort = 'name';

// 大フォルダー = プロジェクトフォルダの親 (projects 直下 / books / books/<series>)。
// 表示名は走査起点からの相対パス (起点がルートなら "irodori_studio/projects" など)。
function listenGroupKey(it) { return it.dir.split('/').slice(0, -1).join('/'); }
function listenGroupLabel(key) {
  const base = listenScope ? listenScope + '/' : '';
  const rel = key.startsWith(base) ? key.slice(base.length) : key;
  return rel || listenScope.split('/').pop() || ROOT_NAME;
}

// グループの折り畳み状態は localStorage に保持 (再描画・再訪でも維持)。
function getListenCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem(LISTEN_COLLAPSED_KEY)) || []); }
  catch { return new Set(); }
}
function toggleListenGroup(key) {
  const c = getListenCollapsed();
  c.has(key) ? c.delete(key) : c.add(key);
  localStorage.setItem(LISTEN_COLLAPSED_KEY, JSON.stringify([...c]));
  renderListenView();
}

function fmtDur(sec) {
  if (!sec || !isFinite(sec) || sec <= 0) return '--:--';
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function parseProjectName(name) {
  const m = name.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_(.+)$/);
  if (!m) return { topic: name, when: '' };
  return { topic: m[6].replace(/_/g, ' '), when: `${m[2]}/${m[3]} ${m[4]}:${m[5]}` };
}

function listenCovPct(it) {
  if (!it.duration_sec) return null;
  return Math.min(100, Math.round(it.covered_sec / it.duration_sec * 100));
}
function listenReachPct(it) {
  if (!it.duration_sec) return null;
  return Math.min(100, Math.round(it.reach_sec / it.duration_sec * 100));
}
function listenStatus(it) {
  if (!it.sessions) return 'unlistened';
  const cov = listenCovPct(it);
  if (cov !== null && cov >= 90) return 'done';
  return 'progress';
}

async function renderListen() {
  content.innerHTML = '<div class="empty-msg">読み込み中…</div>';
  listenScope = currentPath;
  const items = await fetchJson(`/api/playback-overview?path=${encodeURIComponent(listenScope)}`);
  listenItems = Array.isArray(items) ? items : [];
  renderListenView();
}

function listenComparator(a, b) {
  switch (listenSort) {
    case 'played': return (b.last_played || '').localeCompare(a.last_played || '');
    case 'name': return a.name.localeCompare(b.name);
    case 'unlistenedFirst': {
      const ca = listenCovPct(a) ?? 0, cb = listenCovPct(b) ?? 0;
      if (ca !== cb) return ca - cb;
      return (b.mtime || 0) - (a.mtime || 0);
    }
    default: return (b.mtime || 0) - (a.mtime || 0);
  }
}

function listenRowHtml(it) {
  const { topic, when } = parseProjectName(it.name);
  const status = listenStatus(it);
  const cov = listenCovPct(it);
  const reach = listenReachPct(it);
  const statusIcon = { unlistened: '○', progress: '◐', done: '●' }[status];
  const covW = cov ?? 0;
  const marker = (reach !== null && reach > covW)
    ? `<span class="listen-reach-mark" style="left:${reach}%" title="到達 ${reach}%"></span>` : '';
  const last = it.last_played ? timeAgo(it.last_played) : '未再生';
  const pct = cov === null ? '再生記録あり'
    : `カバレッジ ${cov}%${reach !== null ? ` · 到達 ${reach}%` : ''}`;
  const meta = `${pct} · ${fmtDur(it.duration_sec)} · ${last}`;
  return `
    <div class="listen-row listen-${status} clickable" onclick="openAudioFromOverview('${esc(it.dir)}','${esc(it.audio)}')">
      <span class="listen-status listen-status-${status}">${statusIcon}</span>
      <div class="listen-main">
        <div class="listen-title">${esc(topic)}${when ? `<span class="listen-date">${when}</span>` : ''}</div>
        <div class="listen-metrics">
          <span class="listen-bar"><span class="listen-bar-cov" style="width:${covW}%"></span>${marker}</span>
          <span class="listen-meta">${meta}</span>
        </div>
      </div>
    </div>`;
}

function renderListenView() {
  if (listenItems.length === 0) {
    content.innerHTML = `<div class="empty-msg">${esc(listenScopeLabel())} 以下に音声 (output.mp3) はありません</div>`;
    return;
  }
  const counts = { all: listenItems.length, unlistened: 0, progress: 0, done: 0 };
  let totalCovered = 0;
  for (const it of listenItems) {
    counts[listenStatus(it)]++;
    totalCovered += it.covered_sec || 0;
  }
  const filtered = listenItems.filter(it => listenFilter === 'all' || listenStatus(it) === listenFilter);
  const groups = {};
  for (const it of filtered) (groups[listenGroupKey(it)] ||= []).push(it);
  const groupKeys = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  const collapsed = getListenCollapsed();
  const body = groupKeys.map(k => {
    const isCollapsed = collapsed.has(k);
    const header = `<div class="listen-group clickable" onclick="toggleListenGroup('${esc(k)}')">
      <span class="listen-caret">${isCollapsed ? '▶' : '▼'}</span>${esc(listenGroupLabel(k))}<span class="listen-group-n">${groups[k].length}</span></div>`;
    if (isCollapsed) return header;
    const rows = groups[k].slice().sort(listenComparator).map(listenRowHtml).join('');
    return `${header}<div class="list-box">${rows}</div>`;
  }).join('');

  const chip = (key, label) =>
    `<button class="listen-chip${listenFilter === key ? ' active' : ''}" onclick="setListenFilter('${key}')">${label}<span class="listen-chip-n">${counts[key]}</span></button>`;
  const opt = (key, label) => `<option value="${key}"${listenSort === key ? ' selected' : ''}>${label}</option>`;

  content.innerHTML = `
    <div class="listen-summary">${esc(listenScopeLabel())} · 全 ${counts.all} 本 · 未聴 ${counts.unlistened} · 途中 ${counts.progress} · 完聴 ${counts.done} · 合計聴取 ${fmtDur(totalCovered)}</div>
    <div class="listen-toolbar">
      <div class="listen-filters">
        ${chip('all', 'すべて')}${chip('unlistened', '未聴')}${chip('progress', '途中')}${chip('done', '完聴')}
      </div>
      <select class="listen-sort" onchange="setListenSort(this.value)">
        ${opt('name', '名前順')}${opt('recent', '新しい順')}${opt('played', '最終再生順')}${opt('unlistenedFirst', '未聴・低カバレッジ順')}
      </select>
    </div>
    ${body || '<div class="empty-msg">該当なし</div>'}`;
}

function listenScopeLabel() {
  return [ROOT_NAME, ...listenScope.split('/').filter(Boolean)].join(' / ');
}

function setListenFilter(f) { listenFilter = f; renderListenView(); }
function setListenSort(s) { listenSort = s; renderListenView(); }

async function openAudioFromOverview(dir, audio) {
  currentTab = 'files';
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'files'));
  saveState();
  await renderFiles(dir);
  await showBlob(audio);
  const player = document.getElementById('audio-player');
  if (player) player.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
