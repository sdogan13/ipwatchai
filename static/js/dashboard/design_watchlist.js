/**
 * Design watchlist tab — driver for /api/v1/design-watchlist/* and
 * /api/v1/design-alerts/* endpoints.
 *
 * Vanilla JS. Mounts when the user clicks the "Tasarım Takibi" tab.
 */
(function () {
  "use strict";

  var API_LIST     = "/api/v1/design-watchlist";
  var API_ALERTS   = "/api/v1/design-alerts";
  var API_ALERTS_SUMMARY = "/api/v1/design-alerts/summary";

  // --- Module state for pagination / search / sort / threshold (mirrors
  //     the Trademark watchlist UX). Threshold is a 0-100 percent value used
  //     to filter alerts on the wire (`min_score`) AND to colour stat cards.
  var _state = {
    page: 1,
    pageSize: 20,
    sort: "conflicts_desc",
    search: "",
    threshold: 70,
    items: [],          // last-loaded server page (raw)
    total: 0,           // server-side total
  };
  var _searchTimer = null;

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

  function getAuthToken() {
    // Prefer AppAuth.getAuthToken (auth.js / auth-guard.js): it walks the
    // `auth_token` / `access_token` / `token` keys in both storage areas.
    // Reading only `access_token` here misses the real token (stored under
    // `auth_token` by the login flow), causing fetches to go out unauthed,
    // hit the global 401 interceptor, and force-redirect to login.
    if (window.AppAuth && typeof window.AppAuth.getAuthToken === "function") {
      return window.AppAuth.getAuthToken() || "";
    }
    return (
      (window.localStorage && (localStorage.getItem("auth_token") || localStorage.getItem("access_token") || localStorage.getItem("token"))) ||
      (window.sessionStorage && (sessionStorage.getItem("auth_token") || sessionStorage.getItem("access_token") || sessionStorage.getItem("token"))) ||
      ""
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function authedFetch(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    var token = getAuthToken();
    if (token) opts.headers["Authorization"] = "Bearer " + token;
    return fetch(url, opts);
  }

  function setStatus(text, kind) {
    var el = $("design-watchlist-status");
    var err = $("design-watchlist-error");
    if (kind === "error") {
      if (err) { err.textContent = text || ""; show(err); }
      hide(el);
    } else {
      hide(err);
      if (el) {
        if (text) { el.textContent = text; show(el); }
        else hide(el);
      }
    }
  }

  function clearAllStatus() {
    hide($("design-watchlist-status"));
    hide($("design-watchlist-error"));
  }

  function showHttpError(resp, payload, fallbackKey) {
    if (resp.status === 401 || resp.status === 403) {
      var detail = payload && payload.detail;
      if (detail && detail.error === "limit_exceeded") {
        setStatus(t("design_watchlist.error_quota", "Watchlist limit reached. Upgrade your plan."), "error");
      } else {
        setStatus(t("design_watchlist.error_auth", "Please sign in"), "error");
      }
    } else if (resp.status === 413) {
      setStatus(t("design_watchlist.error_image_too_large", "Image is too large (max 10 MB)"), "error");
    } else if (resp.status === 422 || resp.status === 400) {
      setStatus(
        (payload && (typeof payload.detail === "string" ? payload.detail : null)) ||
        t("design_watchlist.error_invalid_input", "Invalid input"),
        "error"
      );
    } else {
      setStatus(t(fallbackKey || "design_watchlist.error_generic", "Request failed"), "error");
    }
  }

  // ---------------------------------------------------------------
  // Item card render
  // ---------------------------------------------------------------

  function fmtDate(s) {
    if (!s) return "—";
    try {
      var d = new Date(s);
      if (isNaN(d.getTime())) return s;
      return d.toLocaleDateString();
    } catch (e) { return s; }
  }

  function severityColor(sev) {
    switch (sev) {
      case "critical": return "#dc2626";
      case "high":     return "#ea580c";
      case "medium":   return "#d97706";
      case "low":      return "#0891b2";
      default:         return "var(--color-text-muted)";
    }
  }

  function renderAlertRow(alert) {
    var sev = alert.severity || "medium";
    var sevColor = severityColor(sev);
    var sevLabel = t("design_watchlist.alert_severity_" + sev, sev);
    var statusLabel = t("design_watchlist.alert_status_" + (alert.status || "new"), alert.status || "new");
    var conf = alert.conflicting || {};
    var sim = typeof alert.scores === "object" && alert.scores
      ? Math.round((alert.scores.overall || 0) * 100)
      : 0;
    var locarno = (conf.locarno_classes || []).join(", ");

    var canAct = alert.status !== "resolved" && alert.status !== "dismissed";
    var actions = "";
    if (canAct) {
      actions =
        '<div class="flex items-center gap-2 mt-2 flex-wrap">' +
          '<button data-action="acknowledge" class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
            'style="background:var(--color-bg-muted);color:var(--color-text-primary);border:1px solid var(--color-border)" ' +
            'data-alert-id="' + escapeHtml(alert.id) + '">' +
            escapeHtml(t("design_watchlist.alert_action_acknowledge", "Acknowledge")) + '</button>' +
          '<button data-action="resolve" class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
            'style="background:#059669;color:white" ' +
            'data-alert-id="' + escapeHtml(alert.id) + '">' +
            escapeHtml(t("design_watchlist.alert_action_resolve", "Resolve")) + '</button>' +
          '<button data-action="dismiss" class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
            'style="background:var(--color-bg-muted);color:var(--color-text-secondary);border:1px solid var(--color-border)" ' +
            'data-alert-id="' + escapeHtml(alert.id) + '">' +
            escapeHtml(t("design_watchlist.alert_action_dismiss", "Dismiss")) + '</button>' +
        '</div>';
    }

    return (
      '<div class="rounded-md border p-3 text-xs" ' +
      'style="border-color:var(--color-border);background:var(--color-bg-page)">' +
        '<div class="flex items-center justify-between gap-2 mb-1">' +
          '<div class="flex items-center gap-2 flex-wrap">' +
            '<span class="font-mono px-2 py-0.5 rounded text-[11px]" style="background:' + sevColor + ';color:white">' +
              escapeHtml(sevLabel) + ' · ' + sim + '%</span>' +
            '<span class="text-[11px] uppercase tracking-wide" style="color:var(--color-text-muted)">' +
              escapeHtml(statusLabel) + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="font-medium" style="color:var(--color-text-primary)">' +
          escapeHtml(conf.product_name || conf.application_no || conf.registration_no || "—") + '</div>' +
        '<div class="mt-1 space-y-0.5" style="color:var(--color-text-secondary)">' +
          (conf.application_no ? '<div>' + escapeHtml(conf.application_no) + '</div>' : '') +
          (conf.holder_name ? '<div>' + escapeHtml(conf.holder_name) + '</div>' : '') +
          (locarno ? '<div>Locarno: ' + escapeHtml(locarno) + '</div>' : '') +
        '</div>' +
        actions +
      '</div>'
    );
  }

  function renderAlertsBlock(itemId, alerts) {
    if (!alerts || alerts.length === 0) {
      return (
        '<div class="text-xs py-3 text-center" style="color:var(--color-text-faint)">' +
          escapeHtml(t("design_watchlist.alerts_empty_title", "No alerts yet")) +
        '</div>'
      );
    }
    return (
      '<div class="space-y-2 pt-2" data-alerts-list="' + escapeHtml(itemId) + '">' +
        alerts.map(renderAlertRow).join("") +
      '</div>'
    );
  }

  function renderCard(item) {
    var locarno = (item.locarno_classes || []).join(", ");
    var imgUrl = item.image_path ? "/" + item.image_path.replace(/^\/+/, "") : "";
    var newAlerts = item.new_alerts_count || 0;
    var totalAlerts = item.total_alerts_count || 0;
    var lastScan = fmtDate(item.last_scan_at);

    var imgHtml = imgUrl
      ? '<img src="' + escapeHtml(imgUrl) + '" alt="' + escapeHtml(item.product_name) + '" loading="lazy" ' +
        'class="w-24 h-24 object-contain rounded-md shrink-0" style="background:var(--color-bg-muted)" />'
      : '<div class="w-24 h-24 flex items-center justify-center text-xs rounded-md shrink-0" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        escapeHtml(t("design_watchlist.no_image", "No image")) + "</div>";

    var alertBadge = newAlerts > 0
      ? '<span class="px-2 py-0.5 rounded-full text-[11px] font-mono" ' +
        'style="background:#dc2626;color:white">' + newAlerts + ' ' +
        escapeHtml(t("design_watchlist.alerts_count_label", "new")) + '</span>'
      : "";

    return (
      '<article class="rounded-lg border" data-item-id="' + escapeHtml(item.id) + '" ' +
      'style="border-color:var(--color-border);background:var(--color-bg-card)">' +
        '<div class="flex items-start gap-3 p-3">' +
          imgHtml +
          '<div class="flex-1 min-w-0">' +
            '<div class="flex items-start justify-between gap-2 flex-wrap">' +
              '<h4 class="text-sm font-semibold leading-snug" style="color:var(--color-text-primary)">' +
                escapeHtml(item.product_name) + '</h4>' +
              alertBadge +
            '</div>' +
            '<dl class="mt-1 space-y-0.5 text-xs" style="color:var(--color-text-secondary)">' +
              (locarno ? '<div>Locarno: <span class="font-mono">' + escapeHtml(locarno) + '</span></div>' : '') +
              (item.customer_application_no ? '<div>' +
                escapeHtml(t("design_watchlist.customer_app_no_label", "App No")) + ': <span class="font-mono">' +
                escapeHtml(item.customer_application_no) + '</span></div>' : '') +
              '<div style="color:var(--color-text-faint)">' +
                escapeHtml(t("design_watchlist.last_scan_label", "Last scan")) + ': ' + escapeHtml(lastScan) +
              '</div>' +
            '</dl>' +
            '<div class="flex items-center gap-2 mt-2 flex-wrap">' +
              '<button data-action="toggle-alerts" data-item-id="' + escapeHtml(item.id) + '" ' +
                'class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
                'style="background:var(--color-bg-muted);color:var(--color-text-primary);border:1px solid var(--color-border)">' +
                escapeHtml(t("design_watchlist.view_alerts_button", "View alerts")) + ' (' + totalAlerts + ')' +
              '</button>' +
              '<button data-action="upload-image" data-item-id="' + escapeHtml(item.id) + '" ' +
                'class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
                'style="background:var(--color-bg-muted);color:var(--color-text-primary);border:1px solid var(--color-border)">' +
                escapeHtml(t("design_watchlist.upload_image_button", "Upload image")) + '</button>' +
              '<button data-action="scan-now" data-item-id="' + escapeHtml(item.id) + '" ' +
                'class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
                'style="background:var(--color-bg-muted);color:var(--color-text-primary);border:1px solid var(--color-border)">' +
                escapeHtml(t("design_watchlist.scan_now_button", "Scan now")) + '</button>' +
              '<button data-action="edit" data-item-id="' + escapeHtml(item.id) + '" ' +
                'class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors" ' +
                'style="background:var(--color-bg-muted);color:var(--color-text-primary);border:1px solid var(--color-border)">' +
                escapeHtml(t("design_watchlist.edit_button", "Edit")) + '</button>' +
              '<button data-action="delete" data-item-id="' + escapeHtml(item.id) + '" ' +
                'class="text-xs px-2 py-1 rounded-md hover:opacity-80 transition-colors ml-auto" ' +
                'style="background:transparent;color:#dc2626;border:1px solid #fecaca">' +
                escapeHtml(t("design_watchlist.delete_button", "Delete")) + '</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div data-alerts-block="' + escapeHtml(item.id) + '" class="hidden border-t p-3" ' +
          'style="border-color:var(--color-border);background:var(--color-bg-page)"></div>' +
      '</article>'
    );
  }

  function renderList(payload) {
    var list = $("design-watchlist-list");
    var empty = $("design-watchlist-empty");
    var totalBadge = $("design-watchlist-total-badge");
    if (!list) return;
    var rows = (payload && payload.items) || [];
    list.innerHTML = rows.map(renderCard).join("");
    if (totalBadge) totalBadge.textContent = String(payload && payload.total != null ? payload.total : rows.length);
    if (rows.length === 0) {
      hide(list);
      show(empty);
    } else {
      show(list);
      hide(empty);
    }
  }

  // ---------------------------------------------------------------
  // API calls
  // ---------------------------------------------------------------

  async function loadList() {
    show($("design-watchlist-loading"));
    hide($("design-watchlist-list"));
    hide($("design-watchlist-empty"));
    clearAllStatus();
    try {
      var qs = "?page=" + encodeURIComponent(_state.page) +
               "&page_size=" + encodeURIComponent(_state.pageSize);
      var resp = await authedFetch(API_LIST + qs);
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      _state.items = (payload && payload.items) || [];
      _state.total = (payload && payload.total) || _state.items.length;
      _renderFiltered();
      _renderPagination();
      _refreshStats();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    } finally {
      hide($("design-watchlist-loading"));
    }
  }

  // ---------------------------------------------------------------
  // Client-side search/sort + render
  // ---------------------------------------------------------------
  function _filteredSortedItems() {
    var arr = (_state.items || []).slice();
    var q = (_state.search || "").trim().toLowerCase();
    if (q) {
      arr = arr.filter(function (it) {
        var name = (it.product_name || "").toLowerCase();
        var appNo = (it.customer_application_no || "").toLowerCase();
        var loc = ((it.locarno_classes || []).join(",")).toLowerCase();
        return name.indexOf(q) !== -1 || appNo.indexOf(q) !== -1 || loc.indexOf(q) !== -1;
      });
    }
    var s = _state.sort;
    if (s === "date_desc") {
      arr.sort(function (a, b) { return _parseTs(b.created_at) - _parseTs(a.created_at); });
    } else if (s === "date_asc") {
      arr.sort(function (a, b) { return _parseTs(a.created_at) - _parseTs(b.created_at); });
    } else if (s === "name_asc") {
      arr.sort(function (a, b) { return (a.product_name || "").localeCompare(b.product_name || ""); });
    } else { // conflicts_desc (default)
      arr.sort(function (a, b) {
        var na = a.new_alerts_count || 0, nb = b.new_alerts_count || 0;
        if (nb !== na) return nb - na;
        return (b.total_alerts_count || 0) - (a.total_alerts_count || 0);
      });
    }
    return arr;
  }

  function _parseTs(s) {
    if (!s) return 0;
    var t = Date.parse(s);
    return isNaN(t) ? 0 : t;
  }

  function _renderFiltered() {
    var list = $("design-watchlist-list");
    var empty = $("design-watchlist-empty");
    var totalBadge = $("design-watchlist-total-badge");
    if (!list) return;
    var rows = _filteredSortedItems();
    list.innerHTML = rows.map(renderCard).join("");
    if (totalBadge) totalBadge.textContent = String(rows.length);
    var info = $("dwl-count-info");
    if (info) info.textContent = t("sort.results_count", { count: _state.total });
    if (rows.length === 0) {
      hide(list);
      show(empty);
    } else {
      show(list);
      hide(empty);
    }
  }

  function _renderPagination() {
    var pag = $("dwl-pagination");
    var prev = $("dwl-prev-btn");
    var next = $("dwl-next-btn");
    var info = $("dwl-page-info");
    if (!pag) return;
    var totalPages = Math.max(1, Math.ceil((_state.total || 0) / _state.pageSize));
    if (totalPages <= 1) {
      hide(pag);
      if (info) hide(info);
      return;
    }
    pag.classList.remove("hidden");
    pag.classList.add("flex");
    if (prev) prev.disabled = _state.page <= 1;
    if (next) next.disabled = _state.page >= totalPages;
    if (info) {
      info.textContent = t("common.page_x_of_y", { page: _state.page, total: totalPages })
        || (_state.page + " / " + totalPages);
      info.classList.remove("hidden");
    }
  }

  // ---------------------------------------------------------------
  // Stats compute — Phase 2: prefer the org-scoped /stats endpoint.
  // Falls back to client-side compute if the endpoint isn't deployed
  // yet (older backend), so the UI keeps working during a partial roll.
  // ---------------------------------------------------------------
  async function _refreshStats() {
    var totalEl = $("dwl-stat-total");
    var threatenedEl = $("dwl-stat-threatened");
    var criticalEl = $("dwl-stat-critical");
    var newAlertsEl = $("dwl-stat-new-alerts");
    try {
      var resp = await authedFetch(API_LIST + "/stats");
      if (resp.ok) {
        var s = await resp.json().catch(function () { return null; });
        if (s) {
          if (totalEl) totalEl.textContent = String(s.total || 0);
          if (threatenedEl) threatenedEl.textContent = String(s.threatened || 0);
          if (criticalEl) criticalEl.textContent = String(s.critical || 0);
          if (newAlertsEl) newAlertsEl.textContent = String(s.new_alerts || 0);
          return;
        }
      }
    } catch (e) { /* fall through */ }
    // Fallback (pre-Phase-2 backend): compute from list + alerts/summary.
    if (totalEl) totalEl.textContent = String(_state.total || 0);
    var threatened = (_state.items || []).filter(function (it) {
      return (it.new_alerts_count || 0) > 0;
    }).length;
    if (threatenedEl) threatenedEl.textContent = String(threatened);
    try {
      var resp2 = await authedFetch(API_ALERTS_SUMMARY);
      if (!resp2.ok) return;
      var payload = await resp2.json().catch(function () { return null; });
      var bySev = (payload && payload.by_severity) || {};
      if (criticalEl) criticalEl.textContent = String(bySev.critical || 0);
      if (newAlertsEl) newAlertsEl.textContent = String((payload && payload.total_new) || 0);
    } catch (e) { /* best-effort */ }
  }

  async function submitCreate() {
    var product = ($("design-watchlist-product-name") || {}).value || "";
    var locarno = ($("design-watchlist-locarno") || {}).value || "";
    var appNo = ($("design-watchlist-app-no") || {}).value || "";
    if (!product.trim()) {
      setStatus(t("design_watchlist.error_invalid_input", "Product name is required"), "error");
      return;
    }
    var locarnoArr = locarno.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var body = {
      product_name: product.trim(),
      locarno_classes: locarnoArr,
    };
    if (appNo.trim()) body.customer_application_no = appNo.trim();

    try {
      var resp = await authedFetch(API_LIST, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      // Reset form, hide it, refresh list
      $("design-watchlist-product-name").value = "";
      $("design-watchlist-locarno").value = "";
      $("design-watchlist-app-no").value = "";
      hide($("design-watchlist-add-card"));
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  async function loadAlertsForItem(itemId) {
    var block = document.querySelector('[data-alerts-block="' + cssEscape(itemId) + '"]');
    if (!block) return;
    block.innerHTML = '<div class="text-xs py-3 text-center" style="color:var(--color-text-muted)">' +
      escapeHtml(t("design_watchlist.loading", "Loading…")) + '</div>';
    show(block);
    try {
      // The threshold dropdown filters which alerts the user sees per item.
      // API accepts min_score in 0-100; _state.threshold is already that scale.
      var qs = "?watchlist_item_id=" + encodeURIComponent(itemId) +
               "&page_size=20" +
               "&min_score=" + encodeURIComponent(_state.threshold || 0);
      var resp = await authedFetch(API_ALERTS + qs);
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        block.innerHTML = '<div class="text-xs py-3 text-center" style="color:var(--color-text-error)">' +
          escapeHtml(t("design_watchlist.error_generic", "Failed to load alerts")) + '</div>';
        return;
      }
      block.innerHTML = renderAlertsBlock(itemId, (payload && payload.items) || []);
    } catch (e) {
      block.innerHTML = '<div class="text-xs py-3 text-center" style="color:var(--color-text-error)">' +
        escapeHtml(t("design_watchlist.error_network", "Network error")) + '</div>';
    }
  }

  function cssEscape(s) {
    return String(s).replace(/"/g, '\\"');
  }

  function toggleAlerts(itemId) {
    var block = document.querySelector('[data-alerts-block="' + cssEscape(itemId) + '"]');
    if (!block) return;
    if (block.classList.contains("hidden")) {
      loadAlertsForItem(itemId);
    } else {
      hide(block);
      block.innerHTML = "";
    }
  }

  async function deleteItem(itemId) {
    if (!window.confirm(t("design_watchlist.delete_confirm", "Delete this watchlist item?"))) {
      return;
    }
    try {
      var resp = await authedFetch(API_LIST + "/" + encodeURIComponent(itemId), { method: "DELETE" });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  async function scanNow(itemId) {
    try {
      var resp = await authedFetch(API_LIST + "/" + encodeURIComponent(itemId) + "/scan", { method: "POST" });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      setStatus(t("design_watchlist.scan_queued", "Scan queued"));
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  function startImageUpload(itemId) {
    var input = $("design-watchlist-image-input");
    if (!input) return;
    input.dataset.targetItemId = itemId;
    input.value = "";
    input.click();
  }

  async function uploadImageFor(itemId, file) {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      setStatus(t("design_watchlist.error_image_too_large", "Image is too large (max 10 MB)"), "error");
      return;
    }
    var fd = new FormData();
    fd.append("image", file);
    setStatus(t("design_watchlist.loading", "Loading…"));
    try {
      var resp = await authedFetch(API_LIST + "/" + encodeURIComponent(itemId) + "/image", {
        method: "POST",
        body: fd,
      });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      clearAllStatus();
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  async function alertAction(alertId, action) {
    var ep = API_ALERTS + "/" + encodeURIComponent(alertId) + "/" + action;
    try {
      var resp = await authedFetch(ep, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      // Reload alerts for the parent item
      var btn = document.querySelector('button[data-alert-id="' + cssEscape(alertId) + '"][data-action="' + action + '"]');
      var article = btn && btn.closest("article[data-item-id]");
      if (article) {
        var itemId = article.getAttribute("data-item-id");
        if (itemId) await loadAlertsForItem(itemId);
      }
      // Refresh list to update alert count badges
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  // ---------------------------------------------------------------
  // Scan-all — Phase 2: single POST /scan-all kicks off background scans
  // server-side. Falls back to a per-item loop if the endpoint isn't
  // deployed yet (e.g. an older backend).
  // ---------------------------------------------------------------
  async function scanAll() {
    var btn = $("dwl-btn-scan-all");
    if (btn && btn.disabled) return;
    var totalKnown = (_state.items || []).length;
    if (totalKnown === 0 && (_state.total || 0) === 0) {
      setStatus(t("design_watchlist.scan_all_empty", "No items to scan."));
      return;
    }
    if (!window.confirm(t("design_watchlist.scan_all_confirm", "Scan all watchlist items now?"))) return;
    if (btn) btn.disabled = true;
    setStatus(t("design_watchlist.scan_all_in_progress", "Scanning {n} items…")
      .replace("{n}", String(_state.total || totalKnown)));
    try {
      var resp = await authedFetch(API_LIST + "/scan-all", { method: "POST" });
      if (resp.ok) {
        var payload = await resp.json().catch(function () { return null; });
        var queued = payload && typeof payload.queued === "number" ? payload.queued : (_state.total || totalKnown);
        setStatus(t("design_watchlist.scan_all_done", "Scan queued for {ok}/{total} items.")
          .replace("{ok}", String(queued)).replace("{total}", String(_state.total || totalKnown)));
      } else if (resp.status === 404 || resp.status === 405) {
        // Backend doesn't have the endpoint yet — fall back to per-item loop.
        await _scanAllFallback();
      } else {
        var p = await resp.json().catch(function () { return null; });
        showHttpError(resp, p, "design_watchlist.error_generic");
      }
    } catch (e) {
      // Network or unknown error — try the fallback once.
      try { await _scanAllFallback(); } catch (_) {
        setStatus(t("design_watchlist.error_network", "Network error"), "error");
      }
    } finally {
      if (btn) btn.disabled = false;
      await loadList();
    }
  }

  async function _scanAllFallback() {
    var ids = (_state.items || []).map(function (it) { return it.id; }).filter(Boolean);
    var ok = 0;
    for (var i = 0; i < ids.length; i++) {
      try {
        var r = await authedFetch(API_LIST + "/" + encodeURIComponent(ids[i]) + "/scan", { method: "POST" });
        if (r.ok) ok++;
      } catch (e) { /* skip */ }
      if (i < ids.length - 1) await new Promise(function (r) { setTimeout(r, 200); });
    }
    setStatus(t("design_watchlist.scan_all_done", "Scan queued for {ok}/{total} items.")
      .replace("{ok}", String(ok)).replace("{total}", String(ids.length)));
  }

  // ---------------------------------------------------------------
  // Delete-all — Phase 2: single DELETE /all (FK CASCADE clears alerts).
  // Falls back to per-item DELETE loop on 404/405.
  // ---------------------------------------------------------------
  async function deleteAll() {
    var totalKnown = (_state.items || []).length;
    if (totalKnown === 0 && (_state.total || 0) === 0) {
      setStatus(t("design_watchlist.delete_all_empty", "Nothing to delete."));
      return;
    }
    if (!window.confirm(t("design_watchlist.delete_all_confirm", "Delete ALL watchlist items? This cannot be undone."))) return;
    var btn = $("dwl-btn-delete-all");
    if (btn) btn.disabled = true;
    try {
      var resp = await authedFetch(API_LIST + "/all", { method: "DELETE" });
      if (resp.ok) {
        var payload = await resp.json().catch(function () { return null; });
        var deleted = payload && typeof payload.deleted === "number" ? payload.deleted : (_state.total || totalKnown);
        setStatus(t("design_watchlist.delete_all_done", "Deleted {n} items.").replace("{n}", String(deleted)));
      } else if (resp.status === 404 || resp.status === 405) {
        await _deleteAllFallback();
      } else {
        var p = await resp.json().catch(function () { return null; });
        showHttpError(resp, p, "design_watchlist.error_generic");
      }
    } catch (e) {
      try { await _deleteAllFallback(); } catch (_) {
        setStatus(t("design_watchlist.error_network", "Network error"), "error");
      }
    } finally {
      if (btn) btn.disabled = false;
      _state.page = 1;
      await loadList();
    }
  }

  async function _deleteAllFallback() {
    var ids = (_state.items || []).map(function (it) { return it.id; }).filter(Boolean);
    var deleted = 0;
    for (var i = 0; i < ids.length; i++) {
      try {
        var r = await authedFetch(API_LIST + "/" + encodeURIComponent(ids[i]), { method: "DELETE" });
        if (r.ok) deleted++;
      } catch (e) { /* skip */ }
    }
    setStatus(t("design_watchlist.delete_all_done", "Deleted {n} items.").replace("{n}", String(deleted)));
  }

  // ---------------------------------------------------------------
  // Edit modal — open / close / submit
  // ---------------------------------------------------------------
  var _editingItemId = null;

  function openEditModal(itemId) {
    var item = (_state.items || []).find(function (it) { return it.id === itemId; });
    if (!item) return;
    _editingItemId = itemId;
    var modal = $("design-watchlist-edit-modal");
    if (!modal) return;
    var pn = $("edit-dwl-product-name");      if (pn) pn.value = item.product_name || "";
    var lc = $("edit-dwl-locarno");           if (lc) lc.value = (item.locarno_classes || []).join(", ");
    var th = $("edit-dwl-threshold");         if (th) th.value = String(item.similarity_threshold != null ? item.similarity_threshold : 0.5);
    var ds = $("edit-dwl-description");       if (ds) ds.value = item.description || "";
    var fr = $("edit-dwl-frequency");         if (fr) fr.value = item.alert_frequency || "daily";
    var mt = $("edit-dwl-monitor-text");      if (mt) mt.checked = item.monitor_text !== false;
    var mv = $("edit-dwl-monitor-visual");    if (mv) mv.checked = item.monitor_visual !== false;
    show(modal);
  }

  function closeEditModal() {
    _editingItemId = null;
    var modal = $("design-watchlist-edit-modal");
    if (modal) hide(modal);
  }

  async function submitEditModal() {
    if (!_editingItemId) return;
    var btn = $("edit-dwl-submit-btn");
    var product = ($("edit-dwl-product-name") || {}).value || "";
    var locarno = ($("edit-dwl-locarno") || {}).value || "";
    var threshold = parseFloat(($("edit-dwl-threshold") || {}).value || "0.5");
    var description = ($("edit-dwl-description") || {}).value || "";
    var frequency = ($("edit-dwl-frequency") || {}).value || "daily";
    var monitorText = !!($("edit-dwl-monitor-text") || {}).checked;
    var monitorVisual = !!($("edit-dwl-monitor-visual") || {}).checked;

    var body = {
      product_name: product.trim(),
      locarno_classes: locarno.split(",").map(function (s) { return s.trim(); }).filter(Boolean),
      similarity_threshold: isNaN(threshold) ? 0.5 : threshold,
      description: description || null,
      alert_frequency: frequency,
      monitor_text: monitorText,
      monitor_visual: monitorVisual,
    };
    if (btn) btn.disabled = true;
    try {
      var resp = await authedFetch(API_LIST + "/" + encodeURIComponent(_editingItemId), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      closeEditModal();
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // Expose the two modal helpers globally so the modal's inline onclick
  // attributes (in _modals.html) can reach them.
  window.closeEditDesignWatchlistModal = closeEditModal;
  window.submitEditDesignWatchlist = submitEditModal;

  // ---------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------

  var _wired = false;
  function wireOnce() {
    if (_wired) return;
    _wired = true;

    var addToggle = $("design-watchlist-add-toggle");
    var submit = $("design-watchlist-submit");
    var cancel = $("design-watchlist-cancel");
    var imageInput = $("design-watchlist-image-input");
    var list = $("design-watchlist-list");

    if (addToggle) addToggle.addEventListener("click", function () {
      var card = $("design-watchlist-add-card");
      if (!card) return;
      if (card.classList.contains("hidden")) show(card);
      else hide(card);
    });
    if (submit) submit.addEventListener("click", submitCreate);
    if (cancel) cancel.addEventListener("click", function () {
      hide($("design-watchlist-add-card"));
      clearAllStatus();
    });
    if (imageInput) imageInput.addEventListener("change", function (e) {
      var file = e.target.files && e.target.files[0];
      var itemId = e.target.dataset.targetItemId;
      if (file && itemId) uploadImageFor(itemId, file);
    });
    if (list) list.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("button[data-action]");
      if (!btn) return;
      var action = btn.getAttribute("data-action");
      var itemId = btn.getAttribute("data-item-id");
      var alertId = btn.getAttribute("data-alert-id");
      if (action === "toggle-alerts") toggleAlerts(itemId);
      else if (action === "delete") deleteItem(itemId);
      else if (action === "upload-image") startImageUpload(itemId);
      else if (action === "scan-now") scanNow(itemId);
      else if (action === "edit") openEditModal(itemId);
      else if (action === "acknowledge" || action === "resolve" || action === "dismiss") {
        if (alertId) alertAction(alertId, action);
      }
    });

    // --- New toolbar / filters / pagination / threshold ---
    var thresholdSel = $("dwl-threshold-slider");
    if (thresholdSel) thresholdSel.addEventListener("change", function () {
      var v = parseInt(this.value, 10);
      if (!isNaN(v)) _state.threshold = v;
      // Re-load any currently-open alerts blocks so they respect the new threshold.
      document.querySelectorAll('[data-alerts-block]:not(.hidden)').forEach(function (blk) {
        var id = blk.getAttribute("data-alerts-block");
        if (id) loadAlertsForItem(id);
      });
    });

    var searchInput = $("dwl-search-input");
    var searchClear = $("dwl-search-clear");
    if (searchInput) searchInput.addEventListener("input", function () {
      _state.search = this.value || "";
      if (searchClear) searchClear.classList.toggle("hidden", _state.search.length === 0);
      if (_searchTimer) clearTimeout(_searchTimer);
      _searchTimer = setTimeout(function () { _renderFiltered(); }, 200);
    });
    if (searchClear) searchClear.addEventListener("click", function () {
      if (searchInput) searchInput.value = "";
      _state.search = "";
      searchClear.classList.add("hidden");
      _renderFiltered();
    });

    var sortSel = $("dwl-sort-select");
    if (sortSel) sortSel.addEventListener("change", function () {
      _state.sort = this.value || "conflicts_desc";
      _renderFiltered();
    });

    var prevBtn = $("dwl-prev-btn");
    var nextBtn = $("dwl-next-btn");
    if (prevBtn) prevBtn.addEventListener("click", function () {
      if (_state.page > 1) { _state.page -= 1; loadList(); }
    });
    if (nextBtn) nextBtn.addEventListener("click", function () {
      var totalPages = Math.max(1, Math.ceil((_state.total || 0) / _state.pageSize));
      if (_state.page < totalPages) { _state.page += 1; loadList(); }
    });

    var scanAllBtn = $("dwl-btn-scan-all");
    if (scanAllBtn) scanAllBtn.addEventListener("click", scanAll);

    var deleteAllBtn = $("dwl-btn-delete-all");
    if (deleteAllBtn) deleteAllBtn.addEventListener("click", deleteAll);

    // Phase 3: enable the Yükle button to open the bulk-upload modal.
    var bulkUploadBtn = $("dwl-btn-bulk-upload");
    if (bulkUploadBtn) {
      bulkUploadBtn.disabled = false;
      bulkUploadBtn.classList.remove("opacity-50", "cursor-not-allowed");
      bulkUploadBtn.addEventListener("click", openDesignBulkUploadModal);
    }
  }

  // ---------------------------------------------------------------
  // Phase 3 — Bulk CSV upload (3-step modal)
  // ---------------------------------------------------------------
  var _DWL_FIELD_LABEL_KEYS = {
    product_name: "design_watchlist.product_name_label",
    locarno_classes: "design_watchlist.locarno_label",
    description: "design_watchlist.description",
    customer_application_no: "design_watchlist.customer_app_no_label",
    customer_registration_no: "design_watchlist.upload_field_customer_reg_no",
    similarity_threshold: "design_watchlist.similarity_threshold",
    priority: "design_watchlist.upload_field_priority",
    tags: "design_watchlist.upload_field_tags",
    alert_email: "design_watchlist.upload_field_alert_email",
    alert_frequency: "design_watchlist.alert_frequency_label",
  };

  var _uploadState = { file: null, columns: [], suggested: {}, totalRows: 0 };

  function openDesignBulkUploadModal() {
    _uploadState = { file: null, columns: [], suggested: {}, totalRows: 0 };
    var modal = $("design-watchlist-upload-modal");
    if (!modal) return;
    showDesignUploadStepOne();
    show(modal);
  }

  function closeDesignBulkUploadModal() {
    var modal = $("design-watchlist-upload-modal");
    if (modal) hide(modal);
    var fileInput = $("dwl-upload-file");
    if (fileInput) fileInput.value = "";
    var nameEl = $("dwl-upload-filename");
    if (nameEl) { nameEl.textContent = ""; nameEl.classList.add("hidden"); }
  }

  function showDesignUploadStepOne() {
    var s1 = $("dwl-upload-step-1");
    var s2 = $("dwl-upload-step-2");
    var sR = $("dwl-upload-result");
    if (s1) s1.classList.remove("hidden");
    if (s2) s2.classList.add("hidden");
    if (sR) sR.classList.add("hidden");
  }

  function _showStepTwo() {
    var s1 = $("dwl-upload-step-1");
    var s2 = $("dwl-upload-step-2");
    if (s1) s1.classList.add("hidden");
    if (s2) s2.classList.remove("hidden");
  }

  function _showResult() {
    var s1 = $("dwl-upload-step-1");
    var s2 = $("dwl-upload-step-2");
    var sR = $("dwl-upload-result");
    if (s1) s1.classList.add("hidden");
    if (s2) s2.classList.add("hidden");
    if (sR) sR.classList.remove("hidden");
  }

  function onDesignUploadFilePicked(input) {
    var f = input && input.files && input.files[0];
    _uploadState.file = f || null;
    var nameEl = $("dwl-upload-filename");
    if (nameEl) {
      if (f) { nameEl.textContent = f.name; nameEl.classList.remove("hidden"); }
      else { nameEl.textContent = ""; nameEl.classList.add("hidden"); }
    }
  }

  async function downloadDesignWatchlistTemplate() {
    try {
      var resp = await authedFetch(API_LIST + "/upload/template");
      if (!resp.ok) {
        setStatus(t("design_watchlist.error_generic", "Failed"), "error");
        return;
      }
      var blob = await resp.blob();
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "tasarim_takibi_sablon.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    }
  }

  async function detectDesignUploadColumns() {
    if (!_uploadState.file) {
      setStatus(t("design_watchlist.upload_pick_file_first", "Pick a CSV file first"), "error");
      return;
    }
    var btn = $("dwl-upload-detect-btn");
    if (btn) btn.disabled = true;
    var fd = new FormData();
    fd.append("file", _uploadState.file);
    try {
      var resp = await authedFetch(API_LIST + "/upload/detect-columns", { method: "POST", body: fd });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      _uploadState.columns = (payload && payload.columns) || [];
      _uploadState.suggested = (payload && payload.suggested_mapping) || {};
      _uploadState.totalRows = (payload && payload.total_rows) || 0;
      _renderDesignUploadMapping();
      _showStepTwo();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function _renderDesignUploadMapping() {
    var wrap = $("dwl-upload-mapping");
    var rowCount = $("dwl-upload-row-count");
    if (!wrap) return;
    var html = "";
    Object.keys(_DWL_FIELD_LABEL_KEYS).forEach(function (field) {
      var label = t(_DWL_FIELD_LABEL_KEYS[field], field);
      var selected = _uploadState.suggested[field] || "";
      var opts = '<option value="">—</option>';
      _uploadState.columns.forEach(function (col) {
        var sel = col === selected ? " selected" : "";
        opts += '<option value="' + escapeHtml(col) + '"' + sel + '>' + escapeHtml(col) + "</option>";
      });
      html += (
        '<div class="flex items-center gap-2">' +
          '<label class="text-xs flex-1" style="color:var(--color-text-secondary)">' + escapeHtml(label) +
            (field === "product_name" ? ' <span style="color:#dc2626">*</span>' : '') + '</label>' +
          '<select data-dwl-map-field="' + escapeHtml(field) + '" ' +
            'class="text-xs px-2 py-1.5 rounded-lg flex-1" ' +
            'style="border:1px solid var(--color-border-input);color:var(--color-text-primary);background:var(--color-bg-input)">' +
            opts +
          '</select>' +
        '</div>'
      );
    });
    wrap.innerHTML = html;
    if (rowCount) {
      rowCount.textContent = t("design_watchlist.upload_row_count", { count: _uploadState.totalRows })
        || (_uploadState.totalRows + " rows");
    }
  }

  async function submitDesignBulkUpload() {
    if (!_uploadState.file) return;
    var mapping = {};
    document.querySelectorAll('select[data-dwl-map-field]').forEach(function (sel) {
      var field = sel.getAttribute("data-dwl-map-field");
      var v = sel.value;
      if (field && v) mapping[field] = v;
    });
    if (!mapping.product_name) {
      setStatus(t("design_watchlist.upload_product_name_required", "Map the Product Name column"), "error");
      return;
    }
    var btn = $("dwl-upload-submit-btn");
    if (btn) btn.disabled = true;
    var fd = new FormData();
    fd.append("file", _uploadState.file);
    fd.append("column_mapping", JSON.stringify(mapping));
    try {
      var resp = await authedFetch(API_LIST + "/upload/with-mapping", { method: "POST", body: fd });
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      _renderDesignUploadResult(payload || {});
      _showResult();
      await loadList();
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function _renderDesignUploadResult(r) {
    var body = $("dwl-upload-result-body");
    if (!body) return;
    var added = r.added || 0;
    var skipped = r.skipped || 0;
    var errors = r.errors || 0;
    var total = r.total || 0;
    var lines = [];
    lines.push('<div class="text-center py-3">' +
      '<svg class="w-8 h-8 mx-auto mb-2 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>' +
      '<p class="font-medium" style="color:var(--color-text-primary)">' +
        escapeHtml(t("design_watchlist.upload_result_summary", { added: added, total: total })
          || (added + "/" + total + " added")) + '</p>' +
      (skipped > 0 ? '<p class="text-xs mt-1" style="color:var(--color-text-faint)">' +
        escapeHtml(t("design_watchlist.upload_skipped_label", { count: skipped })
          || (skipped + " duplicates skipped")) + '</p>' : '') +
      (errors > 0 ? '<p class="text-xs mt-1" style="color:var(--color-risk-high-text)">' +
        escapeHtml(t("design_watchlist.upload_errors_label", { count: errors })
          || (errors + " errors")) + '</p>' : '') +
      (r.limit_reached ? '<p class="text-xs mt-1" style="color:var(--color-risk-high-text)">' +
        escapeHtml(t("design_watchlist.upload_limit_reached", "Plan limit reached")) + '</p>' : '') +
    '</div>');
    body.innerHTML = lines.join("");
  }

  // Expose helpers globally so the modal's inline onclick attributes reach them.
  window.openDesignBulkUploadModal = openDesignBulkUploadModal;
  window.closeDesignBulkUploadModal = closeDesignBulkUploadModal;
  window.showDesignUploadStepOne = showDesignUploadStepOne;
  window.onDesignUploadFilePicked = onDesignUploadFilePicked;
  window.downloadDesignWatchlistTemplate = downloadDesignWatchlistTemplate;
  window.detectDesignUploadColumns = detectDesignUploadColumns;
  window.submitDesignBulkUpload = submitDesignBulkUpload;

  function initDesignWatchlistTab() {
    wireOnce();
    loadList();
  }

  window.initDesignWatchlistTab = initDesignWatchlistTab;
})();
