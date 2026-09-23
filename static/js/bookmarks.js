// Reading bookmarks ("しおり") on Markdown sentences, SRT cues and code lines.

// --- Bookmark helpers ---
async function fetchBookmark(path) {
  try {
    const r = await fetch(`/api/bookmark?path=${encodeURIComponent(path)}`);
    if (!r.ok) return {};
    return await r.json();
  } catch {
    return {};
  }
}

async function setBookmark(path, type, index) {
  try {
    const r = await fetch('/api/bookmark', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, type, index}),
    });
    if (!r.ok) alert('しおりの保存に失敗しました');
  } catch {
    alert('しおりの保存に失敗しました');
  }
}

async function deleteBookmark(path) {
  try {
    const r = await fetch('/api/bookmark', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path}),
    });
    if (!r.ok) alert('しおりの削除に失敗しました');
  } catch {
    alert('しおりの削除に失敗しました');
  }
}

// ----- Markdown 文単位しおり -----

// 句点判定: 全角は常に文末。半角 . ! ? は後続が空白/末尾なら文末。
const MD_SENT_END = /[。！？]|[.!?](?=\s|$)/g;

// 文単位に分割しないコンテナ（中身を再帰)
const MD_CONTAINER_TAGS = new Set(['UL','OL','BLOCKQUOTE','TABLE','THEAD','TBODY','TR']);
// 1 単位のままで data-bm-idx を付ける（中身は再帰しない）
const MD_ATOMIC_TAGS = new Set(['H1','H2','H3','H4','H5','H6','PRE']);
// 文単位に分割する
const MD_SPLIT_TAGS = new Set(['P','LI','TH','TD']);

function isBlockChild(node) {
  if (node.nodeType !== 1) return false;
  const t = node.tagName;
  return MD_CONTAINER_TAGS.has(t) || MD_ATOMIC_TAGS.has(t) || MD_SPLIT_TAGS.has(t);
}

// テキストノード列内で文末位置を見つけ、文ごとに span でラップする。
function wrapInlineRunIntoSentences(parent, nodes, ctx) {
  const flushAfter = new Set();

  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    if (n.nodeType !== 3) continue;
    // text は分割前の元の nodeValue を保持し続ける。offsetBase が「どれだけ前に切り出したか」を追う
    let text = n.nodeValue;
    let offsetBase = 0;
    let cur = n;
    // splice の挿入位置を毎回進めて、nodes 配列の順序を DOM 順と一致させる
    let insertAt = i + 1;
    MD_SENT_END.lastIndex = 0;
    let m;
    while ((m = MD_SENT_END.exec(text)) !== null) {
      const cut = m.index + m[0].length - offsetBase;
      if (cut < cur.nodeValue.length) {
        const tail = cur.splitText(cut);
        flushAfter.add(cur);
        nodes.splice(insertAt, 0, tail);
        insertAt++;
        offsetBase = m.index + m[0].length;
        cur = tail;
      } else {
        flushAfter.add(cur);
      }
    }
  }

  const spans = [];
  let bucket = [];
  for (const n of nodes) {
    bucket.push(n);
    if (n.nodeType === 3 && flushAfter.has(n)) {
      spans.push(bucket);
      bucket = [];
    }
  }
  if (bucket.length > 0) spans.push(bucket);

  // 空白だけのグループは前にマージ
  const merged = [];
  for (const group of spans) {
    const onlyWs = group.every(n => n.nodeType === 3 && /^\s*$/.test(n.nodeValue));
    if (onlyWs && merged.length > 0) {
      merged[merged.length - 1].push(...group);
    } else {
      merged.push(group);
    }
  }

  const firstAnchor = nodes[0];
  if (!firstAnchor) return;
  for (const group of merged) {
    const span = document.createElement('span');
    span.className = 'bm-target';
    span.setAttribute('data-bm-idx', String(ctx.counter++));
    parent.insertBefore(span, group[0]);
    for (const n of group) span.appendChild(n);
  }
}

function markAtomic(el, ctx) {
  el.setAttribute('data-bm-idx', String(ctx.counter++));
  el.classList.add('bm-target');
}

function splitElementIntoSentences(parent, ctx) {
  const children = [...parent.childNodes];
  let inlineRun = [];
  for (const child of children) {
    if (isBlockChild(child)) {
      if (inlineRun.length > 0) {
        wrapInlineRunIntoSentences(parent, inlineRun, ctx);
        inlineRun = [];
      }
      processMdBlock(child, ctx);
    } else {
      inlineRun.push(child);
    }
  }
  if (inlineRun.length > 0) {
    wrapInlineRunIntoSentences(parent, inlineRun, ctx);
  }
}

