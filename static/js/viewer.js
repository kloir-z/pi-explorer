// File preview: text/code/Markdown/SRT/HTML/image/PDF rendering, editing,
// and prev/next navigation between sibling files.

function resolveMdAssetUrl(url, baseDir) {
  if (!url) return url;
  if (/^(https?:)?\/\//i.test(url) || /^data:/i.test(url) || /^(mailto|tel|javascript):/i.test(url) || url.startsWith('#')) {
    return url;
  }
  const hashIdx = url.search(/[?#]/);
  const rawPath = hashIdx >= 0 ? url.slice(0, hashIdx) : url;
  let target = rawPath.startsWith('/') ? rawPath.slice(1) : (baseDir ? baseDir + '/' + rawPath : rawPath);
  const parts = [];
  for (const seg of target.split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') { parts.pop(); continue; }
    parts.push(seg);
  }
  const normalized = parts.join('/');
  if (!normalized) return url;
  return `/api/blob?path=${encodeURIComponent(normalized)}`;
}

function rewriteMdAssets(container, baseDir) {
  container.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src');
    if (src) img.setAttribute('src', resolveMdAssetUrl(src, baseDir));
  });
  container.querySelectorAll('source, video, audio').forEach(el => {
    const src = el.getAttribute('src');
    if (src) el.setAttribute('src', resolveMdAssetUrl(src, baseDir));
  });
}

const IMAGE_EXTS = ['jpg','jpeg','png','gif','svg','webp','ico','bmp','avif','heic','heif'];
const PDF_EXTS = ['pdf'];
const AUDIO_EXTS = ['mp3','wav','ogg','m4a','flac','aac','opus'];
const VIDEO_EXTS = ['mp4','webm','m4v','mov','ogv'];
const TEXT_EXTS = [
  'py','js','ts','jsx','tsx','sh','bash','ps1','psm1','psd1','json','yml','yaml',
  'xml','html','htm','css','toml','md','txt','cfg','ini','conf','env','service','timer',
  'csv','sql','rb','go','rs','java','c','h','cpp','hpp','vue','svelte','gitignore',
  'dockerignore','makefile','lock','log','srt',
];
const LANG_MAP = {
  '.py':'python', '.js':'javascript', '.ts':'typescript', '.jsx':'javascript',
  '.tsx':'typescript', '.sh':'bash', '.bash':'bash', '.json':'json',
  '.yml':'yaml', '.yaml':'yaml', '.xml':'xml', '.html':'html', '.css':'css',
  '.toml':'toml', '.rb':'ruby', '.go':'go', '.rs':'rust', '.java':'java',
  '.c':'c', '.h':'c', '.cpp':'cpp', '.hpp':'cpp', '.sql':'sql',
  '.vue':'html', '.svelte':'html',
  '.ps1':'powershell', '.psm1':'powershell', '.psd1':'powershell',
};

function splitHighlightedIntoLines(html) {
  const lines = [];
  let openTags = [];
  let currentLine = '';
  let i = 0;
  while (i < html.length) {
    const ch = html[i];
    if (ch === '<') {
      const end = html.indexOf('>', i);
      if (end < 0) { currentLine += html.slice(i); break; }
      const tag = html.slice(i, end + 1);
      currentLine += tag;
      if (tag.startsWith('</')) openTags.pop();
      else if (!tag.endsWith('/>')) openTags.push(tag);
      i = end + 1;
    } else if (ch === '\n') {
      currentLine += '</span>'.repeat(openTags.length);
      lines.push(currentLine);
      currentLine = openTags.join('');
      i++;
    } else {
      currentLine += ch;
      i++;
    }
  }
  lines.push(currentLine);
  return lines;
}

// ----- Mermaid diagram support -----

const MERMAID_THEME_MAP = { dark: 'dark', light: 'default', sepia: 'neutral' };

function mermaidThemeForUi(uiTheme) {
  return MERMAID_THEME_MAP[uiTheme] || 'default';
}

