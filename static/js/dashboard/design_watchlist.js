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
    return (
      (window.localStorage && localStorage.getItem("access_token")) ||
      (window.sessionStorage && sessionStorage.getItem("access_token")) ||
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
      var resp = await authedFetch(API_LIST + "?page=1&page_size=50");
      var payload = await resp.json().catch(function () { return null; });
      if (!resp.ok) {
        showHttpError(resp, payload, "design_watchlist.error_generic");
        return;
      }
      renderList(payload || {});
    } catch (e) {
      setStatus(t("design_watchlist.error_network", "Network error"), "error");
    } finally {
      hide($("design-watchlist-loading"));
    }
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
      var resp = await authedFetch(API_ALERTS + "?watchlist_item_id=" + encodeURIComponent(itemId) + "&page_size=20");
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
      else if (action === "acknowledge" || action === "resolve" || action === "dismiss") {
        if (alertId) alertAction(alertId, action);
      }
    });
  }

  function initDesignWatchlistTab() {
    wireOnce();
    loadList();
  }

  window.initDesignWatchlistTab = initDesignWatchlistTab;
})();
