// Tabs, favorites and startup. Loaded last so everything it calls exists.

// --- Favorites ---
function buildFavOptions() {
  let html = '<option value="">&#9733; Favorites</option>';
  for (const fav of favorites) {
    html += `<option value="${esc(fav)}">${esc(fav.split('/').join(' / '))}</option>`;
  }
  favSelect.innerHTML = html;
}

async function toggleFavorite(event, dirPath) {
  event.stopPropagation();
  const isFav = favorites.includes(dirPath);
  await fetch('/api/favorites', {
    method: isFav ? 'DELETE' : 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: dirPath}),
  });
  if (isFav) {
    favorites = favorites.filter(f => f !== dirPath);
  } else {
    favorites.push(dirPath);
  }
  buildFavOptions();
  renderFiles();
}

function jumpToFavorite(fav) {
  if (!fav) return;
  currentTab = 'files';
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'files'));
  renderFiles(fav);
}

// --- Init ---
async function init() {
  migrateLegacyStorage();
  const saved = loadState();
  if (TABS.includes(saved.tab)) currentTab = saved.tab;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === currentTab));
  if (saved.path) currentPath = saved.path;

  const favsPromise = fetchJson('/api/favorites');
  const navDirsPromise = fetchJson('/api/nav_direction').catch(() => ({}));
  renderTab();
  favorites = await favsPromise;
  navDirections = await navDirsPromise || {};
  buildFavOptions();
}

function refresh() {
  renderTab();
}

// --- Tabs ---
function switchTab(tab) {
  currentTab = tab;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  saveState();
  renderTab();
}

async function renderTab() {
  clearBlobUi();
  if (currentTab === 'files') await renderFiles();
  else if (currentTab === 'listen') await renderListen();
}

init();
