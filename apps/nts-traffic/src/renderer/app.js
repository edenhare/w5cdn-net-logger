/* global window */
const api = window.ntsApi;
const $ = (id) => document.getElementById(id);

const state = {
  settings: null,
  queue: "needs_action",
  bookFilter: null,
  search: "",
  radiograms: [],
  books: [],
  selectedId: null,
  checkedIds: new Set(),
};

const ARL_NUM_WORDS = new Set([
  "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
  "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
  "EIGHTEEN", "NINETEEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
]);

function escHtml(raw) {
  return String(raw || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function nowUtcCompact() {
  const d = new Date();
  return `${String(d.getUTCHours()).padStart(2, "0")}${String(d.getUTCMinutes()).padStart(2, "0")}Z`;
}

function nowUtcDateShort() {
  const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const d = new Date();
  return `${months[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2, "0")}`;
}

function computeCheckLocal(text) {
  const tokens = (text || "").trim().split(/\s+/).filter(Boolean);
  const upper = tokens.map((t) => t.toUpperCase());
  const isArl = upper.some(
    (tok, i) => tok === "ARL" && i + 1 < upper.length && (ARL_NUM_WORDS.has(upper[i + 1]) || /^\d+$/.test(upper[i + 1]))
  );
  return { count: tokens.length, label: isArl && tokens.length ? `ARL ${tokens.length}` : String(tokens.length) };
}

let toastTimer = null;
function showToast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2800);
}

function updateClock() {
  const d = new Date();
  $("utcClock").textContent = `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}:${String(d.getUTCSeconds()).padStart(2, "0")}Z`;
}

function alertBadgesFor(rg) {
  const badges = [];
  if (rg.svc_required && !rg.svc_sent_at && rg.status !== "CANCELLED") {
    badges.push(`<span class="badge badge-alert" title="HXC/HXD requires SVC reply">SVC DUE</span>`);
  }
  if (rg.hxb_expires_at && (rg.status === "LISTED" || rg.status === "ASSIGNED")) {
    const minsLeft = Math.round((Date.parse(rg.hxb_expires_at) - Date.now()) / 60000);
    const lbl = minsLeft <= 0 ? "HXB EXPIRED" : minsLeft < 120 ? `HXB ${minsLeft}m` : `HXB ${Math.round(minsLeft / 60)}h`;
    badges.push(`<span class="badge badge-alert" title="Expires ${escHtml(rg.hxb_expires_at)}">${lbl}</span>`);
  }
  return badges.join(" ");
}

async function refreshAll() {
  const [counts, rgs, books] = await Promise.all([
    api.getQueueCounts(),
    api.listRadiograms({ queue: state.queue, search: state.search, bookId: state.bookFilter }),
    api.listBooks(),
  ]);
  $("cntNeedsAction").textContent = String(counts.needs_action);
  $("cntReadyRelay").textContent = String(counts.ready_relay);
  $("cntFollowup").textContent = String(counts.followup);
  $("cntArchive").textContent = String(counts.archive);
  $("cntAll").textContent = String(counts.all);

  state.radiograms = rgs;
  state.books = books;
  renderBooks();
  renderTable();
  if (state.selectedId) {
    const stillExists = await api.getRadiogram(state.selectedId);
    if (stillExists) {
      await renderDetail(stillExists);
    } else {
      state.selectedId = null;
      $("detailContent").classList.add("hidden");
      $("emptyDetail").classList.remove("hidden");
    }
  }
}

function renderBooks() {
  const box = $("booksList");
  if (!state.books.length) {
    box.innerHTML = `<div class="muted-small">No book messages yet.</div>`;
    return;
  }
  box.innerHTML = state.books
    .map(
      (b) => `
      <div class="book-card ${state.bookFilter === b.id ? "active" : ""}" data-book-id="${b.id}">
        <div class="book-card-title">${escHtml(b.title)}</div>
        <div class="muted-small">${b.member_count} msgs • ${b.delivered_count} dlvd • ${b.relayed_count} rly</div>
        <button type="button" class="btn btn-xs" style="margin-top:5px;" data-copy-book="${b.id}">Copy Book Text</button>
      </div>`
    )
    .join("");
}

