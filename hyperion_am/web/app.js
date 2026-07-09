/* Hyperion Account Manager — front-end controller (vanilla JS).
   State-driven off REST calls + the /ws event stream. */

const $ = (id) => document.getElementById(id);
const api = (path, opts) => fetch(path, opts).then((r) => r.json());
const jsonPost = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const patchAcc = (uid, body) => api(`/api/accounts/${uid}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const PRESENCE = {
  0: { label: "Offline", cls: "off" },
  1: { label: "Online", cls: "online" },
  2: { label: "In Game", cls: "ingame" },
  3: { label: "In Studio", cls: "studio" },
};

const state = { exists: false, unlocked: false, accounts: [], selected: new Set(), config: {}, search: "" };

/* ───────────────────────── WebSocket ───────────────────────── */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const conn = $("conn");
  ws.onopen = () => { conn.className = "conn online"; conn.querySelector(".conn-label").textContent = "live"; };
  ws.onclose = () => { conn.className = "conn offline"; conn.querySelector(".conn-label").textContent = "reconnecting…"; setTimeout(connectWS, 1500); };
  ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
}
function handleEvent(e) {
  switch (e.type) {
    case "state": state.exists = e.exists; state.unlocked = e.unlocked; render(); break;
    case "accounts": state.accounts = e.accounts || []; pruneSelection(); renderAccounts(); break;
    case "config": state.config = e.config || {}; applyConfigToUI(); break;
    case "toast": showToast(e.message, e.level); break;
    case "login_status": setLoginStatus(e.message); break;
    case "login_done": onLoginDone(e); break;
  }
}

/* ───────────────────────── Top-level render ───────────────────────── */
let _wasLocked = null;
function render() {
  const locked = !state.unlocked;
  $("topbar").hidden = locked;
  $("lock-view").hidden = !locked;
  $("app-view").hidden = locked;
  if (locked) { if (_wasLocked !== true) renderLock(); }
  else renderAccounts();
  _wasLocked = locked;
}
function renderLock() {
  $("lock-form").hidden = false;
  $("lock-error").hidden = true;
  if (state.exists) {
    $("lock-title").textContent = "Welcome back";
    $("lock-sub").textContent = "Enter your master password to unlock your vault.";
    $("pw-label").textContent = "Master password";
    $("pw2-field").hidden = true; $("lock-submit").textContent = "Unlock";
  } else {
    $("lock-title").textContent = "Create your vault";
    $("lock-sub").textContent = "Set a master password. It encrypts your accounts locally and is never stored.";
    $("pw-label").textContent = "New master password";
    $("pw2-field").hidden = false; $("lock-submit").textContent = "Create vault";
  }
  $("pw").value = ""; $("pw2").value = ""; $("pw").focus();
}

/* ───────────────────────── Accounts ───────────────────────── */
function visibleAccounts() {
  const q = state.search.trim().toLowerCase();
  if (!q) return state.accounts;
  return state.accounts.filter((a) =>
    [a.display_name, a.username, a.alias, a.group].some((s) => (s || "").toLowerCase().includes(q)));
}
function pruneSelection() {
  const ids = new Set(state.accounts.map((a) => a.user_id));
  for (const id of [...state.selected]) if (!ids.has(id)) state.selected.delete(id);
}

function renderAccounts() {
  closeMenu();
  const list = $("account-list");
  const accs = visibleAccounts();
  $("count-pill").textContent = state.selected.size ? `${state.selected.size} / ${state.accounts.length}` : `${state.accounts.length}`;
  $("running-count").textContent = `${state.config.roblox_running ?? 0} running`;

  $("empty-state").hidden = state.accounts.length !== 0;
  if (state.accounts.length === 0) { list.innerHTML = ""; return; }

  list.innerHTML = accs.map((a) => {
    const p = PRESENCE[a.last_presence?.type ?? 0] || PRESENCE[0];
    const sel = state.selected.has(a.user_id) ? "selected" : "";
    const displayName = a.display_name || a.username || String(a.user_id);
    const initial = displayName.charAt(0).toUpperCase();
    const avatar = a.avatar_url ? `style="background-image:url('${a.avatar_url}')"` : "";
    const aliasChip = a.alias ? `<span class="chip ghost">✎ ${escapeHtml(a.alias)}</span>` : "";
    const invalid = a.cookie_valid === false ? `<span class="chip bad">cookie expired</span>` : "";
    const kaBadge = a.keep_alive ? `<span class="chip ka">📌 keep online</span>` : "";
    return `
      <div class="acct-card ${sel}" data-uid="${a.user_id}">
        <input type="checkbox" class="acct-check" data-uid="${a.user_id}" ${sel ? "checked" : ""} />
        <div class="acct-avatar" ${avatar}>${a.avatar_url ? "" : initial}
          <span class="pres-dot ${p.cls}" title="${p.label}"></span>
        </div>
        <div class="acct-info">
          <div class="acct-name">${escapeHtml(displayName)}</div>
          <div class="acct-sub">@${escapeHtml(a.username || "unknown")}</div>
          <div class="acct-badges">
            <span class="chip pres">${p.label}</span>
            <span class="chip">${escapeHtml(a.group || "Default")}</span>${aliasChip}${invalid}${kaBadge}
          </div>
        </div>
        <div class="acct-actions">
          <button class="icon-btn ka ${a.keep_alive ? "active" : ""}" data-act="keepalive" data-uid="${a.user_id}" title="Keep online (auto-relaunch)">📌</button>
          <button class="btn small primary" data-act="launch" data-uid="${a.user_id}" title="Launch this account">▶</button>
          <button class="icon-btn" data-act="chrome" data-uid="${a.user_id}" title="Open this account in Chrome">🌐</button>
          <button class="icon-btn" data-act="menu" data-uid="${a.user_id}" title="More">⋯</button>
        </div>
      </div>`;
  }).join("");
  $("select-all").checked = accs.length > 0 && accs.every((a) => state.selected.has(a.user_id));
  renderOptimizerMain();
}

$("account-list").addEventListener("click", (ev) => {
  const check = ev.target.closest(".acct-check");
  if (check) {
    const uid = +check.dataset.uid;
    check.checked ? state.selected.add(uid) : state.selected.delete(uid);
    renderAccounts(); return;
  }
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const uid = +btn.dataset.uid;
  const act = btn.dataset.act;
  if (act === "launch") launchOne(uid);
  else if (act === "menu") accountMenu(uid, btn);
  else if (act === "keepalive") toggleKeepAlive(uid);
  else if (act === "chrome") openChrome(uid);
});

async function toggleKeepAlive(uid) {
  const a = state.accounts.find((x) => x.user_id === uid);
  if (a) await api(`/api/accounts/${uid}/keepalive`, jsonPost({ enabled: !a.keep_alive }));
}
async function openChrome(uid) {
  const res = await api(`/api/accounts/${uid}/open_chrome`, { method: "POST" });
  if (!res.ok) showToast(res.error || "Couldn't open Chrome.", "error");
}

$("select-all").addEventListener("change", (e) => {
  const accs = visibleAccounts();
  if (e.target.checked) accs.forEach((a) => state.selected.add(a.user_id));
  else accs.forEach((a) => state.selected.delete(a.user_id));
  renderAccounts();
});
$("search").addEventListener("input", (e) => { state.search = e.target.value; renderAccounts(); });

/* Context menu */
function closeMenu() { document.querySelector(".ctx-menu")?.remove(); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".ctx-menu") && !e.target.closest('[data-act="menu"]')) closeMenu();
});
function accountMenu(uid, anchorEl) {
  closeMenu();
  const a = state.accounts.find((x) => x.user_id === uid);
  if (!a) return;
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.innerHTML = `
    <button data-m="home">🏠 Launch to Home <small>(no game)</small></button>
    <button data-m="launch">▶ Launch to current Place/Job</button>
    <button data-m="chrome">🌐 Open in Chrome</button>
    <button data-m="validate">✓ Validate cookie</button>
    <button data-m="alias">✎ Set alias</button>
    <button data-m="group">▦ Set group</button>
    <button data-m="copyuser">⧉ Copy username</button>
    <button data-m="remove" class="danger">🗑 Remove account</button>`;
  document.body.appendChild(menu);
  const r = anchorEl.getBoundingClientRect();
  const mw = menu.offsetWidth || 220, mh = menu.offsetHeight || 300;
  menu.style.top = `${Math.max(8, Math.min(r.bottom + 4, window.innerHeight - mh - 8))}px`;
  menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - mw - 8))}px`;
  menu.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    const m = b.dataset.m; closeMenu();
    if (m === "home") { const res = await api(`/api/accounts/${uid}/launch_home`, { method: "POST" }); if (!res.ok) showToast(res.error || "Launch failed.", "error"); }
    else if (m === "launch") launchOne(uid);
    else if (m === "chrome") openChrome(uid);
    else if (m === "validate") await api(`/api/accounts/${uid}/validate`, { method: "POST" });
    else if (m === "alias") { const v = prompt(`Alias for ${a.display_name || a.username}:`, a.alias || ""); if (v !== null) await patchAcc(uid, { alias: v }); }
    else if (m === "group") { const v = prompt(`Group for ${a.display_name || a.username}:`, a.group || "Default"); if (v !== null) await patchAcc(uid, { group: v }); }
    else if (m === "copyuser") navigator.clipboard?.writeText(a.username || "").then(() => showToast("Username copied.", "info"), () => {});
    else if (m === "remove") { if (confirm(`Remove ${a.display_name || a.username}?`)) await api(`/api/accounts/${uid}`, { method: "DELETE" }); }
  });
}

