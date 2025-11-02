const state = {
  currentPath: "",
  readOnly: true,
  selection: new Set(),
  loadedOnce: false,
  serverInfo: null,
};

const CHUNK_SIZE = 1024 * 512; // 512 KiB
const TEXT_PREVIEW_LIMIT = 1024 * 200; // 200 KiB
const VIDEO_EXT = new Set(["mp4", "webm", "mkv", "mov", "m4v"]);
const AUDIO_EXT = new Set(["mp3", "aac", "m4a", "flac", "wav", "ogg"]);
const TEXT_EXT = new Set(["txt", "md", "csv", "log", "json", "js", "py", "html", "css"]);
const THEME_KEY = "homeshare-theme";
const LANGUAGE_KEY = "homeshare-language";

const translations = {
  en: {
    refresh: "Refresh",
    zip: "Download ZIP",
    upload: "Upload Files",
    mkdir: "New Folder",
    previewTitle: "Preview",
    previewClose: "Close",
    thName: "Name",
    thSize: "Size",
    thDate: "Modified",
    thOps: "Actions",
    folderTag: "Folder",
    download: "Download",
    preview: "Preview",
    rename: "Rename",
    delete: "Delete",
    renamePrompt: "Enter a new name",
    renameSuccess: "Renamed successfully",
    deleteConfirm: "Delete {name}?",
    deleteSuccess: "Deleted successfully",
    requestFailed: "Request failed: {status} {detail}",
    previewUnsupported: "Preview not available for this file",
    loading: "Loading…",
    videoError: "Video failed to load. Try downloading.",
    audioError: "Audio failed to load. Try downloading.",
    previewTruncated: "Showing the first 200 KB. Download to view everything.",
    previewFailed: "Preview failed: {error}",
    uploadComplete: "Upload complete: {name}",
    uploadFailed: "Upload failed: {name} ({error})",
    folderCreated: "Folder created",
    zipStarted: "ZIP download started",
    initFailed: "Initialization failed: {error}",
    mkdirPrompt: "Folder name",
    root: "Root",
    modeReadOnly: "Read-only",
    modeReadWrite: "Read-write",
    serverInfo: "Sharing {path} · Mode: {mode}",
    zipDefaultSuffix: "selection",
    languageSwitch: "Switch language",
    themeDark: "Dark",
    themeLight: "Light",
    themeAuto: "Auto",
    themeToggleAria: "Theme: {mode}",
  },
  ja: {
    refresh: "再読み込み",
    zip: "ZIP をダウンロード",
    upload: "ファイルをアップロード",
    mkdir: "新しいフォルダ",
    previewTitle: "プレビュー",
    previewClose: "閉じる",
    thName: "名前",
    thSize: "サイズ",
    thDate: "更新日時",
    thOps: "操作",
    folderTag: "フォルダ",
    download: "ダウンロード",
    preview: "プレビュー",
    rename: "名前を変更",
    delete: "削除",
    renamePrompt: "新しい名前を入力してください",
    renameSuccess: "名前を変更しました",
    deleteConfirm: "{name} を削除しますか？",
    deleteSuccess: "削除しました",
    requestFailed: "リクエストに失敗しました: {status} {detail}",
    previewUnsupported: "このファイルはプレビューできません",
    loading: "読み込み中…",
    videoError: "動画を読み込めませんでした。ダウンロードして再生してください。",
    audioError: "音声を読み込めませんでした。ダウンロードして再生してください。",
    previewTruncated: "先頭 200 KB を表示しています。全体を表示するにはダウンロードしてください。",
    previewFailed: "プレビューに失敗しました: {error}",
    uploadComplete: "アップロード完了: {name}",
    uploadFailed: "{name} のアップロードに失敗しました: {error}",
    folderCreated: "フォルダを作成しました",
    zipStarted: "ZIP ダウンロードを開始しました",
    initFailed: "初期化に失敗しました: {error}",
    mkdirPrompt: "フォルダ名",
    root: "ルート",
    modeReadOnly: "読み取り専用",
    modeReadWrite: "読み書き",
    serverInfo: "{path} を共有中 · モード: {mode}",
    zipDefaultSuffix: "選択",
    languageSwitch: "言語を切り替える",
    themeDark: "ダーク",
    themeLight: "ライト",
    themeAuto: "自動",
    themeToggleAria: "テーマ: {mode}",
  },
};

