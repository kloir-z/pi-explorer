// Audio/video: SRT sync, chapter index, autoplay/continuous play,
// playback logging and the keep-awake pinger.

function parseSrt(text) {
  const cleaned = text.replace(/^﻿/, '').replace(/\r\n/g, '\n');
  const blocks = cleaned.split(/\n\s*\n+/);
  const timeRe = /(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)/;
  const speakerRe = /^([^\s:：]{1,16})[:：]\s*/;
  const cues = [];
  for (const block of blocks) {
    const raw = block.split('\n');
    let s = 0, e = raw.length;
    while (s < e && raw[s].trim() === '') s++;
    while (e > s && raw[e - 1].trim() === '') e--;
    const lines = raw.slice(s, e);
    if (lines.length === 0) continue;

    let i = 0;
    let index = '';
    if (/^\d+$/.test(lines[i].trim())) {
      index = lines[i].trim();
      i++;
    }
    let start = '', end = '';
    if (i < lines.length) {
      const m = lines[i].match(timeRe);
      if (m) {
        start = m[1];
        end = m[2];
        i++;
      }
    }
    const textLines = lines.slice(i);
    if (textLines.length === 0 && !index && !start) continue;
    let body = textLines.join('\n');
    let speaker = '';
    const spk = body.match(speakerRe);
    if (spk) {
      speaker = spk[1];
      body = body.slice(spk[0].length);
    }
    cues.push({index, start, end, speaker, text: body});
  }
  return cues;
}

const SPEAKER_COLOR_OVERRIDES = {
  'ソウタ': 'hsl(210, 70%, 60%)',   // 青
  'リン': 'hsl(340, 70%, 62%)',     // ローズ (青と十分コントラスト)。kotoha→rin 交代でハッシュ色が青に近接した実害を固定色で回避 (2026-06-14)
};
function speakerColor(name) {
  if (!name) return '';
  if (SPEAKER_COLOR_OVERRIDES[name]) return SPEAKER_COLOR_OVERRIDES[name];
  // FNV-1a 32bit → golden-angle で hue を散らし、S/L にも上位ビットを混ぜて
  // `% 360` の近接コリジョン（例: コトハ/ソウタ）を回避する
  let h = 2166136261 >>> 0;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  const hue = (h * 137.508) % 360;
  const sat = 50 + ((h >>> 16) & 0x1f); // 50–81%
  const lig = 55 + ((h >>> 24) & 0x0f); // 55–70%
  return `hsl(${hue.toFixed(1)}, ${sat}%, ${lig}%)`;
}

function timeToSeconds(tc) {
  if (!tc) return NaN;
  const m = tc.match(/^(\d+):(\d+):(\d+)[,.](\d+)$/);
  if (!m) return NaN;
  return (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) + (+m[4]) / Math.pow(10, m[4].length);
}

async function loadSiblingSrt(audioPath) {
  const slash = audioPath.lastIndexOf('/');
  const dir = slash >= 0 ? audioPath.slice(0, slash + 1) : '';
  const base = slash >= 0 ? audioPath.slice(slash + 1) : audioPath;
  const stem = base.replace(/\.[^./]+$/, '');
  const candidates = [dir + stem + '.srt', dir + base + '.srt'];
  for (const p of candidates) {
    const data = await fetchJson(
      `/api/blob?path=${encodeURIComponent(p)}`
    );
    if (data && typeof data.content === 'string') return { path: p, text: data.content };
  }
  return null;
}