/* ───────────────────────── Launching ───────────────────────── */
function launchTarget() {
  return {
    place_id: $("place-id").value.trim() ? +$("place-id").value.trim() : null,
    job_id: $("job-id").value.trim() || null,
    follow_username: $("follow-user").value.trim() || null,
    vip_link: $("vip-link").value.trim() || null,
  };
}
async function launchOne(uid) {
  const res = await api(`/api/accounts/${uid}/launch`, jsonPost(launchTarget()));
  if (!res.ok) showToast(res.error || "Launch failed.", "error");
}
$("launch-selected").addEventListener("click", async () => {
  const ids = [...state.selected];
  if (ids.length === 0) return showToast("Select one or more accounts first.", "error");
  const t = launchTarget();
  const res = await api("/api/accounts/launch_batch", jsonPost({ user_ids: ids, place_id: t.place_id, job_id: t.job_id, follow_username: t.follow_username, vip_link: t.vip_link }));
  if (!res.ok) showToast(res.error || "Launch failed.", "error");
});

/* Auto-Detect */
function showDetect(msg, cls) { const el = $("detect-status"); el.hidden = false; el.className = "detect-status " + cls; el.textContent = msg; }
$("autodetect-btn").addEventListener("click", async () => {
  const name = $("follow-user").value.trim();
  if (!name) return showDetect("Type a username first.", "warn");
  const btn = $("autodetect-btn"); btn.disabled = true; btn.textContent = "🔍 Detecting…";
  try {
    const res = await api("/api/autodetect", jsonPost({ username: name }));
    if (!res.ok) return showDetect(res.error || "Detection failed.", "bad");
    $("place-id").value = res.place_id || "";
    $("job-id").value = res.job_id || "";
    if (res.server_hidden)
      showDetect(`Found ${res.username} in place ${res.place_id}, but their exact server is hidden by their privacy — bots will join a random server of that game.`, "warn");
    else
      showDetect(`✓ ${res.username} is in place ${res.place_id} — exact server locked. Bots will join that server.`, "ok");
  } finally { btn.disabled = false; btn.textContent = "🔍 Auto-Detect"; }
});

