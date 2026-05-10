/**
 * Patent watchlist tab driver.
 *
 * Sister to design_watchlist.js. Vanilla JS / document-level event
 * delegation; mounted when the user clicks the Patent sub-view in
 * the Watchlist tab. Lazy-init via window.initPatentWatchlistTab so
 * the dashboard tab toggle can call it on first activation.
 *
 * Two watch types:
 *   - holder    — alerts on every new patent by the watched holder
 *   - reference — alerts on text+embedding similarity to a reference
 */
(function () {
  "use strict";

  var API_WL    = "/api/v1/patent-watchlist";
  var API_ALERT = "/api/v1/patent-alerts";

  var state = {
    items: [],
    alerts: [],
    initialized: false,
    alertStatusFilter: "new",
  };

  function $(id) { return document.getElementById(id); }
  function show(el) { if (el) el.classList.remove("hidden"); }
  function hide(el) { if (el) el.classList.add("hidden"); }

  function t(key, fallback) {
    if (window.AppI18n && typeof window.AppI18n.t === "function") {
      var v = window.AppI18n.t(key);
      if (v && v !== key) return v;
    }
    return fallback || key;
  }

  function token() {
    if (window.AppAuth && typeof window.AppAuth.getAuthToken === "function") {
      return window.AppAuth.getAuthToken() || "";
    }
    return (
      (window.localStorage && (
        localStorage.getItem("auth_token") ||
        localStorage.getItem("access_token") ||
        localStorage.getItem("token"))) || ""
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(s) {
    if (!s) return "";
    var d = new Date(s);
    if (isNaN(d.getTime())) return s;
    return d.toLocaleDateString();
  }

  function authFetch(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    var tk = token();
    if (tk) opts.headers["Authorization"] = "Bearer " + tk;
    return fetch(url, opts);
  }

  function toast(msg, kind) {
    if (window.AppToast && typeof window.AppToast.show === "function") {
      window.AppToast.show(msg, kind || "info");
    } else {
      console.log("[" + (kind || "info") + "] " + msg);
    }
  }

  // ---------------------------------------------------------------
  // Stats
  // ---------------------------------------------------------------

  function refreshStats() {
    var holder = state.items.filter(function (i) { return i.watch_type === "holder"; }).length;
    var ref = state.items.filter(function (i) { return i.watch_type === "reference"; }).length;
    var newAlerts = state.alerts.filter(function (a) { return a.status === "new"; }).length;
    var statTotal = $("pwl-stat-total");
    var statHolder = $("pwl-stat-holder");
    var statRef = $("pwl-stat-reference");
    var statNew = $("pwl-stat-new-alerts");
    if (statTotal)  statTotal.textContent  = String(state.items.length);
    if (statHolder) statHolder.textContent = String(holder);
    if (statRef)    statRef.textContent    = String(ref);
    if (statNew)    statNew.textContent    = String(newAlerts);
  }

  // ---------------------------------------------------------------
  // List rendering
  // ---------------------------------------------------------------

  function renderItem(item) {
    var typeBadge = item.watch_type === "holder"
      ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs" style="background:#ede9fe;color:#6d28d9">' +
        escapeHtml(t("patent_watchlist.watch_type_holder", "Holder")) + '</span>'
      : '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs" style="background:#fef3c7;color:#92400e">' +
        escapeHtml(t("patent_watchlist.watch_type_reference", "Reference")) + '</span>';

    var subtitle = "";
    if (item.watch_type === "holder") {
      subtitle = item.holder_name || item.holder_tpe_client_id || "";
    } else {
      subtitle = item.reference_query || ("Patent #" + (item.reference_patent_id || "").slice(0, 8));
    }

    var ipc = (item.ipc_classes || []).slice(0, 4).map(function (c) {
      return '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
             escapeHtml(c) + '</span>';
    }).join(" ");

    var lastScan = item.last_scan_at ? fmtDate(item.last_scan_at) :
      escapeHtml(t("patent_watchlist.never_scanned", "never"));

    return (
      '<div class="rounded-lg border p-3 flex items-center justify-between gap-3" ' +
      'style="background:var(--color-bg-page);border-color:var(--color-border)">' +
        '<div class="min-w-0 flex-1">' +
          '<div class="flex items-center gap-2 mb-1">' +
            typeBadge +
            '<h4 class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' +
              escapeHtml(item.label || "—") + '</h4>' +
          '</div>' +
          '<p class="text-xs truncate" style="color:var(--color-text-muted)">' + escapeHtml(subtitle) + '</p>' +
          (ipc ? ('<div class="flex flex-wrap gap-1 mt-1">' + ipc + '</div>') : '') +
          '<p class="text-[10px] mt-1" style="color:var(--color-text-faint)">' +
            escapeHtml(t("patent_watchlist.last_scan", "Last scan")) + ': ' + lastScan +
          '</p>' +
        '</div>' +
        '<div class="flex items-center gap-1 shrink-0">' +
          '<button type="button" data-pwl-scan="' + escapeHtml(item.id) + '" ' +
          'class="p-2 rounded hover:bg-indigo-50" style="color:var(--color-primary)" ' +
          'title="' + escapeHtml(t("patent_watchlist.scan_btn", "Scan")) + '">' +
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
              'd="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>' +
            '</svg>' +
          '</button>' +
          '<button type="button" data-pwl-delete="' + escapeHtml(item.id) + '" ' +
          'class="p-2 rounded hover:bg-red-50 text-red-500" ' +
          'title="' + escapeHtml(t("patent_watchlist.delete_btn", "Delete")) + '">' +
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
              'd="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>' +
            '</svg>' +
          '</button>' +
        '</div>' +
      '</div>'
    );
  }

  function renderList() {
    var container = $("pwl-list");
    var empty = $("pwl-empty");
    if (!container) return;
    if (state.items.length === 0) {
      container.innerHTML = "";
      show(empty);
      return;
    }
    hide(empty);
    container.innerHTML = state.items.map(renderItem).join("");
  }

  // ---------------------------------------------------------------
  // Alerts list rendering
  // ---------------------------------------------------------------

  function severityColor(sev) {
    return ({critical: "#dc2626", high: "#ea580c", medium: "#d97706", low: "#0891b2"})[sev] || "#6b7280";
  }

  function renderAlert(a) {
    var pubNo = escapeHtml(a.conflicting_publication_no || a.conflicting_application_no || "");
    var title = escapeHtml(a.conflicting_title || "—");
    var holder = escapeHtml(a.conflicting_holder_name || "");
    var sevBadge =
      '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium" ' +
      'style="background:' + severityColor(a.severity) + ';color:white">' +
      escapeHtml(t("patent_watchlist.severity_" + a.severity, a.severity)) + '</span>';
    var statusBadge =
      '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px]" ' +
      'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
      escapeHtml(t("patent_watchlist.status_" + a.status, a.status)) + '</span>';
    var score = a.overall_similarity_score != null
      ? Math.round(Number(a.overall_similarity_score) * 100) + "%"
      : "";
    var actionsHtml = "";
    if (a.status !== "resolved" && a.status !== "dismissed") {
      actionsHtml =
        '<button type="button" data-pwl-alert-ack="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-primary)">' +
          escapeHtml(t("patent_watchlist.action_ack", "Acknowledge")) + '</button>' +
        '<button type="button" data-pwl-alert-resolve="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-success,#059669)">' +
          escapeHtml(t("patent_watchlist.action_resolve", "Resolve")) + '</button>' +
        '<button type="button" data-pwl-alert-dismiss="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-text-faint)">' +
          escapeHtml(t("patent_watchlist.action_dismiss", "Dismiss")) + '</button>';
    }

    var openAttr = a.conflicting_patent_id
      ? ' data-pd-open="' + escapeHtml(a.conflicting_patent_id) + '" class="rounded-lg border p-3 cursor-pointer"'
      : ' class="rounded-lg border p-3"';
    return (
      '<div' + openAttr + ' style="background:var(--color-bg-page);border-color:var(--color-border)">' +
        '<div class="flex items-start justify-between gap-2">' +
          '<div class="min-w-0 flex-1">' +
            '<div class="flex items-center gap-2 mb-1">' +
              sevBadge + statusBadge +
              (score ? '<span class="text-xs font-mono" style="color:var(--color-text-muted)">' + score + '</span>' : '') +
            '</div>' +
            '<p class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' + title + '</p>' +
            '<p class="text-xs font-mono" style="color:var(--color-text-muted)">' + pubNo + '</p>' +
            (holder ? '<p class="text-xs" style="color:var(--color-text-faint)">' + holder + '</p>' : '') +
          '</div>' +
        '</div>' +
        (actionsHtml ? '<div class="flex items-center gap-1 mt-2 pt-2" style="border-top:1px solid var(--color-border)">' + actionsHtml + '</div>' : '') +
      '</div>'
    );
  }

  function renderAlerts() {
    var box = $("pwl-alerts-list");
    var empty = $("pwl-alerts-empty");
    if (!box) return;
    if (state.alerts.length === 0) {
      box.innerHTML = "";
      show(empty);
      return;
    }
    hide(empty);
    box.innerHTML = state.alerts.map(renderAlert).join("");
  }

  // ---------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------

  function fetchItems() {
    return authFetch(API_WL + "?limit=200")
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (d) { state.items = d.items || []; });
  }

  function fetchAlerts() {
    var url = API_ALERT + "?page_size=50";
    if (state.alertStatusFilter) url += "&status=" + encodeURIComponent(state.alertStatusFilter);
    return authFetch(url)
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (d) { state.alerts = d.items || []; });
  }

  function refreshAll() {
    return Promise.all([fetchItems(), fetchAlerts()]).then(function () {
      renderList();
      renderAlerts();
      refreshStats();
    });
  }

  // ---------------------------------------------------------------
  // CRUD actions
  // ---------------------------------------------------------------

  function openAddModal() {
    var modal = $("pwl-add-modal");
    if (!modal) return;
    // reset
    var lbl = $("pwl-add-label"); if (lbl) lbl.value = "";
    var hn = $("pwl-add-holder-name"); if (hn) hn.value = "";
    var rq = $("pwl-add-reference-query"); if (rq) rq.value = "";
    var ipc = $("pwl-add-ipc"); if (ipc) ipc.value = "";
    var typeRadios = document.querySelectorAll('input[name="pwl-watch-type"]');
    typeRadios.forEach(function (r) { r.checked = (r.value === "holder"); });
    syncWatchTypeFields("holder");
    var err = $("pwl-add-error");
    if (err) { err.classList.add("hidden"); err.textContent = ""; }
    show(modal);
  }
  function closeAddModal() { hide($("pwl-add-modal")); }

  function syncWatchTypeFields(watchType) {
    var holder = $("pwl-holder-fields");
    var ref = $("pwl-reference-fields");
    var thresh = $("pwl-threshold-row");
    if (watchType === "holder") {
      show(holder); hide(ref); hide(thresh);
    } else {
      hide(holder); show(ref); show(thresh);
    }
  }

  function readWatchType() {
    var checked = document.querySelector('input[name="pwl-watch-type"]:checked');
    return checked ? checked.value : "holder";
  }

  function submitAdd() {
    var err = $("pwl-add-error");
    var label = (($("pwl-add-label") || {}).value || "").trim();
    var watchType = readWatchType();
    var ipcRaw = (($("pwl-add-ipc") || {}).value || "").trim();
    var ipc = ipcRaw ? ipcRaw.split(",").map(function (s) { return s.trim(); }).filter(Boolean) : [];
    var body = { watch_type: watchType, label: label, ipc_classes: ipc };

    if (!label) {
      if (err) { err.textContent = t("patent_watchlist.error_label_required", "Label is required"); err.classList.remove("hidden"); }
      return;
    }
    if (watchType === "holder") {
      var holderName = (($("pwl-add-holder-name") || {}).value || "").trim();
      if (!holderName) {
        if (err) { err.textContent = t("patent_watchlist.error_holder_required", "Holder name is required"); err.classList.remove("hidden"); }
        return;
      }
      body.holder_name = holderName;
    } else {
      var refQ = (($("pwl-add-reference-query") || {}).value || "").trim();
      if (!refQ) {
        if (err) { err.textContent = t("patent_watchlist.error_reference_required", "Reference query is required"); err.classList.remove("hidden"); }
        return;
      }
      body.reference_query = refQ;
      var thr = (($("pwl-add-threshold") || {}).value || "0.50");
      body.similarity_threshold = parseFloat(thr);
    }

    authFetch(API_WL, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.ok) return r.json();
      return r.json().then(function (d) { throw d; });
    }).then(function () {
      closeAddModal();
      toast(t("patent_watchlist.add_success", "Watchlist item added"), "success");
      refreshAll();
    }).catch(function (d) {
      var msg = (d && d.detail && d.detail.message) || (d && d.detail) || t("patent_watchlist.error_generic", "Add failed");
      if (err) { err.textContent = (typeof msg === "string" ? msg : JSON.stringify(msg)); err.classList.remove("hidden"); }
    });
  }

  function scanItem(id) {
    authFetch(API_WL + "/" + encodeURIComponent(id) + "/scan", { method: "POST" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function (d) {
        toast(
          t("patent_watchlist.scan_done", "Scan complete") + ": " +
          (d.alerts_created || 0) + " " + t("patent_watchlist.alerts_created", "new alerts"),
          "success",
        );
        refreshAll();
      }).catch(function () { toast(t("patent_watchlist.scan_failed", "Scan failed"), "error"); });
  }

  function deleteItem(id) {
    if (!confirm(t("patent_watchlist.confirm_delete", "Delete this watchlist item?"))) return;
    authFetch(API_WL + "/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () { toast(t("patent_watchlist.delete_success", "Deleted"), "success"); refreshAll(); })
      .catch(function () { toast(t("patent_watchlist.delete_failed", "Delete failed"), "error"); });
  }

  function scanAll() {
    if (state.items.length === 0) return;
    if (!confirm(t("patent_watchlist.confirm_scan_all", "Scan all watchlist items?"))) return;
    var btn = $("pwl-btn-scan-all");
    if (btn) btn.disabled = true;
    Promise.all(state.items.map(function (it) {
      return authFetch(API_WL + "/" + encodeURIComponent(it.id) + "/scan", { method: "POST" })
        .then(function (r) { return r.ok ? r.json() : { alerts_created: 0 }; })
        .catch(function () { return { alerts_created: 0 }; });
    })).then(function (results) {
      var total = results.reduce(function (a, r) { return a + (r.alerts_created || 0); }, 0);
      toast(t("patent_watchlist.scan_all_done", "Scan complete") + ": " + total + " " + t("patent_watchlist.alerts_created", "new alerts"), "success");
    }).finally(function () {
      if (btn) btn.disabled = false;
      refreshAll();
    });
  }

  function alertAction(id, action) {
    authFetch(API_ALERT + "/" + encodeURIComponent(id) + "/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () { refreshAll(); })
      .catch(function () { toast(t("patent_watchlist.alert_action_failed", "Action failed"), "error"); });
  }

  // ---------------------------------------------------------------
  // Wire-up
  // ---------------------------------------------------------------

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;
      if (t_.closest("#pwl-btn-add"))      { ev.preventDefault(); openAddModal(); return; }
      if (t_.closest("#pwl-add-close"))    { ev.preventDefault(); closeAddModal(); return; }
      if (t_.closest("#pwl-add-cancel"))   { ev.preventDefault(); closeAddModal(); return; }
      if (t_.closest("#pwl-add-submit"))   { ev.preventDefault(); submitAdd(); return; }
      if (t_.closest("#pwl-btn-scan-all")) { ev.preventDefault(); scanAll(); return; }

      var scanBtn = t_.closest("[data-pwl-scan]");
      if (scanBtn) { scanItem(scanBtn.getAttribute("data-pwl-scan")); return; }
      var delBtn = t_.closest("[data-pwl-delete]");
      if (delBtn) { deleteItem(delBtn.getAttribute("data-pwl-delete")); return; }

      var ackBtn = t_.closest("[data-pwl-alert-ack]");
      if (ackBtn) { alertAction(ackBtn.getAttribute("data-pwl-alert-ack"), "acknowledge"); return; }
      var resBtn = t_.closest("[data-pwl-alert-resolve]");
      if (resBtn) { alertAction(resBtn.getAttribute("data-pwl-alert-resolve"), "resolve"); return; }
      var disBtn = t_.closest("[data-pwl-alert-dismiss]");
      if (disBtn) { alertAction(disBtn.getAttribute("data-pwl-alert-dismiss"), "dismiss"); return; }
    });

    document.addEventListener("change", function (ev) {
      if (!ev.target) return;
      if (ev.target.name === "pwl-watch-type") {
        syncWatchTypeFields(ev.target.value);
      } else if (ev.target.id === "pwl-alert-status-filter") {
        state.alertStatusFilter = ev.target.value || "";
        fetchAlerts().then(renderAlerts).then(refreshStats);
      }
    });
  }

  function init() {
    if (state.initialized) {
      refreshAll();
      return;
    }
    state.initialized = true;
    wire();
    refreshAll();
  }

  window.initPatentWatchlistTab = init;
})();