function renderTable() {
  const tbody = $("rgTbody");
  if (!state.radiograms.length) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:28px; color:var(--muted);">No radiograms in this queue.</td></tr>`;
    return;
  }
  tbody.innerHTML = state.radiograms
    .map((rg) => {
      const checked = state.checkedIds.has(rg.id) ? "checked" : "";
      const sel = state.selectedId === rg.id ? "selected" : "";
      const dest = [rg.to_name, rg.to_callsign, rg.to_city_state_zip].filter(Boolean).join(" • ");
      return `
        <tr data-rg-id="${rg.id}" class="${sel}">
          <td><input type="checkbox" data-chk-id="${rg.id}" ${checked} /></td>
          <td class="mono"><strong>${escHtml(rg.msg_number)}</strong></td>
          <td><span class="badge badge-prec-${escHtml(rg.precedence)}">${escHtml(rg.precedence)}</span></td>
          <td class="mono">${escHtml(rg.hx)}</td>
          <td class="mono">${escHtml(rg.station_of_origin)}</td>
          <td class="mono">${escHtml(rg.check_text)}</td>
          <td>${escHtml(dest || "—")}</td>
          <td><span class="badge badge-status-${escHtml(rg.status)}">${escHtml(rg.status)}</span></td>
          <td>${alertBadgesFor(rg)}</td>
        </tr>`;
    })
    .join("");
}

async function renderDetail(rg) {
  state.selectedId = rg.id;
  $("emptyDetail").classList.add("hidden");
  $("detailContent").classList.remove("hidden");
  $("detailTitle").textContent = `NR ${rg.msg_number} (${rg.precedence}) — ${rg.to_name || rg.to_callsign || "No Addressee"}`;
  $("detailMeta").textContent = `Origin: ${rg.station_of_origin} (${rg.place_of_origin || "N/A"}) • Check: ${rg.check_text} • Status: ${rg.status}${rg.net_name ? ` • Net: ${rg.net_name}` : ""}`;

  const alerts = [];
  if (rg.svc_required && !rg.svc_sent_at && rg.status !== "CANCELLED") {
    alerts.push(`HX (${rg.hx}) requires a Service Message (SVC) back to ${rg.station_of_origin}.`);
  }
  if (rg.hxb_expires_at && (rg.status === "LISTED" || rg.status === "ASSIGNED")) {
    alerts.push(`HXB deadline: ${rg.hxb_expires_at}`);
  }
  const banner = $("detailAlertBanner");
  banner.textContent = alerts.join("  |  ");
  banner.classList.toggle("hidden", alerts.length === 0);

  const [formatted, logs] = await Promise.all([api.formatRadiogramText(rg.id), api.getHandlingLog(rg.id)]);
  $("detailPreview").textContent = formatted;
  $("detailLogs").innerHTML = logs
    .map(
      (l) => `
      <div class="log-row">
        <span><strong>${escHtml(l.action)}</strong> ${escHtml(l.counterparty)} ${escHtml(l.mode)}</span>
        <span class="mono muted-small">${escHtml(l.timestamp_utc)}</span>
      </div>`
    )
    .join("");
}

function updateComposerCheck() {
  const { count, label } = computeCheckLocal($("rgMessageText").value);
  $("rgCheckBadge").textContent = label;
  $("rgWordAdvisory").classList.toggle("hidden", count <= 25);
}

function openRgModal(rg = null) {
  const isEdit = Boolean(rg && rg.id);
  const s = state.settings;
  $("rgId").value = isEdit ? String(rg.id) : "";
  $("rgModalTitle").textContent = isEdit ? `Edit ARRL Radiogram NR ${rg.msg_number}` : "New ARRL Radiogram";
  $("rgNumber").value = rg?.msg_number || "";
  $("rgPrecedence").value = rg?.precedence || s.default_precedence || "R";
  $("rgHx").value = rg?.hx || "";
  $("rgDirection").value = rg?.direction || "INBOUND";
  $("rgOrigin").value = rg?.station_of_origin || s.operator_call || "W5CDN";
  $("rgPlace").value = rg?.place_of_origin ?? (rg ? "" : s.operator_qth || "");
  $("rgTimeFiled").value = rg?.time_filed || nowUtcCompact();
  $("rgDateFiled").value = rg?.date_filed || nowUtcDateShort();
  $("rgToName").value = rg?.to_name || "";
  $("rgToCall").value = rg?.to_callsign || "";
  $("rgToAddress").value = rg?.to_address || "";
  $("rgToCity").value = rg?.to_city_state_zip || "";
  $("rgToPhone").value = rg?.to_phone || "";
  $("rgToEmail").value = rg?.to_email || "";
  $("rgMessageText").value = rg?.message_text || "";
  $("rgSignature").value = rg?.signature || "";
  $("rgNetName").value = rg?.net_name || "";
  $("rgStatus").value = rg?.status || "LISTED";
  $("rgRcvdFrom").value = rg?.received_from || "";
  $("rgRcvdAt").value = rg?.received_at || "";
  $("rgSentTo").value = rg?.sent_to || "";
  $("rgSentAt").value = rg?.sent_at || "";
  $("arlPicker").value = "";
  $("btnSaveAddAnother").classList.toggle("hidden", isEdit);
  updateComposerCheck();
  $("rgModal").classList.remove("hidden");
  $("rgToName").focus();
}