async function setupSrtEmbed(audioPath) {
  // Capture DOM refs *before* the await below: on rapid file-switch the late
  // resolution lands on the detached old #srt-embed and the write is GC'd.
  const container = document.getElementById('srt-embed');
  const audio = document.getElementById('audio-player');
  if (!container || !audio) return;

  const result = await loadSiblingSrt(audioPath);
  if (!result) return;

  const parsed = parseSrt(result.text);
  if (parsed.length === 0) {
    container.innerHTML = `<div class="srt-embed-empty">字幕を読み込めませんでした</div>`;
    return;
  }
  const cues = parsed.map(c => ({
    ...c,
    startSec: timeToSeconds(c.start),
    endSec: timeToSeconds(c.end),
  }));

  const cardsHtml = cues.map(c => {
    const color = speakerColor(c.speaker);
    const styleAttr = color ? ` style="--speaker-color: ${color};"` : '';
    const timeStr = (c.start && c.end) ? `${esc(c.start)} → ${esc(c.end)}` : '--';
    return `<div class="srt-cue" data-start="${c.startSec}" data-end="${c.endSec}"${styleAttr}>
      <div class="srt-meta"><span class="srt-time">${timeStr}</span></div>
      <div class="srt-text">${esc(c.text)}</div>
    </div>`;
  }).join('');

  container.innerHTML = cardsHtml;

  setupSrtSync(audio, container, cues);

  await fetchNotes(result.path);
  notesState.embedMode = true;
  notesState.embedCues = cues;
  decorateEmbeddedSrtNotes(container, cues);
}

// While a note modal is open, suppress SRT auto-scroll so the modal
// (and the user's textarea focus on mobile) doesn't get yanked around.
let activeNoteModals = 0;

function setupSrtSync(audio, container, cues) {
  const cueEls = container.querySelectorAll('.srt-cue');
  const isVideo = audio.tagName === 'VIDEO';
  let currentIdx = -1;
  let followEnabled = true;
  let programmaticScrollUntil = 0;
  let manualPauseTimer = null;

  // 字幕は入れ子スクロールをやめ .content 単一スクロールで流す。手動スクロール検知も
  // .content に張る (file 切替の度に重複登録しないよう、前回ハンドラを差し替える)。
  const scroller = document.querySelector('.content');

  const resumeFollow = () => {
    followEnabled = true;
    clearTimeout(manualPauseTimer);
    manualPauseTimer = null;
  };
  const pauseFromManualScroll = () => {
    followEnabled = false;
    clearTimeout(manualPauseTimer);
    manualPauseTimer = setTimeout(resumeFollow, 5000);
  };

  if (scroller) {
    if (scroller._srtFollowScrollHandler) {
      scroller.removeEventListener('scroll', scroller._srtFollowScrollHandler);
    }
    const onScroll = () => {
      if (performance.now() < programmaticScrollUntil) return;
      pauseFromManualScroll();
    };
    scroller._srtFollowScrollHandler = onScroll;
    scroller.addEventListener('scroll', onScroll, { passive: true });
  }

  const findIdx = (t) => {
    if (currentIdx >= 0 && currentIdx < cues.length) {
      const c = cues[currentIdx];
      if (t >= c.startSec && t <= c.endSec) return currentIdx;
    }
    for (let i = 0; i < cues.length; i++) {
      if (t >= cues[i].startSec && t <= cues[i].endSec) return i;
    }
    return -1;
  };

  const update = () => {
    const idx = findIdx(audio.currentTime);
    if (idx === currentIdx) return;
    if (currentIdx >= 0 && cueEls[currentIdx]) cueEls[currentIdx].classList.remove('current');
    currentIdx = idx;
    if (idx < 0) return;
    const el = cueEls[idx];
    if (!el) return;
    el.classList.add('current');
    if (followEnabled && !isVideo && activeNoteModals === 0 && scroller) {
      // 上部固定の章帯の下端より少し下に現在 cue が来るようスクロール (中央寄せだと
      // 背の高い章帯の裏に隠れるため)。章が無い (帯非表示) 時は navH=0 で素直に上寄せ。
      const navEl = document.getElementById('chapter-nav');
      const navH = navEl ? navEl.offsetHeight : 0;
      const offsetTop = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
      const target = scroller.scrollTop + offsetTop - navH - 16;
      // Suppress scroll events fired by the smooth scroll so the manual-scroll
      // handler does not misread them as user input and pause follow.
      programmaticScrollUntil = performance.now() + 800;
      scroller.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
    }
  };

  audio.addEventListener('timeupdate', update);
  audio.addEventListener('seeked', () => { resumeFollow(); update(); });

  cueEls.forEach((el, idx) => {
    el.addEventListener('dblclick', () => {
      const sec = cues[idx].startSec;
      if (!Number.isFinite(sec)) return;
      audio.currentTime = sec;
      audio.play().catch(() => {});
    });
  });
}

