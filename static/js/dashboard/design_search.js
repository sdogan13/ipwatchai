/**
 * Design search tab — driver for /api/v1/design-search/quick.
 *
 * Vanilla JS to avoid extending the 8K-line Alpine app.js with reactive
 * state. Mounts when the user clicks the "Tasarım Arama" tab; lazy-attaches
 * event listeners on first activation.
 */
(function () {
  "use strict";

  var API_QUICK = "/api/v1/design-search/quick";

  function $(id) { return document.getElementById(id); }
  function t(key, fallback) {
    if (window.AppI18n && typeof window.AppI18n.t === "function") {
      var v = window.AppI18n.t(key);
      if (v && v !== key) return v;
    }
    return fallback || key;
  }

  function getAuthToken() {
    // Mirrors the existing dashboard's token lookup.
    return (
      (window.localStorage && localStorage.getItem("access_token")) ||
      (window.sessionStorage && sessionStorage.getItem("access_token")) ||
      ""
    );
  }

  function show(el) { if (el) el.classList.remove("hidden"); }
  function hide(el) { if (el) el.classList.add("hidden"); }

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

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

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

  async function runSearch() {
    var query = ($("design-search-input") || {}).value || "";
    var locarno = ($("design-search-locarno") || {}).value || "";
    var imageInput = $("design-search-image");
    var hasImage = imageInput && imageInput.files && imageInput.files.length > 0;
    var hasQuery = query.trim().length > 0;
    if (!hasImage && !hasQuery) {
      setStatus(t("design_search.error_empty", "Provide a query or upload an image"), "error");
      return;
    }

    clearError();
    setStatus("");
    show($("design-search-results-card"));
    show($("design-search-loading"));
    hide($("design-search-grid"));
    hide($("design-search-empty"));

    var fd = new FormData();
    if (hasQuery) fd.append("query", query.trim());
    if (locarno.trim()) fd.append("locarno", locarno.trim());
    if (hasImage) fd.append("image", imageInput.files[0]);

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
    } catch (e) {
      showError(t("design_search.error_network", "Network error"));
    } finally {
      hide($("design-search-loading"));
    }
  }

  function resetForm() {
    var q = $("design-search-input");
    var l = $("design-search-locarno");
    var img = $("design-search-image");
    var clearBtn = $("design-search-image-clear");
    if (q) q.value = "";
    if (l) l.value = "";
    if (img) img.value = "";
    if (clearBtn) clearBtn.classList.add("hidden");
    hide($("design-search-results-card"));
    clearError();
    setStatus("");
  }

  var _wired = false;
  function wireOnce() {
    if (_wired) return;
    _wired = true;
    var submit = $("design-search-submit");
    var reset = $("design-search-reset");
    var input = $("design-search-input");
    var imgInput = $("design-search-image");
    var imgClear = $("design-search-image-clear");
    if (submit) submit.addEventListener("click", runSearch);
    if (reset) reset.addEventListener("click", resetForm);
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); runSearch(); }
      });
    }
    if (imgInput) {
      imgInput.addEventListener("change", function () {
        if (imgInput.files && imgInput.files.length > 0) {
          show(imgClear);
        } else {
          hide(imgClear);
        }
      });
    }
    if (imgClear) {
      imgClear.addEventListener("click", function () {
        if (imgInput) imgInput.value = "";
        hide(imgClear);
      });
    }
  }

  function initDesignSearchTab() {
    wireOnce();
    var input = $("design-search-input");
    if (input) setTimeout(function () { input.focus(); }, 60);
  }

  // Public surface
  window.initDesignSearchTab = initDesignSearchTab;
})();