async function saveRadiogramForm(addAnother = false) {
  const idVal = $("rgId").value;
  const payload = {
    msg_number: $("rgNumber").value.trim(),
    precedence: $("rgPrecedence").value,
    hx: $("rgHx").value.trim(),
    direction: $("rgDirection").value,
    station_of_origin: $("rgOrigin").value.trim(),
    place_of_origin: $("rgPlace").value.trim(),
    time_filed: $("rgTimeFiled").value.trim(),
    date_filed: $("rgDateFiled").value.trim(),
    to_name: $("rgToName").value.trim(),
    to_callsign: $("rgToCall").value.trim(),
    to_address: $("rgToAddress").value.trim(),
    to_city_state_zip: $("rgToCity").value.trim(),
    to_phone: $("rgToPhone").value.trim(),
    to_email: $("rgToEmail").value.trim(),
    message_text: $("rgMessageText").value.trim(),
    signature: $("rgSignature").value.trim(),
    net_name: $("rgNetName").value.trim(),
    status: $("rgStatus").value,
    received_from: $("rgRcvdFrom").value.trim(),
    received_at: $("rgRcvdAt").value.trim(),
    sent_to: $("rgSentTo").value.trim(),
    sent_at: $("rgSentAt").value.trim(),
  };

  let saved;
  if (idVal) {
    saved = await api.updateRadiogram(Number(idVal), payload);
    showToast(`Radiogram NR ${saved.msg_number} updated`);
  } else {
    saved = await api.createRadiogram(payload);
    showToast(addAnother ? `NR ${saved.msg_number} saved — ready for next` : `Radiogram NR ${saved.msg_number} saved`);
  }
  state.selectedId = saved.id;
  await refreshAll();

  if (addAnother && !idVal) {
    const nextNum = /^\d+$/.test(payload.msg_number) ? String(Number(payload.msg_number) + 1) : "";
    openRgModal({
      msg_number: nextNum,
      precedence: payload.precedence,
      direction: payload.direction,
      station_of_origin: payload.station_of_origin,
      place_of_origin: payload.place_of_origin,
      net_name: payload.net_name,
      received_from: payload.received_from,
      received_at: payload.received_from ? nowUtcCompact() : "",
      time_filed: nowUtcCompact(),
      date_filed: nowUtcDateShort(),
    });
  } else {
    $("rgModal").classList.add("hidden");
  }
}

async function openSarModal() {
  const now = new Date();
  const ym = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  $("sarMonth").value = ym;
  await loadSarStats(ym);
  $("sarModal").classList.remove("hidden");
}

async function loadSarStats(month) {
  const s = await api.getMonthlySarStats(month);
  $("sarGrid").innerHTML = `
    <div class="sar-card"><span>Originated (O)</span><span class="sar-val">${s.originated}</span></div>
    <div class="sar-card"><span>Received (R)</span><span class="sar-val">${s.received}</span></div>
    <div class="sar-card"><span>Sent / Relayed (S)</span><span class="sar-val">${s.sent}</span></div>
    <div class="sar-card"><span>Delivered (D)</span><span class="sar-val">${s.delivered}</span></div>
    <div class="sar-card"><span>Total SAR Points</span><span class="sar-val">${s.total}</span></div>
    <div class="sar-card"><span>BPL Eligible</span><span class="sar-val">${s.bpl_eligible ? "YES" : "NO"}</span></div>
  `;
}