// `marked.parse` が `<pre><code class="language-mermaid">` を生成するので、
// それを `<div class="mermaid">` に置換する (sync)。元ソースは data 属性に保持して
// テーマ切替時に再描画できるようにする。
function prepareMermaidBlocks(container) {
  const codeBlocks = container.querySelectorAll('pre code.language-mermaid');
  const divs = [];
  codeBlocks.forEach(codeEl => {
    const pre = codeEl.closest('pre');
    if (!pre) return;
    const source = codeEl.textContent;
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.dataset.mermaidSource = source;
    div.textContent = source;
    pre.replaceWith(div);
    divs.push(div);
  });
  return divs;
}

async function renderMermaidBlocks(divs, uiTheme) {
  if (typeof mermaid === 'undefined') return;
  if (!divs || divs.length === 0) return;
  try {
    mermaid.initialize({
      startOnLoad: false,
      theme: mermaidThemeForUi(uiTheme),
      securityLevel: 'loose',
      fontFamily: 'Yu Gothic UI, "Segoe UI", "Hiragino Sans", sans-serif',
      flowchart: { padding: 14, nodeSpacing: 35, rankSpacing: 50, htmlLabels: true, useMaxWidth: true },
      sequence: { actorMargin: 60, boxMargin: 10, noteMargin: 10, messageMargin: 35, wrap: true, noteAlign: 'left', useMaxWidth: true },
    });
    await mermaid.run({ nodes: divs });
  } catch (e) {
    console.error('Mermaid render error:', e);
  }
}

async function rerenderMermaidWithTheme(uiTheme) {
  if (typeof mermaid === 'undefined') return;
  const divs = document.querySelectorAll('.mermaid');
  if (divs.length === 0) return;
  divs.forEach(div => {
    const src = div.dataset.mermaidSource;
    if (!src) return;
    div.removeAttribute('data-processed');
    div.innerHTML = '';
    div.textContent = src;
  });
  await renderMermaidBlocks([...divs], uiTheme);
}

// --- Blob preview state (FAB / nav zones / sibling tracking) ---
let currentBlobPath = '';
let currentBlobSiblings = [];
let currentBlobIdx = -1;
let lastDirFetch = { key: '', entries: null };
let navDirections = {}; // { "dir|ext": "reversed" } — absent = normal

function navDirKey(path) {
  return `${getBlobDir(path)}|${getExt(path)}`;
}
function isNavReversed(path) {
  return navDirections[navDirKey(path)] === 'reversed';
}
async function toggleNavDirection() {
  if (!currentBlobPath) return;
  const key = navDirKey(currentBlobPath);
  const next = navDirections[key] === 'reversed' ? 'normal' : 'reversed';
  if (next === 'normal') delete navDirections[key];
  else navDirections[key] = next;
  renderBlobNavZones();
  renderNavDirToggle();
  try {
    await fetch('/api/nav_direction', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, direction: next}),
    });
  } catch (e) { console.error('nav_direction save failed', e); }
}

function getBlobDir(p) {
  const i = p.lastIndexOf('/');
  return i >= 0 ? p.substring(0, i) : '';
}
function getExt(p) {
  const slash = p.lastIndexOf('/');
  const dot = p.lastIndexOf('.');
  return dot > slash ? p.substring(dot + 1).toLowerCase() : '';
}
function isEditableExt(ext) {
  return TEXT_EXTS.includes(ext);
}

function isHtmlExt(ext) {
  return ext === 'html' || ext === 'htm';
}

// Build a /raw/ URL that mirrors the on-disk layout so the rendered
// page's relative CSS/JS/image references resolve against sibling /raw/ URLs.
function rawUrl(path) {
  const segs = path.split('/').filter(Boolean).map(encodeURIComponent);
  return '/raw/' + segs.join('/');
}

// Per-session toggle: HTML files render in an iframe by default; this flips to
// source view. Persists across files (a viewing preference, like autoplay).
let htmlSourceMode = false;
function toggleHtmlSource() {
  htmlSourceMode = !htmlSourceMode;
  if (currentBlobPath) showBlob(currentBlobPath);
}

