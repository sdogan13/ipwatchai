/**
 * Patent / Faydalı Model search tab driver.
 *
 * Mirrors design_search.js patterns (vanilla JS, document-level event
 * delegation, localStorage history, AppI18n + AppAuth integration) but
 * tailored for the patent search shape:
 *
 *   - No image upload (figures aren't part of v1 search UX)
 *   - IPC autocomplete combobox over /api/v1/patent-search/ipc-autocomplete
 *   - Holder + date range + kind code filters
 *   - Different result-card shape: title, IPC chips, holder, dates,
 *     publication_no, kind code
 */
(function () {
  "use strict";

  var API_QUICK = "/api/v1/patent-search/quick";
  var API_IPC_AUTOCOMPLETE = "/api/v1/patent-search/ipc-autocomplete";
  var HISTORY_KEY = "patent_search_history";
  var HISTORY_MAX = 20;
  var HISTORY_SUGGEST = 10;

  var ipcSelected = []; // current IPC chip values
  var ipcDebounceTimer = null;

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
    var el = $("patent-search-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color =
      kind === "error" ? "var(--color-text-error,#dc2626)" :
      kind === "ok"    ? "var(--color-success,#059669)"   :
                          "var(--color-text-muted)";
  }

  function clearError() {
    var err = $("patent-search-error");
    if (err) { err.textContent = ""; hide(err); }
  }

  function showError(text) {
    var err = $("patent-search-error");
    if (!err) return;
    err.textContent = text || t("patent_search.error_generic", "Search failed");
    show(err);
  }

  function hasQuery() {
    var q = ($("patent-search-input") || {}).value || "";
    return q.trim().length > 0;
  }

  function hasFilters() {
    return (
      ipcSelected.length > 0 ||
      (($("patent-search-holder") || {}).value || "").trim().length > 0 ||
      (($("patent-search-date-from") || {}).value || "") !== "" ||
      (($("patent-search-date-to") || {}).value || "") !== "" ||
      (($("patent-search-kind-code") || {}).value || "") !== ""
    );
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
    var q = (($("patent-search-input") || {}).value || "").trim().toLowerCase();
    var arr = loadHistory();
    if (!q) return arr.slice(0, HISTORY_SUGGEST);
    return arr.filter(function (h) { return h.toLowerCase().indexOf(q) !== -1; })
              .slice(0, HISTORY_SUGGEST);
  }

  function renderHistoryDropdown() {
    var dropdown = $("patent-search-history");
    var listEl = $("patent-search-history-list");
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
        '<div data-patent-history-item ' +
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
          '<span data-patent-history-remove class="shrink-0 p-1 rounded hover:opacity-70" ' +
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
  function hideHistory() { hide($("patent-search-history")); }

  // ---------------------------------------------------------------
  // IPC autocomplete
  // ---------------------------------------------------------------

  function renderIpcChips() {
    var box = $("patent-search-ipc-chips");
    if (!box) return;
    if (ipcSelected.length === 0) { box.innerHTML = ""; return; }
    box.innerHTML = ipcSelected.map(function (code) {
      var safe = escapeHtml(code);
      return (
        '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-primary)">' +
          safe +
          '<button type="button" data-ipc-remove="' + safe + '" ' +
          'class="hover:opacity-70" style="color:var(--color-text-faint)">×</button>' +
        '</span>'
      );
    }).join("");
  }

  function addIpc(code) {
    var c = String(code || "").trim().toUpperCase();
    if (!c) return;
    if (ipcSelected.indexOf(c) === -1) ipcSelected.push(c);
    renderIpcChips();
  }
  function removeIpc(code) {
    ipcSelected = ipcSelected.filter(function (c) { return c !== code; });
    renderIpcChips();
  }

  function renderIpcDropdown(items) {
    var dd = $("patent-search-ipc-dropdown");
    if (!dd) return;
    if (!items || items.length === 0) {
      dd.innerHTML = "";
      hide(dd);
      return;
    }
    var lang = (window.AppI18n && window.AppI18n.lang) ? window.AppI18n.lang : "en";
    dd.innerHTML = items.map(function (item) {
      var code = escapeHtml(item.code || "");
      var desc = "";
      if (lang === "tr" && item.description_tr) desc = escapeHtml(item.description_tr);
      else if (item.description_en) desc = escapeHtml(item.description_en);
      return (
        '<div data-ipc-pick="' + code + '" ' +
        'class="px-3 py-2 cursor-pointer text-sm transition-colors" ' +
        'style="color:var(--color-text-primary)" ' +
        'onmouseover="this.style.background=\'var(--color-bg-muted)\'" ' +
        'onmouseout="this.style.background=\'\'">' +
          '<span class="font-mono font-medium">' + code + '</span>' +
          (desc ? ('<span class="ml-2 text-xs" style="color:var(--color-text-muted)">' + desc + '</span>') : '') +
        '</div>'
      );
    }).join("");
    show(dd);
  }

  function fetchIpcAutocomplete(prefix) {
    var url = API_IPC_AUTOCOMPLETE + "?q=" + encodeURIComponent(prefix);
    var headers = {};
    var token = getAuthToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    fetch(url, { headers: headers })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (data) { renderIpcDropdown((data && data.items) || []); })
      .catch(function () { renderIpcDropdown([]); });
  }

  function onIpcInput() {
    var v = (($("patent-search-ipc") || {}).value || "").trim();
    if (ipcDebounceTimer) clearTimeout(ipcDebounceTimer);
    if (v.length < 1) {
      renderIpcDropdown([]);
      return;
    }
    ipcDebounceTimer = setTimeout(function () { fetchIpcAutocomplete(v); }, 180);
  }

  // ---------------------------------------------------------------
  // Result rendering
  // ---------------------------------------------------------------

  // Per-signal similarity bar for the card breakdown row
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

  function _abstractBlock(rawAbstract, cardId) {
    if (!rawAbstract) return "";
    var safe = escapeHtml(rawAbstract);
    // Short abstracts: just inline. Long ones: line-clamp + toggle.
    var COLLAPSE_AT = 280;
    if (safe.length <= COLLAPSE_AT) {
      return '<p class="text-xs leading-relaxed mt-2" style="color:var(--color-text-secondary)">' +
             safe + '</p>';
    }
    var preview = safe.slice(0, COLLAPSE_AT) + '…';
    return (
      '<div class="text-xs leading-relaxed mt-2" style="color:var(--color-text-secondary)">' +
        '<span data-abstract-preview="' + cardId + '">' + preview + '</span>' +
        '<span data-abstract-full="' + cardId + '" class="hidden">' + safe + '</span>' +
        ' <button type="button" data-abstract-toggle="' + cardId + '" ' +
        'class="ml-1 text-xs font-medium hover:underline" style="color:var(--color-primary)">' +
        escapeHtml(t("patent_search.show_more", "Show more")) +
        '</button>' +
      '</div>'
    );
  }

  function renderResultCard(item) {
    var cardId = "ps-" + (item.id || Math.random().toString(36).slice(2, 9));
    var title = escapeHtml(item.title || t("patent_search.untitled", "Untitled"));
    var pubNo = escapeHtml(item.publication_no || item.application_no || "");
    var kind = escapeHtml(item.kind_code || "");
    var holderName = item.holder ? escapeHtml(item.holder.name || "") : "";
    var holderCountry = item.holder ? escapeHtml(item.holder.country || "") : "";
    var appDate = escapeHtml(item.application_date || "");
    var pubDate = escapeHtml(item.publication_date || "");
    var bulletinNo = escapeHtml(item.bulletin_no || "");
    var bulletinDate = escapeHtml(item.bulletin_date || "");
    var inventors = item.inventors || [];
    var attorney = item.attorney || null;
    var bd = item.similarity_breakdown || {};
    var sim = item.similarity != null ? Number(item.similarity).toFixed(0) : "";

    // ALL IPC classes — no truncation. Patent users frequently care about
    // the full classification spread.
    var ipcChips = (item.ipc_classes || []).map(function (c) {
      return '<span class="inline-block px-2 py-0.5 rounded text-xs font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
             escapeHtml(c) + '</span>';
    }).join("");

    var inventorsHtml = "";
    if (inventors.length > 0) {
      var visible = inventors.slice(0, 3).map(escapeHtml).join(", ");
      var extra = inventors.length > 3 ? ' <span style="color:var(--color-text-faint)">+' +
                  (inventors.length - 3) + '</span>' : '';
      inventorsHtml = '<div class="text-xs mt-1.5"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("patent_search.inventors_label", "Inventors")) + ':</span> ' +
        '<span style="color:var(--color-text-secondary)">' + visible + extra + '</span></div>';
    }

    var attorneyHtml = "";
    if (attorney && (attorney.name || attorney.firm)) {
      var aName = escapeHtml(attorney.name || "");
      var aFirm = escapeHtml(attorney.firm || "");
      var aText = aName + (aName && aFirm ? " · " : "") + aFirm;
      attorneyHtml = '<div class="text-xs mt-1"><span style="color:var(--color-text-faint)">' +
        escapeHtml(t("patent_search.attorney_label", "Attorney")) + ':</span> ' +
        '<span style="color:var(--color-text-secondary)">' + aText + '</span></div>';
    }

    var bulletinHtml = "";
    if (bulletinNo) {
      bulletinHtml = '<span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" ' +
        'style="background:var(--color-bg-muted);color:var(--color-text-faint)">' +
        escapeHtml(t("patent_search.bulletin_label", "Bulletin")) + ' ' + bulletinNo +
        (bulletinDate ? ' · ' + bulletinDate : '') + '</span>';
    }

    var bdHtml = "";
    if (bd && (bd.text || bd.embedding)) {
      bdHtml =
        '<div class="mt-2 space-y-1 pt-2" style="border-top:1px solid var(--color-border)">' +
          (bd.text != null ? _signalBar(t("patent_search.score_text", "Text"), bd.text) : "") +
          (bd.embedding != null ? _signalBar(t("patent_search.score_embedding", "Semantic"), bd.embedding) : "") +
        '</div>';
    }

    return (
      '<div class="rounded-lg border p-4 transition-all hover:shadow-md cursor-pointer" ' +
      'data-patent-card-id="' + cardId + '" ' +
      'data-pd-open="' + escapeHtml(item.id || "") + '" ' +
      'style="background:var(--color-bg-card);border-color:var(--color-border)">' +
        '<div class="flex items-start justify-between gap-2 mb-2">' +
          '<h4 class="text-sm font-semibold leading-snug" style="color:var(--color-text-primary)">' +
            title +
          '</h4>' +
          (sim ? ('<span class="shrink-0 text-xs font-medium px-2 py-0.5 rounded-full" ' +
                  'style="background:var(--color-primary);color:white">' + sim + '%</span>') : '') +
        '</div>' +
        (pubNo ? ('<p class="text-xs font-mono mb-1" style="color:var(--color-text-muted)">' +
                  pubNo + (kind ? (' <span class="ml-1">' + kind + '</span>') : '') + '</p>') : '') +
        (holderName ? ('<p class="text-xs mb-2" style="color:var(--color-text-primary)">' +
                       holderName + (holderCountry ? (' <span style="color:var(--color-text-faint)">(' + holderCountry + ')</span>') : '') +
                       '</p>') : '') +
        (ipcChips ? ('<div class="flex flex-wrap gap-1 mb-2">' + ipcChips + '</div>') : '') +
        _abstractBlock(item.abstract, cardId) +
        inventorsHtml +
        attorneyHtml +
        '<div class="flex flex-wrap items-center gap-2 mt-2 text-xs" style="color:var(--color-text-faint)">' +
          (appDate ? ('<span>' + escapeHtml(t("patent_search.filed", "Filed")) + ': ' + appDate + '</span>') : '') +
          (pubDate ? ('<span>' + escapeHtml(t("patent_search.published", "Pub")) + ': ' + pubDate + '</span>') : '') +
          bulletinHtml +
        '</div>' +
        bdHtml +
      '</div>'
    );
  }

  function renderResults(data) {
    var card = $("patent-search-results-card");
    var grid = $("patent-search-grid");
    var empty = $("patent-search-empty");
    var totalBadge = $("patent-search-total-badge");
    var dur = $("patent-search-duration");
    if (!card || !grid) return;
    show(card);
    hide($("patent-search-loading"));
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

  // ---------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------

  function hasImage() {
    var inp = $("patent-search-image");
    return !!(inp && inp.files && inp.files.length > 0);
  }

  function buildFormData() {
    var fd = new FormData();
    var q = (($("patent-search-input") || {}).value || "").trim();
    var holder = (($("patent-search-holder") || {}).value || "").trim();
    var dfrom = (($("patent-search-date-from") || {}).value || "").trim();
    var dto = (($("patent-search-date-to") || {}).value || "").trim();
    var kind = (($("patent-search-kind-code") || {}).value || "").trim();
    if (q) fd.append("query", q);
    if (ipcSelected.length) fd.append("ipc", ipcSelected.join(","));
    if (holder) fd.append("holder", holder);
    if (dfrom) fd.append("date_from", dfrom);
    if (dto) fd.append("date_to", dto);
    if (kind) fd.append("kind_code", kind);
    var imgInp = $("patent-search-image");
    if (imgInp && imgInp.files && imgInp.files.length > 0) {
      fd.append("image", imgInp.files[0]);
    }
    fd.append("limit", "20");
    return fd;
  }

  function doSearch() {
    if (!hasQuery() && !hasFilters() && !hasImage()) {
      setStatus(t("patent_search.empty_query_status", "Enter a query, filter, or upload a figure"), "error");
      return;
    }
    var card = $("patent-search-results-card");
    var loading = $("patent-search-loading");
    show(card);
    show(loading);
    hide($("patent-search-empty"));
    clearError();
    setStatus("");

    var headers = {};
    var token = getAuthToken();
    if (token) headers["Authorization"] = "Bearer " + token;

    var q = (($("patent-search-input") || {}).value || "").trim();
    if (q) pushHistory(q);

    fetch(API_QUICK, { method: "POST", headers: headers, body: buildFormData() })
      .then(function (r) {
        if (r.status === 429) {
          return r.json().then(function (d) {
            showError(t("patent_search.quota_exceeded", "Daily search quota exceeded"));
            hide(loading);
            throw new Error("quota");
          });
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) { renderResults(data); })
      .catch(function (err) {
        hide(loading);
        if (err && err.message === "quota") return; // already shown
        showError(t("patent_search.error_generic", "Search failed"));
      });
  }

  // ---------------------------------------------------------------
  // Wire-up (document-level delegation)
  // ---------------------------------------------------------------

  function wire() {
    document.addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t) return;
      // Submit button
      if (t.closest && t.closest("#patent-search-submit")) {
        ev.preventDefault();
        hideHistory();
        doSearch();
        return;
      }
      // Clear button on text input
      if (t.closest && t.closest("#patent-search-input-clear")) {
        var inp = $("patent-search-input");
        if (inp) inp.value = "";
        $("patent-search-input-clear").classList.add("hidden");
        hideHistory();
        return;
      }
      // History items
      var histItem = t.closest && t.closest("[data-patent-history-item]");
      if (histItem) {
        var removeBtn = t.closest("[data-patent-history-remove]");
        if (removeBtn) {
          removeFromHistory(removeBtn.getAttribute("data-history-value") || "");
          renderHistoryDropdown();
          return;
        }
        var val = histItem.getAttribute("data-history-value") || "";
        var inp2 = $("patent-search-input");
        if (inp2) inp2.value = val;
        hideHistory();
        doSearch();
        return;
      }
      if (t.closest && t.closest("#patent-search-history-clear-all")) {
        clearAllHistory();
        renderHistoryDropdown();
        return;
      }
      // IPC dropdown picks
      var pick = t.closest && t.closest("[data-ipc-pick]");
      if (pick) {
        addIpc(pick.getAttribute("data-ipc-pick") || "");
        var ipcInp = $("patent-search-ipc");
        if (ipcInp) ipcInp.value = "";
        hide($("patent-search-ipc-dropdown"));
        return;
      }
      // IPC chip remove
      if (t.matches && t.matches("[data-ipc-remove]")) {
        removeIpc(t.getAttribute("data-ipc-remove") || "");
        return;
      }
      // Abstract show-more / show-less toggle
      var absToggle = t.closest && t.closest("[data-abstract-toggle]");
      if (absToggle) {
        var cardId = absToggle.getAttribute("data-abstract-toggle");
        var prev = document.querySelector('[data-abstract-preview="' + cardId + '"]');
        var full = document.querySelector('[data-abstract-full="' + cardId + '"]');
        if (prev && full) {
          var showingFull = !full.classList.contains("hidden");
          if (showingFull) {
            full.classList.add("hidden");
            prev.classList.remove("hidden");
            absToggle.textContent = t("patent_search.show_more", "Show more");
          } else {
            full.classList.remove("hidden");
            prev.classList.add("hidden");
            absToggle.textContent = t("patent_search.show_less", "Show less");
          }
        }
        return;
      }
      // Click outside IPC -> hide dropdown
      if (!t.closest("#patent-search-ipc") &&
          !t.closest("#patent-search-ipc-dropdown")) {
        hide($("patent-search-ipc-dropdown"));
      }
      // Click outside history -> hide
      if (!t.closest("#patent-search-input") &&
          !t.closest("#patent-search-history")) {
        hideHistory();
      }
    });

    document.addEventListener("input", function (ev) {
      if (!ev.target) return;
      if (ev.target.id === "patent-search-input") {
        var hasV = (ev.target.value || "").length > 0;
        var clearBtn = $("patent-search-input-clear");
        if (clearBtn) clearBtn.classList.toggle("hidden", !hasV);
        renderHistoryDropdown();
      } else if (ev.target.id === "patent-search-ipc") {
        onIpcInput();
      }
    });

    document.addEventListener("focusin", function (ev) {
      if (!ev.target) return;
      if (ev.target.id === "patent-search-input") {
        renderHistoryDropdown();
      }
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter") return;
      if (ev.target && (ev.target.id === "patent-search-input" ||
                        ev.target.id === "patent-search-holder" ||
                        ev.target.id === "patent-search-date-from" ||
                        ev.target.id === "patent-search-date-to")) {
        ev.preventDefault();
        hideHistory();
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