let currentLang = "en";

const els = {
  breadcrumbs: document.querySelector("#breadcrumbs"),
  tableBody: document.querySelector("#fileTableBody"),
  selectAll: document.querySelector("#selectAll"),
  zipBtn: document.querySelector("#zipBtn"),
  fileInput: document.querySelector("#fileInput"),
  uploadLabel: document.querySelector("#uploadLabel"),
  uploadLabelText: document.querySelector("#uploadLabelText"),
  mkdirBtn: document.querySelector("#mkdirBtn"),
  refreshBtn: document.querySelector("#refreshBtn"),
  messages: document.querySelector("#messages"),
  uploadStatus: document.querySelector("#uploadStatus"),
  serverInfo: document.querySelector("#serverInfo"),
  previewOverlay: document.querySelector("#previewOverlay"),
  previewClose: document.querySelector("#previewClose"),
  previewTitle: document.querySelector("#previewTitle"),
  previewBody: document.querySelector("#previewBody"),
  themeToggle: document.querySelector("#themeToggle"),
  languageToggle: document.querySelector("#languageToggle"),
  thName: document.querySelector("#thName"),
  thSize: document.querySelector("#thSize"),
  thDate: document.querySelector("#thDate"),
  thOps: document.querySelector("#thOps"),
};

function t(key, params = {}) {
  const dict = translations[currentLang] || translations.en;
  const fallback = translations.en[key] ?? key;
  const template = dict[key] ?? fallback;
  return template.replace(/\{(\w+)\}/g, (match, param) => {
    if (Object.prototype.hasOwnProperty.call(params, param)) {
      return params[param];
    }
    return match;
  });
}