function processMdBlock(node, ctx) {
  if (node.nodeType !== 1) return;
  const tag = node.tagName;
  if (MD_CONTAINER_TAGS.has(tag)) {
    for (const child of [...node.children]) processMdBlock(child, ctx);
    return;
  }
  if (MD_ATOMIC_TAGS.has(tag)) {
    markAtomic(node, ctx);
    return;
  }
  if (MD_SPLIT_TAGS.has(tag)) {
    splitElementIntoSentences(node, ctx);
    return;
  }
}

function splitMdIntoSentences(root) {
  const ctx = { counter: 0 };
  for (const child of [...root.children]) processMdBlock(child, ctx);
  return ctx.counter;
}

function bookmarkTarget(blobView, type, index) {
  if (type === 'md') {
    return blobView.querySelector(`[data-bm-idx="${index}"]`);
  }
  return blobView.querySelector(`.code-line[data-bm-line="${index}"]`);
}

function applyBookmarkUi(blobView, bm) {
  blobView.querySelectorAll('.bookmarked').forEach(el => el.classList.remove('bookmarked'));
  const jumpBtn = blobView.querySelector('#bm-jump-btn');
  if (!bm || !bm.type) {
    delete blobView.dataset.bmIdx;
    delete blobView.dataset.bmType;
    if (jumpBtn) jumpBtn.innerHTML = '';
    return;
  }
  blobView.dataset.bmIdx = String(bm.index);
  blobView.dataset.bmType = bm.type;
  const target = bookmarkTarget(blobView, bm.type, bm.index);
  if (target) {
    target.classList.add('bookmarked');
    if (bm.type === 'text') {
      const gutter = blobView.querySelector(`.code-line-num[data-bm-line="${bm.index}"]`);
      if (gutter) gutter.classList.add('bookmarked');
    }
    if (jumpBtn) {
      jumpBtn.innerHTML = `<button class="btn-jump" onclick="jumpToBookmark()">しおりへ</button>`;
    }
  } else if (jumpBtn) {
    jumpBtn.innerHTML = '';
  }
}

async function onBookmarkClick(path, type, index) {
  const blobView = document.getElementById('blob-view');
  if (!blobView) return;
  const sameType = blobView.dataset.bmType === type;
  const sameIdx = blobView.dataset.bmIdx === String(index);
  if (sameType && sameIdx) {
    await deleteBookmark(path);
    applyBookmarkUi(blobView, {});
  } else {
    await setBookmark(path, type, index);
    applyBookmarkUi(blobView, {type, index});
  }
}

function jumpToBookmark() {
  const blobView = document.getElementById('blob-view');
  if (!blobView) return;
  const idx = blobView.dataset.bmIdx;
  const type = blobView.dataset.bmType;
  if (idx === undefined || !type) return;
  const target = bookmarkTarget(blobView, type, Number(idx));
  if (target) target.scrollIntoView({block: 'center'});
}

// iOS Safari 等で touch による dblclick が発火しない/ズームが優先される問題の回避。
// ブックマーク対象要素上の 400ms 以内の 2 回タップを合成 dblclick として発火する。
(function setupMobileDoubleTap() {
  const SELECTOR = '.bm-target, .code-line, .code-line-num, .srt-embed-container .srt-cue';
  const IGNORE = 'a, button, input, select, textarea, img';
  document.addEventListener('touchend', (e) => {
    if (e.changedTouches.length !== 1) return;
    if (e.target.closest(IGNORE)) return;
    const target = e.target.closest(SELECTOR);
    if (!target) return;
    const now = Date.now();
    const last = Number(target.dataset.lastTap || 0);
    if (now - last < 400) {
      e.preventDefault();
      target.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, cancelable: true}));
      target.dataset.lastTap = '0';
    } else {
      target.dataset.lastTap = String(now);
    }
  }, {passive: false});

  // PC: ブックマーク対象要素上でのダブルクリック時の単語選択を抑止する。
  // mousedown の detail >= 2 (2 連打目以降) を preventDefault すれば、
  // 単発クリック→ドラッグでの範囲選択や長押しは従来通り動く。
  document.addEventListener('mousedown', (e) => {
    if (e.detail < 2) return;
    if (e.target.closest(IGNORE)) return;
    if (!e.target.closest(SELECTOR)) return;
    e.preventDefault();
  });
})();
