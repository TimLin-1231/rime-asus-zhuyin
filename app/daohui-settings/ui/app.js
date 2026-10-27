'use strict';
// 島輝 設定 — plain JS frontend. Talks to the Rust side through
// window.__TAURI__ (withGlobalTauri). Opened in a normal browser it uses a
// small built-in mock so the layout can be previewed.

const TAURI = window.__TAURI__;
const invoke = TAURI ? TAURI.core.invoke : (cmd, args) => mockInvoke(cmd, args);
const listen = TAURI ? TAURI.event.listen : (name, fn) => mockListen(name, fn);

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

function fmtBytes(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

let toastTimer = null;
function toast(msg, kind = 'info') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'error' ? 7000 : 3500);
}

/** invoke() that reports errors as a toast and rethrows. */
async function call(cmd, args) {
  try {
    return await invoke(cmd, args);
  } catch (e) {
    toast(String(e), 'error');
    throw e;
  }
}

function busy(btn, on) {
  btn.disabled = on;
  btn.classList.toggle('busy', on);
}

function confirmDialog(title, body, okLabel = '確定', danger = false) {
  return new Promise((resolve) => {
    const m = $('#modal');
    $('#modal-title').textContent = title;
    $('#modal-body').textContent = body;
    const ok = $('#modal-ok');
    const cancel = $('#modal-cancel');
    ok.textContent = okLabel;
    ok.classList.toggle('danger', danger);
    m.hidden = false;
    ok.focus();
    const done = (v) => {
      m.hidden = true;
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      document.removeEventListener('keydown', onKey);
      resolve(v);
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    document.addEventListener('keydown', onKey);
  });
}

// ------------------------------------------------------------ navigation

const loaders = {};
const loaded = new Set();

function show(page) {
  if (!loaders[page]) page = 'about';
  for (const b of $$('.nav-item')) b.classList.toggle('active', b.dataset.page === page);
  for (const s of $$('.page')) s.hidden = s.id !== `page-${page}`;
  try { localStorage.setItem('daohui.page', page); } catch (_) { /* ignore */ }
  const first = !loaded.has(page);
  loaded.add(page);
  loaders[page](first);
}

for (const b of $$('.nav-item')) b.addEventListener('click', () => show(b.dataset.page));

// Links and folders (fixed targets only).
let about = null;
const LINKS = {
  repo: () => about && about.repoUrl,
  license: () => about && about.licenseUrl,
  model: () => modelState && modelState.sourceUrl,
};
document.addEventListener('click', (e) => {
  const link = e.target.closest('[data-link]');
  if (link) {
    const url = LINKS[link.dataset.link] && LINKS[link.dataset.link]();
    if (url) call('open_link', { url }).catch(() => {});
  }
  const open = e.target.closest('[data-open]');
  if (open) call('open_folder', { target: open.dataset.open }).catch(() => {});
});

async function redeploy(btn) {
  busy(btn, true);
  try {
    const msg = await call('redeploy');
    toast(msg, 'info');
  } catch (_) { /* toast shown */ } finally {
    busy(btn, false);
  }
}

// ------------------------------------------------------------ 關於

loaders.about = async () => {
  if (!about) about = await call('about_info');
  $('#about-version').textContent = about.version;
  $('#about-userdir').textContent = about.userDir;
  $('#about-sys').textContent = `${about.os} · ${about.arch} · Tauri ${about.tauriVersion}`;
  const list = $('#credits');
  list.replaceChildren(...about.credits.map((c) => el('div', { class: 'credit' },
    el('div', { class: 'credit-main' },
      el('div', { class: 'credit-name' }, c.name),
      el('div', { class: 'credit-role' }, c.role)),
    el('span', { class: 'badge' }, c.license),
    el('button', { class: 'link-btn', onclick: () => call('open_link', { url: c.url }).catch(() => {}) }, '網站 ↗'))));
};

// ------------------------------------------------------------ 檢查更新

let updateBusy = false;

function setUpdateStatus(state, title, desc) {
  $('#upd-icon').dataset.state = state;
  $('#upd-status-title').textContent = title;
  $('#upd-status-desc').textContent = desc;
}

async function checkUpdate() {
  const btn = $('#upd-check');
  busy(btn, true);
  setUpdateStatus('busy', '正在檢查…', '連線到更新伺服器。');
  $('#upd-release').hidden = true;
  try {
    const r = await invoke('check_update');
    $('#upd-current').textContent = r.current;
    const when = `檢查時間 ${r.checkedAt}`;
    $('#nav-update-dot').hidden = r.status !== 'available';
    switch (r.status) {
      case 'available': {
        const rel = r.release;
        setUpdateStatus('accent', `有新版本 ${rel.displayVersion}`, when);
        $('#upd-title').textContent = rel.title;
        const meta = [rel.pubDate, rel.length ? fmtBytes(rel.length) : null,
          rel.sha256 ? 'SHA-256 已提供' : '沒有 SHA-256，無法安裝'].filter(Boolean);
        $('#upd-meta').textContent = meta.join(' · ');
        $('#upd-notes').textContent = rel.notes || '';
        const link = $('#upd-notes-link');
        link.hidden = !rel.notesLink;
        link.onclick = () => call('open_link', { url: rel.notesLink }).catch(() => {});
        $('#upd-install').disabled = !rel.sha256;
        $('#upd-release').hidden = false;
        break;
      }
      case 'up_to_date':
        setUpdateStatus('ok', '已是最新版本', `最新版本 ${r.latest}。${when}`);
        break;
      case 'no_feed':
        setUpdateStatus('idle', '目前沒有可用的更新資訊', `更新資訊尚未發布。${when}`);
        break;
      case 'empty':
        setUpdateStatus('idle', '目前沒有可用的更新', `更新資訊中還沒有任何版本。${when}`);
        break;
      default:
        setUpdateStatus('warn', '沒有適用於這台電腦的版本', when);
    }
  } catch (e) {
    setUpdateStatus('warn', '檢查失敗', String(e));
  } finally {
    busy(btn, false);
  }
}

function showProgress(barId, textId, p, label) {
  const bar = $(barId);
  bar.hidden = false;
  const pct = p.total ? Math.min(100, (p.downloaded / p.total) * 100) : null;
  bar.classList.toggle('indeterminate', pct === null);
  $('.bar', bar).style.width = pct === null ? '' : `${pct.toFixed(1)}%`;
  $(textId).textContent = label;
}

function speedTracker() {
  let last = null;
  let speed = 0;
  return (downloaded) => {
    const now = performance.now();
    if (last && now - last.t > 400) {
      const s = ((downloaded - last.d) * 1000) / (now - last.t);
      speed = speed ? speed * 0.7 + s * 0.3 : s;
      last = { t: now, d: downloaded };
    } else if (!last) {
      last = { t: now, d: downloaded };
    }
    return speed;
  };
}

const updateSpeed = speedTracker();
listen('update-progress', (ev) => {
  const p = ev.payload;
  if (p.phase === 'download') {
    const sp = updateSpeed(p.downloaded);
    const total = p.total ? ` / ${fmtBytes(p.total)}` : '';
    showProgress('#upd-progress', '#upd-progress-text', p,
      `下載中 ${fmtBytes(p.downloaded)}${total}${sp ? ` · ${fmtBytes(sp)}/s` : ''}`);
  } else if (p.phase === 'launch') {
    $('#upd-progress-text').textContent = 'SHA-256 驗證通過，正在啟動安裝程式…';
  }
});

async function installUpdate() {
  const ok = await confirmDialog('下載並安裝更新',
    '會下載安裝程式並驗證 SHA-256，通過後啟動安裝。安裝開始後設定程式會關閉。', '下載並安裝');
  if (!ok) return;
  updateBusy = true;
  const btn = $('#upd-install');
  busy(btn, true);
  $('#upd-cancel').hidden = false;
  $('#upd-check').disabled = true;
  try {
    const msg = await invoke('install_update');
    $('#upd-progress-text').textContent = msg;
    toast(msg);
  } catch (e) {
    $('#upd-progress').hidden = true;
    $('#upd-progress-text').textContent = String(e);
    if (!String(e).startsWith('已取消')) toast(String(e), 'error');
  } finally {
    updateBusy = false;
    busy(btn, false);
    $('#upd-cancel').hidden = true;
    $('#upd-check').disabled = false;
  }
}

$('#upd-check').addEventListener('click', checkUpdate);
$('#upd-install').addEventListener('click', installUpdate);
$('#upd-cancel').addEventListener('click', () => invoke('cancel_update_download'));

loaders.updates = (first) => {
  if (about) $('#upd-current').textContent = about.version;
  if (first && !updateBusy) checkUpdate();
};

// ------------------------------------------------------------ 功能開關

let feat = null;

function readForm() {
  return {
    rawKeys: $('#f-raw').checked,
    prediction: $('#f-pred').checked,
    tabDoubleMs: Number($('#f-tab').value),
    learnEnabled: $('#f-learn').checked,
    englishCompose: $('#f-encompose').checked,
  };
}

function fillForm(s) {
  $('#f-raw').checked = s.rawKeys;
  $('#f-pred').checked = s.prediction;
  $('#f-learn').checked = s.learnEnabled;
  $('#f-encompose').checked = s.englishCompose;
  $('#f-tab').value = s.tabDoubleMs;
  $('#f-tab-out').textContent = `${s.tabDoubleMs} 毫秒`;
}

function same(a, b) {
  return a.rawKeys === b.rawKeys && a.prediction === b.prediction
    && a.tabDoubleMs === b.tabDoubleMs && a.learnEnabled === b.learnEnabled
    && a.englishCompose === b.englishCompose;
}

function updateDirty() {
  const cur = readForm();
  $('#f-tab-out').textContent = `${cur.tabDoubleMs} 毫秒`;
  $('#feat-save').disabled = !feat || same(cur, feat.settings) || !!feat.warning;
}

function renderFeatures(state) {
  feat = state;
  $('#f-tab').min = state.tabMin;
  $('#f-tab').max = state.tabMax;
  fillForm(state.settings);
  $('#feat-path').textContent = state.path;
  $('#feat-exists').textContent = state.exists ? '' : '（尚未建立，目前使用預設值）';
  $('#feat-deployer').textContent = state.deployer || '找不到（輸入法可能尚未安裝）';
  $('#feat-deploy').disabled = !state.deployer;
  $('#feat-saved-deploy').disabled = !state.deployer;
  const w = $('#feat-warning');
  w.hidden = !state.warning && state.userDirExists;
  w.textContent = state.warning
    || `找不到使用者資料夾 ${state.userDir}。輸入法可能尚未安裝或尚未執行過；儲存時會建立這個資料夾。`;
  updateDirty();
}

loaders.features = async () => {
  renderFeatures(await call('get_features'));
};

for (const id of ['#f-raw', '#f-pred', '#f-learn', '#f-encompose', '#f-tab']) {
  $(id).addEventListener('input', () => { $('#feat-saved').hidden = true; updateDirty(); });
}

$('#feat-save').addEventListener('click', async () => {
  const btn = $('#feat-save');
  busy(btn, true);
  try {
    renderFeatures(await call('save_features', { settings: readForm() }));
    $('#feat-saved').hidden = false;
  } catch (_) {
    btn.disabled = false;
  } finally {
    btn.classList.remove('busy');
    updateDirty();
  }
});

$('#feat-defaults').addEventListener('click', () => {
  if (!feat) return;
  fillForm(feat.defaults);
  $('#feat-saved').hidden = true;
  updateDirty();
});

$('#feat-deploy').addEventListener('click', (e) => redeploy(e.currentTarget));
$('#feat-saved-deploy').addEventListener('click', (e) => redeploy(e.currentTarget));

// ------------------------------------------------------------ 學習資料

function renderUserdata(info) {
  $('#ud-total').textContent = fmtBytes(info.totalBytes);
  $('#ud-count').textContent = info.userdbs.length;
  $('#ud-backups').textContent = info.backups.length;
  $('#ud-dir').textContent = info.userDirExists ? info.userDir : `${info.userDir}（不存在）`;
  const tbody = $('#ud-table tbody');
  if (!info.userdbs.length) {
    tbody.replaceChildren(el('tr', {}, el('td', { class: 'empty', colspan: 3 }, '還沒有學習資料。')));
  } else {
    tbody.replaceChildren(...info.userdbs.map((d) => el('tr', {},
      el('td', { class: 'mono' }, d.name),
      el('td', { class: 'num' }, fmtBytes(d.bytes)),
      el('td', { class: 'state' }, el('span', { class: `badge ${d.inUse ? 'warn' : 'ok'}` }, d.inUse ? '使用中' : '未使用')))));
  }
  const list = $('#ud-backup-list');
  if (!info.backups.length) {
    list.replaceChildren(el('div', { class: 'empty' }, '還沒有備份。'));
    return;
  }
  list.replaceChildren(...info.backups.map((b) => {
    const tags = [];
    if (b.automatic) tags.push(el('span', { class: 'badge' }, '還原前自動備份'));
    if (b.skipped.length) tags.push(el('span', { class: 'badge warn' }, `略過 ${b.skipped.length} 個檔案`));
    const desc = `${fmtBytes(b.bytes)} · ${b.files} 個檔案 · ${b.userdbs.length ? b.userdbs.join('、') : '沒有學習資料'}`;
    return el('div', { class: 'backup' },
      el('div', { class: 'backup-main' },
        el('div', { class: 'backup-title' }, b.label, tags),
        el('div', { class: 'backup-desc' }, desc)),
      el('button', { class: 'btn small', onclick: () => call('open_folder', { target: `backup:${b.name}` }).catch(() => {}) }, '開啟'),
      el('button', { class: 'btn small', onclick: (e) => restore(b, e.currentTarget) }, '還原'));
  }));
}

async function loadUserdata() {
  renderUserdata(await call('userdata_info'));
}

async function restore(b, btn) {
  const ok = await confirmDialog(`還原 ${b.label} 的備份？`,
    '目前的學習資料和設定檔會被這份備份取代（還原前會自動再備份一次）。\n輸入法正在使用學習資料時無法還原，需要先結束輸入法。',
    '還原', true);
  if (!ok) return;
  busy(btn, true);
  try {
    const r = await call('restore_backup', { name: b.name });
    let msg = `已還原：${r.restored.join('、') || '（沒有檔案）'}。`;
    if (r.notRestored.length) msg += `\n這份備份不完整，沒有還原：${r.notRestored.join('、')}。`;
    msg += '\n請重新部署或重新啟動輸入法。';
    const box = $('#ud-result');
    box.textContent = msg;
    box.className = `notice pre ${r.notRestored.length ? 'warn' : 'ok'}`;
    box.hidden = false;
  } catch (_) { /* toast shown */ } finally {
    busy(btn, false);
    loadUserdata().catch(() => {});
  }
}

$('#ud-backup').addEventListener('click', async () => {
  const btn = $('#ud-backup');
  busy(btn, true);
  try {
    const b = await call('backup_now');
    const box = $('#ud-result');
    let msg = `已備份到 ${b.path}（${b.files} 個檔案，${fmtBytes(b.bytes)}）。`;
    if (b.skipped.length) {
      msg += `\n有 ${b.skipped.length} 個檔案正被輸入法使用，沒有複製：${b.skipped.slice(0, 6).join('、')}${b.skipped.length > 6 ? '…' : ''}`;
    }
    box.textContent = msg;
    box.className = `notice pre ${b.skipped.length ? 'warn' : 'ok'}`;
    box.hidden = false;
  } catch (_) { /* toast shown */ } finally {
    busy(btn, false);
    loadUserdata().catch(() => {});
  }
});
$('#ud-refresh').addEventListener('click', () => loadUserdata().catch(() => {}));

loaders.userdata = () => loadUserdata().catch(() => {});

// ------------------------------------------------------------ 模型

let modelState = null;
const modelSpeed = speedTracker();

function renderModel(s) {
  modelState = s;
  $('#m-size').textContent = `（約 ${fmtBytes(s.sizeHint)}）`;
  $('#m-name').textContent = s.fileName;
  $('#m-path').textContent = s.path;
  const badge = $('#m-badge');
  const icon = $('#m-icon');
  const dl = $('#m-download');
  if (s.exists) {
    badge.className = 'badge ok';
    badge.textContent = '已安裝';
    icon.dataset.state = 'ok';
    $('#m-desc').textContent = `${fmtBytes(s.bytes)} · 授權 ${s.license}`;
    dl.textContent = '重新下載';
  } else {
    badge.className = 'badge warn';
    badge.textContent = '未安裝';
    icon.dataset.state = 'warn';
    $('#m-desc').textContent = s.partialBytes
      ? `已下載 ${fmtBytes(s.partialBytes)}，可以繼續。`
      : '尚未下載。沒有模型時仍可打字，但整句選字較不準。';
    dl.textContent = s.partialBytes ? '繼續下載' : '下載';
  }
  dl.disabled = !s.configured || s.downloading;
  $('#m-verify').disabled = !s.exists;
  $('#m-hint').textContent = s.configured
    ? '下載可以暫停，之後從中斷處繼續；完成後會驗證 SHA-256。下載後請重新部署。'
    : '模型的下載網址尚未設定（開發中版本）。';
}

listen('model-progress', (ev) => {
  const p = ev.payload;
  if (p.phase === 'download') {
    const sp = modelSpeed(p.downloaded);
    const total = p.total ? ` / ${fmtBytes(p.total)}` : '';
    let eta = '';
    if (sp > 0 && p.total) {
      const sec = Math.round((p.total - p.downloaded) / sp);
      eta = sec > 90 ? ` · 約 ${Math.round(sec / 60)} 分鐘` : ` · 約 ${sec} 秒`;
    }
    showProgress('#m-progress', '#m-progress-text', p,
      `${fmtBytes(p.downloaded)}${total}${sp ? ` · ${fmtBytes(sp)}/s` : ''}${eta}`);
  } else if (p.phase === 'verify') {
    showProgress('#m-progress', '#m-progress-text', { downloaded: 0, total: null }, '正在計算 SHA-256…');
  } else if (p.phase === 'done') {
    $('#m-progress').hidden = true;
  }
});

$('#m-download').addEventListener('click', async () => {
  const btn = $('#m-download');
  busy(btn, true);
  $('#m-cancel').hidden = false;
  $('#m-verify').disabled = true;
  try {
    renderModel(await invoke('download_model'));
    $('#m-progress-text').textContent = '下載完成，SHA-256 驗證通過。請重新部署讓輸入法載入模型。';
  } catch (e) {
    $('#m-progress-text').textContent = String(e);
    if (!String(e).startsWith('已取消')) toast(String(e), 'error');
    renderModel(await invoke('model_status'));
  } finally {
    btn.classList.remove('busy');
    $('#m-cancel').hidden = true;
    $('#m-progress').hidden = true;
  }
});

$('#m-cancel').addEventListener('click', () => invoke('cancel_model_download'));

$('#m-verify').addEventListener('click', async () => {
  const btn = $('#m-verify');
  busy(btn, true);
  try {
    const r = await call('verify_model');
    if (r.matches === true) toast('模型檔案完整（SHA-256 相符）。');
    else if (r.matches === false) toast(`SHA-256 不符，建議重新下載。\n${r.sha256}`, 'error');
    else toast(`SHA-256：${r.sha256}\n（沒有可比對的雜湊值）`);
  } catch (_) { /* toast shown */ } finally {
    busy(btn, false);
    $('#m-progress').hidden = true;
  }
});

$('#m-deploy').addEventListener('click', (e) => redeploy(e.currentTarget));

loaders.model = async () => renderModel(await call('model_status'));

// ------------------------------------------------------------ start

(async () => {
  try {
    about = await invoke('about_info');
    $('#foot-version').textContent = `版本 ${about.version}`;
  } catch (_) { /* shown on the about page */ }
  let start = location.hash.slice(1);
  if (!start) {
    try { start = localStorage.getItem('daohui.page') || 'about'; } catch (_) { start = 'about'; }
  }
  show(start);
})();

// ------------------------------------------------------------ browser preview mock

function mockListen() { return Promise.resolve(() => {}); }

function mockInvoke(cmd, args) {
  const settings = { rawKeys: true, prediction: true, tabDoubleMs: 400, learnEnabled: true, englishCompose: false };
  const data = {
    about_info: {
      name: '島輝', nameEn: 'DaoHui', version: '0.1.0', license: 'GPL-3.0-or-later',
      licenseUrl: 'https://www.gnu.org/licenses/gpl-3.0.html',
      repoUrl: 'https://github.com/TimLin-1231/rime-asus-zhuyin',
      credits: [
        { name: 'RIME 中州韻輸入法引擎（librime）', role: '輸入法引擎', license: 'BSD-3-Clause', url: 'https://github.com/rime/librime' },
        { name: '小狼毫 Weasel', role: 'Windows 版的基礎', license: 'GPL-3.0', url: 'https://github.com/rime/weasel' },
        { name: '萬象語言模型 wanxiang（RIME-LMDG，amzxyz）', role: '整句選字的語言模型', license: 'CC-BY-4.0', url: 'https://github.com/amzxyz/RIME-LMDG' },
      ],
      os: 'windows', arch: 'x86_64', tauriVersion: '2.x', userDir: 'C:\\Users\\you\\AppData\\Roaming\\DaoHui',
    },
    check_update: {
      current: '0.1.0', status: 'available', latest: '0.2.0', feedUrl: '', checkedAt: '2026/10/25 16:30',
      release: {
        version: '0.2.0', displayVersion: '0.2.0', title: '島輝 0.2.0', pubDate: 'Sat, 31 Oct 2026',
        notes: '新功能\n\n• 模型下載與續傳\n• 更快的部署', notesLink: 'https://example.com', url: '', length: 9876543,
        sha256: 'a'.repeat(64), installerArgs: null, fileName: 'DaoHui-0.2.0-setup.exe',
      },
    },
    get_features: {
      settings, defaults: { rawKeys: true, prediction: true, tabDoubleMs: 400, learnEnabled: true, englishCompose: false },
      path: 'C:\\Users\\you\\AppData\\Roaming\\DaoHui\\asus_zhuyin.custom.yaml', exists: true,
      userDir: 'C:\\Users\\you\\AppData\\Roaming\\DaoHui', userDirExists: true, tabMin: 150, tabMax: 1500,
      warning: null, deployer: 'C:\\Program Files\\DaoHui\\DaoHuiDeployer.exe',
    },
    userdata_info: {
      userDir: 'C:\\Users\\you\\AppData\\Roaming\\DaoHui', userDirExists: true, totalBytes: 3456789,
      userdbs: [
        { name: 'asus_learn.userdb', bytes: 456789, inUse: true },
        { name: 'terra_pinyin.userdb', bytes: 3000000, inUse: true },
      ],
      backupRoot: '', backups: [
        { name: '20261025-163000', label: '2026/10/25 16:30:00', path: '', bytes: 3456000, files: 14, userdbs: ['asus_learn.userdb', 'terra_pinyin.userdb'], skipped: ['terra_pinyin.userdb/000012.log'], automatic: false },
        { name: '20261020-090000-before-restore', label: '2026/10/20 09:00:00', path: '', bytes: 3100000, files: 12, userdbs: ['terra_pinyin.userdb'], skipped: [], automatic: true },
      ],
    },
    model_status: {
      fileName: 'wanxiang-lts-zh-hant.gram', path: 'C:\\Users\\you\\AppData\\Roaming\\DaoHui\\wanxiang-lts-zh-hant.gram',
      exists: false, bytes: 0, partialBytes: 0, sizeHint: 410679340, configured: false, url: null, sha256: null,
      downloading: false, license: 'CC-BY-4.0', sourceUrl: 'https://github.com/amzxyz/RIME-LMDG',
    },
  };
  if (cmd === 'save_features') return Promise.resolve({ ...data.get_features, settings: args.settings });
  if (cmd in data) return Promise.resolve(data[cmd]);
  return Promise.reject(new Error(`預覽模式不支援 ${cmd}`));
}