$("refresh-btn").addEventListener("click", async () => {
  $("refresh-btn").disabled = true;
  await api("/api/accounts/refresh", { method: "POST" });
  $("refresh-btn").disabled = false;
});
$("kill-roblox-btn").addEventListener("click", async () => {
  if (!confirm("End ALL Roblox processes on this PC? Every running client will close.")) return;
  const res = await api("/api/roblox/kill", { method: "POST" });
  if (!res.ok) showToast(res.error || "Kill failed.", "error");
});
$("adv-toggle").addEventListener("click", () => {
  const vip = $("vip-link"); vip.hidden = !vip.hidden;
  $("adv-toggle").textContent = vip.hidden ? "VIP / private link ▾" : "VIP / private link ▴";
  if (!vip.hidden) vip.focus();
});

/* ───────────────────────── Lock form ───────────────────────── */
$("lock-form").addEventListener("submit", async (ev) => {
  ev.preventDefault(); $("lock-error").hidden = true;
  const pw = $("pw").value;
  if (!pw) return showLockError("Please enter a password.");
  let res;
  if (state.exists) res = await api("/api/vault/unlock", jsonPost({ password: pw }));
  else {
    if (pw !== $("pw2").value) return showLockError("Passwords don't match.");
    if (pw.length < 4) return showLockError("Use at least 4 characters.");
    res = await api("/api/vault/create", jsonPost({ password: pw }));
  }
  if (!res.ok) showLockError(res.error || "Something went wrong.");
});
const showLockError = (m) => { $("lock-error").textContent = m; $("lock-error").hidden = false; };
$("lock-btn").addEventListener("click", () => api("/api/vault/lock", { method: "POST" }));

