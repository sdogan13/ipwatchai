/**
 * Coğrafi İşaret (GI) search tab driver.
 *
 * Mirrors patent_search.js patterns (vanilla JS, document-level event
 * delegation, localStorage history, AppI18n + AppAuth integration) but
 * tailored for the cografi search shape:
 *
 *   - Optional figure upload (DINOv2 hybrid retrieval)
 *   - Autocomplete returns {names, regions} (not IPC items)
 *   - GI-specific filters: gi_type, region, section_keys (multi),
 *     application_no, registration_no, date range, include_admin
 *   - Result-card shape: name, region, gi_type, applicant, bulletin,
 *     similarity badge + per-signal score breakdown bars
 */
(function () {
  "use strict";

  var API_QUICK = "/api/v1/cografi-search/quick";
  var API_AUTOCOMPLETE = "/api/v1/cografi-search/autocomplete";
  var HISTORY_KEY = "cografi_search_history";
  var HISTORY_MAX = 20;
  var HISTORY_SUGGEST = 10;

  var autocompleteDebounceTimer = null;
  var autocompleteData = { names: [], regions: [] };

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
      (window.localStorage && (
        localStorage.getItem("auth_token") ||
        localStorage.getItem("access_token") ||
        localStorage.getItem("token"))) ||
      ""
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function setStatus(text, kind) {
    var el = $("cografi-search-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color =
      kind === "error" ? "var(--color-text-error,#dc2626)" :
      kind === "ok"    ? "var(--color-success,#059669)"   :
                          "var(--color-text-muted)";
  }

  function clearError() {
    var err = $("cografi-search-error");
    if (err) { err.textContent = ""; hide(err); }
  }

  function showError(text) {
    var err = $("cografi-search-error");
    if (!err) return;
    err.textContent = text || t("cografi_search.error_generic", "Search failed");
    show(err);
  }

  function selectedSectionKeys() {
    var nodes = document.querySelectorAll(".cografi-section-key:checked");
    var out = [];
    for (var i = 0; i < nodes.length; i++) out.push(nodes[i].value);
    return out;
  }

  function hasQuery() {
    var q = ($("cografi-search-input") || {}).value || "";
    return q.trim().length > 0;
  }

  function hasFilters() {
    return (
      (($("cografi-search-region") || {}).value || "").trim().length > 0 ||
      (($("cografi-search-gi-type") || {}).value || "").trim() !== "" ||
      (($("cografi-search-application-no") || {}).value || "").trim() !== "" ||
      (($("cografi-search-registration-no") || {}).value || "").trim() !== "" ||
      (($("cografi-search-date-from") || {}).value || "") !== "" ||
      (($("cografi-search-date-to") || {}).value || "") !== "" ||
      selectedSectionKeys().length > 0
    );
  }

  function hasImage() {
    var inp = $("cografi-search-image");
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
    saveHistory(loadHistory().filter(function (h) { return h !== query; }));
  }
  function clearAllHistory() {
    try { localStorage.removeItem(HISTORY_KEY); } catch (e) {}
  }
  function filteredHistory() {
    var q = (($("cografi-search-input") || {}).value || "").trim().toLowerCase();
    var arr = loadHistory();
    if (!q) return arr.slice(0, HISTORY_SUGGEST);
    return arr.filter(function (h) { return h.toLowerCase().indexOf(q) !== -1; })
              .slice(0, HISTORY_SUGGEST);
  }

  // ---------------------------------------------------------------
  // Combined dropdown: autocomplete (when query >= 2 chars) OR history (empty/short)
  // ---------------------------------------------------------------

  function _historyItem(item) {
    var safe = escapeHtml(item);
    return (
      '<div data-cografi-history-item ' +
      'class="flex items-center justify-between px-4 py-2.5 text-left text-sm cursor-pointer transition-colors" ' +
      'style="color:var(--color-text-primary)" data-history-value="' + safe + '" ' +
      'onmouseover="this.style.background=\'var(--color-bg-muted)\'" ' +
      'onmouseout="this.style.background=\'\'">' +
        '<span class="flex items-center gap-2 min-w-0">' +
          '<svg class="w-4 h-4 shrink-0" style="color:var(--color-text-faint)" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>' +
          '</svg>' +
          '<span class="truncate">' + safe + '</span>' +
        '</span>' +
        '<span data-cografi-history-remove class="shrink-0 p-1 rounded hover:opacity-70" ' +
        'style="color:var(--color-text-faint)" data-history-value="' + safe + '">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>' +
          '</svg>' +
        '</span>' +
      '</div>'
    );
  }

  function _autocompleteItem(value, kind) {
    var safe = escapeHtml(value);
    var icon = kind === "region"
      ? 'd="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0zM15 11a3 3 0 11-6 0 3 3 0 016 0z"'
      : 'd="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"';
    var kindLabel = kind === "region"
      ? t("cografi_search.region_label_card", "Region")
      : t("cografi_search.name_label_card", "Name");
    return (
      '<div data-cografi-autocomplete ' +
      'class="flex items-center gap-2 px-4 py-2 text-sm cursor-pointer transition-colors" ' +
      'data-ac-kind="' + kind + '" data-ac-value="' + safe + '" ' +
      'style="color:var(--color-text-primary)" ' +
      'onmouseover="this.style.background=\'var(--color-bg-muted)\'" ' +
      'onmouseout="this.style.background=\'\'">' +
        '<svg class="w-4 h-4 shrink-0" style="color:var(--color-text-faint)" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' + icon + '/>' +
        '</svg>' +
        '<span class="truncate flex-1">' + safe + '</span>' +
        '<span class="text-[10px] shrink-0" style="color:var(--color-text-faint)">' + escapeHtml(kindLabel) + '</span>' +
      '</div>'
    );
  }

  function renderDropdown() {
    var dropdown = $("cografi-search-history");
    var listEl = $("cografi-search-history-list");
    if (!dropdown || !listEl) return;
    var q = (($("cografi-search-input") || {}).value || "").trim();

    var html = "";
    if (q.length >= 2 && (autocompleteData.names.length > 0 || autocompleteData.regions.length > 0)) {
      var names = autocompleteData.names.slice(0, 6);
      var regions = autocompleteData.regions.slice(0, 4);
      html += names.map(function (n) { return _autocompleteItem(n, "name"); }).join("");
      html += regions.map(function (r) { return _autocompleteItem(r, "region"); }).join("");
    }
    var items = filteredHistory();
    if (items.length > 0) {
      if (html) {
        html += '<div class="px-4 py-1.5 text-[10px] uppercase tracking-wide" ' +
                'style="color:var(--color-text-faint);background:var(--color-bg-muted);border-top:1px solid var(--color-border)">' +
                escapeHtml(t("cografi_search.recent_searches", "Recent searches")) + '</div>';
      }
      html += items.map(_historyItem).join("");
    }
    if (!html) {
      hide(dropdown);
      listEl.innerHTML = "";
      return;
    }
    listEl.innerHTML = html;
    show(dropdown);
  }

  function hideDropdown() { hide($("cografi-search-history")); }

  function fetchAutocomplete(prefix) {
    var url = API_AUTOCOMPLETE + "?q=" + encodeURIComponent(prefix);
    var headers = {};
    var token = getAuthToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    fetch(url, { headers: headers })
      .then(function (r) { return r.ok ? r.json() : { names: [], regions: [] }; })
      .then(function (data) {
        autocompleteData = {
          names: (data && data.names) || [],
          regions: (data && data.regions) || [],
        };
        renderDropdown();
      })
      .catch(function () {
        autocompleteData = { names: [], regions: [] };
        renderDropdown();
      });
  }

  function onQueryInput() {
    var v = (($("cografi-search-input") || {}).value || "").trim();
    if (autocompleteDebounceTimer) clearTimeout(autocompleteDebounceTimer);
    if (v.length < 2) {
      autocompleteData = { names: [], regions: [] };
      renderDropdown();
      return;
    }
    autocompleteDebounceTimer = setTimeout(function () { fetchAutocomplete(v); }, 180);
  }

  // ---------------------------------------------------------------
  // Result rendering
  // ---------------------------------------------------------------

  function _signalBar(label, value) {
    var v = Math.max(0, Math.min(1, Number(value) || 0));
    var pct = Math.round(v * 100);
    return (
      '<div class="flex items-center gap-2 text-[10px]">' +
        '<span class="w-14 shrink-0" style="color:var(--color-text-faint)">' + label + '</span>' +
        '<div class="flex-1 h-1 rounded-full overflow-hidden" style="background:var(--color-bg-muted)">' +
          '<div class="h-full rounded-full" style="width:' + pct + '%;background:var(--color-primary)"></div>' +
        '</div>' +
        '<span class="w-8 text-right shrink-0 font-mono" style="color:var(--color-text-secondary)">' + pct + '%</span>' +
      '</div>'
    );
  }

  // Risk bucket + 3-column score grid + status pill helpers — match
  // the patent + design card visual language so the dashboard reads
  // consistently across registries.
  function _riskBucket(pct) {
    if (pct >= 85) return "critical";
    if (pct >= 70) return "high";
    if (pct >= 50) return "medium";
    return "low";
  }
  function _riskBorderStyle(risk) {
    var map = {
      critical: "border-color:#dc2626",
      high: "border-color:#ea580c",
      medium: "border-color:#d97706",
      low: "border-color:var(--color-border)",
    };
    return map[risk] || map.low;
  }
  function _scoreGridCell(label, value) {
    var pct = value != null ? Math.round(Math.max(0, Math.min(1, Number(value))) * 100) : 0;
    return (
      '<div class="text-center p-2 rounded-md" style="background:var(--color-bg-muted)">' +
        '<div class="text-[10px] mb-0.5" style="color:var(--color-text-faint)">' + label + '</div>' +
        '<div class="text-sm font-semibold" style="color:var(--color-text-primary)">' + pct + '%</div>' +
      '</div>'
    );
  }
  // The cografi_records.section_key enum uses long names
  // ("article_40_modified") but the existing locale keys are
  // abbreviated ("section_art40_modified"). Map between them so the
  // header pill text stays localized.
  function _sectionLocaleKey(key) {
    var map = {
      article_40_modified:        "section_art40_modified",
      article_42_change_requests: "section_art42_change_requests",
      article_42_finalized:       "section_art42_finalized",
      article_43_modified:        "section_art43_modified",
      gazette_only_announcements: "section_gazette_only",
    };
    return map[key] || ("section_" + key);
  }

  // Section_key colours — registered + finalized lean green (live
  // records), modifications/corrections lean amber, examined lean
  // grey. Used both for the header pill and the section-aware label.
  function _sectionColors(key) {
    var map = {
      registered:                { bg: "#dcfce7", color: "#166534" },
      article_42_finalized:      { bg: "#dcfce7", color: "#166534" },
      examined:                  { bg: "#f3f4f6", color: "#374151" },
      article_40_modified:       { bg: "#fef3c7", color: "#92400e" },
      article_42_change_requests:{ bg: "#fef3c7", color: "#92400e" },
      article_43_modified:       { bg: "#fef3c7", color: "#92400e" },
      corrections:               { bg: "#fef3c7", color: "#92400e" },
      gazette_only_announcements:{ bg: "#e0e7ff", color: "#3730a3" },
    };
    return map[key] || { bg: "var(--color-bg-muted)", color: "var(--color-text-muted)" };
  }

  function _usageBlock(rawText, cardId) {
    if (!rawText) return "";
    var safe = escapeHtml(rawText);
    var COLLAPSE_AT = 240;
    if (safe.length <= COLLAPSE_AT) {
      return '<p class="text-xs leading-relaxed mt-2" style="color:var(--color-text-secondary)">' +
             safe + '</p>';
    }
    var preview = safe.slice(0, COLLAPSE_AT) + '…';
    return (
      '<div class="text-xs leading-relaxed mt-2" style="color:var(--color-text-secondary)">' +
        '<span data-usage-preview="' + cardId + '">' + preview + '</span>' +
        '<span data-usage-full="' + cardId + '" class="hidden">' + safe + '</span>' +
        ' <button type="button" data-usage-toggle="' + cardId + '" ' +
        'class="ml-1 text-xs font-medium hover:underline" style="color:var(--color-primary)">' +
        escapeHtml(t("cografi_search.show_more", "Show more")) +
        '</button>' +
      '</div>'
    );
  }

  function renderResultCard(item) {
    var cardId = "cs-" + (item.id || Math.random().toString(36).slice(2, 9));
    var titleStr = item.name || t("cografi_search.untitled", "Unnamed");
    var giType = item.gi_type || "";
    var region = item.geographical_boundary || "";
    var productGroup = item.product_group || "";
    var appNo = item.application_no || "";
    var regNo = item.registration_no != null ? String(item.registration_no) : "";
    var appDate = item.application_date || "";
    var regDate = item.registration_date || "";
    var bulletinNo = item.bulletin_no || "";
    var bulletinDate = item.bulletin_date || "";
    var sectionKey = item.section_key || "";
    var recordType = item.record_type || "";
    var imgUrl = item.image_url || "";
    var bd = item.similarity_breakdown || {};
    var simNum = item.similarity != null ? Number(item.similarity) : 0;
    var simPct = Math.round(simNum);
    var risk = _riskBucket(simPct);
    var sectionColors = _sectionColors(sectionKey);

    // Image (header-prominent, like the design/patent cards)
    var noImgLabel = escapeHtml(t("cografi_search.no_image", "No image"));
    var imgHtml = imgUrl
      ? '<img src="' + escapeHtml(imgUrl) +
        '" alt="' + escapeHtml(titleStr) +
        '" loading="lazy" class="w-full h-40 object-contain rounded-md" ' +
        'style="background:var(--color-bg-muted)" ' +
        'onerror="this.onerror=null;this.replaceWith(Object.assign(document.createElement(\'div\'),{className:\'w-full h-40 flex items-center justify-center text-xs rounded-md\',style:\'background:var(--color-bg-muted);color:var(--color-text-faint)\',textContent:\'' +
        noImgLabel + '\'}));" />'
      : '<div class="w-full h-40 flex items-center justify-center text-xs rounded-md" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        noImgLabel + "</div>";

    // Similarity badge (risk-coloured)
    var simBadgeBgMap = {
      critical: "#dc2626", high: "#ea580c",
      medium: "#d97706", low: "#0891b2",
    };
    var simBadge = simPct
      ? '<span class="px-2 py-0.5 rounded-full text-xs font-semibold shrink-0" ' +
          'style="background:' + simBadgeBgMap[risk] + ';color:white">' + simPct + '%</span>'
      : '';

    // Bulletin chip (header-visible meta)
    var bulletinHtml = "";
    if (bulletinNo) {
      bulletinHtml = '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.bulletin_label", "Bulletin")) + ' ' + escapeHtml(bulletinNo) +
        (bulletinDate ? ' · ' + escapeHtml(bulletinDate) : '') + '</span>';
    }

    // App-no / reg-no line (always visible identifier)
    var idLine = "";
    if (appNo || regNo) {
      var bits = [];
      if (appNo) {
        bits.push('<span class="font-mono" style="color:var(--color-text-secondary)">' + escapeHtml(appNo) + '</span>');
      }
      if (regNo) {
        bits.push('<span class="font-mono" style="color:var(--color-text-secondary)">#' + escapeHtml(regNo) + '</span>');
      }
      idLine = '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
               escapeHtml(t("cografi_search.id_label", "No")) + ':</span> ' +
               bits.join(' · ') + '</div>';
    }

    var headerHtml =
      '<div data-cografi-card-header class="p-3 cursor-pointer">' +
        imgHtml +
        '<div class="mt-2 flex items-start justify-between gap-2">' +
          '<h4 class="text-sm font-semibold leading-snug min-w-0 truncate" ' +
            'style="color:var(--color-text-primary)" title="' + escapeHtml(titleStr) + '">' +
            escapeHtml(titleStr) + '</h4>' +
          simBadge +
        '</div>' +
        '<div class="mt-1 flex flex-wrap items-center gap-1.5">' +
          (sectionKey
            ? '<span class="text-[10px] px-2 py-0.5 rounded-full font-medium" ' +
              'style="background:' + sectionColors.bg + ';color:' + sectionColors.color + '">' +
              escapeHtml(t("cografi_search." + _sectionLocaleKey(sectionKey), sectionKey)) + '</span>'
            : '') +
          (recordType
            ? '<span class="text-[10px] px-2 py-0.5 rounded font-medium" ' +
              'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
              escapeHtml(window.translateStatus ? window.translateStatus(recordType) : recordType) + '</span>'
            : '') +
          bulletinHtml +
        '</div>' +
        (idLine ? '<div class="mt-1.5">' + idLine + '</div>' : '') +
        '<div class="mt-2 flex items-center justify-center gap-1 text-[11px]" ' +
          'style="color:var(--color-text-faint)">' +
          '<span data-cografi-card-expand-label>' +
            escapeHtml(t("cografi_search.expand_details", "Show details")) +
          '</span>' +
          '<svg data-cografi-card-chevron class="w-3.5 h-3.5 transition-transform" ' +
            'fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>' +
          '</svg>' +
        '</div>' +
      '</div>';

    // 3-column score grid — Text / Semantic / Image. The image cell
    // shows 0% when no figure is in play (matches the design card).
    var bdHtml =
      '<div class="grid grid-cols-3 gap-2 mb-3">' +
        _scoreGridCell(t("cografi_search.score_text", "Metin"), bd.text) +
        _scoreGridCell(t("cografi_search.score_embedding", "Anlam"), bd.embedding) +
        _scoreGridCell(t("cografi_search.score_image", "Görsel"), bd.image) +
      '</div>';

    var giTypeHtml = giType
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.gi_type_label", "GI Type")) + ':</span> ' +
        '<span class="inline-block px-2 py-0.5 rounded text-[10px] ml-1" ' +
        'style="background:var(--color-primary-light);color:var(--color-primary)">' +
        escapeHtml(giType) + '</span></div>'
      : '';

    var regionHtml = region
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.region_label_card", "Region")) + ':</span> ' +
        '<span style="color:var(--color-text-secondary)">' + escapeHtml(region) + '</span></div>'
      : '';

    var productHtml = productGroup
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.product_group_label", "Product")) + ':</span> ' +
        '<span style="color:var(--color-text-secondary)">' + escapeHtml(productGroup) + '</span></div>'
      : '';

    // Applicant: clickable when an id is available (TPE id or internal
    // UUID). Falls back to plain text for ingest rows missing both.
    var applicantObj = item.applicant || null;
    var applicantName = applicantObj && applicantObj.name
      ? (window._stripTurkishAddress ? window._stripTurkishAddress(applicantObj.name) : applicantObj.name)
      : (item.applicant_name || "");
    var applicantId = (applicantObj && (applicantObj.tpe_client_id || applicantObj.id)) || "";
    var applicantInner = applicantId
      ? ('<button type="button" data-portfolio-trigger="cografi-applicant" ' +
         'data-holder-id="' + escapeHtml(applicantId) + '" ' +
         'class="text-left hover:underline cursor-pointer" ' +
         'style="color:var(--color-primary);background:transparent;border:0;padding:0;">' +
         escapeHtml(applicantName) + '</button>')
      : ('<span style="color:var(--color-text-secondary)">' + escapeHtml(applicantName) + '</span>');
    var applicantHtml = applicantName
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.applicant_label", "Applicant")) + ':</span> ' +
        applicantInner + '</div>'
      : '';

    // Agent: text-only column, clickable via normalized name match.
    var agentRaw = String(item.agent || "").trim();
    var agentInner = agentRaw
      ? ('<button type="button" data-portfolio-trigger="cografi-agent" ' +
         'data-agent-name="' + escapeHtml(agentRaw) + '" ' +
         'class="text-left hover:underline cursor-pointer" ' +
         'style="color:var(--color-primary);background:transparent;border:0;padding:0;">' +
         escapeHtml(window._stripTurkishAddress ? window._stripTurkishAddress(agentRaw) : agentRaw) + '</button>')
      : "";
    var agentHtml = agentRaw
      ? '<div class="text-xs"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_search.agent_label", "Agent")) + ':</span> ' +
        agentInner + '</div>'
      : '';

    // Filed / Reg dates row
    var datesHtml =
      (appDate || regDate)
        ? '<div class="flex flex-wrap items-center gap-3 text-xs" style="color:var(--color-text-faint)">' +
            (appDate ? '<span>' + escapeHtml(t("cografi_search.filed", "Filed")) + ': ' + escapeHtml(appDate) + '</span>' : '') +
            (regDate ? '<span>' + escapeHtml(t("cografi_search.registered", "Reg.")) + ': ' + escapeHtml(regDate) + '</span>' : '') +
          '</div>'
        : '';

    // Watchlist add (reference-watch on this row's id — clones the
    // record's text_embedding into reference_embedding so the scanner
    // has something to cosine-against). Same pattern design + patent
    // cards use.
    var watchlistBtn = "";
    if (item.id) {
      var wlPayload = JSON.stringify({
        watch_type: "reference",
        reference_record_id: item.id,
        label: (titleStr || "").slice(0, 200),
      }).replace(/"/g, '&quot;');
      watchlistBtn = '<button type="button" data-cografi-add-watchlist ' +
        'data-payload="' + wlPayload + '" ' +
        'class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors" ' +
        'style="color:var(--color-risk-high-text);background:var(--color-risk-high-bg)">' +
        '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>' +
        '</svg>' +
        escapeHtml(t("watchlist.add_to_watchlist", "Add to watchlist")) + '</button>';
    }
    var detailBtn = item.id
      ? '<button type="button" data-cd-open="' + escapeHtml(item.id) + '" ' +
        'class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors" ' +
        'style="color:var(--color-text-primary);background:var(--color-bg-muted)">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>' +
          '</svg>' +
          escapeHtml(t("cografi_search.view_detail", "Detaylar")) +
        '</button>'
      : "";
    var actionRow = (watchlistBtn || detailBtn)
      ? '<div class="mt-3 flex flex-wrap items-center gap-2">' + watchlistBtn + detailBtn + '</div>'
      : "";

    var detailsHtml =
      '<div data-cografi-card-details hidden class="px-3 pb-3 pt-2" ' +
        'style="border-top:1px solid var(--color-border)">' +
        bdHtml +
        '<div class="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1.5 mb-2">' +
          datesHtml +
          giTypeHtml +
          regionHtml +
          productHtml +
          applicantHtml +
          agentHtml +
        '</div>' +
        _usageBlock(item.usage_description, cardId) +
        actionRow +
      '</div>';

    return (
      '<article class="rounded-lg border-2 overflow-hidden transition-shadow hover:shadow" ' +
      'data-cografi-card-id="' + cardId + '" ' +
      'data-cografi-card-expanded="false" ' +
      'style="' + _riskBorderStyle(risk) + ';background:var(--color-bg-card)">' +
        headerHtml +
        detailsHtml +
      '</article>'
    );
  }

  function renderResults(data) {
    var card = $("cografi-search-results-card");
    var grid = $("cografi-search-grid");
    var empty = $("cografi-search-empty");
    var totalBadge = $("cografi-search-total-badge");
    var dur = $("cografi-search-duration");
    if (!card || !grid) return;
    show(card);
    hide($("cografi-search-loading"));
    setSubmitLoading(false);
    clearError();
    var items = (data && data.results) || [];
    totalBadge && (totalBadge.textContent = String(items.length));
    if (dur && data && data.duration_ms != null) {
      dur.textContent = data.duration_ms + " ms";
    }
    if (items.length === 0) {
      grid.innerHTML = "";
      show(empty);
      return;
    }
    hide(empty);
    grid.innerHTML = items.map(renderResultCard).join("");
  }

  function setSubmitLoading(loading) {
    var btn = $("cografi-search-submit");
    if (btn) {
      btn.disabled = !!loading;
      var searchIcon = btn.querySelector('[data-cografi-submit-icon="search"]');
      var spinIcon = btn.querySelector('[data-cografi-submit-icon="spinner"]');
      if (searchIcon) searchIcon.classList.toggle("hidden", !!loading);
      if (spinIcon) spinIcon.classList.toggle("hidden", !loading);
    }
    var idleHint = $("cografi-search-hint");
    var loadHint = $("cografi-search-hint-loading");
    if (idleHint) idleHint.classList.toggle("hidden", !!loading);
    if (loadHint) loadHint.classList.toggle("hidden", !loading);
  }

  // ---------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------

  function buildFormData() {
    var fd = new FormData();
    var q = (($("cografi-search-input") || {}).value || "").trim();
    var region = (($("cografi-search-region") || {}).value || "").trim();
    var giType = (($("cografi-search-gi-type") || {}).value || "").trim();
    var appNo = (($("cografi-search-application-no") || {}).value || "").trim();
    var regNo = (($("cografi-search-registration-no") || {}).value || "").trim();
    var dfrom = (($("cografi-search-date-from") || {}).value || "").trim();
    var dto = (($("cografi-search-date-to") || {}).value || "").trim();
    var sectionKeys = selectedSectionKeys();
    var includeAdmin = !!($("cografi-search-include-admin") && $("cografi-search-include-admin").checked);

    if (q) fd.append("query", q);
    if (region) fd.append("region", region);
    if (giType) fd.append("gi_type", giType);
    if (appNo) fd.append("application_no", appNo);
    if (regNo) fd.append("registration_no", regNo);
    if (dfrom) fd.append("date_from", dfrom);
    if (dto) fd.append("date_to", dto);
    if (sectionKeys.length) fd.append("section_keys", sectionKeys.join(","));
    if (includeAdmin) fd.append("include_admin", "true");

    var imgInp = $("cografi-search-image");
    if (imgInp && imgInp.files && imgInp.files.length > 0) {
      fd.append("image", imgInp.files[0]);
    }
    fd.append("limit", "20");
    return fd;
  }

  function doSearch() {
    if (!hasQuery() && !hasFilters() && !hasImage()) {
      setStatus(t("cografi_search.empty_query_status", "Enter a query, region, or filter"), "error");
      return;
    }
    var card = $("cografi-search-results-card");
    var loading = $("cografi-search-loading");
    show(card);
    show(loading);
    hide($("cografi-search-empty"));
    clearError();
    setStatus("");
    setSubmitLoading(true);

    var headers = {};
    var token = getAuthToken();
    if (token) headers["Authorization"] = "Bearer " + token;

    var q = (($("cografi-search-input") || {}).value || "").trim();
    if (q) pushHistory(q);

    fetch(API_QUICK, { method: "POST", headers: headers, body: buildFormData() })
      .then(function (r) {
        if (r.status === 429) {
          showError(t("cografi_search.quota_exceeded", "Daily search quota exceeded"));
          hide(loading);
          setSubmitLoading(false);
          throw new Error("quota");
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) { renderResults(data); })
      .catch(function (err) {
        hide(loading);
        setSubmitLoading(false);
        if (err && err.message === "quota") return;
        showError(t("cografi_search.error_generic", "Search failed"));
      });
  }

  // ---------------------------------------------------------------
  // Wire-up (document-level delegation)
  // ---------------------------------------------------------------

  // -----------------------------------------------------------------
  // Add to cografi watchlist (called from result card delegation).
  // POSTs a watch_type=reference watch — the server clones the
  // record's text_embedding into reference_embedding so the scanner
  // has something to cosine-against. Mirrors the design + patent
  // search "Add to watchlist" flow.
  // -----------------------------------------------------------------
  async function addCografiToWatchlist(btn, payload) {
    if (!payload || !payload.reference_record_id) return;
    var originalLabel = btn.innerHTML;
    btn.disabled = true;
    btn.style.opacity = "0.6";
    try {
      var headers = { "Content-Type": "application/json" };
      var token = getAuthToken();
      if (token) headers["Authorization"] = "Bearer " + token;
      var resp = await fetch("/api/v1/cografi-watchlist", {
        method: "POST", headers: headers, body: JSON.stringify(payload),
      });
      if (resp.ok) {
        btn.outerHTML = '<span class="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg" ' +
          'style="background:#dcfce7;color:#166534">' +
          '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>' +
          escapeHtml(t("watchlist.already_watching", "Already watching")) + '</span>';
        if (window.AppToast && typeof window.AppToast.success === "function") {
          window.AppToast.success(t("watchlist.add_to_watchlist", "Added to watchlist"));
        }
        if (typeof window.showDashboardTab === "function") {
          window.showDashboardTab("cografi-watchlist");
        }
      } else if (resp.status === 401) {
        btn.innerHTML = originalLabel;
        btn.disabled = false;
        btn.style.opacity = "";
        if (window.AppAuth && typeof window.AppAuth.requireLogin === "function") {
          window.AppAuth.requireLogin();
        }
      } else {
        var detail = null;
        try { detail = (await resp.json()).detail; } catch (e) {}
        var msg = (typeof detail === "string" ? detail : (detail && (detail.message || detail.message_en))) || "Failed";
        btn.innerHTML = originalLabel;
        btn.disabled = false;
        btn.style.opacity = "";
        if (window.AppToast) window.AppToast.error(msg);
      }
    } catch (e) {
      btn.innerHTML = originalLabel;
      btn.disabled = false;
      btn.style.opacity = "";
      if (window.AppToast) window.AppToast.error(t("cografi_search.error_network", "Network error"));
    }
  }

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;
      // Submit button
      if (t_.closest && t_.closest("#cografi-search-submit")) {
        ev.preventDefault();
        hideDropdown();
        doSearch();
        return;
      }
      // Clear button on text input
      if (t_.closest && t_.closest("#cografi-search-input-clear")) {
        var inp = $("cografi-search-input");
        if (inp) inp.value = "";
        $("cografi-search-input-clear").classList.add("hidden");
        autocompleteData = { names: [], regions: [] };
        hideDropdown();
        return;
      }
      // Autocomplete pick: name -> fill query + search; region -> fill region + open filters
      var ac = t_.closest && t_.closest("[data-cografi-autocomplete]");
      if (ac) {
        var kind = ac.getAttribute("data-ac-kind");
        var val = ac.getAttribute("data-ac-value") || "";
        if (kind === "region") {
          var rInp = $("cografi-search-region");
          if (rInp) rInp.value = val;
        } else {
          var qInp = $("cografi-search-input");
          if (qInp) qInp.value = val;
        }
        hideDropdown();
        doSearch();
        return;
      }
      // History items
      var histItem = t_.closest && t_.closest("[data-cografi-history-item]");
      if (histItem) {
        var removeBtn = t_.closest("[data-cografi-history-remove]");
        if (removeBtn) {
          removeFromHistory(removeBtn.getAttribute("data-history-value") || "");
          renderDropdown();
          return;
        }
        var val2 = histItem.getAttribute("data-history-value") || "";
        var inp2 = $("cografi-search-input");
        if (inp2) inp2.value = val2;
        hideDropdown();
        doSearch();
        return;
      }
      if (t_.closest && t_.closest("#cografi-search-history-clear-all")) {
        clearAllHistory();
        renderDropdown();
        return;
      }
      // Add to cografi watchlist (reference-watch on this row's id).
      var wlBtn = t_.closest && t_.closest("[data-cografi-add-watchlist]");
      if (wlBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        var raw = wlBtn.getAttribute("data-payload") || "";
        var payload = null;
        try { payload = JSON.parse(raw); } catch (_) { payload = null; }
        if (payload) addCografiToWatchlist(wlBtn, payload);
        return;
      }

      // Portfolio click-through (applicant / agent). Stop propagation
      // so the card header doesn't also collapse.
      var portTrig = t_.closest && t_.closest("[data-portfolio-trigger]");
      if (portTrig) {
        ev.preventDefault();
        ev.stopPropagation();
        var pkind = portTrig.getAttribute("data-portfolio-trigger");
        try {
          if (pkind === "cografi-applicant" && typeof window.openCografiApplicantPortfolio === "function") {
            window.openCografiApplicantPortfolio(
              portTrig.getAttribute("data-holder-id") || "",
              portTrig,
            );
          } else if (pkind === "cografi-agent" && typeof window.openCografiAgentPortfolio === "function") {
            window.openCografiAgentPortfolio(
              portTrig.getAttribute("data-agent-name") || "",
              portTrig,
            );
          }
        } catch (_) { /* swallow — best-effort */ }
        return;
      }

      // Result card: toggle expand/collapse on header click. Skip if
      // the click was on a button/link/inner-toggle so action buttons
      // still work as expected.
      var hdr = t_.closest && t_.closest("[data-cografi-card-header]");
      if (hdr && !t_.closest("button, a, [data-usage-toggle], [data-cd-open]")) {
        var card = hdr.closest("article[data-cografi-card-expanded]");
        if (card) {
          var details = card.querySelector("[data-cografi-card-details]");
          var chevron = card.querySelector("[data-cografi-card-chevron]");
          var label = card.querySelector("[data-cografi-card-expand-label]");
          var nowExpanded = card.getAttribute("data-cografi-card-expanded") !== "true";
          card.setAttribute("data-cografi-card-expanded", nowExpanded ? "true" : "false");
          if (details) {
            if (nowExpanded) details.removeAttribute("hidden");
            else details.setAttribute("hidden", "");
          }
          if (chevron) chevron.style.transform = nowExpanded ? "rotate(180deg)" : "";
          if (label) {
            label.textContent = nowExpanded
              ? t("cografi_search.hide_details", "Hide details")
              : t("cografi_search.expand_details", "Show details");
          }
        }
        return;
      }

      // Usage description show-more / show-less toggle
      var usageToggle = t_.closest && t_.closest("[data-usage-toggle]");
      if (usageToggle) {
        var cardId = usageToggle.getAttribute("data-usage-toggle");
        var prev = document.querySelector('[data-usage-preview="' + cardId + '"]');
        var full = document.querySelector('[data-usage-full="' + cardId + '"]');
        if (prev && full) {
          var showingFull = !full.classList.contains("hidden");
          if (showingFull) {
            full.classList.add("hidden");
            prev.classList.remove("hidden");
            usageToggle.textContent = t("cografi_search.show_more", "Show more");
          } else {
            full.classList.remove("hidden");
            prev.classList.add("hidden");
            usageToggle.textContent = t("cografi_search.show_less", "Show less");
          }
        }
        return;
      }
      // Click outside dropdown -> hide
      if (!t_.closest("#cografi-search-input") &&
          !t_.closest("#cografi-search-history")) {
        hideDropdown();
      }
    });

    document.addEventListener("input", function (ev) {
      if (!ev.target) return;
      if (ev.target.id === "cografi-search-input") {
        var hasV = (ev.target.value || "").length > 0;
        var clearBtn = $("cografi-search-input-clear");
        if (clearBtn) clearBtn.classList.toggle("hidden", !hasV);
        onQueryInput();
      }
    });

    document.addEventListener("focusin", function (ev) {
      if (!ev.target) return;
      if (ev.target.id === "cografi-search-input") {
        renderDropdown();
      }
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter") return;
      if (ev.target && (ev.target.id === "cografi-search-input" ||
                        ev.target.id === "cografi-search-region" ||
                        ev.target.id === "cografi-search-application-no" ||
                        ev.target.id === "cografi-search-registration-no" ||
                        ev.target.id === "cografi-search-date-from" ||
                        ev.target.id === "cografi-search-date-to")) {
        ev.preventDefault();
        hideDropdown();
        doSearch();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