// Extra header controls shown when an HTML file is displayed as source.
function htmlSourceHeaderExtras(path, ext) {
  if (!isHtmlExt(ext)) return '';
  const rUrl = rawUrl(path);
  return `<a href="${rUrl}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--accent);font-size:11px;">新しいタブで開く</a>` +
    `<button onclick="toggleHtmlSource()" style="margin-left:8px;font-size:11px;padding:1px 6px;">レンダリング表示</button>`;
}

async function loadSiblings(path) {
  const dir = getBlobDir(path);
  const ext = getExt(path);
  const key = dir;
  let entries = (lastDirFetch.key === key) ? lastDirFetch.entries : null;
  if (!entries) {
    entries = await fetchJson(`/api/tree?path=${encodeURIComponent(dir)}`);
    if (Array.isArray(entries)) lastDirFetch = { key, entries };
  }
  if (!Array.isArray(entries)) return { siblings: [], idx: -1 };
  const sorted = sortEntries(entries, getSortConfig(dir));
  const siblings = sorted
    .filter(e => e.type === 'blob' && getExt(e.path) === ext)
    .map(e => e.path);
  return { siblings, idx: siblings.indexOf(path) };
}

function highlightCurrentInList() {
  document.querySelectorAll('.list-row.active').forEach(el => el.classList.remove('active'));
  if (!currentBlobPath) return;
  const sel = (window.CSS && CSS.escape) ? CSS.escape(currentBlobPath) : currentBlobPath.replace(/"/g, '\\"');
  const row = document.querySelector(`.list-row[data-blob-path="${sel}"]`);
  if (row) row.classList.add('active');
}

function renderEditFab(path) {
  const old = document.getElementById('edit-fab');
  if (old) old.remove();
  const btn = document.createElement('button');
  btn.id = 'edit-fab';
  btn.className = 'btn-edit-fab';
  btn.textContent = '編集';
  btn.onclick = () => editBlob(path);
  document.body.appendChild(btn);
}
function clearEditFab() {
  const old = document.getElementById('edit-fab');
  if (old) old.remove();
}

function clearBlobNavZones() {
  document.querySelectorAll('.blob-nav-zone').forEach(el => el.remove());
}
function renderBlobNavZones() {
  clearBlobNavZones();
  if (currentBlobSiblings.length <= 1 || currentBlobIdx < 0) return;
  const prev = currentBlobIdx > 0 ? currentBlobSiblings[currentBlobIdx - 1] : null;
  const next = currentBlobIdx < currentBlobSiblings.length - 1 ? currentBlobSiblings[currentBlobIdx + 1] : null;
  // reversed: swap which side is prev vs next (e.g. vertical-text book = next on left)
  const reversed = isNavReversed(currentBlobPath);
  const leftPath = reversed ? next : prev;
  const rightPath = reversed ? prev : next;
  if (leftPath) {
    const el = document.createElement('div');
    el.className = 'blob-nav-zone left';
    el.innerHTML = '<span>‹</span>';
    el.title = leftPath.split('/').pop();
    el.onclick = () => showBlob(leftPath);
    document.body.appendChild(el);
  }
  if (rightPath) {
    const el = document.createElement('div');
    el.className = 'blob-nav-zone right';
    el.innerHTML = '<span>›</span>';
    el.title = rightPath.split('/').pop();
    el.onclick = () => showBlob(rightPath);
    document.body.appendChild(el);
  }
}

function clearNavDirToggle() {
  const slot = document.getElementById('nav-dir-toggle-slot');
  if (slot) slot.innerHTML = '';
}
function renderNavDirToggle() {
  const slot = document.getElementById('nav-dir-toggle-slot');
  if (!slot) return;
  if (currentBlobSiblings.length <= 1 || currentBlobIdx < 0) {
    slot.innerHTML = '';
    return;
  }
  const reversed = isNavReversed(currentBlobPath);
  slot.innerHTML = `<button id="nav-dir-toggle" class="btn-nav-dir-toggle" title="このフォルダ・拡張子のナビ方向を反転">${reversed ? '次 ←' : '→ 次'}</button>`;
  const btn = document.getElementById('nav-dir-toggle');
  if (btn) btn.onclick = toggleNavDirection;
}

function clearBlobUi() {
  currentBlobPath = '';
  currentBlobSiblings = [];
  currentBlobIdx = -1;
  clearEditFab();
  clearBlobNavZones();
  clearNavDirToggle();
}

// Preload an image so the new <img> is decoded before we swap the DOM.
// The browser caches the response, so the subsequent <img src=...> renders
// instantly from cache — no flicker while the previous image is removed.
function preloadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = resolve;
    img.onerror = resolve;
    img.src = url;
  });
}