/* ───────────────────────── Add account ───────────────────────── */
function openAdd() { $("add-modal").hidden = false; ["cookie-error", "creds-error", "login-status"].forEach((id) => ($(id).hidden = true)); switchTab("cookie"); }
const closeAdd = () => ($("add-modal").hidden = true);
$("add-btn").addEventListener("click", openAdd);
$("empty-add-btn").addEventListener("click", openAdd);
$("add-close").addEventListener("click", closeAdd);
$("add-cancel").addEventListener("click", closeAdd);
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("pane-cookie").hidden = name !== "cookie";
  $("pane-creds").hidden = name !== "creds";
  $("add-run").dataset.mode = name;
  $("add-run").textContent = name === "creds" ? "Open login" : "Add";
}
$("add-run").addEventListener("click", async () => {
  const mode = $("add-run").dataset.mode || "cookie";
  if (mode === "cookie") {
    const cookie = $("cookie-input").value.trim();
    if (!cookie) return showErr("cookie-error", "Paste a .ROBLOSECURITY cookie.");
    setBusy(true, "Validating…");
    const res = await api("/api/accounts/add_cookie", jsonPost({ cookie, group: $("cookie-group").value.trim() || "Default" }));
    setBusy(false);
    if (!res.ok) return showErr("cookie-error", res.error || "Could not add account.");
    closeAdd();
  } else {
    const username = $("cred-user").value.trim(), password = $("cred-pass").value;
    if (!username || !password) return showErr("creds-error", "Enter username and password.");
    setLoginStatus("Starting login…"); setBusy(true, "Opening browser…");
    const res = await api("/api/accounts/add_credentials", jsonPost({ username, password, group: $("cred-group").value.trim() || "Default" }));
    if (!res.ok) { setBusy(false); return showErr("creds-error", res.error || "Could not start login."); }
  }
});
function setLoginStatus(msg) { const el = $("login-status"); el.hidden = false; el.textContent = msg; }
function onLoginDone(e) { setBusy(false); if (e.ok) closeAdd(); else if (e.error) showErr("creds-error", e.error); }
function setBusy(busy, label) { const b = $("add-run"); b.disabled = busy; if (busy) b.textContent = label || "Working…"; else switchTab($("pane-creds").hidden ? "cookie" : "creds"); }
const showErr = (id, msg) => { const el = $(id); el.textContent = msg; el.hidden = false; };

/* ───────────────────────── Import ───────────────────────── */
const DEFAULT_RAM_PATH = "";
function openImport() { $("import-modal").hidden = false; $("import-error").hidden = true; $("src-path").value = DEFAULT_RAM_PATH; detectMode(); }
const closeImport = () => ($("import-modal").hidden = true);
$("import-btn").addEventListener("click", openImport);
$("empty-import-btn").addEventListener("click", openImport);
$("import-close").addEventListener("click", closeImport);
$("import-cancel").addEventListener("click", closeImport);
let detectTimer;
$("src-path").addEventListener("input", () => { clearTimeout(detectTimer); detectTimer = setTimeout(detectMode, 350); });
async function detectMode() {
  const path = $("src-path").value.trim();
  const hint = $("detect-hint");
  if (!path) { hint.textContent = ""; return; }
  const res = await api(`/api/migrate/detect?source_path=${encodeURIComponent(path)}`);
  const map = {
    password: ["ok", "Encrypted with a master password — enter it below."],
    dpapi: ["ok", "Machine-bound vault detected (decrypts on this PC)."],
    plaintext: ["ok", "Unencrypted vault detected."],
    empty: ["warn", "That file is empty — nothing to import."],
    missing: ["warn", "File not found at that path."],
  };
  $("src-pw-field").hidden = res.mode !== "password";
  const [cls, text] = map[res.mode] || ["", ""];
  hint.className = "hint" + (cls ? " " + cls : ""); hint.textContent = text;
}
$("import-run").addEventListener("click", async () => {
  $("import-error").hidden = true;
  const body = { source_path: $("src-path").value.trim(), password: $("src-pw-field").hidden ? null : $("src-pw").value };
  const btn = $("import-run"); btn.disabled = true; btn.textContent = "Importing…";
  try {
    const res = await api("/api/migrate", jsonPost(body));
    if (!res.ok) {
      if (res.error === "password_required") { $("src-pw-field").hidden = false; showErr("import-error", "This vault needs the RAM master password."); }
      else showErr("import-error", res.error || "Import failed.");
      return;
    }
    closeImport();
  } finally { btn.disabled = false; btn.textContent = "Import"; }
});

