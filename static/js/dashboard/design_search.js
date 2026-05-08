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

  // Locarno picker state — module scope, populated on first panel open
  var _locarnoCatalogue = null;       // [{class_number, name_tr, name_en}, ...]
  var _locarnoCataloguePromise = null;
  var _locarnoSelected = [];          // sorted top-level codes

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
  // Result rendering (unchanged shape from prior version)
  // ---------------------------------------------------------------

  function renderResultCard(row) {
    var title = row.product_name_tr || row.product_name_en
              || row.application_no || row.registration_no || "—";
    var holder = row.holder && row.holder.name ? row.holder.name : "";
    var locarno = (row.locarno_classes || []).join(", ");
    var sim = typeof row.similarity === "number" ? row.similarity.toFixed(1) : "—";
    var imgUrl = row.image_url || "";
    var appLine = row.application_no
      ? row.application_no + (row.design_index ? " · #" + row.design_index : "")
      : (row.registration_no || "");
    var bulletinLabel = row.bulletin_no ? "Bülten " + row.bulletin_no : "";
    var statusLabel = row.current_status || "";

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

    var simBar =
      '<div class="w-full h-1.5 rounded-full overflow-hidden mt-2" style="background:var(--color-bg-muted)">' +
      '<div class="h-full bg-indigo-500" style="width:' + Math.min(100, Number(sim) || 0) + '%"></div>' +
      "</div>";

    return (
      '<article class="rounded-lg border p-3 transition-shadow hover:shadow" ' +
      'style="border-color:var(--color-border);background:var(--color-bg-card)" ' +
      'data-design-id="' + escapeHtml(row.id || "") + '">' +
        imgHtml +
        '<div class="mt-2 flex items-start justify-between gap-2">' +
          '<h4 class="text-sm font-semibold leading-snug" style="color:var(--color-text-primary)">' +
            escapeHtml(title) + "</h4>" +
          '<span class="text-xs font-mono shrink-0" style="color:var(--color-text-muted)">' +
            sim + "%</span>" +
        "</div>" +
        simBar +
        '<dl class="mt-2 space-y-1 text-xs" style="color:var(--color-text-secondary)">' +
          (appLine ? '<div><dt class="inline">' + escapeHtml(t("design_search.appno_label", "App")) +
            ':</dt> <dd class="inline font-mono">' + escapeHtml(appLine) + "</dd></div>" : "") +
          (holder ? '<div><dt class="inline">' + escapeHtml(t("design_search.holder_label", "Holder")) +
            ':</dt> <dd class="inline">' + escapeHtml(holder) + "</dd></div>" : "") +
          (locarno ? '<div><dt class="inline">' + escapeHtml(t("design_search.locarno_label", "Locarno")) +
            ':</dt> <dd class="inline">' + escapeHtml(locarno) + "</dd></div>" : "") +
          (bulletinLabel ? '<div><dt class="inline" style="color:var(--color-text-faint)">' +
            escapeHtml(bulletinLabel) + "</dt></div>" : "") +
          (statusLabel ? '<div><dt class="inline" style="color:var(--color-text-faint)">' +
            escapeHtml(statusLabel) + "</dt></div>" : "") +
        "</dl>" +
      "</article>"
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
  // runSearch / resetForm
  // ---------------------------------------------------------------

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
    }
  }

  function resetForm() {
    var q = $("design-search-input");
    var img = $("design-search-image");
    if (q) q.value = "";
    if (img) img.value = "";
    // Clear Locarno picker selection (also clears the hidden input)
    clearLocarnoSelection();
    setLocarnoPanelOpen(false);
    var aiInput = $("design-search-locarno-ai-input");
    if (aiInput) aiInput.value = "";
    renderLocarnoAiSuggestions([]);
    showLocarnoAiError("");
    // Notify Alpine drag-drop wrapper to clear its preview
    var dragRoot = q && q.closest && q.closest("[x-data]");
    if (dragRoot && dragRoot._x_dataStack && dragRoot._x_dataStack[0]) {
      try { dragRoot._x_dataStack[0].designClearImage(); } catch (e) {}
    }
    hideHistory();
    hide($("design-search-results-card"));
    clearError();
    setStatus("");
    updateClearInputBtnVisibility();
  }

  // ---------------------------------------------------------------
  // Locarno class picker
  // ---------------------------------------------------------------

  function loadLocarnoCatalogue() {
    if (_locarnoCatalogue) return Promise.resolve(_locarnoCatalogue);
    if (_locarnoCataloguePromise) return _locarnoCataloguePromise;
    _locarnoCataloguePromise = fetch(API_LOCARNO_LIST, { method: "GET" })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (payload) {
        _locarnoCatalogue = (payload && payload.items) || [];
        return _locarnoCatalogue;
      })
      .catch(function () { _locarnoCatalogue = []; return _locarnoCatalogue; });
    return _locarnoCataloguePromise;
  }

  function localizedLocarnoName(c) {
    var locale = (window.AppI18n && window.AppI18n.locale) || "tr";
    if (locale === "en") return c.name_en || c.name_tr || c.class_number;
    return c.name_tr || c.name_en || c.class_number;
  }

  function syncLocarnoHiddenInput() {
    var inp = $("design-search-locarno");
    if (inp) inp.value = _locarnoSelected.join(",");
  }

  function renderLocarnoChips() {
    var emptyLabel = $("design-search-locarno-empty-label");
    var chipsRow = $("design-search-locarno-chips");
    var countBadge = $("design-search-locarno-count");
    if (!chipsRow || !emptyLabel || !countBadge) return;
    if (_locarnoSelected.length === 0) {
      show(emptyLabel);
      hide(chipsRow);
      hide(countBadge);
      chipsRow.innerHTML = "";
      return;
    }
    hide(emptyLabel);
    show(chipsRow);
    show(countBadge);
    countBadge.textContent = (window.AppI18n && window.AppI18n.t)
      ? window.AppI18n.t("design_search.locarno_classes_selected", { count: _locarnoSelected.length })
      : (_locarnoSelected.length + " selected");
    var byNumber = {};
    (_locarnoCatalogue || []).forEach(function (c) { byNumber[c.class_number] = c; });
    chipsRow.innerHTML = _locarnoSelected.slice(0, 6).map(function (cn) {
      var meta = byNumber[cn];
      var name = meta ? localizedLocarnoName(meta) : "";
      var label = cn + (name ? " · " + name : "");
      return (
        '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" ' +
        'style="background:var(--color-primary-light);color:var(--color-primary)">' +
        escapeHtml(label) +
        '<span data-locarno-remove data-class-number="' + escapeHtml(cn) + '" ' +
        'class="ml-1 cursor-pointer hover:opacity-80" style="font-size:13px;line-height:1">×</span>' +
        '</span>'
      );
    }).join("") + (
      _locarnoSelected.length > 6
        ? '<span class="text-xs px-2 py-0.5 rounded-full" style="background:var(--color-bg-card);color:var(--color-text-muted)">+' + (_locarnoSelected.length - 6) + '</span>'
        : ""
    );
  }

  function renderLocarnoGrid() {
    var grid = $("design-search-locarno-grid");
    if (!grid) return;
    var items = _locarnoCatalogue || [];
    var selectedSet = {};
    _locarnoSelected.forEach(function (cn) { selectedSet[cn] = true; });
    grid.innerHTML = items.map(function (c) {
      var name = localizedLocarnoName(c);
      var isSel = !!selectedSet[c.class_number];
      var bg = isSel ? "var(--color-primary-light)" : "var(--color-bg-card)";
      var color = isSel ? "var(--color-primary)" : "var(--color-text-primary)";
      var border = isSel ? "var(--color-primary)" : "var(--color-border)";
      return (
        '<button type="button" data-locarno-toggle data-class-number="' + escapeHtml(c.class_number) + '" ' +
        'class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-all hover:opacity-90" ' +
        'style="background:' + bg + ';color:' + color + ';border:1px solid ' + border + '">' +
        '<span class="font-mono text-xs px-1.5 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' +
        escapeHtml(c.class_number) + '</span>' +
        '<span class="truncate">' + escapeHtml(name) + '</span>' +
        (isSel ? '<svg class="w-4 h-4 ml-auto shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="color:var(--color-primary)"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>' : '') +
        '</button>'
      );
    }).join("");
  }

  function toggleLocarno(cn) {
    if (!cn) return;
    var i = _locarnoSelected.indexOf(cn);
    if (i >= 0) _locarnoSelected.splice(i, 1);
    else _locarnoSelected.push(cn);
    _locarnoSelected.sort();
    syncLocarnoHiddenInput();
    renderLocarnoChips();
    renderLocarnoGrid();
  }

  function setLocarnoPanelOpen(open) {
    var panel = $("design-search-locarno-panel");
    var chevron = $("design-search-locarno-chevron");
    if (!panel) return;
    if (open) {
      show(panel);
      if (chevron) chevron.style.transform = "rotate(180deg)";
      loadLocarnoCatalogue().then(renderLocarnoGrid);
    } else {
      hide(panel);
      if (chevron) chevron.style.transform = "";
    }
  }

  function isLocarnoPanelOpen() {
    var panel = $("design-search-locarno-panel");
    return !!(panel && !panel.classList.contains("hidden"));
  }

  function clearLocarnoSelection() {
    _locarnoSelected = [];
    syncLocarnoHiddenInput();
    renderLocarnoChips();
    renderLocarnoGrid();
  }

  // ---- AI suggest ----

  function showLocarnoAiError(text) {
    var el = $("design-search-locarno-ai-error");
    if (!el) return;
    if (text) { el.textContent = text; show(el); }
    else { el.textContent = ""; hide(el); }
  }

  function setLocarnoAiBusy(busy) {
    var btn = $("design-search-locarno-ai-button");
    var label = $("design-search-locarno-ai-button-label");
    if (!btn) return;
    btn.disabled = !!busy;
    if (label) {
      label.textContent = busy
        ? t("design_search.locarno_ai_loading", "Suggesting…")
        : t("design_search.locarno_ai_button", "Suggest classes");
    }
  }

  function renderLocarnoAiSuggestions(suggestions) {
    var row = $("design-search-locarno-ai-suggestions");
    if (!row) return;
    if (!suggestions || suggestions.length === 0) {
      row.innerHTML = "";
      hide(row);
      return;
    }
    row.innerHTML = suggestions.map(function (s) {
      var name = localizedLocarnoName(s);
      var alreadySelected = _locarnoSelected.indexOf(s.class_number) >= 0;
      var bg = alreadySelected ? "var(--color-primary-light)" : "var(--color-bg-muted)";
      var color = alreadySelected ? "var(--color-primary)" : "var(--color-text-primary)";
      return (
        '<button type="button" data-locarno-suggest-add data-class-number="' + escapeHtml(s.class_number) + '" ' +
        'class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs hover:opacity-90 transition-opacity" ' +
        'style="background:' + bg + ';color:' + color + '" ' +
        (s.reason ? 'title="' + escapeHtml(s.reason) + '"' : '') + '>' +
        '<span class="font-mono">' + escapeHtml(s.class_number) + '</span>' +
        '<span>' + escapeHtml(name) + '</span>' +
        (alreadySelected ? '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>' : '<span class="text-xs">+</span>') +
        '</button>'
      );
    }).join("");
    show(row);
  }

  async function runLocarnoAiSuggest() {
    var input = $("design-search-locarno-ai-input");
    var description = (input && input.value || "").trim();
    if (description.length < 2) {
      showLocarnoAiError(t("design_search.error_invalid_input", "Provide a short description"));
      return;
    }
    showLocarnoAiError("");
    setLocarnoAiBusy(true);
    try {
      var headers = { "Content-Type": "application/json" };
      var token = getAuthToken();
      if (token) headers["Authorization"] = "Bearer " + token;
      var resp = await fetch(API_LOCARNO_SUGGEST, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({
          description: description,
          language: (window.AppI18n && window.AppI18n.locale) || "tr",
          count: 5,
        }),
      });
      var payload = null;
      try { payload = await resp.json(); } catch (e) {}
      if (!resp.ok) {
        if (resp.status === 401 || resp.status === 403) {
          showLocarnoAiError(t("design_search.error_auth", "Please sign in"));
        } else if (resp.status === 402) {
          showLocarnoAiError(t("design_search.locarno_ai_no_credits", "AI credits exhausted"));
        } else {
          var msg = (payload && payload.detail && (payload.detail.message || payload.detail.message_en)) ||
                    t("design_search.locarno_ai_error", "Could not generate suggestions");
          showLocarnoAiError(msg);
        }
        return;
      }
      // Make sure catalogue is loaded so localized names render
      await loadLocarnoCatalogue();
      renderLocarnoAiSuggestions((payload && payload.suggestions) || []);
    } catch (e) {
      showLocarnoAiError(t("design_search.error_network", "Network error"));
    } finally {
      setLocarnoAiBusy(false);
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

      // Reset (Sıfırla)
      if (t.closest("#design-search-reset")) {
        e.preventDefault();
        resetForm();
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

      // Locarno picker — toggle bar
      if (t.closest("#design-search-locarno-toggle")) {
        e.preventDefault();
        setLocarnoPanelOpen(!isLocarnoPanelOpen());
        return;
      }
      // Locarno chip remove (×) in collapsed bar
      var rmChip = t.closest("[data-locarno-remove]");
      if (rmChip) {
        e.preventDefault();
        e.stopPropagation();
        toggleLocarno(rmChip.getAttribute("data-class-number"));
        return;
      }
      // Locarno class toggle in expanded grid
      var grid = t.closest("[data-locarno-toggle]");
      if (grid) {
        e.preventDefault();
        toggleLocarno(grid.getAttribute("data-class-number"));
        return;
      }
      // AI suggest — submit
      if (t.closest("#design-search-locarno-ai-button")) {
        e.preventDefault();
        runLocarnoAiSuggest();
        return;
      }
      // AI suggestion chip click → add to selection
      var aiChip = t.closest("[data-locarno-suggest-add]");
      if (aiChip) {
        e.preventDefault();
        toggleLocarno(aiChip.getAttribute("data-class-number"));
        // Re-render suggestions so the "added" tick updates
        var row = $("design-search-locarno-ai-suggestions");
        if (row && !row.classList.contains("hidden")) {
          var current = Array.prototype.slice.call(row.querySelectorAll("[data-locarno-suggest-add]")).map(function (b) {
            var meta = (_locarnoCatalogue || []).filter(function (c) { return c.class_number === b.getAttribute("data-class-number"); })[0];
            return meta || { class_number: b.getAttribute("data-class-number") };
          });
          renderLocarnoAiSuggestions(current);
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
      if (t.id === "design-search-locarno-ai-input" && e.key === "Enter") {
        e.preventDefault();
        runLocarnoAiSuggest();
      }
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

    // Focus event for the text query (show history dropdown)
    document.addEventListener("focusin", function (e) {
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
