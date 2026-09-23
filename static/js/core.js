// Shared state and small helpers used by every other script.
// Classic scripts share one global scope; load order is set in index.html.

const ICON_FOLDER = `<svg width="14" height="12" viewBox="0 0 16 14" fill="none" style="vertical-align:-2px;"><path d="M1 2C1 1.45 1.45 1 2 1H6L8 3H14C14.55 3 15 3.45 15 4V12C15 12.55 14.55 13 14 13H2C1.45 13 1 12.55 1 12V2Z" fill="#e8a735" stroke="#c4891e" stroke-width=".5"/><path d="M1 5H15V12C15 12.55 14.55 13 14 13H2C1.45 13 1 12.55 1 12V5Z" fill="#f0c04a"/></svg>`;
const ICON_FOLDER_OPEN = `<svg width="14" height="12" viewBox="0 0 16 14" fill="none" style="vertical-align:-2px;"><path d="M1 2C1 1.45 1.45 1 2 1H6L8 3H14C14.55 3 15 3.45 15 4V5H3L0 12V2C0 1.45 .45 1 1 1Z" fill="#e8a735" stroke="#c4891e" stroke-width=".5"/><path d="M0 12L3 5H15L12 12H0Z" fill="#f0c04a" stroke="#c4891e" stroke-width=".5"/></svg>`;
const ICON_FILE = `<svg width="12" height="14" viewBox="0 0 12 14" fill="none" style="vertical-align:-2px;"><path d="M1 1H7L11 5V13H1V1Z" fill="#2d333b" stroke="#545d68" stroke-width=".7"/><path d="M7 1L11 5H7V1Z" fill="#3d444d"/><line x1="3" y1="7" x2="9" y2="7" stroke="#545d68" stroke-width=".5"/><line x1="3" y1="9" x2="9" y2="9" stroke="#545d68" stroke-width=".5"/><line x1="3" y1="11" x2="7" y2="11" stroke="#545d68" stroke-width=".5"/></svg>`;
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const content = $('#content');
const favSelect = $('#fav-select');

const TABS = ['files', 'listen'];
let currentTab = 'files';
let currentPath = '';  // ROOT-relative directory shown in the Files tab
let favorites = [];

// --- State persistence ---
const STATE_KEY = 'explorer_state';
function saveState() {
  localStorage.setItem(STATE_KEY, JSON.stringify({
    tab: currentTab,
    path: currentPath,
  }));
}
function loadState() {
  try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; }
}

// One-time move of the git-viewer era keys, which stored a repo name next to
// a repo-relative path, into the ROOT-relative layout.
function migrateLegacyStorage() {
  const take = k => { const v = localStorage.getItem(k); localStorage.removeItem(k); return v; };
  const joinPath = (...parts) => parts.filter(Boolean).join('/');
  try {
    const st = JSON.parse(take('gitviewer_state') || 'null');
    if (st && localStorage.getItem(STATE_KEY) === null) {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        tab: TABS.includes(st.tab) ? st.tab : 'files',
        path: joinPath(st.repo, st.path),
      }));
    }
    const sortDefault = take('gitviewer_tree_sort_default');
    if (sortDefault && localStorage.getItem(SORT_DEFAULT_KEY) === null) {
      localStorage.setItem(SORT_DEFAULT_KEY, sortDefault);
    }
    const sortByDir = JSON.parse(take('gitviewer_tree_sort_by_dir') || 'null');
    if (sortByDir && localStorage.getItem(SORT_BY_DIR_KEY) === null) {
      const out = {};
      for (const [k, v] of Object.entries(sortByDir)) {
        const i = k.indexOf('|');
        out[i < 0 ? k : joinPath(k.slice(0, i), k.slice(i + 1))] = v;
      }
      localStorage.setItem(SORT_BY_DIR_KEY, JSON.stringify(out));
    }
    take('gitviewer_listen_collapsed');  // group keys were repo-relative; drop
  } catch {}
}

async function fetchJson(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return [];
    return await r.json();
  } catch { return []; }
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  const diff = Date.now() - new Date(isoStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'now';
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h';
  const d = Math.floor(h / 24);
  return d + 'd';
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function absolutePathFor(path) {
  const sep = ROOT_DIR.includes('\\') ? '\\' : '/';
  const joined = ROOT_DIR + '/' + path;
  return joined.replace(/[\\\/]+/g, sep);
}

async function copyPath(path) {
  const text = absolutePathFor(path);
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    }
  } catch { ok = false; }
  showCopyToast(ok ? 'パスをコピーしました' : 'コピーに失敗しました', text);
}

function showCopyToast(msg, detail) {
  let t = document.getElementById('copy-toast');
  if (t) t.remove();
  t = document.createElement('div');
  t.id = 'copy-toast';
  t.className = 'copy-toast';
  t.innerHTML = `<div>${esc(msg)}</div>` + (detail ? `<div class="copy-toast-detail">${esc(detail)}</div>` : '');
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('show'), 10);
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 250); }, 1800);
}

function copyPathBtn(path) {
  return `<button class="btn-copy" onclick="copyPath('${esc(path)}')" title="フルパスをコピー">📋</button>`;
}