/* ───────────────────────── Settings ───────────────────────── */
$("settings-btn").addEventListener("click", () => ($("settings-modal").hidden = false));
$("settings-close").addEventListener("click", () => ($("settings-modal").hidden = true));
$("settings-save").addEventListener("click", () => ($("settings-modal").hidden = true));

function applyConfigToUI() {
  const c = state.config;
  $("cfg-multi").checked = !!c.multi_instance;
  $("cfg-privacy").checked = !!c.privacy_mode;
  $("cfg-close").checked = !!c.close_existing_on_launch;
  $("cfg-delay").value = c.launch_delay_secs ?? 3;
  $("cfg-retry").checked = !!c.join_retry_enabled;
  $("cfg-retry-interval").value = c.join_retry_interval_secs ?? 5;
  $("cfg-max-retries").value = c.join_max_retries ?? 12;
  $("cfg-watcher").checked = !!c.watcher_enabled;
  $("cfg-interval").value = c.watcher_interval_secs ?? 30;
  $("cfg-grace").value = c.watcher_grace_checks ?? 2;
  $("cfg-fps").checked = !!c.fps_unlock_enabled;
  $("cfg-fpscap").value = c.fps_cap ?? 240;
  $("cfg-folder").value = c.roblox_folder ?? "";
  $("cfg-extapi").checked = !!c.external_api_enabled;
  $("cfg-ext-list").checked = !!c.external_allow_list;
  $("cfg-ext-launch").checked = !!c.external_allow_launch;
  $("cfg-ext-cookie").checked = !!c.external_allow_get_cookie;
  $("extapi-key").value = c.external_api_key || "";
  $("extapi-detail").hidden = !c.external_api_enabled;
  $("extapi-tag").textContent = c.external_api_enabled ? "on" : "off";
  $("extapi-tag").classList.toggle("on", !!c.external_api_enabled);
  $("cfg-opt").checked = !!c.optimizer_enabled;
  $("cfg-opt-min").checked = !!c.optimizer_minimize_alts;
  $("cfg-opt-ram").value = c.optimizer_soft_ram_mb ?? 500;
  $("cfg-opt-trim").value = c.optimizer_trim_interval_secs ?? 30;
  $("cfg-opt-warmup").value = c.optimizer_warmup_minutes ?? 0.5;
  $("cfg-opt-cores").value = c.optimizer_bot_cores ?? 3;
  $("running-count").textContent = `${c.roblox_running ?? 0} running`;
  renderPresets();
  renderOptimizerMain();
}
function renderOptimizerMain() {
  const sel = $("cfg-opt-main");
  if (!sel) return;
  const cur = state.config.optimizer_main_user_id;
  sel.innerHTML = '<option value="">— none (first-opened is main) —</option>' +
    state.accounts.map((a) => `<option value="${a.user_id}">${escapeHtml(a.display_name || a.username)} (@${escapeHtml(a.username)})</option>`).join("");
  sel.value = cur != null ? String(cur) : "";
}
const cfgFields = {
  "cfg-multi": "multi_instance", "cfg-privacy": "privacy_mode", "cfg-close": "close_existing_on_launch",
  "cfg-retry": "join_retry_enabled", "cfg-watcher": "watcher_enabled", "cfg-fps": "fps_unlock_enabled",
  "cfg-opt": "optimizer_enabled", "cfg-opt-min": "optimizer_minimize_alts",
  "cfg-extapi": "external_api_enabled", "cfg-ext-list": "external_allow_list",
  "cfg-ext-launch": "external_allow_launch", "cfg-ext-cookie": "external_allow_get_cookie",
};
Object.entries(cfgFields).forEach(([id, key]) => $(id).addEventListener("change", () => saveConfig({ [key]: $(id).checked })));
$("cfg-delay").addEventListener("change", () => saveConfig({ launch_delay_secs: +$("cfg-delay").value || 0 }));
$("cfg-retry-interval").addEventListener("change", () => saveConfig({ join_retry_interval_secs: +$("cfg-retry-interval").value || 5 }));
$("cfg-max-retries").addEventListener("change", () => saveConfig({ join_max_retries: +$("cfg-max-retries").value || 0 }));
$("cfg-interval").addEventListener("change", () => saveConfig({ watcher_interval_secs: +$("cfg-interval").value || 30 }));
$("cfg-grace").addEventListener("change", () => saveConfig({ watcher_grace_checks: +$("cfg-grace").value || 2 }));
$("cfg-fpscap").addEventListener("change", () => saveConfig({ fps_cap: +$("cfg-fpscap").value || 240 }));
$("cfg-folder").addEventListener("change", () => saveConfig({ roblox_folder: $("cfg-folder").value.trim() }));
$("cfg-opt-ram").addEventListener("change", () => saveConfig({ optimizer_soft_ram_mb: +$("cfg-opt-ram").value || 500 }));
$("cfg-opt-trim").addEventListener("change", () => saveConfig({ optimizer_trim_interval_secs: +$("cfg-opt-trim").value || 30 }));
$("cfg-opt-warmup").addEventListener("change", () => saveConfig({ optimizer_warmup_minutes: parseFloat($("cfg-opt-warmup").value) || 0 }));
$("cfg-opt-cores").addEventListener("change", () => saveConfig({ optimizer_bot_cores: +$("cfg-opt-cores").value || 1 }));
$("cfg-opt-main").addEventListener("change", () => saveConfig({ optimizer_main_user_id: $("cfg-opt-main").value ? +$("cfg-opt-main").value : null }));
const saveConfig = (patch) => api("/api/config", jsonPost(patch));

