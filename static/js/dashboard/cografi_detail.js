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

  var USAGE_COLLAPSE_AT = 320;

  function renderUsage(rec) {
    var el = $("cd-usage-description");
    if (!el) return;
    var text = rec.usage_description ? String(rec.usage_description) : "";
    if (!text.trim()) {
      el.textContent = t("cografi_detail.no_description", "No description.");
      return;
    }
    if (text.length <= USAGE_COLLAPSE_AT) {
      el.textContent = text;
      return;
    }
    // Preview + hidden full + show-more toggle. Clicking the toggle is
    // handled by the delegated [data-cd-usage-toggle] listener in wire().
    var safe = escapeHtml(text);
    var preview = escapeHtml(text.slice(0, USAGE_COLLAPSE_AT)) + '…';
    el.innerHTML = (
      '<span data-cd-usage-preview class="whitespace-pre-line">' + preview + '</span>' +
      '<span data-cd-usage-full class="hidden whitespace-pre-line">' + safe + '</span>' +
      ' <button type="button" data-cd-usage-toggle ' +
      'class="ml-1 text-xs font-medium hover:underline" ' +
      'style="color:var(--color-primary)">' +
        escapeHtml(t("cografi_detail.show_more", "Show more")) +
      '</button>'
    );
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
    ].filter(function (b) {
      return sections[b.key] && String(sections[b.key]).trim();
    });

    if (blocks.length === 0) {
      el.innerHTML = "";
      el.classList.add("hidden");
      return;
    }
    el.classList.remove("hidden");

    // Single section → labelled card, no tab UI (avoids awkward 1-tab bar).
    if (blocks.length === 1) {
      var only = blocks[0];
      el.innerHTML = (
        '<div class="rounded-lg border p-3.5" ' +
        'style="border-color:var(--color-border);background:var(--color-bg-muted)">' +
          '<p class="text-[10px] font-medium uppercase tracking-wider mb-1.5" ' +
          'style="color:var(--color-text-faint)">' +
            escapeHtml(t(only.label, only.key)) +
          '</p>' +
          '<p class="text-sm leading-relaxed whitespace-pre-line" ' +
          'style="color:var(--color-text-primary);max-width:64ch">' +
            escapeHtml(sections[only.key]) +
          '</p>' +
        '</div>'
      );
      return;
    }

    // Multi-section → tab bar above a single content panel. First tab
    // active by default; clicks handled by delegated [data-cd-tab] listener.
    var tabs = blocks.map(function (b, i) {
      var active = i === 0;
      return (
        '<button type="button" role="tab" ' +
        'data-cd-tab="' + escapeHtml(b.key) + '" ' +
        'aria-selected="' + (active ? "true" : "false") + '" ' +
        'class="px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap" ' +
        'style="border-color:' + (active ? "var(--color-primary)" : "transparent") + ';' +
        'color:' + (active ? "var(--color-primary)" : "var(--color-text-faint)") + ';' +
        'background:transparent">' +
          escapeHtml(t(b.label, b.key)) +
        '</button>'
      );
    }).join("");

    var panels = blocks.map(function (b, i) {
      var active = i === 0;
      return (
        '<div role="tabpanel" data-cd-tabpanel="' + escapeHtml(b.key) + '" ' +
        'class="' + (active ? '' : 'hidden') + '">' +
          '<p class="text-sm leading-relaxed whitespace-pre-line" ' +
          'style="color:var(--color-text-primary);max-width:64ch">' +
            escapeHtml(sections[b.key]) +
          '</p>' +
        '</div>'
      );
    }).join("");

    el.innerHTML = (
      '<div class="rounded-lg border overflow-hidden" ' +
      'style="border-color:var(--color-border);background:var(--color-bg-muted)">' +
        '<div role="tablist" class="flex flex-wrap border-b overflow-x-auto" ' +
        'style="border-color:var(--color-border)">' + tabs + '</div>' +
        '<div class="p-3.5">' + panels + '</div>' +
      '</div>'
    );
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

  function renderFigures(figures, titleStr) {
    var el = $("cd-figures");
    if (!el) return;
    if (!figures || figures.length === 0) {
      el.innerHTML = '<p class="text-xs col-span-full" style="color:var(--color-text-faint)">' +
        escapeHtml(t("cografi_detail.no_figures", "No figures.")) + '</p>';
      return;
    }
    var total = figures.length;
    var visibleIdx = 0;
    el.innerHTML = figures.map(function (f) {
      var url = imageUrl(f);
      if (!url) return "";
      visibleIdx += 1;
      var subtitle = t("cografi_detail.figure_n_of_m", "Figure {n}/{m}")
        .replace("{n}", String(visibleIdx))
        .replace("{m}", String(total));
      // Was a <a target="_blank"> that opened the raw image in a new
      // tab — replaced with a button that dispatches the shared
      // open-lightbox CustomEvent (handled in _modals.html).
      return (
        '<button type="button" data-cd-zoom ' +
        'data-zoom-src="' + escapeHtml(url) + '" ' +
        'data-zoom-title="' + escapeHtml(titleStr || "") + '" ' +
        'data-zoom-subtitle="' + escapeHtml(subtitle) + '" ' +
        'class="block rounded-md overflow-hidden border cursor-zoom-in transition-transform hover:scale-105" ' +
        'style="border-color:var(--color-border);background:var(--color-bg-muted);padding:0">' +
          '<img src="' + escapeHtml(url) + '" alt="" loading="lazy" ' +
          'class="w-full h-24 object-contain pointer-events-none"/>' +
        '</button>'
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
    renderFigures(data.figures, rec.name || rec.application_no || "");
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

      // Figure thumbnail click → shared lightbox.
      var zoom = t_.closest && t_.closest("[data-cd-zoom]");
      if (zoom) {
        ev.preventDefault();
        ev.stopPropagation();
        window.dispatchEvent(new CustomEvent("open-lightbox", {
          detail: {
            src: zoom.getAttribute("data-zoom-src") || "",
            title: zoom.getAttribute("data-zoom-title") || "",
            subtitle: zoom.getAttribute("data-zoom-subtitle") || "",
          },
        }));
        return;
      }

      // Body-section tab click → activate matching tab + panel.
      var tabBtn = t_.closest && t_.closest("[data-cd-tab]");
      if (tabBtn) {
        ev.preventDefault();
        var key = tabBtn.getAttribute("data-cd-tab");
        var bs = $("cd-body-sections");
        if (bs && key) {
          var tabs = bs.querySelectorAll("[data-cd-tab]");
          for (var i = 0; i < tabs.length; i++) {
            var on = tabs[i].getAttribute("data-cd-tab") === key;
            tabs[i].setAttribute("aria-selected", on ? "true" : "false");
            tabs[i].style.borderColor = on ? "var(--color-primary)" : "transparent";
            tabs[i].style.color = on ? "var(--color-primary)" : "var(--color-text-faint)";
          }
          var panels = bs.querySelectorAll("[data-cd-tabpanel]");
          for (var j = 0; j < panels.length; j++) {
            var match = panels[j].getAttribute("data-cd-tabpanel") === key;
            if (match) panels[j].classList.remove("hidden");
            else panels[j].classList.add("hidden");
          }
        }
        return;
      }

      // Usage-description show-more / show-less toggle.
      var usageBtn = t_.closest && t_.closest("[data-cd-usage-toggle]");
      if (usageBtn) {
        ev.preventDefault();
        var u = $("cd-usage-description");
        if (u) {
          var prev = u.querySelector("[data-cd-usage-preview]");
          var full = u.querySelector("[data-cd-usage-full]");
          if (prev && full) {
            var expanding = !prev.classList.contains("hidden");
            if (expanding) {
              prev.classList.add("hidden");
              full.classList.remove("hidden");
              usageBtn.textContent = t("cografi_detail.show_less", "Show less");
            } else {
              prev.classList.remove("hidden");
              full.classList.add("hidden");
              usageBtn.textContent = t("cografi_detail.show_more", "Show more");
            }
          }
        }
        return;
      }

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
