/**
 * Cografi (GI) detail modal — driver for the global #cografi-detail-modal.
 *
 * Public entry: window.AppCografiDetail.open(recordId)
 *
 * Hydrates from /api/v1/cografi/{record_id}; populates the modal sections;
 * shows loading/error states. All result cards on the cografi search +
 * (future) watchlist + alert surfaces call into AppCografiDetail.open
 * so this single modal is the only detail UX.
 *
 * Response shape:
 *   { record: {...}, holders: [...], change_requests: [...],
 *     figures: [...], related: [...] }
 */
(function () {
  "use strict";

  var API_DETAIL = "/api/v1/cografi/";
  var API_IMAGE_PREFIX = "/api/v1/cografi-image/";

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

  function fmt(d) { return d ? String(d) : "—"; }

  function authFetch(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    var tk = token();
    if (tk) opts.headers["Authorization"] = "Bearer " + tk;
    return fetch(url, opts);
  }

  function imageUrl(figure) {
    // Each figure row carries an `image_url` pre-computed by the detail
    // service. Fall back to building one from {bulletin_folder, image_path}
    // if not present.
    if (figure && figure.image_url) return figure.image_url;
    if (figure && figure.bulletin_folder && figure.image_path) {
      return API_IMAGE_PREFIX +
        encodeURIComponent(figure.bulletin_folder) + "/" + figure.image_path;
    }
    return "";
  }

  // ---------------------------------------------------------------
  // Render sections
  // ---------------------------------------------------------------

  function renderHeader(rec) {
    var titleEl = $("cd-title");
    if (titleEl) titleEl.textContent = rec.name || "—";

    var sec = $("cd-section-badge");
    if (sec) {
      var sk = rec.section_key || "";
      sec.textContent = sk ? t("cografi_detail.section_" + sk.replace(/^article_/, "art").replace(/_change_requests$/, "_change_requests"), sk) : "";
      sec.style.display = sk ? "" : "none";
    }
    var giType = $("cd-gi-type-badge");
    if (giType) {
      giType.textContent = rec.gi_type || "";
      giType.style.display = rec.gi_type ? "" : "none";
    }
    var appNo = $("cd-app-no");
    if (appNo) {
      appNo.textContent = rec.application_no || "";
      appNo.style.display = rec.application_no ? "" : "none";
    }
    var regNo = $("cd-reg-no");
    if (regNo) {
      regNo.textContent = rec.registration_no != null ? "#" + rec.registration_no : "";
      regNo.style.display = rec.registration_no != null ? "" : "none";
    }
  }

  function renderDates(rec) {
    if ($("cd-application-date")) $("cd-application-date").textContent = fmt(rec.application_date);
    if ($("cd-registration-date")) $("cd-registration-date").textContent = fmt(rec.registration_date);
    var bul = (rec.bulletin_no || "—") + (rec.bulletin_date ? " · " + rec.bulletin_date : "");
    if ($("cd-bulletin")) $("cd-bulletin").textContent = bul;
  }

  function renderRegionAndProduct(rec) {
    if ($("cd-region")) $("cd-region").textContent = rec.geographical_boundary || "—";
    if ($("cd-product-group")) $("cd-product-group").textContent = rec.product_group || "—";
  }

  function renderUsage(rec) {
    var el = $("cd-usage-description");
    if (el) el.textContent = rec.usage_description || t("cografi_detail.no_description", "No description.");
  }

  function renderBodySections(rec) {
    var el = $("cd-body-sections");
    if (!el) return;
    var sections = rec.body_sections || {};
    var blocks = [
      { key: "product_description", label: "cografi_detail.product_description" },
      { key: "production_method",   label: "cografi_detail.production_method" },
      { key: "boundary_processing", label: "cografi_detail.boundary_processing" },
      { key: "inspection",          label: "cografi_detail.inspection" },
    ];
    var html = blocks
      .filter(function (b) { return sections[b.key] && String(sections[b.key]).trim(); })
      .map(function (b) {
        return (
          '<div>' +
            '<h4 class="text-xs font-medium mb-1" style="color:var(--color-text-faint)">' +
              escapeHtml(t(b.label, b.key)) +
            '</h4>' +
            '<p class="text-sm leading-relaxed whitespace-pre-line" ' +
            'style="color:var(--color-text-primary)">' +
              escapeHtml(sections[b.key]) +
            '</p>' +
          '</div>'
        );
      }).join("");
    el.innerHTML = html;
  }

  function renderHolders(holders) {
    var el = $("cd-holders");
    if (!el) return;
    if (!holders || holders.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_detail.no_holders", "No registered holders.")) + '</p>';
      return;
    }
    el.innerHTML = holders.map(function (h) {
      var roleLabels = {
        applicant: t("cografi_detail.applicant", "Applicant"),
        registrant: t("cografi_detail.holders", "Holders"),
        agent: t("cografi_detail.agent", "Agent"),
      };
      var role = roleLabels[h.role] || h.role || "";
      var country = h.country ? ' <span style="color:var(--color-text-faint)">(' + escapeHtml(h.country) + ')</span>' : "";
      var tpe = h.tpe_client_id ? ' <span class="text-[10px] font-mono" style="color:var(--color-text-faint)">TPE ' + escapeHtml(h.tpe_client_id) + '</span>' : '';
      var addr = [h.city, h.state, h.country].filter(Boolean).join(", ");
      return (
        '<div class="text-xs">' +
          '<p class="font-medium" style="color:var(--color-text-primary)">' +
            escapeHtml(h.name || "—") + country + tpe +
            (role ? ' <span class="text-[10px] uppercase tracking-wide ml-1" ' +
                    'style="color:var(--color-text-faint)">' + escapeHtml(role) + '</span>' : '') +
          '</p>' +
          (addr ? ('<p style="color:var(--color-text-faint)">' + escapeHtml(addr) + '</p>') : '') +
        '</div>'
      );
    }).join("");
  }

  function renderChangeRequests(reqs) {
    var el = $("cd-change-requests");
    if (!el) return;
    if (!reqs || reqs.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_detail.no_change_requests", "No change requests.")) + '</p>';
      return;
    }
    el.innerHTML = reqs.map(function (r) {
      var date = r.bulletin_date || r.application_date || "";
      return (
        '<div class="text-xs flex items-start justify-between gap-2 py-1.5" ' +
        'style="border-bottom:1px solid var(--color-border-light, var(--color-border))">' +
          '<div class="min-w-0 flex-1">' +
            '<p style="color:var(--color-text-primary)">' +
              escapeHtml(r.change_type || r.applicant_name || "—") +
            '</p>' +
            (r.description ? '<p class="mt-0.5" style="color:var(--color-text-faint)">' + escapeHtml(r.description) + '</p>' : '') +
          '</div>' +
          '<span class="font-mono text-[10px] shrink-0" style="color:var(--color-text-faint)">' +
            escapeHtml(date) + '</span>' +
        '</div>'
      );
    }).join("");
  }

  function renderFigures(figures) {
    var el = $("cd-figures");
    if (!el) return;
    if (!figures || figures.length === 0) {
      el.innerHTML = '<p class="text-xs col-span-full" style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_detail.no_figures", "No figures.")) + '</p>';
      return;
    }
    el.innerHTML = figures.map(function (f) {
      var url = imageUrl(f);
      if (!url) return "";
      return (
        '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener" ' +
        'class="block rounded-md overflow-hidden border" ' +
        'style="border-color:var(--color-border);background:var(--color-bg-muted)">' +
          '<img src="' + escapeHtml(url) + '" alt="" loading="lazy" ' +
          'class="w-full h-24 object-contain"/>' +
        '</a>'
      );
    }).join("");
  }

  function renderRelated(related) {
    var el = $("cd-related");
    if (!el) return;
    if (!related || related.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_detail.no_related", "No other records.")) + '</p>';
      return;
    }
    el.innerHTML = related.map(function (r) {
      var sec = r.section_key
        ? t("cografi_detail.section_" + r.section_key.replace(/^article_/, "art"), r.section_key)
        : "";
      var date = r.bulletin_date || "";
      return (
        '<div class="text-xs flex items-center justify-between gap-2 py-1 cursor-pointer hover:opacity-80" ' +
        'data-cd-open="' + escapeHtml(r.id || "") + '" ' +
        'style="border-bottom:1px solid var(--color-border-light, var(--color-border))">' +
          '<span style="color:var(--color-text-primary)">' + escapeHtml(sec) + '</span>' +
          '<span class="font-mono text-[10px]" style="color:var(--color-text-faint)">' +
            escapeHtml(date) + '</span>' +
        '</div>'
      );
    }).join("");
  }

  function renderSource(rec) {
    var el = $("cd-source");
    if (!el) return;
    var bits = [rec.bulletin_folder, rec.bulletin_no ? "Bulletin #" + rec.bulletin_no : ""].filter(Boolean);
    el.textContent = bits.length ? bits.join(" / ") : "—";
  }

  function render(data) {
    var rec = (data && data.record) || {};
    renderHeader(rec);
    renderDates(rec);
    renderRegionAndProduct(rec);
    renderUsage(rec);
    renderBodySections(rec);
    renderHolders(data.holders);
    renderChangeRequests(data.change_requests);
    renderFigures(data.figures);
    renderRelated(data.related);
    renderSource(rec);
  }

  // ---------------------------------------------------------------
  // Open / close / fetch
  // ---------------------------------------------------------------

  function showError(msg) {
    var err = $("cd-error");
    var errMsg = $("cd-error-msg");
    if (errMsg) errMsg.textContent = msg || t("cografi_detail.error", "Failed to load.");
    show(err);
  }

  function openModal(recordId) {
    var modal = $("cografi-detail-modal");
    if (!modal) {
      console.warn("cografi-detail-modal element not found");
      return;
    }
    show(modal);
    hide($("cd-body"));
    hide($("cd-error"));
    show($("cd-loading"));

    authFetch(API_DETAIL + encodeURIComponent(recordId))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        hide($("cd-loading"));
        render(data);
        show($("cd-body"));
      })
      .catch(function (err) {
        hide($("cd-loading"));
        showError(err && err.message);
      });
  }

  function closeModal() {
    hide($("cografi-detail-modal"));
  }

  // ---------------------------------------------------------------
  // Event delegation: any element with [data-cd-open="<id>"] opens
  // ---------------------------------------------------------------

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;

      // Open from any [data-cd-open="<record_id>"] click — but skip if
      // the actual click target is an interactive child (button, link,
      // input). This lets show-more toggles inside cards keep working
      // without opening the modal.
      var trigger = t_.closest && t_.closest("[data-cd-open]");
      if (trigger) {
        var interactive = t_.closest(
          "button, a, input, select, textarea, [data-usage-toggle]"
        );
        if (interactive && interactive !== trigger) {
          return;  // let the inner handler take it
        }
        var rid = trigger.getAttribute("data-cd-open");
        if (rid) {
          ev.preventDefault();
          openModal(rid);
        }
        return;
      }
      // Close button
      if (t_.closest && t_.closest("#cd-close")) {
        ev.preventDefault();
        closeModal();
        return;
      }
      // Click on backdrop
      if (t_.id === "cografi-detail-modal") {
        closeModal();
      }
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        var modal = document.getElementById("cografi-detail-modal");
        if (modal && !modal.classList.contains("hidden")) {
          closeModal();
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  window.AppCografiDetail = {
    open: openModal,
    close: closeModal,
  };
})();