function formatSize(size) {
  if (size === null || size === undefined) {
    return "-";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let value = size / 1024;
  let unit = "KB";
  for (const next of units) {
    if (value < 1024) {
      unit = next;
      break;
    }
    value /= 1024;
    unit = next;
  }
  return `${value.toFixed(1)} ${unit}`;
}

function getExtension(name) {
  const parts = name.toLowerCase().split(".");
  if (parts.length < 2) {
    return "";
  }
  return parts.pop() || "";
}

function detectPreviewType(name) {
  const ext = getExtension(name);
  if (VIDEO_EXT.has(ext)) {
    return "video";
  }
  if (AUDIO_EXT.has(ext)) {
    return "audio";
  }
  if (TEXT_EXT.has(ext)) {
    return "text";
  }
  return null;
}

function encodePathSegments(path) {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function flash(message, level = "info", timeout = 4000) {
  const text = typeof message === "string" ? message : String(message);
  const wrapper = document.createElement("div");
  wrapper.className = `message ${level}`;
  wrapper.textContent = text;
  els.messages.appendChild(wrapper);
  if (timeout) {
    setTimeout(() => wrapper.remove(), timeout);
  }
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.text()) || response.statusText;
    } catch {
      // ignore
    }
    throw new Error(t("requestFailed", { status: response.status, detail }));
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function joinPath(...parts) {
  const cleaned = parts
    .filter(Boolean)
    .map((p) => p.replace(/^\/+|\/+$/g, ""))
    .filter((p) => p.length);
  return cleaned.join("/");
}

function renderBreadcrumbs() {
  const container = els.breadcrumbs;
  if (!container) return;
  container.innerHTML = "";
  const segments = state.currentPath ? state.currentPath.split("/") : [];
  const crumbs = [{ label: t("root"), path: "" }];
  let acc = "";
  for (const segment of segments) {
    acc = joinPath(acc, segment);
    crumbs.push({ label: segment, path: acc });
  }
  crumbs.forEach((crumb, idx) => {
    const btn = document.createElement("button");
    btn.textContent = crumb.label || "/";
    btn.addEventListener("click", () => loadDirectory(crumb.path));
    container.appendChild(btn);
    if (idx < crumbs.length - 1) {
      const sep = document.createElement("span");
      sep.textContent = "/";
      sep.className = "muted";
      container.appendChild(sep);
    }
  });
}

function updateSelection() {
  els.zipBtn.disabled = state.selection.size === 0;
  const rowCount = els.tableBody.querySelectorAll("tr").length;
  els.selectAll.checked = state.selection.size > 0 && state.selection.size === rowCount;
}

function createRow(entry) {
  const tr = document.createElement("tr");
  const fullPath = joinPath(state.currentPath, entry.name);
  tr.dataset.path = fullPath;

  const selectCell = document.createElement("td");
  selectCell.className = "select-col";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selection.has(fullPath);
  checkbox.addEventListener("change", (ev) => {
    if (ev.target.checked) {
      state.selection.add(fullPath);
    } else {
      state.selection.delete(fullPath);
    }
    updateSelection();
  });
  selectCell.appendChild(checkbox);

  const nameCell = document.createElement("td");
  if (entry.type === "dir") {
    const btn = document.createElement("button");
    btn.textContent = entry.name || "/";
    btn.addEventListener("click", () => loadDirectory(fullPath));
    nameCell.appendChild(btn);
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = t("folderTag");
    tag.style.marginLeft = "6px";
    nameCell.appendChild(tag);
  } else {
    const link = document.createElement("a");
    link.href = `/files/${encodePathSegments(fullPath)}`;
    link.textContent = entry.name;
    link.target = "_blank";
    nameCell.appendChild(link);
  }

  const sizeCell = document.createElement("td");
  sizeCell.className = "size-col";
  sizeCell.textContent = entry.type === "dir" ? "-" : formatSize(entry.size);

  const dateCell = document.createElement("td");
  dateCell.className = "date-col";
  dateCell.textContent = entry.modified;

  const opsCell = document.createElement("td");
  opsCell.className = "ops-col";
  if (entry.type === "file") {
    const dlBtn = document.createElement("button");
    dlBtn.textContent = t("download");
    dlBtn.addEventListener("click", () => {
      window.location.href = `/files/${encodePathSegments(fullPath)}`;
    });
    opsCell.appendChild(dlBtn);
    if (detectPreviewType(entry.name)) {
      const previewBtn = document.createElement("button");
      previewBtn.textContent = t("preview");
      previewBtn.addEventListener("click", () => openPreview(fullPath, entry.name));
      opsCell.appendChild(previewBtn);
    }
  }
  if (!state.readOnly) {
    const renameBtn = document.createElement("button");
    renameBtn.textContent = t("rename");
    renameBtn.addEventListener("click", async () => {
      const nextName = window.prompt(t("renamePrompt"), entry.name);
      if (!nextName || nextName === entry.name) {
        return;
      }
      try {
        await renameEntry(fullPath, joinPath(state.currentPath, nextName));
        flash(t("renameSuccess"), "success");
        await loadDirectory(state.currentPath);
      } catch (err) {
        flash(err.message, "error", 6000);
      }
    });
    opsCell.appendChild(renameBtn);

    const delBtn = document.createElement("button");
    delBtn.textContent = t("delete");
    delBtn.addEventListener("click", async () => {
      const confirmed = window.confirm(t("deleteConfirm", { name: entry.name }));
      if (!confirmed) return;
      try {
        await fetchJSON("/api/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: fullPath }),
        });
        flash(t("deleteSuccess"), "success");
        await loadDirectory(state.currentPath);
      } catch (err) {
        flash(err.message, "error", 6000);
      }
    });
    opsCell.appendChild(delBtn);
  }

  tr.appendChild(selectCell);
  tr.appendChild(nameCell);
  tr.appendChild(sizeCell);
  tr.appendChild(dateCell);
  tr.appendChild(opsCell);
  return tr;
}

async function loadDirectory(path = "") {
  try {
    const rel = path.replace(/^\/+/, "");
    const data = await fetchJSON(`/api/list?path=${encodeURIComponent(rel)}`);
    state.currentPath = rel;
    state.selection.clear();
    els.selectAll.checked = false;
    els.tableBody.innerHTML = "";
    data.entries.forEach((entry) => {
      els.tableBody.appendChild(createRow(entry));
    });
    renderBreadcrumbs();
    updateSelection();
    state.loadedOnce = true;
  } catch (err) {
    flash(err.message, "error", 6000);
  }
}