async function showBlob(path) {
  const blobView = document.getElementById('blob-view');
  if (!blobView) return;

  // Preserve current blob-view height during the swap so the document does not
  // shrink and yank the scroll position to the top. Old content stays visible
  // until the new render replaces it (no jarring "Loading..." flash mid-swap).
  const prevHeight = blobView.offsetHeight;
  if (prevHeight > 0) {
    blobView.style.minHeight = prevHeight + 'px';
  } else {
    blobView.innerHTML = '<div style="padding:4px 8px;color:var(--text-muted);">Loading...</div>';
  }
  // Release the placeholder height once the new content has had time to settle
  // (incl. <img> decode). Stale-guard so a rapid switch keeps the new height.
  setTimeout(() => {
    if (currentBlobPath === path) blobView.style.minHeight = '';
  }, 1500);

  const ext = path.includes('.') ? path.split('.').pop().toLowerCase() : '';
  const blobUrl = `/api/blob?path=${encodeURIComponent(path)}`;
  const fileName = path.split('/').pop();

  // Common preview UI: edit FAB, list highlight, sibling navigation zones
  currentBlobPath = path;
  if (isEditableExt(ext)) renderEditFab(path);
  else clearEditFab();
  highlightCurrentInList();
  clearBlobNavZones();
  clearNavDirToggle();
  loadSiblings(path).then(({ siblings, idx }) => {
    if (currentBlobPath !== path) return; // stale
    currentBlobSiblings = siblings;
    currentBlobIdx = idx;
    renderBlobNavZones();
    renderNavDirToggle();
  });

  // Binary files — show download link only
  if (!IMAGE_EXTS.includes(ext) && !PDF_EXTS.includes(ext) && !AUDIO_EXTS.includes(ext) && !VIDEO_EXTS.includes(ext) && !TEXT_EXTS.includes(ext) && ext !== '') {
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span></div>
      <div class="empty-msg">バイナリファイル — <a href="${blobUrl}" download style="color:var(--accent);">ダウンロード</a></div>`;
    return;
  }

  // Images: preload first so the prior image stays visible until the new one
  // is decoded. Stale-guard: if user clicked past this one while preloading,
  // skip the render.
  if (IMAGE_EXTS.includes(ext)) {
    await preloadImage(blobUrl);
    if (currentBlobPath !== path) return;
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span></div>
      <div class="blob-container" style="text-align:center;padding:8px;">
        <img src="${blobUrl}" alt="${esc(fileName)}" style="max-width:100%;height:auto;">
      </div>`;
    renderNavDirToggle();
    return;
  }

  // Video
  if (VIDEO_EXTS.includes(ext)) {
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span>${autoplayToggleHtml()}</div>
      <div class="blob-container" style="padding:8px; overflow: visible;">
        <video id="audio-player" controls preload="metadata" src="${blobUrl}"${autoplayMedia?' autoplay':''} style="width:100%; max-height:70vh; background:#000;"></video>
        <div id="playback-log-list" class="playback-log"></div>
        <div id="chapter-nav" class="chapter-nav"></div>
        <div id="srt-embed" class="srt-embed-container"></div>
      </div>`;
    wireAutoplayToggle();
    setupPlaybackLog(path);
    setupChapterNav(path);
    setupSrtEmbed(path);
    return;
  }

  // Audio
  if (AUDIO_EXTS.includes(ext)) {
    const audioAutoplay = autoplayMedia || audioContinuous;
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span>${autoplayToggleHtml()}${continuousToggleHtml()}</div>
      <div class="blob-container" style="padding:8px; overflow: visible;">
        <div class="audio-float"><audio id="audio-player" controls preload="metadata" src="${blobUrl}"${audioAutoplay?' autoplay':''}></audio></div>
        <div id="playback-log-list" class="playback-log"></div>
        <div id="chapter-nav" class="chapter-nav"></div>
        <div id="srt-embed" class="srt-embed-container"></div>
      </div>`;
    wireAutoplayToggle();
    wireContinuousToggle();
    const audioPlayer = document.getElementById('audio-player');
    audioPlayer.defaultPlaybackRate = 1.2;
    audioPlayer.playbackRate = 1.2;
    setupAudioContinuous();
    setupPlaybackLog(path);
    setupChapterNav(path);
    setupSrtEmbed(path);
    return;
  }

  // PDF
  if (PDF_EXTS.includes(ext)) {
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span>
        <a href="${blobUrl}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--accent);font-size:11px;">新しいタブで開く</a>
      </div>
      <iframe src="${blobUrl}" style="width:100%;height:70vh;border:1px solid #21262d;border-radius:4px;"></iframe>`;
    return;
  }

  // HTML — render as a webpage in an iframe. The /raw/ URL mirrors the
  // directory layout, so relative CSS/JS/images resolve. "新しいタブで開く"
  // gives a clean full-screen view (handy on mobile). Toggle to view source.
  if (isHtmlExt(ext) && !htmlSourceMode) {
    const rUrl = rawUrl(path);
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span>
        <a href="${rUrl}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--accent);font-size:11px;">新しいタブで開く</a>
        <button onclick="toggleHtmlSource()" style="margin-left:8px;font-size:11px;padding:1px 6px;">ソースを表示</button>
      </div>
      <iframe src="${rUrl}" style="width:100%;height:80vh;border:1px solid #21262d;border-radius:4px;background:#fff;"></iframe>`;
    renderNavDirToggle();
    return;
  }

  // Text / Code / Markdown
  const data = await fetchJson(blobUrl);
  if (data.content === undefined) {
    blobView.innerHTML = '<div class="empty-msg">ファイルを表示できません</div>';
    return;
  }

  const fileExt = data.ext || '';
  if (fileExt === '.md') {
    const mdPrefs = JSON.parse(localStorage.getItem('mdPrefs') || '{}');
    const fontSize = mdPrefs.fontSize || '13';
    const spacing = mdPrefs.spacing || '6';
    const theme = mdPrefs.theme || 'dark';
    const themeClass = theme === 'dark' ? '' : ` md-theme-${theme}`;
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span><span id="bm-jump-btn"></span></div>
      <div class="md-toolbar">
        <label>文字サイズ<select id="md-font-size" onchange="updateMdPref('fontSize',this.value)">
          <option value="11"${fontSize==='11'?' selected':''}>小</option>
          <option value="13"${fontSize==='13'?' selected':''}>中</option>
          <option value="15"${fontSize==='15'?' selected':''}>大</option>
          <option value="18"${fontSize==='18'?' selected':''}>特大</option>
        </select></label>
        <label>段落間隔<select id="md-spacing" onchange="updateMdPref('spacing',this.value)">
          <option value="4"${spacing==='4'?' selected':''}>狭い</option>
          <option value="6"${spacing==='6'?' selected':''}>普通</option>
          <option value="10"${spacing==='10'?' selected':''}>広い</option>
          <option value="16"${spacing==='16'?' selected':''}>とても広い</option>
        </select></label>
        <label>テーマ<select id="md-theme" onchange="updateMdPref('theme',this.value)">
          <option value="dark"${theme==='dark'?' selected':''}>ダーク</option>
          <option value="light"${theme==='light'?' selected':''}>ライト</option>
          <option value="sepia"${theme==='sepia'?' selected':''}>セピア</option>
        </select></label>
      </div>
      <div class="blob-container markdown-body${themeClass}" id="md-body" style="padding:12px;--md-font-size:${fontSize}px;--md-spacing:${spacing}px;"></div>`;
    const mdBody = blobView.querySelector('#md-body');
    mdBody.innerHTML = marked.parse(data.content, { breaks: true });
    rewriteMdAssets(mdBody, parentPath(path));
    // Mermaid: <pre><code class="language-mermaid"> を <div class="mermaid"> に変換 (sync)
    // hljs / splitMdIntoSentences が触る前にやる
    const mermaidDivs = prepareMermaidBlocks(mdBody);
    blobView.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    // 文単位 / 見出し単位 / コードブロック単位で data-bm-idx を付与
    splitMdIntoSentences(mdBody);
    // Mermaid を非同期描画 (fire-and-forget、ブックマーク等の処理を待たせない)
    renderMermaidBlocks(mermaidDivs, theme);
    mdBody.querySelectorAll('[data-bm-idx]').forEach(el => {
      const idx = Number(el.getAttribute('data-bm-idx'));
      el.addEventListener('dblclick', (e) => {
        if (e.target.closest('a, button, input, select, textarea, img')) return;
        onBookmarkClick(path, 'md', idx);
      });
    });
    const bm = await fetchBookmark(path);
    applyBookmarkUi(blobView, bm);
    if (path.endsWith('.notes.md')) {
      // Viewing a *.notes.md directly. Fetch via the target path so we get
      // parsed sections + resolution. Skip the regular markdown note
      // decoration (which would add nonsensical "+" markers to every heading
      // and target the wrong bm-idx numbering) and the unresolved panel.
      const target = path.slice(0, -'.notes.md'.length);
      await fetchNotes(target);
      notesState.viewPath = path;
      decorateNotesMdView(mdBody);
    } else {
      await fetchNotes(path);
      decorateMdNotes(mdBody);
      renderUnresolvedPanel(blobView);
    }
  } else if (fileExt === '.srt') {
    const cues = parseSrt(data.content);
    if (cues.length === 0) {
      blobView.innerHTML = `
        <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span></div>
        <div class="empty-msg">字幕を読み込めませんでした</div>
        <pre class="blob-container" style="padding:8px;white-space:pre-wrap;">${esc(data.content)}</pre>`;
      renderNavDirToggle();
      return;
    }
    const cardsHtml = cues.map((c, i) => {
      const color = speakerColor(c.speaker);
      const styleAttr = color ? ` style="--speaker-color: ${color};"` : '';
      const timeStr = (c.start && c.end) ? `${c.start} → ${c.end}` : '--';
      const idxStr = c.index || '';
      return `
        <div class="srt-cue bm-target" data-bm-idx="${i}"${styleAttr}>
          <div class="srt-meta">
            <span class="srt-index">${esc(idxStr)}</span>
            <span class="srt-time">${esc(timeStr)}</span>
          </div>
          <div class="srt-text">${esc(c.text)}</div>
        </div>`;
    }).join('');
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span><span id="bm-jump-btn"></span></div>
      <div class="blob-container srt-container">${cardsHtml}</div>`;
    blobView.querySelectorAll('.srt-cue').forEach((el, idx) => {
      el.addEventListener('dblclick', (e) => {
        if (e.target.closest('a, button, input, select, textarea, img')) return;
        onBookmarkClick(path, 'md', idx);
      });
    });
    const bm = await fetchBookmark(path);
    applyBookmarkUi(blobView, bm);
    await fetchNotes(path);
    decorateSrtNotes(blobView, cues);
    renderUnresolvedPanel(blobView);
  } else {
    const lang = LANG_MAP[fileExt] || 'plaintext';
    let highlighted;
    try {
      highlighted = hljs.highlight(data.content, {language: lang, ignoreIllegals: true}).value;
    } catch {
      highlighted = data.content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    const lines = splitHighlightedIntoLines(highlighted);
    const rowsHtml = lines.map((line, i) =>
      `<div class="code-line-num" data-bm-line="${i+1}">${i+1}</div>` +
      `<div class="code-line" data-bm-line="${i+1}">${line || ' '}</div>`
    ).join('');
    blobView.innerHTML = `
      <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span><span id="bm-jump-btn"></span>${htmlSourceHeaderExtras(path, ext)}</div>
      <div class="blob-container">
        <div class="code-wrapper hljs language-${lang}">${rowsHtml}</div>
      </div>`;
    blobView.querySelectorAll('.code-line-num, .code-line').forEach(el => {
      el.addEventListener('dblclick', () => {
        const line = Number(el.getAttribute('data-bm-line'));
        onBookmarkClick(path, 'text', line);
      });
    });
    const bm = await fetchBookmark(path);
    applyBookmarkUi(blobView, bm);
    await fetchNotes(path);
    decorateTextNotes(blobView, path);
    renderUnresolvedPanel(blobView);
  }
  // Re-render the nav-direction toggle now that the slot exists in the new
  // innerHTML. For async branches (text/MD/SRT/code) the loadSiblings .then
  // can resolve before the await fetchJson does, so the .then's render call
  // hits a missing slot — this catches that race.
  renderNavDirToggle();
}

function updateMdPref(key, value) {
  const prefs = JSON.parse(localStorage.getItem('mdPrefs') || '{}');
  prefs[key] = value;
  localStorage.setItem('mdPrefs', JSON.stringify(prefs));
  const body = document.getElementById('md-body');
  if (!body) return;
  if (key === 'fontSize') body.style.setProperty('--md-font-size', value + 'px');
  if (key === 'spacing') body.style.setProperty('--md-spacing', value + 'px');
  if (key === 'theme') {
    body.classList.remove('md-theme-light', 'md-theme-sepia');
    if (value !== 'dark') body.classList.add('md-theme-' + value);
    rerenderMermaidWithTheme(value);
  }
}

let editingPath = '';
let editingOriginal = '';

async function editBlob(path) {
  const blobView = document.getElementById('blob-view');
  if (!blobView) return;

  clearEditFab();
  clearBlobNavZones();
  clearNavDirToggle();
  editingPath = path;
  const blobUrl = `/api/blob?path=${encodeURIComponent(path)}`;
  const data = await fetchJson(blobUrl);
  if (data.content === undefined) {
    blobView.innerHTML = '<div class="empty-msg">ファイルを編集できません</div>';
    return;
  }

  editingOriginal = data.content;
  const escaped = data.content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  blobView.innerHTML = `
    <div class="section-header" style="margin-top:8px;">${esc(path)}${copyPathBtn(path)}<span id="nav-dir-toggle-slot"></span>
      <span style="margin-left:8px;color:var(--text-muted);font-size:11px;">編集中</span>
    </div>
    <div class="blob-container">
      <textarea id="edit-textarea" class="edit-textarea" spellcheck="false">${escaped}</textarea>
      <div class="edit-controls">
        <button class="btn-cancel" onclick="showBlob('${esc(editingPath)}')">キャンセル</button>
        <button class="btn-save" onclick="saveBlob()">保存</button>
      </div>
    </div>`;

  const ta = document.getElementById('edit-textarea');
  ta.style.height = Math.max(200, ta.scrollHeight) + 'px';
}

async function saveBlob() {
  const ta = document.getElementById('edit-textarea');
  if (!ta) return;

  const res = await fetch('/api/blob', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: editingPath, content: ta.value}),
  });

  if (res.ok) {
    showBlob(editingPath);
  } else {
    alert('保存に失敗しました');
  }
}

// 左右キーで前後 sibling に移動 (ナビゾーンと同じ方向、反転設定も反映)
(function setupBlobKeyboardNav() {
  const SKIP_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT', 'AUDIO', 'VIDEO']);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (!currentBlobPath || currentBlobSiblings.length <= 1 || currentBlobIdx < 0) return;
    const ae = document.activeElement;
    if (ae) {
      if (SKIP_TAGS.has(ae.tagName)) return;
      if (ae.isContentEditable) return;
    }
    const prev = currentBlobIdx > 0 ? currentBlobSiblings[currentBlobIdx - 1] : null;
    const next = currentBlobIdx < currentBlobSiblings.length - 1 ? currentBlobSiblings[currentBlobIdx + 1] : null;
    const reversed = isNavReversed(currentBlobPath);
    const leftPath = reversed ? next : prev;
    const rightPath = reversed ? prev : next;
    const target = e.key === 'ArrowLeft' ? leftPath : rightPath;
    if (!target) return;
    e.preventDefault();
    showBlob(target);
  });
})();
