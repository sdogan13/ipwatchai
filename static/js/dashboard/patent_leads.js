/**
 * Patent leads driver — fed by /api/v1/patent-leads.
 *
 * Powers the Patent registry of Opposition Radar. Surfaces actionable
 * events from patent_events as a paginated list with category + holder
 * + watchlist-scope filters.
 *
 * Categories: lapse / transfer / license / rejected (chip strip wired
 * via window.switchPatentLeadsCategory).
 * Watchlist scope (optional) restricts to events on patents whose
 * holder matches the user's active 'holder' watchlist rows.
 */
(function () {
  "use strict";

  var API_LEADS = "/api/v1/patent-leads";
  var state = {
    initialized: false,
    page: 1,
    pageSize: 20,
    category: "lapse",
    watchlistScoped: false,
    holder: "",
    total: 0,
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

  function authFetch(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    var tk = token();
    if (tk) opts.headers["Authorization"] = "Bearer " + tk;
    return fetch(url, opts);
  }

  // ---------------------------------------------------------------
  // Stats summary (small chips above the feed)
  // ---------------------------------------------------------------

  function renderStatsChips(summary) {
    var box = $("patent-leads-stats");
    if (!box) return;
    var by = (summary && summary.by_category) || {};
    var labels = {
      lapse:    t("patent_leads.cat_lapse",    "Düşmüş"),
      transfer: t("patent_leads.cat_transfer", "Devir"),
      license:  t("patent_leads.cat_license",  "Lisans"),
      rejected: t("patent_leads.cat_rejected", "Reddedilen"),
    };
    var html = ["lapse", "transfer", "license", "rejected"].map(function (k) {
      var n = by[k] != null ? by[k] : "-";
      var active = state.category === k;
      var bg = active ? "var(--color-primary)" : "var(--color-bg-card)";
      var fg = active ? "white" : "var(--color-text-primary)";
      return (
        '<button data-pl-category="' + k + '" ' +
        'class="rounded-lg border p-2 text-xs flex flex-col items-start gap-0.5 transition-colors" ' +
        'style="background:' + bg + ';color:' + fg + ';border-color:var(--color-border)">' +
          '<span class="font-medium">' + escapeHtml(labels[k]) + '</span>' +
          '<span class="text-base font-bold">' + escapeHtml(String(n)) + '</span>' +
        '</button>'
      );
    }).join("");
    box.innerHTML = html;
  }

  function fetchSummary() {
    var url = API_LEADS + "/summary";
    if (state.watchlistScoped) url += "?watchlist_scoped=true";
    authFetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s) renderStatsChips(s); })
      .catch(function () {});
  }

  // ---------------------------------------------------------------
  // Card rendering
  // ---------------------------------------------------------------

  function severityForCategory(cat) {
    return ({lapse: "#dc2626", transfer: "#0891b2", license: "#059669", rejected: "#d97706"})[cat] || "#6b7280";
  }

  function renderLeadCard(item) {
    var holder = item.holder || {};
    var holderName = holder.name || "—";
    var holderCountry = holder.country || "";
    var title = item.title || "—";
    var pubNo = item.publication_no || item.application_no || "";
    var kind = item.kind_code || "";
    var bulletinDate = item.bulletin_date || "";
    var eventTypeLabel = item.event_type
      ? t("patent_leads.event_" + item.event_type.toLowerCase(), item.event_type)
      : "";
    var ipcChips = (item.ipc_classes || []).slice(0, 3).map(function (c) {
      return '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
             escapeHtml(c) + '</span>';
    }).join(" ");

    var openAttr = item.patent_id
      ? ' data-pd-open="' + escapeHtml(item.patent_id) + '" class="rounded-lg border p-3 cursor-pointer hover:shadow-md transition-all"'
      : ' class="rounded-lg border p-3"';
    return (
      '<div' + openAttr + ' ' +
      'style="background:var(--color-bg-card);border-color:var(--color-border)">' +
        '<div class="flex items-start justify-between gap-3 mb-1">' +
          '<div class="min-w-0 flex-1">' +
            '<div class="flex items-center gap-2 flex-wrap mb-0.5">' +
              '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium" ' +
              'style="background:' + severityForCategory(state.category) + ';color:white">' +
                escapeHtml(eventTypeLabel) + '</span>' +
              (bulletinDate
                ? '<span class="text-[10px]" style="color:var(--color-text-faint)">' +
                  escapeHtml(t("patent_leads.bulletin_date", "Bülten")) + ' ' + escapeHtml(bulletinDate) + '</span>'
                : '') +
            '</div>' +
            '<h4 class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' +
              escapeHtml(title) + '</h4>' +
            '<p class="text-xs font-mono" style="color:var(--color-text-muted)">' +
              escapeHtml(pubNo) + (kind ? ' <span class="ml-1">' + escapeHtml(kind) + '</span>' : '') +
            '</p>' +
            '<p class="text-xs mt-1" style="color:var(--color-text-secondary)">' +
              escapeHtml(holderName) +
              (holderCountry ? ' <span style="color:var(--color-text-faint)">(' + escapeHtml(holderCountry) + ')</span>' : '') +
            '</p>' +
            (ipcChips ? '<div class="flex flex-wrap gap-1 mt-1.5">' + ipcChips + '</div>' : '') +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function renderFeed(data) {
    var loading = $("patent-leads-loading");
    var empty = $("patent-leads-empty");
    var cards = $("patent-leads-cards");
    var pag = $("patent-leads-pagination");
    var info = $("patent-leads-total-info");
    var pageInfo = $("patent-leads-page-info");
    var prev = $("patent-leads-prev");
    var next = $("patent-leads-next");
    if (!cards) return;

    hide(loading);
    var items = (data && data.items) || [];
    state.total = (data && data.total) || 0;
    if (items.length === 0) {
      hide(cards);
      hide(pag);
      show(empty);
      cards.innerHTML = "";
      return;
    }
    hide(empty);
    cards.innerHTML = items.map(renderLeadCard).join("");
    show(cards);

    // Pagination
    var totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (info) info.textContent = state.total + " " + t("patent_leads.total_found", "fırsat");
    if (pageInfo) pageInfo.textContent = state.page + " / " + totalPages;
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= totalPages;
    show(pag);
  }

  // ---------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------

  function load(page) {
    state.page = Math.max(1, parseInt(page || 1, 10) || 1);
    var loading = $("patent-leads-loading");
    var cards = $("patent-leads-cards");
    var empty = $("patent-leads-empty");
    show(loading);
    hide(cards);
    hide(empty);

    var params = new URLSearchParams({
      category: state.category,
      page: String(state.page),
      page_size: String(state.pageSize),
    });
    if (state.watchlistScoped) params.set("watchlist_scoped", "true");
    if (state.holder && state.holder.length >= 2) params.set("holder", state.holder);

    authFetch(API_LEADS + "?" + params.toString())
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          hide(loading);
          var emptyEl = $("patent-leads-empty");
          if (emptyEl) {
            emptyEl.textContent = t("patent_leads.upgrade_required", "Bu özellik için yükseltme gerekli.");
            show(emptyEl);
          }
          return null;
        }
        return r.ok ? r.json() : Promise.reject(r);
      })
      .then(function (d) { if (d) { renderFeed(d); fetchSummary(); } })
      .catch(function () {
        hide(loading);
        var emptyEl = $("patent-leads-empty");
        if (emptyEl) {
          emptyEl.textContent = t("patent_leads.error", "Yükleme başarısız.");
          show(emptyEl);
        }
      });
  }

  // Public entry — called by the Patent registry x-init / $watch in _leads_panel.html
  window.loadPatentLeadsFeed = load;

  // Sub-category switcher — mirrors switchRadarMode for the Marka chips.
  // Wired up via inline onclick handlers on the chip strip in _leads_panel.html.
  var PATENT_CATS = {
    lapse:    "patent-cat-lapse",
    transfer: "patent-cat-transfer",
    license:  "patent-cat-license",
    rejected: "patent-cat-rejected",
  };
  window.switchPatentLeadsCategory = function (cat) {
    if (!PATENT_CATS.hasOwnProperty(cat)) return;
    state.category = cat;
    Object.keys(PATENT_CATS).forEach(function (key) {
      var btn = $(PATENT_CATS[key]);
      if (!btn) return;
      if (key === cat) {
        btn.style.background = "var(--color-primary)";
        btn.style.color = "white";
      } else {
        btn.style.background = "transparent";
        btn.style.color = "var(--color-text-secondary)";
      }
    });
    load(1);
  };

  // ---------------------------------------------------------------
  // Wire-up
  // ---------------------------------------------------------------

  function wire() {
    if (state.initialized) return;
    state.initialized = true;

    var scopeEl = $("patent-leads-watchlist-scope");
    if (scopeEl) scopeEl.addEventListener("change", function () {
      state.watchlistScoped = !!scopeEl.checked;
      load(1);
    });
    var holderEl = $("patent-leads-holder");
    if (holderEl) holderEl.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        state.holder = holderEl.value || "";
        load(1);
      }
    });
    var refreshEl = $("patent-leads-refresh");
    if (refreshEl) refreshEl.addEventListener("click", function () {
      state.holder = (holderEl && holderEl.value) || "";
      load(1);
    });
    var exportEl = $("patent-leads-export-csv");
    if (exportEl) exportEl.addEventListener("click", function () {
      // CSV download via direct GET — token in Authorization header is
      // not natively supported by anchor downloads, so route through
      // a fetch + blob to preserve the auth session.
      var params = new URLSearchParams({ category: state.category });
      if (state.watchlistScoped) params.set("watchlist_scoped", "true");
      if (state.holder && state.holder.length >= 2) params.set("holder", state.holder);
      authFetch("/api/v1/patent-leads/export.csv?" + params.toString())
        .then(function (r) {
          if (r.status === 403) {
            alert(t("patent_leads.upgrade_required", "Bu özellik için yükseltme gerekli."));
            return null;
          }
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.blob();
        })
        .then(function (blob) {
          if (!blob) return;
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = "patent_leads_" + state.category + ".csv";
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        })
        .catch(function () {
          alert(t("patent_leads.error", "Yükleme başarısız."));
        });
    });
    var prev = $("patent-leads-prev");
    if (prev) prev.addEventListener("click", function () { if (state.page > 1) load(state.page - 1); });
    var next = $("patent-leads-next");
    if (next) next.addEventListener("click", function () { load(state.page + 1); });

    // Stats chips clickable to switch category
    document.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest("[data-pl-category]");
      if (!btn) return;
      var cat = btn.getAttribute("data-pl-category");
      if (cat) {
        state.category = cat;
        if (catEl) catEl.value = cat;
        load(1);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
