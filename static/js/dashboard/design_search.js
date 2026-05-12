/**
 * Design search tab — driver for /api/v1/design-search/quick.
 *
 * Vanilla JS to avoid extending the 8K-line Alpine app.js with reactive
 * state. Mounts when the user clicks the Search tab; lazy-attaches event
 * delegation on first activation.
 *
 * Behaviors mirror the trademark (Marka) search:
 *   - Submit on button click or Enter key
 *   - Clear button (×) on the text input
 *   - Sıfırla resets all inputs + hides results
 *   - Search history persisted in localStorage (last 20, dedup,
 *     case-insensitive) with auto-suggest dropdown
 *   - Clear-on-empty: results pane hides when both query and image are empty
 *   - Document-level event delegation throughout, so listeners survive
 *     Alpine re-renders inside <template x-if> blocks (e.g. drag-drop
 *     image preview).
 */
(function () {
  "use strict";

  var API_QUICK = "/api/v1/design-search/quick";
  var API_LOCARNO_LIST = "/api/v1/locarno-classes";
  var API_LOCARNO_SUGGEST = "/api/v1/tools/suggest-locarno-classes";
  var HISTORY_KEY = "design_search_history";
  var HISTORY_MAX = 20;
  var HISTORY_SUGGEST = 10;

  // Locarno picker is now Alpine-driven (see _search_panel.html x-data block).
  // The hidden #design-search-locarno input is updated by Alpine; this module
  // only reads its value when running a search.

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

  function setStatus(text, kind) {
    var el = $("design-search-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color =
      kind === "error" ? "var(--color-text-error,#dc2626)" :
      kind === "ok"    ? "var(--color-success,#059669)"   :
                          "var(--color-text-muted)";
  }

  function clearError() {
    var err = $("design-search-error");
    if (err) { err.textContent = ""; hide(err); }
  }

  function showError(text) {
    var err = $("design-search-error");
    if (!err) return;
    err.textContent = text || t("design_search.error_generic", "Search failed");
    show(err);
  }

  function hasQuery() {
    var q = ($("design-search-input") || {}).value || "";
    return q.trim().length > 0;
  }

  function hasImage() {
    var inp = $("design-search-image");
    return !!(inp && inp.files && inp.files.length > 0);
  }

  // ---------------------------------------------------------------
  // History (localStorage)
  // ---------------------------------------------------------------

  function loadHistory() {
    try {
      var raw = localStorage.getItem(HISTORY_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }

  function saveHistory(arr) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(arr || [])); } catch (e) {}
  }

  function pushHistory(query) {
    if (!query) return;
    var q = String(query).trim();
    if (!q) return;
    var arr = loadHistory().filter(function (h) { return h.toLowerCase() !== q.toLowerCase(); });
    arr.unshift(q);
    if (arr.length > HISTORY_MAX) arr = arr.slice(0, HISTORY_MAX);
    saveHistory(arr);
  }

  function removeFromHistory(query) {
    var arr = loadHistory().filter(function (h) { return h !== query; });
    saveHistory(arr);
  }

  function clearAllHistory() {
    try { localStorage.removeItem(HISTORY_KEY); } catch (e) {}
  }

  function filteredHistory() {
    var q = (($("design-search-input") || {}).value || "").trim().toLowerCase();
    var arr = loadHistory();
    if (!q) return arr.slice(0, HISTORY_SUGGEST);
    return arr.filter(function (h) { return h.toLowerCase().indexOf(q) !== -1; }).slice(0, HISTORY_SUGGEST);
  }

  function renderHistoryDropdown() {
    var dropdown = $("design-search-history");
    var listEl = $("design-search-history-list");
    if (!dropdown || !listEl) return;
    var items = filteredHistory();
    if (items.length === 0) {
      hide(dropdown);
      listEl.innerHTML = "";
      return;
    }
    var html = items.map(function (item) {
      var safe = escapeHtml(item);
      return (
        '<div data-design-history-item class="flex items-center justify-between px-4 py-2.5 text-left text-sm cursor-pointer transition-colors" ' +
        'style="color:var(--color-text-primary)" data-history-value="' + safe + '" ' +
        'onmouseover="this.style.background=\'var(--color-bg-muted)\'" onmouseout="this.style.background=\'\'">' +
          '<span class="flex items-center gap-2 min-w-0">' +
            '<svg class="w-4 h-4 shrink-0" style="color:var(--color-text-faint)" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>' +
            '</svg>' +
            '<span class="truncate">' + safe + '</span>' +
          '</span>' +
          '<span data-design-history-remove class="shrink-0 p-1 rounded hover:opacity-70" ' +
          'style="color:var(--color-text-faint)" data-history-value="' + safe + '">' +
            '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>' +
            '</svg>' +
          '</span>' +
        '</div>'
      );
    }).join("");
    listEl.innerHTML = html;
    show(dropdown);
  }

  function hideHistory() { hide($("design-search-history")); }

  // ---------------------------------------------------------------
  // Result card visibility tied to input state
  // ---------------------------------------------------------------

  function maybeHideResultsIfEmpty() {
    if (!hasQuery() && !hasImage()) {
      hide($("design-search-results-card"));
      hide($("design-search-loading"));
      hide($("design-search-grid"));
      hide($("design-search-empty"));
      clearError();
      setStatus("");
      var grid = $("design-search-grid");
      if (grid) grid.innerHTML = "";
      var totalBadge = $("design-search-total-badge");
      if (totalBadge) totalBadge.textContent = "0";
    }
  }

  function updateClearInputBtnVisibility() {
    var btn = $("design-search-input-clear");
    if (!btn) return;
    if (hasQuery()) show(btn);
    else hide(btn);
  }

  // ---------------------------------------------------------------
  // Result rendering — feature parity with the trademark card
  // ---------------------------------------------------------------

  function _riskBucket(pct) {
    // Mirrors getScoreRiskLevel in result-card.js (bucketed 4-tier).
    if (pct >= 85) return "critical";
    if (pct >= 65) return "high";
    if (pct >= 35) return "medium";
    return "low";
  }

  function _riskBorderStyle(level) {
    var map = {
      critical: "border-color:var(--color-risk-critical-border,#dc2626);box-shadow:0 0 0 1px var(--color-risk-critical-border,#dc2626) inset",
      high:     "border-color:var(--color-risk-high-border,#ea580c)",
      medium:   "border-color:var(--color-risk-medium-border,#d97706)",
      low:      "border-color:var(--color-risk-low-border,#0891b2)",
    };
    return map[level] || "border-color:var(--color-border)";
  }

  // Small set of common Turkish design statuses → bg/text colors.
  function _statusColors(status) {
    var s = String(status || "").toLowerCase();
    if (s.indexOf("yayında") >= 0)        return { bg: "#dbeafe", color: "#1e40af" };
    if (s.indexOf("tescil") >= 0)         return { bg: "#dcfce7", color: "#166534" };
    if (s.indexOf("yenilen") >= 0)        return { bg: "#dcfce7", color: "#166534" };
    if (s.indexOf("hükümsüz") >= 0)       return { bg: "#fef2f2", color: "#991b1b" };
    if (s.indexOf("iptal") >= 0)          return { bg: "#fef2f2", color: "#991b1b" };
    if (s.indexOf("süresi doldu") >= 0)   return { bg: "#f3f4f6", color: "#6b7280" };
    if (s.indexOf("devred") >= 0)         return { bg: "#fef3c7", color: "#92400e" };
    if (s.indexOf("ertelen") >= 0)        return { bg: "#fef3c7", color: "#92400e" };
    return { bg: "var(--color-bg-muted)", color: "var(--color-text-secondary)" };
  }

  function _formatDateShort(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString();
    } catch (e) { return iso; }
  }

  // TÜRKPATENT design portal URL — public lookup by application number.
  function _turkpatentDesignUrl(applicationNo) {
    if (!applicationNo) return "";
    return "https://www.turkpatent.gov.tr/arastirma-yap?form=design&_q=" + encodeURIComponent(applicationNo);
  }

  function _signalBar(label, value) {
    var v = Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100);
    return (
      '<div class="flex items-center gap-2 text-[10px]" style="color:var(--color-text-faint)">' +
        '<span class="font-mono w-12 shrink-0">' + escapeHtml(label) + '</span>' +
        '<div class="flex-1 h-1 rounded-full overflow-hidden" style="background:var(--color-bg-muted)">' +
          '<div class="h-full bg-indigo-400" style="width:' + v + '%"></div>' +
        '</div>' +
        '<span class="font-mono w-8 text-right shrink-0">' + v + '%</span>' +
      '</div>'
    );
  }

  // 3-column score breakdown grid cell — mirrors the trademark
  // result card's expanded score grid. Each cell shows a label on
  // top + the percentage in a risk-colored bold number below.
  function _scoreGridCell(label, value01) {
    var v = Math.round(Math.max(0, Math.min(1, Number(value01) || 0)) * 100);
    var risk = _riskBucket(v);
    var colorMap = { critical: "#dc2626", high: "#ea580c", medium: "#d97706", low: "#0891b2" };
    return (
      '<div class="text-center p-2 rounded-lg" style="background:var(--color-bg-muted)">' +
        '<div class="text-[10px] uppercase tracking-wide mb-1" style="color:var(--color-text-muted)">' +
          escapeHtml(label) +
        '</div>' +
        '<div class="text-sm font-bold" style="color:' + colorMap[risk] + '">' + v + '%</div>' +
      '</div>'
    );
  }

  function renderResultCard(row) {
    var title = row.product_name_tr || row.product_name_en
              || row.application_no || row.registration_no || "—";
    var holder = (row.holder && row.holder.name) ? row.holder.name : "";
    // Prefer the public TPE client ID; fall back to the internal
    // holders.id UUID when this holder was never assigned a TPE ID
    // (foreign entities + legacy records — ~10% of designs).
    // Backend resolves either via _resolve_holder_row.
    var holderRef = (row.holder
        && (row.holder.tpe_client_id || row.holder.id))
        ? (row.holder.tpe_client_id || row.holder.id)
        : "";
    var locarno = row.locarno_classes || [];
    var designers = row.designers || [];
    var simNum = typeof row.similarity === "number" ? Number(row.similarity) : 0;
    var simStr = simNum.toFixed(1);
    var simPct = Math.round(simNum);
    var risk = _riskBucket(simPct);
    var imgUrl = row.image_url || "";
    var appLine = row.application_no
      ? row.application_no + (row.design_index ? " · #" + row.design_index : "")
      : "";
    var statusColors = _statusColors(row.current_status);
    var tpUrl = _turkpatentDesignUrl(row.application_no);
    var isExact = simPct >= 99;
    var isAlreadyWatched = (typeof window.isInDesignWatchlist === "function")
      ? !!window.isInDesignWatchlist(row.application_no)
      : false;

    var imgHtml = imgUrl
      ? '<img src="' + escapeHtml(imgUrl) +
        '" alt="' + escapeHtml(title) +
        '" loading="lazy" class="w-full h-40 object-contain rounded-md" ' +
        'style="background:var(--color-bg-muted)" ' +
        'onerror="this.onerror=null;this.replaceWith(Object.assign(document.createElement(\'div\'),{className:\'w-full h-40 flex items-center justify-center text-xs rounded-md\',style:\'background:var(--color-bg-muted);color:var(--color-text-faint)\',textContent:\'' +
        escapeHtml(t("design_search.no_image", "No image")) + '\'}));" />'
      : '<div class="w-full h-40 flex items-center justify-center text-xs rounded-md" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        escapeHtml(t("design_search.no_image", "No image")) + "</div>";

    // Top-right: similarity category badge (like Marka card)
    var simBadgeBgMap = { critical: "#dc2626", high: "#ea580c", medium: "#d97706", low: "#0891b2" };
    var simBadge =
      '<div class="flex flex-col items-end gap-0.5 shrink-0">' +
        '<span class="px-2 py-0.5 rounded-full text-xs font-semibold" ' +
          'style="background:' + simBadgeBgMap[risk] + ';color:white">' + simPct + '%</span>' +
        (isExact
          ? '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full font-bold" ' +
            'style="background:#fef2f2;color:#991b1b">' +
              '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>' +
              escapeHtml(t("scores.exact_match", "Exact match")) +
            '</span>'
          : '') +
      '</div>';

    // Score breakdown — 3-column grid mirroring the trademark
    // card. For design, the three meaningful dimensions are
    // Visual (max of DINOv2 + CLIP, since both encode visual
    // similarity), Color (HSV histogram), Text (product-name
    // trigram). The backend (services/design_search_service.py
    // line 397-399) returns breakdown as { text, dinov2, clip,
    // color } floats in [0,1] — no `_sim` suffix.
    var bd = row.similarity_breakdown || {};
    var visualSim = Math.max(Number(bd.dinov2) || 0, Number(bd.clip) || 0);
    var bdHtml =
      '<div class="grid grid-cols-3 gap-2 mb-3">' +
        _scoreGridCell(t("design_search.score_visual", "Görsel"), visualSim) +
        _scoreGridCell(t("design_search.score_color", "Renk"), bd.color) +
        _scoreGridCell(t("design_search.score_text", "Metin"), bd.text) +
      '</div>';

    // Locarno chips
    var locarnoChips = locarno.length === 0 ? "" :
      '<div class="mt-1.5 flex flex-wrap gap-1">' +
        locarno.map(function (c) {
          return '<span class="inline-block text-[10px] font-mono px-1.5 py-0.5 rounded" ' +
            'style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' +
            escapeHtml(c) + '</span>';
        }).join("") +
      '</div>';

    // Designer chips — each name is a separate clickable button so
    // the user can drill into that designer's portfolio of designs.
    // window.openDesignerPortfolio is wired in static/js/dashboard/
    // app.js and forwards to the same portfolio modal the holder
    // click uses (portfolioType = 'design-designer').
    var designerChips = "";
    if (designers.length > 0) {
      var shownDesigners = designers.slice(0, 2);
      var designerBtns = shownDesigners.map(function (d) {
        var name = String(d || "").trim();
        if (!name) return "";
        if (typeof window.openDesignerPortfolio === "function") {
          return '<button type="button" ' +
            'onclick="window.openDesignerPortfolio(' +
              JSON.stringify(name).replace(/"/g, '&quot;') + ', this)" ' +
            'class="text-left hover:underline" style="color:var(--color-primary)">' +
            escapeHtml(name) + '</button>';
        }
        return '<span>' + escapeHtml(name) + '</span>';
      }).filter(function (s) { return s.length > 0; }).join(', ');
      designerChips =
        '<div class="mt-1 text-xs" style="color:var(--color-text-secondary)">' +
          '<span style="color:var(--color-text-faint)">' +
            escapeHtml(t("design_search.designer_label", "Designer")) + ':</span> ' +
          designerBtns +
          (designers.length > 2 ? ' <span style="color:var(--color-text-faint)">+' + (designers.length - 2) + '</span>' : '') +
        '</div>';
    }

    // Holder line — clickable when modal helper exists
    var holderHtml = "";
    if (holder) {
      var holderInner = '<span style="color:var(--color-text-secondary)">' + escapeHtml(holder) + '</span>';
      if (holderRef && typeof window.openHolderPortfolio === "function") {
        holderInner = '<button type="button" onclick="window.openHolderPortfolio(' +
          JSON.stringify(holderRef).replace(/"/g, '&quot;') + ', this)" ' +
          'class="text-left hover:underline" style="color:var(--color-primary)">' +
          escapeHtml(holder) + '</button>';
      }
      holderHtml = '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("design_search.holder_label", "Holder")) + ':</span> ' +
        holderInner + '</div>';
    }

    // Attorney row — name + firm pair, clickable via
    // window.openAttorneyPortfolio (defined in app.js). When the firm
    // is missing the click still works; the backend uses
    // COALESCE(...,'') to match NULL-firm rows.
    var attorneyHtml = "";
    var attName = (row.attorney_name || "").trim();
    var attFirm = (row.attorney_firm || "").trim();
    if (attName) {
      // Most design rows have the firm baked into the attorney_name
      // already (e.g. "ALPER AKSU (SEMBOL PATENT ... LTD. ŞTİ.)") and
      // ALSO carry it on attorney_firm — concatenating with " — "
      // would duplicate the firm. Skip the join when the name already
      // contains the firm string (case-insensitive substring).
      var attFirmInName = attFirm && attName.toLowerCase().indexOf(attFirm.toLowerCase()) !== -1;
      var attDisplay = (attFirm && !attFirmInName) ? (attName + " — " + attFirm) : attName;
      var attInner = '<span style="color:var(--color-text-secondary)">' + escapeHtml(attDisplay) + '</span>';
      if (typeof window.openAttorneyPortfolio === "function") {
        attInner = '<button type="button" onclick="window.openAttorneyPortfolio(' +
          JSON.stringify(attName).replace(/"/g, '&quot;') + ', ' +
          JSON.stringify(attFirm).replace(/"/g, '&quot;') + ', this)" ' +
          'class="text-left hover:underline" style="color:var(--color-primary)">' +
          escapeHtml(attDisplay) + '</button>';
      }
      attorneyHtml = '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("design_search.attorney_label", "Attorney")) + ':</span> ' +
        attInner + '</div>';
    }

    // Bulletin chip
    var bulletinHtml = "";
    if (row.bulletin_no) {
      bulletinHtml = '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        escapeHtml(t("common.bulletin_label", "Bülten")) + ' ' + escapeHtml(row.bulletin_no) +
        (row.bulletin_date ? ' · ' + escapeHtml(_formatDateShort(row.bulletin_date)) : '') +
        '</span>';
    }

    // Application date row
    var appDateHtml = row.application_date
      ? '<div class="text-xs" style="color:var(--color-text-faint)">' +
        escapeHtml(t("common.application_date", "Application date")) + ' ' +
        escapeHtml(_formatDateShort(row.application_date)) +
        '</div>'
      : "";

    // Registration number
    var regNoHtml = row.registration_no
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">№</span> ' +
        '<span class="font-mono" style="color:var(--color-text-secondary)">' +
        escapeHtml(row.registration_no) + '</span></div>'
      : "";

    // Action row — styles + order match the trademark card
    // (_search_panel.html:885-912): watchlist FIRST in risk-high
    // (red) theme with the "eye" icon, Türkpatent SECOND in the
    // primary (indigo) theme with the external-link icon. Both
    // use the same px-3 py-1.5 / text-xs / rounded-lg / gap-1.5
    // sizing as trademark for visual parity.
    var watchlistBtn = "";
    if (row.application_no) {
      if (isAlreadyWatched) {
        watchlistBtn = '<span class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg" ' +
          'style="background:#dcfce7;color:#166534">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>' +
          escapeHtml(t("watchlist.already_watching", "Already watching")) + '</span>';
      } else {
        var wlPayload = JSON.stringify({
          product_name: title,
          customer_application_no: row.application_no,
          locarno_classes: locarno,
        }).replace(/"/g, '&quot;');
        watchlistBtn = '<button type="button" data-design-add-watchlist ' +
          'data-payload="' + wlPayload + '" ' +
          'class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors" ' +
          'style="color:var(--color-risk-high-text);background:var(--color-risk-high-bg)">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>' +
          '</svg>' +
          escapeHtml(t("watchlist.add_to_watchlist", "Add to watchlist")) + '</button>';
      }
    }

    var tpBtn = tpUrl
      ? '<a href="' + tpUrl + '" target="_blank" rel="noopener" ' +
        'class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors cursor-pointer" ' +
        'style="color:var(--color-primary);background:var(--color-primary-light)" ' +
        'onclick="event.stopPropagation()">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>' +
          escapeHtml(t("landing.view_on_turkpatent", "Türkpatent'te Gör")) +
        '</a>'
      : "";

    var actionRow = (tpBtn || watchlistBtn)
      ? '<div class="mt-3 flex flex-wrap items-center gap-2">' + watchlistBtn + tpBtn + '</div>'
      : "";

    // App-no identity row (always visible inside the collapsed header)
    var appLineHtml = appLine
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("design_search.appno_label", "App")) + ':</span> ' +
        '<span class="font-mono" style="color:var(--color-text-secondary)">' + escapeHtml(appLine) + '</span></div>'
      : "";

    // Header: image, title + sim badge, status pill + bulletin
    // chip, app no, and an expand chevron. Click anywhere on the
    // header (outside inner buttons/links) toggles the details
    // below. Mirrors the trademark result card UX.
    var headerHtml =
      '<div data-design-card-header class="p-3 cursor-pointer">' +
        imgHtml +
        '<div class="mt-2 flex items-start justify-between gap-2">' +
          '<h4 class="text-sm font-semibold leading-snug min-w-0 truncate" ' +
            'style="color:var(--color-text-primary)" title="' + escapeHtml(title) + '">' +
            escapeHtml(title) + '</h4>' +
          simBadge +
        '</div>' +
        '<div class="mt-1 flex flex-wrap items-center gap-1.5">' +
          (row.current_status
            ? '<span class="text-[10px] px-2 py-0.5 rounded-full font-medium" ' +
              'style="background:' + statusColors.bg + ';color:' + statusColors.color + '">' +
              escapeHtml(row.current_status) + '</span>'
            : '') +
          bulletinHtml +
        '</div>' +
        (appLineHtml ? '<div class="mt-1.5">' + appLineHtml + '</div>' : '') +
        '<div class="mt-2 flex items-center justify-center gap-1 text-[11px]" ' +
          'style="color:var(--color-text-faint)">' +
          '<span data-design-card-expand-label>' +
            escapeHtml(t("design_search.expand_details", "Show details")) +
          '</span>' +
          '<svg data-design-card-chevron class="w-3.5 h-3.5 transition-transform" ' +
            'fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>' +
          '</svg>' +
        '</div>' +
      '</div>';

    // Details: score grid + remaining fields + action buttons.
    // Hidden by default; toggled by the header click handler in
    // the document-level event delegation.
    var detailsHtml =
      '<div data-design-card-details hidden class="px-3 pb-3 pt-2" ' +
        'style="border-top:1px solid var(--color-border)">' +
        bdHtml +
        '<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1.5 mb-2">' +
          regNoHtml +
          appDateHtml +
          holderHtml +
          designerChips +
          attorneyHtml +
        '</div>' +
        locarnoChips +
        actionRow +
      '</div>';

    return (
      '<article class="rounded-lg border-2 overflow-hidden transition-shadow hover:shadow" ' +
      'style="' + _riskBorderStyle(risk) + ';background:var(--color-bg-card)" ' +
      'data-design-id="' + escapeHtml(row.id || "") + '" ' +
      'data-design-card-expanded="false">' +
        headerHtml +
        detailsHtml +
      '</article>'
    );
  }

  function renderResults(payload) {
    var grid = $("design-search-grid");
    var empty = $("design-search-empty");
    var totalBadge = $("design-search-total-badge");
    var duration = $("design-search-duration");
    if (!grid) return;
    var rows = (payload && payload.results) || [];
    grid.innerHTML = rows.map(renderResultCard).join("");
    if (totalBadge) totalBadge.textContent = String(rows.length);
    if (duration && payload && typeof payload.duration_ms === "number") {
      duration.textContent = payload.duration_ms + " ms";
    }
    if (rows.length === 0) {
      hide(grid);
      show(empty);
    } else {
      show(grid);
      hide(empty);
    }
  }

  // ---------------------------------------------------------------
  // runSearch
  // ---------------------------------------------------------------

  function setSubmitLoading(loading) {
    var btn = $("design-search-submit");
    if (btn) {
      btn.disabled = !!loading;
      var searchIcon = btn.querySelector('[data-design-submit-icon="search"]');
      var spinIcon = btn.querySelector('[data-design-submit-icon="spinner"]');
      if (searchIcon) searchIcon.classList.toggle("hidden", !!loading);
      if (spinIcon) spinIcon.classList.toggle("hidden", !loading);
    }
    var idleHint = $("design-search-hint");
    var loadHint = $("design-search-hint-loading");
    if (idleHint) idleHint.classList.toggle("hidden", !!loading);
    if (loadHint) loadHint.classList.toggle("hidden", !loading);
  }

  async function runSearch() {
    var query = ($("design-search-input") || {}).value || "";
    var locarno = ($("design-search-locarno") || {}).value || "";
    var imageInput = $("design-search-image");
    var hasImageFile = imageInput && imageInput.files && imageInput.files.length > 0;
    var hasQueryText = query.trim().length > 0;
    if (!hasImageFile && !hasQueryText) {
      setStatus(t("design_search.error_empty", "Provide a query or upload an image"), "error");
      return;
    }

    hideHistory();
    clearError();
    setStatus("");
    setSubmitLoading(true);
    show($("design-search-results-card"));
    show($("design-search-loading"));
    hide($("design-search-grid"));
    hide($("design-search-empty"));

    var fd = new FormData();
    if (hasQueryText) fd.append("query", query.trim());
    if (locarno.trim()) fd.append("locarno", locarno.trim());
    if (hasImageFile) fd.append("image", imageInput.files[0]);

    try {
      var headers = {};
      var token = getAuthToken();
      if (token) headers["Authorization"] = "Bearer " + token;

      var resp = await fetch(API_QUICK, { method: "POST", headers: headers, body: fd });
      var payload = null;
      try { payload = await resp.json(); } catch (e) { payload = null; }

      if (!resp.ok) {
        if (resp.status === 429) {
          var msg = (payload && (payload.detail && payload.detail.message_en || payload.detail && payload.detail.message)) ||
                    t("design_search.error_quota", "Daily search limit reached");
          showError(msg);
        } else if (resp.status === 401 || resp.status === 403) {
          showError(t("design_search.error_auth", "Please sign in to use design search"));
        } else if (resp.status === 413) {
          showError(t("design_search.error_image_too_large", "Image is too large (max 10 MB)"));
        } else if (resp.status === 422) {
          showError((payload && payload.detail) || t("design_search.error_invalid_input", "Invalid input"));
        } else {
          showError(t("design_search.error_generic", "Search failed"));
        }
        return;
      }
      renderResults(payload || {});
      // Push successful query to history
      if (hasQueryText) pushHistory(query.trim());
    } catch (e) {
      showError(t("design_search.error_network", "Network error"));
    } finally {
      hide($("design-search-loading"));
      setSubmitLoading(false);
    }
  }

  // ---------------------------------------------------------------
  // Add-to-watchlist (called from result card delegation)
  // ---------------------------------------------------------------

  async function addDesignToWatchlist(btn, payload) {
    if (!payload || !payload.product_name) return;
    var originalLabel = btn.innerHTML;
    btn.disabled = true;
    btn.style.opacity = "0.6";
    try {
      var headers = { "Content-Type": "application/json" };
      var token = getAuthToken();
      if (token) headers["Authorization"] = "Bearer " + token;
      var resp = await fetch("/api/v1/design-watchlist", {
        method: "POST", headers: headers, body: JSON.stringify(payload),
      });
      if (resp.ok) {
        // Swap the button to "Already watching"
        btn.outerHTML = '<span class="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded" ' +
          'style="background:#dcfce7;color:#166534">' +
          '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>' +
          escapeHtml(t("watchlist.already_watching", "Already watching")) + '</span>';
        if (window.AppToast && typeof window.AppToast.success === "function") {
          window.AppToast.success(t("watchlist.add_to_watchlist", "Added to watchlist"));
        }
        // Land the user on the Tasarım Takibi tab so they immediately see
        // the new row (toast persists across the tab switch).
        if (typeof window.showDashboardTab === "function") {
          window.showDashboardTab("design-watchlist");
        }
      } else {
        var detail = null;
        try { detail = (await resp.json()).detail; } catch (e) {}
        var msg = (detail && (detail.message || detail.message_en)) || "Failed";
        btn.innerHTML = originalLabel;
        btn.disabled = false;
        btn.style.opacity = "";
        if (window.AppToast && typeof window.AppToast.error === "function") {
          window.AppToast.error(msg);
        }
      }
    } catch (e) {
      btn.innerHTML = originalLabel;
      btn.disabled = false;
      btn.style.opacity = "";
    }
  }

  // ---------------------------------------------------------------
  // Document-level delegation — installed once, survives re-renders
  // ---------------------------------------------------------------

  if (!window.__designSearchDelegated) {
    window.__designSearchDelegated = true;

    // Click delegation
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;

      // Submit button
      if (t.closest("#design-search-submit")) {
        e.preventDefault();
        runSearch();
        return;
      }

      // Clear-input × button
      if (t.closest("#design-search-input-clear")) {
        e.preventDefault();
        var inp = $("design-search-input");
        if (inp) { inp.value = ""; inp.focus(); }
        updateClearInputBtnVisibility();
        renderHistoryDropdown();
        maybeHideResultsIfEmpty();
        return;
      }

      // History "Clear all"
      if (t.closest("#design-search-history-clear-all")) {
        e.preventDefault();
        clearAllHistory();
        hideHistory();
        return;
      }

      // (Locarno picker click handlers live in the Alpine x-data block now.)

      // Result card: Add to design watchlist
      var addBtn = t.closest("[data-design-add-watchlist]");
      if (addBtn) {
        e.preventDefault();
        e.stopPropagation();
        var rawPayload = addBtn.getAttribute("data-payload") || "{}";
        try {
          var payload = JSON.parse(rawPayload.replace(/&quot;/g, '"'));
          addDesignToWatchlist(addBtn, payload);
        } catch (err) {
          // Fail silently — invalid payload, shouldn't happen
        }
        return;
      }

      // Result card: toggle expand/collapse on header click.
      // Don't toggle if the click hit an inner button or link
      // (so action buttons still work as expected).
      var hdr = t.closest("[data-design-card-header]");
      if (hdr && !t.closest("button, a")) {
        var card = hdr.closest("article[data-design-card-expanded]");
        if (card) {
          var details = card.querySelector("[data-design-card-details]");
          var chevron = card.querySelector("[data-design-card-chevron]");
          var label = card.querySelector("[data-design-card-expand-label]");
          var nowExpanded = card.getAttribute("data-design-card-expanded") !== "true";
          card.setAttribute("data-design-card-expanded", nowExpanded ? "true" : "false");
          if (details) {
            if (nowExpanded) details.removeAttribute("hidden");
            else details.setAttribute("hidden", "");
          }
          if (chevron) chevron.style.transform = nowExpanded ? "rotate(180deg)" : "";
          if (label) {
            label.textContent = nowExpanded
              ? t("design_search.hide_details", "Hide details")
              : t("design_search.expand_details", "Show details");
          }
        }
        return;
      }

      // History item × (remove single)
      var rm = t.closest("[data-design-history-remove]");
      if (rm) {
        e.preventDefault();
        e.stopPropagation();
        removeFromHistory(rm.getAttribute("data-history-value") || "");
        renderHistoryDropdown();
        return;
      }

      // History item click (select)
      var item = t.closest("[data-design-history-item]");
      if (item) {
        e.preventDefault();
        var val = item.getAttribute("data-history-value") || "";
        var inp2 = $("design-search-input");
        if (inp2) { inp2.value = val; inp2.focus(); }
        updateClearInputBtnVisibility();
        hideHistory();
        return;
      }

      // Click outside the search-input area → hide history
      var dropdown = $("design-search-history");
      var input = $("design-search-input");
      if (dropdown && !dropdown.classList.contains("hidden")) {
        if (!t.closest("#design-search-history") && t !== input) {
          hideHistory();
        }
      }
    });

    // Keydown delegation (Enter to search, Escape to hide history)
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (!t) return;
      if (t.id === "design-search-input") {
        if (e.key === "Enter") { e.preventDefault(); runSearch(); }
        else if (e.key === "Escape") { hideHistory(); }
      }
      // (Locarno AI Enter is handled by Alpine @keydown.enter on its input.)
    });

    // Input event for the text query (track + auto-suggest + clear-on-empty)
    document.addEventListener("input", function (e) {
      var t = e.target;
      if (!t) return;
      if (t.id === "design-search-input") {
        updateClearInputBtnVisibility();
        renderHistoryDropdown();
        maybeHideResultsIfEmpty();
      }
    });

    // Click on the text query — open the history dropdown only on actual
    // user interaction. (focusin would fire from programmatic focus()
    // when the Search tab activates, leaving the dropdown hanging over
    // the image upload zone.)
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.id === "design-search-input") {
        renderHistoryDropdown();
      }
    });

    // Image input change → react to clear/select
    document.addEventListener("change", function (e) {
      var t = e.target;
      if (t && t.id === "design-search-image") {
        // Defer slightly so Alpine x-data preview state has a chance to settle
        setTimeout(maybeHideResultsIfEmpty, 50);
      }
    });
  }

  // ---------------------------------------------------------------
  // Lazy init when the Search tab activates
  // ---------------------------------------------------------------

  function initDesignSearchTab() {
    updateClearInputBtnVisibility();
    var input = $("design-search-input");
    if (input) setTimeout(function () { input.focus(); }, 60);
  }

  // Public surface
  window.initDesignSearchTab = initDesignSearchTab;
})();