// --- Chapter index (irodori chapters.json) ---
// irodori_test が output.mp3 と同階層に吐く chapters.json (章の見出し + 時刻範囲) を
// 読み、目次パネルを描画する。再生位置で現在章をハイライトし、クリックでその章へ seek。
// SRT の同期スクロールと併存する「地図」(irodori_video の一覧カードの移植)。
const CHAPTER_SPEAKER_LABEL = { sota: 'ソウタ', rin: 'リン', kotoha: 'コトハ' };

function fmtClock(sec) {
  if (!Number.isFinite(sec)) return '--:--';
  const total = Math.floor(sec);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function loadSiblingChapters(audioPath) {
  const slash = audioPath.lastIndexOf('/');
  const dir = slash >= 0 ? audioPath.slice(0, slash + 1) : '';
  const data = await fetchJson(
    `/api/blob?path=${encodeURIComponent(dir + 'chapters.json')}`
  );
  if (data && typeof data.content === 'string') {
    try {
      const arr = JSON.parse(data.content);
      return Array.isArray(arr) && arr.length ? arr : null;
    } catch { return null; }
  }
  return null;
}

async function setupChapterNav(audioPath) {
  // Capture refs before await (same rapid file-switch hazard as setupSrtEmbed).
  const panel = document.getElementById('chapter-nav');
  const audio = document.getElementById('audio-player');
  if (!panel || !audio) return;

  const chapters = await loadSiblingChapters(audioPath);
  if (!chapters) return;

  const rows = chapters.map((ch, i) => {
    // 話者バッジ(ソウタ/コトハ)は表示しないが、現在章の左ボーダー色には話者色を流用する。
    const label = CHAPTER_SPEAKER_LABEL[ch.speaker] || ch.speaker || '';
    const color = speakerColor(label);
    const styleAttr = color ? ` style="--speaker-color: ${color};"` : '';
    const no = String(ch.index != null ? ch.index : i + 1).padStart(2, '0');
    const sub = ch.sub ? `<span class="chapter-sub">${esc(ch.sub)}</span>` : '';
    return `<button type="button" class="chapter-item" data-idx="${i}" data-start="${ch.start}"${styleAttr}>
      <span class="chapter-no">${no}</span>
      <span class="chapter-time">${fmtClock(ch.start)}</span>
      <span class="chapter-body"><span class="chapter-headline">${esc(ch.headline || '')}</span>${sub}</span>
    </button>`;
  }).join('');
  panel.innerHTML = `<div class="chapter-nav-header chapter-nav-toggle">章 (${chapters.length})</div><div class="chapter-nav-list">${rows}</div><div class="chapter-nav-resize" title="ドラッグで高さ調整"></div>`;

  panel.querySelector('.chapter-nav-toggle')
    .addEventListener('click', () => panel.classList.toggle('collapsed'));

  // 章リストは内部スクロールを持ち、下端ハンドルをドラッグして高さ調整できる (PC/スマホ共通、
  // Pointer Events でマウス/タッチ両対応)。値は localStorage に保存して次回・別ファイルでも維持。
  const list = panel.querySelector('.chapter-nav-list');
  {
    const applyH = (h) => { list.classList.add('user-sized'); list.style.height = h + 'px'; };
    const savedH = parseInt(localStorage.getItem('chapterNavHeight') || '', 10);
    if (Number.isFinite(savedH) && savedH >= 56) applyH(savedH);

    const handle = panel.querySelector('.chapter-nav-resize');
    const maxH = () => Math.round(window.innerHeight * 0.85);
    let dragging = false, dragStartY = 0, dragStartH = 0;
    handle.addEventListener('pointerdown', (e) => {
      dragging = true;
      dragStartY = e.clientY;
      dragStartH = list.getBoundingClientRect().height;
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      applyH(Math.max(56, Math.min(maxH(), Math.round(dragStartH + (e.clientY - dragStartY)))));
    });
    const endDrag = (e) => {
      if (!dragging) return;
      dragging = false;
      try { handle.releasePointerCapture(e.pointerId); } catch (err) {}
      const h = parseInt(list.style.height || '', 10);
      if (Number.isFinite(h)) localStorage.setItem('chapterNavHeight', String(h));
    };
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
  }

  const items = Array.from(panel.querySelectorAll('.chapter-item'));
  let curIdx = -1;

  // 章は連続 (各章 end ≒ 次章 start)。pause の隙間では「直前の章」を保つため、
  // start <= t を満たす最後の章を現在章とする。
  const chapterAt = (t) => {
    let idx = -1;
    for (let i = 0; i < chapters.length; i++) {
      if (t >= chapters[i].start) idx = i; else break;
    }
    return idx;
  };

  const update = () => {
    const idx = chapterAt(audio.currentTime);
    if (idx === curIdx) return;
    if (curIdx >= 0 && items[curIdx]) items[curIdx].classList.remove('current');
    curIdx = idx;
    if (idx < 0 || !items[idx]) return;
    items[idx].classList.add('current');
    // 内部スクロールするリスト内で現在章が見えるよう追従 (リスト内スクロールのみ)。
    if (!panel.classList.contains('collapsed')) {
      items[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  };

  audio.addEventListener('timeupdate', update);
  audio.addEventListener('seeked', update);

  // ダブルクリック/ダブルタップでその章へジャンプ (シングルでは誤爆しない)。
  // <button> 上では mobile の dblclick が発火しないため、click の2連を自前で検知する
  // (PC は2クリック、スマホは2タップ、どちらも click が飛ぶので両対応)。
  let lastIdx = -1, lastTime = 0;
  items.forEach((el, idx) => {
    el.addEventListener('click', () => {
      const now = Date.now();
      if (idx === lastIdx && now - lastTime < 400) {
        lastIdx = -1; lastTime = 0;
        const sec = parseFloat(el.dataset.start);
        if (!Number.isFinite(sec)) return;
        audio.currentTime = sec;
        audio.play().catch(() => {});
      } else {
        lastIdx = idx; lastTime = now;
      }
    });
  });

  update();
}

// Media autoplay (audio/video). User-toggleable; persisted in localStorage.
// Applies on every new media load — the toggle change does NOT restart the
// currently playing file.
let autoplayMedia = localStorage.getItem('autoplayMedia') === 'true';
// Audio-only "continuous" mode: when the current audio ends, navigate to the
// next sibling. Implies autoplay (the next track must actually start).
let audioContinuous = localStorage.getItem('audioContinuous') === 'true';

function autoplayToggleHtml() {
  return `<label class="autoplay-toggle" title="次のファイルから自動再生" style="margin-left:8px;font-size:11px;cursor:pointer;color:var(--text-muted);user-select:none;">
    <input type="checkbox" id="autoplay-cb"${autoplayMedia?' checked':''} style="vertical-align:middle;margin:0 3px 0 0;"> 自動再生
  </label>`;
}
function continuousToggleHtml() {
  return `<label class="autoplay-toggle" title="再生終了で次のファイルへ自動移動（自動再生も有効化）" style="margin-left:8px;font-size:11px;cursor:pointer;color:var(--text-muted);user-select:none;">
    <input type="checkbox" id="continuous-cb"${audioContinuous?' checked':''} style="vertical-align:middle;margin:0 3px 0 0;"> 連続再生
  </label>`;
}
function wireAutoplayToggle() {
  const cb = document.getElementById('autoplay-cb');
  if (!cb) return;
  cb.addEventListener('change', () => {
    autoplayMedia = cb.checked;
    localStorage.setItem('autoplayMedia', String(autoplayMedia));
  });
}
function wireContinuousToggle() {
  const cb = document.getElementById('continuous-cb');
  if (!cb) return;
  cb.addEventListener('change', () => {
    audioContinuous = cb.checked;
    localStorage.setItem('audioContinuous', String(audioContinuous));
  });
}
function setupAudioContinuous() {
  const audio = document.getElementById('audio-player');
  if (!audio) return;
  audio.addEventListener('ended', () => {
    if (!audioContinuous) return;
    if (currentBlobIdx < 0 || currentBlobIdx >= currentBlobSiblings.length - 1) return;
    const next = currentBlobSiblings[currentBlobIdx + 1];
    showBlob(next);
  });
}

const PLAYBACK_MIN_SECONDS = 15;

function formatPlaybackTime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = n => n.toString().padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function formatPlaybackDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '-';
  const pad = n => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function refreshPlaybackLog(audioPath) {
  const container = document.getElementById('playback-log-list');
  if (!container) return;
  const hadRows = !!container.querySelector('.playback-log-rows');
  const url = `/api/playback-log?path=${encodeURIComponent(audioPath)}`;
  try {
    const records = await fetchJson(url);
    if (!Array.isArray(records) || records.length === 0) {
      container.innerHTML = '<div class="playback-log-header">再生履歴</div><div class="playback-log-empty">まだありません</div>';
      return;
    }
    const rows = records.slice().reverse().map(r => {
      const when = formatPlaybackDate(r.started_at);
      const start = formatPlaybackTime(r.start_sec);
      const end = formatPlaybackTime(r.end_sec);
      const seekTo = Math.max(r.start_sec, r.end_sec - 10);
      return `<div class="playback-log-row"><span class="playback-log-when">${esc(when)}</span><span class="playback-log-range" data-seek="${seekTo}">${start}〜${end}</span></div>`;
    }).join('');
    container.innerHTML = `<div class="playback-log-header playback-log-toggle">再生履歴 (${records.length})</div><div class="playback-log-rows">${rows}</div>`;
    if (!hadRows) container.classList.add('collapsed');
    const header = container.querySelector('.playback-log-toggle');
    header.addEventListener('click', () => container.classList.toggle('collapsed'));
    container.querySelectorAll('.playback-log-range').forEach(el => {
      el.addEventListener('click', () => {
        const audio = document.getElementById('audio-player');
        if (!audio) return;
        audio.currentTime = parseFloat(el.dataset.seek);
        audio.play().catch(() => {});
      });
    });
  } catch (e) {
    container.innerHTML = '';
  }
}

// 再生しっぱなしのまま離脱すると pause が飛ばず区間ごと消えるため、この間隔で
// 区間を切って書き出す保険を入れる (長い音声で取りこぼしが目立っていた)。
const PLAYBACK_CHECKPOINT_MS = 5 * 60 * 1000;

// 直前のプレーヤーの後始末。showBlob は音声を切り替えるたびに <audio> ごと作り直す
// ので、document/window に張ったリスナーと interval を解除しないと、破棄済み要素を
// 掴んだクロージャが積み上がって二重送信や誤記録の原因になる。
let playbackTeardown = null;

function setupPlaybackLog(audioPath) {
  if (playbackTeardown) { playbackTeardown(); playbackTeardown = null; }
  const audio = document.getElementById('audio-player');
  if (!audio) return;

  let segStart = null;
  let segStartedAt = null;
  let lastTime = 0;
  let pendingRefresh = false;

  const start = () => {
    segStart = audio.currentTime;
    segStartedAt = new Date().toISOString();
    lastTime = audio.currentTime;
  };

  // beacon=true は離脱/バックグラウンド化の経路。fetch は破棄されうるので sendBeacon を
  // 使う (Blob で Content-Type を付けないと Flask 側が JSON として読めない)。
  const finalize = (beacon) => {
    if (segStart === null) return;
    const startCopy = segStart;
    const startedAtCopy = segStartedAt;
    segStart = null;
    segStartedAt = null;
    const endSec = lastTime;
    if (endSec - startCopy < PLAYBACK_MIN_SECONDS) return;
    const body = JSON.stringify({
      path: audioPath,
      start_sec: startCopy,
      end_sec: endSec,
      started_at: startedAtCopy,
      ended_at: new Date().toISOString(),
    });
    if (beacon && navigator.sendBeacon) {
      navigator.sendBeacon('/api/playback-log', new Blob([body], {type: 'application/json'}));
      pendingRefresh = true;
      return;
    }
    fetch('/api/playback-log', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body,
    }).then(() => refreshPlaybackLog(audioPath)).catch(() => {});
  };

  // ここまでを書き出し、再生中なら現在位置から次の区間を始める。
  const checkpoint = (beacon) => {
    const playing = !audio.paused;
    finalize(beacon);
    if (playing) start();
  };

  const onTimeUpdate = () => {
    if (!audio.seeking && !audio.paused) lastTime = audio.currentTime;
  };
  const onPlay = () => start();
  const onStop = () => finalize(false);
  const onSeeked = () => { if (!audio.paused) start(); };
  audio.addEventListener('timeupdate', onTimeUpdate);
  audio.addEventListener('play', onPlay);
  audio.addEventListener('pause', onStop);
  audio.addEventListener('ended', onStop);
  audio.addEventListener('seeking', onStop);
  audio.addEventListener('seeked', onSeeked);

  // 画面ロック / アプリ切替 / タブを閉じる — いずれも pause を伴わずに再生区間が
  // 消えていた経路。hidden になった時点で確定させ、復帰したら履歴表示を更新する。
  const onVisibility = () => {
    if (document.visibilityState === 'hidden') { checkpoint(true); return; }
    if (pendingRefresh) { pendingRefresh = false; refreshPlaybackLog(audioPath); }
  };
  const onPageHide = () => finalize(true);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('pagehide', onPageHide);

  // 前景で再生しっぱなしのままタブが落ちた場合の保険。背面ではタイマーが間引かれる
  // ので、その状態で発火したぶんは beacon で送る。
  const timer = setInterval(() => {
    if (!audio.paused) checkpoint(document.visibilityState === 'hidden');
  }, PLAYBACK_CHECKPOINT_MS);

  playbackTeardown = () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('pagehide', onPageHide);
  };

  refreshPlaybackLog(audioPath);
}

// --- cc-nosleep keep-awake pinger ---
(function() {
  const KEEP_AWAKE_INTERVAL_MS = 20000;
  let clientId = sessionStorage.getItem('keepAwakeClientId');
  if (!clientId) {
    clientId = 'c' + Math.random().toString(16).slice(2, 10);
    sessionStorage.setItem('keepAwakeClientId', clientId);
  }

  function shouldKeepAwake() {
    const audio = document.getElementById('audio-player');
    const audioPlaying = audio && !audio.paused;
    return audioPlaying || document.visibilityState === 'visible';
  }

  function ping() {
    if (!shouldKeepAwake()) return;
    fetch('/api/keep-awake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
      keepalive: true,
    }).catch(() => {});
  }

  setInterval(ping, KEEP_AWAKE_INTERVAL_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') ping();
  });
  document.addEventListener('play', ping, true);
  ping();
})();