async function renameEntry(from, to) {
  await fetchJSON("/api/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: from, destination: to }),
  });
}

function clearPreviewBody() {
  const media = els.previewBody.querySelectorAll("video, audio");
  media.forEach((el) => {
    el.pause();
    el.removeAttribute("src");
  });
  els.previewBody.innerHTML = "";
}

function closePreview() {
  clearPreviewBody();
  els.previewOverlay.classList.add("hidden");
}

async function openPreview(fullPath, displayName) {
  const previewType = detectPreviewType(displayName);
  if (!previewType) {
    flash(t("previewUnsupported"), "warning", 4000);
    return;
  }
  const encoded = encodePathSegments(fullPath);
  els.previewTitle.textContent = t("previewTitle");
  clearPreviewBody();
  els.previewOverlay.classList.remove("hidden");
  const loader = document.createElement("div");
  loader.className = "loader";
  loader.textContent = t("loading");
  els.previewBody.appendChild(loader);

  try {
    if (previewType === "video") {
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.setAttribute("playsinline", "");
      video.setAttribute("webkit-playsinline", "");
      video.preload = "metadata";
      video.src = `/files/${encoded}`;
      video.classList.add("hidden");
      const reveal = () => {
        if (loader.parentElement) loader.remove();
        video.classList.remove("hidden");
      };
      video.addEventListener("loadeddata", reveal, { once: true });
      video.addEventListener("error", () => {
        loader.textContent = t("videoError");
      });
      els.previewBody.appendChild(video);
      video.load();
      return;
    }
    if (previewType === "audio") {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "metadata";
      audio.src = `/files/${encoded}`;
      audio.classList.add("hidden");
      const reveal = () => {
        if (loader.parentElement) loader.remove();
        audio.classList.remove("hidden");
      };
      audio.addEventListener("loadeddata", reveal, { once: true });
      audio.addEventListener("error", () => {
        loader.textContent = t("audioError");
      });
      els.previewBody.appendChild(audio);
      return;
    }
    if (previewType === "text") {
      const res = await fetch(`/files/${encoded}`, {
        headers: { Range: `bytes=0-${TEXT_PREVIEW_LIMIT - 1}` },
      });
      if (!(res.status === 200 || res.status === 206)) {
        throw new Error(await res.text());
      }
      const text = await res.text();
      if (loader.parentElement) loader.remove();
      const pre = document.createElement("pre");
      pre.textContent = text;
      els.previewBody.appendChild(pre);
      const contentRange = res.headers.get("Content-Range");
      let truncated = false;
      if (contentRange) {
        const match = contentRange.match(/bytes \d+-([\d]+)\/(\d+|\*)/);
        if (match) {
          const [, endStr, totalStr] = match;
          const end = parseInt(endStr, 10);
          const total = totalStr === "*" ? null : parseInt(totalStr, 10);
          if (total !== null && !Number.isNaN(total)) {
            truncated = end + 1 < total;
          }
        }
      } else if (text.length >= TEXT_PREVIEW_LIMIT - 1) {
        truncated = true;
      }
      if (truncated) {
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = t("previewTruncated");
        els.previewBody.appendChild(meta);
      }
    }
  } catch (err) {
    closePreview();
    flash(t("previewFailed", { error: err.message || err }), "error", 6000);
  }
}

function storageKey(path, size) {
  return `homeshare.upload.${path}@${size}`;
}

function createUploadItem(file, targetPath) {
  const container = document.createElement("div");
  container.className = "upload-item";
  container.dataset.target = targetPath;
  const title = document.createElement("div");
  title.textContent = `${file.name} (${formatSize(file.size)})`;
  const progress = document.createElement("div");
  progress.className = "progress-bar";
  const bar = document.createElement("span");
  progress.appendChild(bar);
  container.appendChild(title);
  container.appendChild(progress);
  els.uploadStatus.appendChild(container);
  return { container, bar };
}

async function ensureSession(targetPath, file) {
  const key = storageKey(targetPath, file.size);
  const existingId = window.localStorage.getItem(key);
  if (existingId) {
    try {
      const status = await fetchJSON(`/api/upload/${existingId}/status`);
      return { session: status, key };
    } catch {
      window.localStorage.removeItem(key);
    }
  }
  const session = await fetchJSON("/api/upload/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path: targetPath,
      size: file.size,
      resume: true,
    }),
  });
  window.localStorage.setItem(key, session.upload_id);
  return { session, key };
}