async function init() {
  state.settings = await api.getSettings();
  document.documentElement.dataset.theme = state.settings.theme || "shack-dark";
  $("opCallBadge").textContent = state.settings.operator_call;
  updateClock();
  setInterval(updateClock, 1000);

  const arlList = await api.listArlTexts();
  const picker = $("arlPicker");
  for (const item of arlList) {
    const opt = document.createElement("option");
    opt.value = item.code;
    opt.textContent = `${item.code} — ${item.meaning.slice(0, 56)}`;
    picker.appendChild(opt);
  }

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => $(btn.dataset.close).classList.add("hidden"));
  });

  document.querySelectorAll("[data-queue]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.queue = btn.dataset.queue;
      state.bookFilter = null;
      document.querySelectorAll("[data-queue]").forEach((b) => b.classList.toggle("active", b === btn));
      refreshAll();
    });
  });

  $("booksList").addEventListener("click", async (e) => {
    const copyBtn = e.target.closest("[data-copy-book]");
    if (copyBtn) {
      e.stopPropagation();
      const txt = await api.formatBookText(Number(copyBtn.dataset.copyBook));
      await navigator.clipboard.writeText(txt);
      showToast("Book message copied to clipboard");
      return;
    }
    const card = e.target.closest("[data-book-id]");
    if (card) {
      const id = Number(card.dataset.bookId);
      state.bookFilter = state.bookFilter === id ? null : id;
      state.queue = "all";
      refreshAll();
    }
  });

  $("searchInput").addEventListener("input", (e) => {
    state.search = e.target.value;
    refreshAll();
  });

  $("chkSelectAll").addEventListener("change", (e) => {
    state.checkedIds.clear();
    if (e.target.checked) {
      state.radiograms.forEach((rg) => state.checkedIds.add(rg.id));
    }
    renderTable();
  });

  $("rgTbody").addEventListener("click", async (e) => {
    const chk = e.target.closest("[data-chk-id]");
    if (chk) {
      const id = Number(chk.dataset.chkId);
      if (chk.checked) state.checkedIds.add(id);
      else state.checkedIds.delete(id);
      return;
    }
    const tr = e.target.closest("[data-rg-id]");
    if (!tr) return;
    const rg = await api.getRadiogram(Number(tr.dataset.rgId));
    if (rg) {
      await renderDetail(rg);
      renderTable();
    }
  });

  $("btnNewRg").addEventListener("click", () => openRgModal(null));
  $("btnSaveAddAnother").addEventListener("click", () => saveRadiogramForm(true));
  $("rgForm").addEventListener("submit", (e) => {
    e.preventDefault();
    saveRadiogramForm(false);
  });

  $("rgMessageText").addEventListener("input", updateComposerCheck);
  $("btnInsertX").addEventListener("click", () => {
    const ta = $("rgMessageText");
    ta.value = `${ta.value.trimEnd()}${ta.value.trim() ? " " : ""}X `;
    updateComposerCheck();
    ta.focus();
  });
  $("arlPicker").addEventListener("change", (e) => {
    if (!e.target.value) return;
    const ta = $("rgMessageText");
    ta.value = `${ta.value.trimEnd()}${ta.value.trim() ? " " : ""}${e.target.value} `;
    e.target.value = "";
    updateComposerCheck();
    ta.focus();
  });
  $("hxChips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-hx]");
    if (!chip) return;
    const cur = $("rgHx").value.trim().toUpperCase();
    const code = chip.dataset.hx;
    if (!cur.split(/\s+/).includes(code)) {
      $("rgHx").value = cur ? `${cur} ${code}` : code;
    }
  });

  $("btnEditRg").addEventListener("click", async () => {
    if (!state.selectedId) return;
    const rg = await api.getRadiogram(state.selectedId);
    if (rg) openRgModal(rg);
  });
  $("btnCopyText").addEventListener("click", async () => {
    if (!state.selectedId) return;
    const txt = await api.formatRadiogramText(state.selectedId);
    await navigator.clipboard.writeText(txt);
    showToast("ARRL Radiogram copied to clipboard");
  });
  $("btnPrint1Up").addEventListener("click", () => state.selectedId && api.printRadiograms([state.selectedId], "1up"));
  $("btnPrint2Up").addEventListener("click", () => state.selectedId && api.printRadiograms([state.selectedId], "2up"));
  $("btnPrintSelected2Up").addEventListener("click", () => {
    const ids = state.checkedIds.size ? [...state.checkedIds] : state.radiograms.map((r) => r.id);
    if (!ids.length) return showToast("No radiograms to print");
    api.printRadiograms(ids, "2up");
  });
  $("btnDeleteRg").addEventListener("click", async () => {
    if (!state.selectedId) return;
    if (!window.confirm("Delete this radiogram and its synced .txt file?")) return;
    await api.deleteRadiogram(state.selectedId);
    state.selectedId = null;
    showToast("Radiogram deleted");
    await refreshAll();
  });

  document.querySelectorAll("[data-quick-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!state.selectedId) return;
      const status = btn.dataset.quickStatus;
      const patch = { status };
      if (status === "DELIVERED") patch.delivered_at = nowUtcCompact();
      if (status === "RELAYED") patch.sent_at = nowUtcCompact();
      await api.updateRadiogram(state.selectedId, patch);
      showToast(`Marked ${status}`);
      await refreshAll();
    });
  });

  const openSvc = async (kind) => {
    if (!state.selectedId) return;
    const draft = await api.buildSvcDraft(state.selectedId, kind);
    await api.updateRadiogram(state.selectedId, { svc_sent_at: nowUtcCompact() });
    openRgModal(draft);
  };
  $("btnSvcDelivered").addEventListener("click", () => openSvc("delivered"));
  $("btnSvcRelayed").addEventListener("click", () => openSvc("relayed"));
  $("btnSvcUndeliv").addEventListener("click", () => openSvc("undeliverable"));

  $("btnNewBook").addEventListener("click", () => {
    $("bookOrigin").value = state.settings.operator_call;
    $("bookPlace").value = state.settings.operator_qth;
    $("bookSignature").value = state.settings.operator_call;
    $("bookModal").classList.remove("hidden");
    $("bookTitle").focus();
  });
  $("bookForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const lines = $("bookAddressees")
      .value.split(/\r?\n/)
      .map((ln) => ln.trim())
      .filter(Boolean);
    const addressees = lines.map((ln) => {
      const [to_name = "", to_callsign = "", to_address = "", to_city_state_zip = "", to_phone = ""] = ln
        .split("|")
        .map((p) => p.trim());
      return { to_name, to_callsign, to_address, to_city_state_zip, to_phone };
    });
    const startVal = Number.parseInt($("bookStartNum").value.trim(), 10);
    const res = await api.createBook({
      title: $("bookTitle").value.trim(),
      precedence: $("bookPrecedence").value,
      hx: $("bookHx").value.trim(),
      station_of_origin: $("bookOrigin").value.trim(),
      place_of_origin: $("bookPlace").value.trim(),
      signature: $("bookSignature").value.trim(),
      message_text: $("bookMessageText").value.trim(),
      start_number: Number.isFinite(startVal) && startVal > 0 ? startVal : undefined,
      addressees,
    });
    $("bookModal").classList.add("hidden");
    $("bookForm").reset();
    showToast(`Created Book "${res.book.title}" (${res.members.length} radiograms)`);
    await refreshAll();
  });

  $("btnOpenFolder").addEventListener("click", () => api.openRadiogramsFolder());
  $("btnSarModal").addEventListener("click", openSarModal);
  $("sarMonth").addEventListener("change", (e) => loadSarStats(e.target.value));

  $("btnSettings").addEventListener("click", async () => {
    const s = await api.getSettings();
    $("setOpCall").value = s.operator_call;
    $("setOpQth").value = s.operator_qth;
    $("setNextNum").value = String(s.next_msg_number);
    $("setTheme").value = s.theme;
    $("setRgDir").value = s.radiograms_dir;
    $("settingsModal").classList.remove("hidden");
  });
  $("btnPickFolder").addEventListener("click", async () => {
    const picked = await api.pickFolder();
    if (picked) $("setRgDir").value = picked;
  });
  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    state.settings = await api.updateSettings({
      operator_call: $("setOpCall").value.trim(),
      operator_qth: $("setOpQth").value.trim(),
      next_msg_number: Number.parseInt($("setNextNum").value, 10) || 1,
      theme: $("setTheme").value,
      radiograms_dir: $("setRgDir").value.trim(),
    });
    document.documentElement.dataset.theme = state.settings.theme;
    $("opCallBadge").textContent = state.settings.operator_call;
    $("settingsModal").classList.add("hidden");
    showToast("Settings saved");
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "n") {
      e.preventDefault();
      openRgModal(null);
    } else if (e.ctrlKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      $("searchInput").focus();
    } else if (e.ctrlKey && e.key === "." && !$("rgModal").classList.contains("hidden")) {
      e.preventDefault();
      $("btnInsertX").click();
    } else if (e.key === "Escape") {
      ["rgModal", "bookModal", "sarModal", "settingsModal"].forEach((id) => $(id).classList.add("hidden"));
    }
  });

  await refreshAll();
}

init();