/* ───────────────────────── Presets (saved places) ───────────────────────── */
function renderPresets() {
  const sel = $("preset-select");
  const presets = state.config.presets || [];
  const cur = sel.value;
  sel.innerHTML = '<option value="">Saved places…</option>' +
    presets.map((p) => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`).join("");
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  $("del-preset").hidden = !sel.value;
}
$("preset-select").addEventListener("change", () => {
  const p = (state.config.presets || []).find((x) => x.name === $("preset-select").value);
  $("del-preset").hidden = !p;
  if (p) {
    $("place-id").value = p.place_id || "";
    $("job-id").value = p.job_id || "";
    $("follow-user").value = p.follow_username || "";
  }
});
$("save-preset").addEventListener("click", async () => {
  const place = $("place-id").value.trim(), follow = $("follow-user").value.trim();
  if (!place && !follow) return showToast("Enter a Place ID or a username first.", "error");
  const name = prompt("Name this saved place:", "");
  if (!name) return;
  await api("/api/presets", jsonPost({ name: name.trim(), place_id: place ? +place : null, job_id: $("job-id").value.trim() || null, follow_username: follow || null }));
});
$("del-preset").addEventListener("click", async () => {
  const name = $("preset-select").value;
  if (name && confirm(`Delete saved place '${name}'?`)) await api(`/api/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
});

/* ───────────────────────── Backup ───────────────────────── */
let backupMode = "export";
function openBackup(mode) {
  backupMode = mode;
  $("backup-modal").hidden = false; $("backup-error").hidden = true;
  $("backup-title").textContent = mode === "export" ? "Export backup" : "Import backup";
  $("backup-run").textContent = mode === "export" ? "Export" : "Import";
  $("backup-path").value = ""; $("backup-pass").value = "";
}
$("backup-export-btn").addEventListener("click", () => openBackup("export"));
$("backup-import-btn").addEventListener("click", () => openBackup("import"));
$("backup-close").addEventListener("click", () => ($("backup-modal").hidden = true));
$("backup-cancel").addEventListener("click", () => ($("backup-modal").hidden = true));
$("backup-run").addEventListener("click", async () => {
  $("backup-error").hidden = true;
  const body = { path: $("backup-path").value.trim(), password: $("backup-pass").value };
  if (!body.path || !body.password) return showErr("backup-error", "Enter a path and password.");
  const res = await api(`/api/backup/${backupMode}`, jsonPost(body));
  if (!res.ok) return showErr("backup-error", res.error || "Failed.");
  $("backup-modal").hidden = true;
});
$("extapi-key").addEventListener("click", () => {
  $("extapi-key").select();
  navigator.clipboard?.writeText($("extapi-key").value).then(() => showToast("API key copied.", "info"), () => {});
});

/* ───────────────────────── Toasts + helpers ───────────────────────── */
function showToast(message, level = "info") {
  const el = document.createElement("div");
  el.className = `toast ${level}`; el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 300); }, 4200);
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ───────────────────────── Boot ───────────────────────── */
async function boot() {
  const st = await api("/api/vault/status");
  state.exists = st.exists; state.unlocked = st.unlocked;
  const cfg = await api("/api/config");
  if (cfg.ok) state.config = cfg.config;
  applyConfigToUI();
  render();
  connectWS();
}
boot();