async function uploadFile(file) {
  const targetPath = joinPath(state.currentPath, file.name);
  const { session, key } = await ensureSession(targetPath, file);
  const uploadId = session.upload_id;
  let offset = session.received || 0;
  const { bar, container } = createUploadItem(file, targetPath);
  bar.style.width = `${Math.floor((offset / file.size) * 100)}%`;
  try {
    while (offset < file.size) {
      const next = Math.min(offset + CHUNK_SIZE, file.size);
      const chunk = file.slice(offset, next);
      const res = await fetch(`/api/upload/${uploadId}`, {
        method: "PUT",
        headers: {
          "Content-Range": `bytes ${offset}-${next - 1}/${file.size}`,
        },
        body: chunk,
      });
      if (!res.ok) {
        throw new Error(await res.text());
      }
      const meta = await res.json();
      offset = meta.received;
      bar.style.width = `${Math.floor((offset / file.size) * 100)}%`;
    }
    flash(t("uploadComplete", { name: file.name }), "success");
    await loadDirectory(state.currentPath);
  } catch (err) {
    flash(t("uploadFailed", { name: file.name, error: err.message || err }), "error", 6000);
  } finally {
    window.localStorage.removeItem(key);
    setTimeout(() => container.remove(), 4000);
  }
}

async function handleZipDownload() {
  const paths = Array.from(state.selection);
  if (!paths.length) return;
  try {
    const res = await fetch("/api/zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    const suffix =
      paths.length === 1
        ? paths[0].split("/").pop() || t("zipDefaultSuffix")
        : t("zipDefaultSuffix");
    a.href = url;
    a.download = `homeshare-${suffix || "download"}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    flash(t("zipStarted"), "success");
  } catch (err) {
    flash(err.message, "error", 6000);
  }
}

function getCurrentTheme() {
  return document.documentElement.getAttribute("data-theme") || "system";
}

function applyTheme(mode) {
  const root = document.documentElement;
  let effective = mode;
  if (mode === "dark" || mode === "light") {
    root.setAttribute("data-theme", mode);
  } else {
    root.removeAttribute("data-theme");
    effective = "system";
  }
  if (els.themeToggle) {
    const icon = effective === "dark" ? "🌙" : effective === "light" ? "☀️" : "🌓";
    const labelKey = effective === "dark" ? "themeDark" : effective === "light" ? "themeLight" : "themeAuto";
    const label = t(labelKey);
    const aria = t("themeToggleAria", { mode: label });
    els.themeToggle.textContent = `${icon} ${label}`;
    els.themeToggle.setAttribute("aria-label", aria);
    els.themeToggle.title = aria;
  }
}

function initTheme() {
  const stored = localStorage.getItem(THEME_KEY) || "system";
  applyTheme(stored);
  if (els.themeToggle) {
    els.themeToggle.addEventListener("click", () => {
      const current = getCurrentTheme();
      const next = current === "system" ? "dark" : current === "dark" ? "light" : "system";
      if (next === "system") {
        localStorage.removeItem(THEME_KEY);
      } else {
        localStorage.setItem(THEME_KEY, next);
      }
      applyTheme(next);
    });
  }
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => {
      const preference = localStorage.getItem(THEME_KEY) || "system";
      if (preference === "system") {
        applyTheme("system");
      }
    };
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", handleChange);
    } else if (typeof mq.addListener === "function") {
      mq.addListener(handleChange);
    }
  }
}

function updateStaticText() {
  if (els.refreshBtn) els.refreshBtn.textContent = t("refresh");
  if (els.zipBtn) els.zipBtn.textContent = t("zip");
  if (els.uploadLabelText) els.uploadLabelText.textContent = t("upload");
  if (els.mkdirBtn) els.mkdirBtn.textContent = t("mkdir");
  if (els.previewTitle) els.previewTitle.textContent = t("previewTitle");
  if (els.previewClose) els.previewClose.textContent = t("previewClose");
  if (els.thName) els.thName.textContent = t("thName");
  if (els.thSize) els.thSize.textContent = t("thSize");
  if (els.thDate) els.thDate.textContent = t("thDate");
  if (els.thOps) els.thOps.textContent = t("thOps");
  applyTheme(getCurrentTheme());
}

function applyLanguage(lang, { skipReload = false } = {}) {
  const target = translations[lang] ? lang : "en";
  currentLang = target;
  localStorage.setItem(LANGUAGE_KEY, target);
  document.documentElement.lang = target === "ja" ? "ja" : "en";
  if (els.languageToggle) {
    const label = t("languageSwitch");
    els.languageToggle.textContent = target === "ja" ? "日本語" : "EN";
    els.languageToggle.setAttribute("aria-label", label);
    els.languageToggle.title = label;
  }
  updateStaticText();
  if (state.serverInfo && els.serverInfo) {
    const modeKey = state.readOnly ? "modeReadOnly" : "modeReadWrite";
    els.serverInfo.textContent = t("serverInfo", {
      path: state.serverInfo.share_root,
      mode: t(modeKey),
    });
  }
  renderBreadcrumbs();
  if (!skipReload && state.loadedOnce) {
    loadDirectory(state.currentPath);
  }
}

function initLanguage() {
  const stored = localStorage.getItem(LANGUAGE_KEY);
  if (stored && translations[stored]) {
    currentLang = stored;
  }
  applyLanguage(currentLang, { skipReload: true });
  if (els.languageToggle) {
    els.languageToggle.addEventListener("click", () => {
      const next = currentLang === "en" ? "ja" : "en";
      applyLanguage(next);
    });
  }
}

async function init() {
  try {
    const info = await fetchJSON("/api/info");
    state.readOnly = info.read_only;
    state.serverInfo = info;
    if (!state.readOnly) {
      els.uploadLabel.classList.remove("hidden");
      els.mkdirBtn.classList.remove("hidden");
    }
    if (els.serverInfo) {
      const modeKey = state.readOnly ? "modeReadOnly" : "modeReadWrite";
      els.serverInfo.textContent = t("serverInfo", {
        path: info.share_root,
        mode: t(modeKey),
      });
    }
    await loadDirectory("");
  } catch (err) {
    flash(t("initFailed", { error: err.message || err }), "error", 8000);
  }
}

initTheme();
initLanguage();

if (els.selectAll) {
  els.selectAll.addEventListener("change", (ev) => {
    const rows = els.tableBody.querySelectorAll("tr");
    state.selection.clear();
    if (ev.target.checked) {
      rows.forEach((row) => {
        const fullPath = row.dataset.path;
        if (fullPath) {
          state.selection.add(fullPath);
        }
        row.querySelector('input[type="checkbox"]').checked = true;
      });
    } else {
      rows.forEach((row) => {
        row.querySelector('input[type="checkbox"]').checked = false;
      });
    }
    updateSelection();
  });
}

if (els.zipBtn) {
  els.zipBtn.addEventListener("click", handleZipDownload);
}

if (els.refreshBtn) {
  els.refreshBtn.addEventListener("click", () => loadDirectory(state.currentPath));
}

if (els.fileInput) {
  els.fileInput.addEventListener("change", async (ev) => {
    const files = Array.from(ev.target.files || []);
    for (const file of files) {
      await uploadFile(file);
    }
    els.fileInput.value = "";
  });
}

if (els.mkdirBtn) {
  els.mkdirBtn.addEventListener("click", async () => {
    const name = window.prompt(t("mkdirPrompt"));
    if (!name) return;
    const target = joinPath(state.currentPath, name);
    try {
      await fetchJSON("/api/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target }),
      });
      flash(t("folderCreated"), "success");
      await loadDirectory(state.currentPath);
    } catch (err) {
      flash(err.message, "error", 6000);
    }
  });
}

if (els.previewClose) {
  els.previewClose.addEventListener("click", closePreview);
}

if (els.previewOverlay) {
  els.previewOverlay.addEventListener("click", (ev) => {
    if (ev.target === els.previewOverlay) {
      closePreview();
    }
  });
}

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !els.previewOverlay.classList.contains("hidden")) {
    closePreview();
  }
});

init();
