/**
 * Design detail modal — driver for #design-detail-modal.
 *
 * Public entry: window.AppDesignDetail.open(designId)
 *
 * Hydrates from /api/v1/designs/{id}; populates the modal; shows
 * loading/error states. Result cards on the design search /
 * watchlist / alerts surfaces dispatch via [data-dd-open="<id>"].
 *
 * Sister to static/js/dashboard/patent_detail.js — same skeleton,
 * simpler schema (no inventors / no priorities), uses design-specific
 * status enum and event categories.
 */
(function () {
  "use strict";

  var API_DETAIL = "/api/v1/designs/";

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

  // ---------------------------------------------------------------
  // Status pill colours — mirrors design_search.js so card + modal
  // agree visually. The design lifecycle enum is Turkish, so we go
  // through substring matching (translateStatus already knows the
  // canonical Turkish values).
  // ---------------------------------------------------------------
  function _statusColors(status) {
    if (!status) return { bg: "var(--color-bg-muted)", color: "var(--color-text-secondary)" };
    var s = String(status).toLowerCase();
    if (s.indexOf("yayın") !== -1 || s.indexOf("yayim ertelendi") === -1 && s.indexOf("yayım") !== -1) {
      // 'Yayında' published
      if (s.indexOf("ertelendi") !== -1) {
        return { bg: "rgba(245,158,11,0.12)", color: "#d97706" };  // amber
      }
      return { bg: "rgba(34,197,94,0.12)", color: "#16a34a" };  // green
    }
    if (s.indexOf("yenilendi") !== -1 || s.indexOf("devredildi") !== -1 || s.indexOf("tescil edildi") !== -1) {
      return { bg: "rgba(34,197,94,0.12)", color: "#16a34a" };  // green — alive
    }
    if (s.indexOf("hükümsüz") !== -1 || s.indexOf("iptal edildi") !== -1 || s.indexOf("i̇ptal") !== -1) {
      return { bg: "rgba(239,68,68,0.12)", color: "#dc2626" };  // red — terminal
    }
    if (s.indexOf("süresi doldu") !== -1) {
      return { bg: "rgba(124,58,237,0.12)", color: "#7c3aed" };  // purple — expired
    }
    return { bg: "var(--color-bg-muted)", color: "var(--color-text-secondary)" };
  }

  // ---------------------------------------------------------------
  // Event category colours — different enum from patent. Renewals /
  // transfers / injunction-lifted are positive (life-extending or
  // restorative). Cancellations are negative (terminal). Seizures
  // and partial injunctions are neutral encumbrances.
  // ---------------------------------------------------------------
  function _eventCategory(eventType) {
    var POSITIVE = {
      // Synthetic milestone events from the design row's own dates —
      // injected server-side so the timeline is complete.
      application_filed: 1,
      published: 1,
      publication_resumed: 1,
      // Real design_events
      renewal: 1,
      partial_renewal: 1,
      transfer: 1,
      provisional_injunction_lifted: 1,
    };
    var NEGATIVE = {
      full_cancellation_board: 1,
      full_cancellation_applicant: 1,
      partial_cancellation_board: 1,
      partial_cancellation_owner: 1,
    };
    var NEUTRAL = {
      publication_postponed: 1,
      seizure: 1,
      provisional_seizure: 1,
      partial_provisional_injunction: 1,
    };
    if (POSITIVE[eventType]) return "positive";
    if (NEGATIVE[eventType]) return "negative";
    if (NEUTRAL[eventType]) return "neutral";
    return "neutral";
  }

  function _eventColors(category) {
    var map = {
      positive: { bg: "#dcfce7", color: "#166534" },
      negative: { bg: "#fee2e2", color: "#991b1b" },
      neutral:  { bg: "#f3f4f6", color: "#374151" },
    };
    return map[category] || map.neutral;
  }

  // ---------------------------------------------------------------
  // Renderers
  // ---------------------------------------------------------------

  function renderHeader(d) {
    if ($("dd-title")) $("dd-title").textContent = d.product_name_tr || d.product_name_en || d.application_no || "—";

    var statusBadge = $("dd-status-badge");
    if (statusBadge) {
      var statusVal = d.current_status || "";
      if (statusVal) {
        // window.translateStatus knows the Turkish enum + maps to
        // the active locale. Falls back to raw value if not mapped.
        statusBadge.textContent = window.translateStatus
          ? window.translateStatus(statusVal)
          : statusVal;
        var colors = _statusColors(statusVal);
        statusBadge.style.background = colors.bg;
        statusBadge.style.color = colors.color;
        statusBadge.style.display = "";
      } else {
        statusBadge.style.display = "none";
      }
    }

    // Section chip (tr_native / hague / deferred / republished) —
    // a small muted secondary chip. Only show for non-default
    // sections so the typical case stays clean.
    var sectionChip = $("dd-section-chip");
    if (sectionChip) {
      if (d.section && d.section !== "tr_native") {
        sectionChip.textContent = t("design_detail.section_" + d.section, d.section);
        sectionChip.style.display = "";
      } else {
        sectionChip.style.display = "none";
      }
    }

    var appNoEl = $("dd-app-no");
    if (appNoEl) appNoEl.textContent = d.application_no || "";
  }

  function renderDates(d) {
    if ($("dd-application-date")) $("dd-application-date").textContent = fmt(d.application_date);
    if ($("dd-registration-date")) $("dd-registration-date").textContent = fmt(d.registration_date);
    if ($("dd-reg-no")) $("dd-reg-no").textContent = d.registration_no || "—";
    var bul = (d.bulletin_no || "—") + (d.bulletin_date ? " · " + d.bulletin_date : "");
    if ($("dd-bulletin")) $("dd-bulletin").textContent = bul;
  }

  function renderLocarno(d) {
    var el = $("dd-locarno-chips");
    if (!el) return;
    var classes = d.locarno_classes || [];
    if (!classes.length) {
      el.innerHTML = '<span class="text-xs" style="color:var(--color-text-faint)">—</span>';
      return;
    }
    el.innerHTML = classes.map(function (c) {
      return '<span class="inline-block px-2 py-0.5 rounded text-xs font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
             escapeHtml(c) + '</span>';
    }).join(" ");
  }

  function renderViews(data) {
    var block = $("dd-views-block");
    var grid = $("dd-views");
    if (!grid || !block) return;
    var views = data.views || [];
    var folder = (data.design && data.design.source_issue_folder) || "";
    if (!views.length || !folder) {
      hide(block);
      grid.innerHTML = "";
      return;
    }
    show(block);
    var titleStr = (data.design && (data.design.product_name_tr || data.design.product_name_en || data.design.application_no)) || "";
    var total = views.length;
    grid.innerHTML = views.map(function (v, i) {
      // design_image_url backend pattern: /api/v1/design-image/{folder}/{path}
      var raw = (v.image_path || "").replace(/^\//, "");
      var url = "/api/v1/design-image/" + encodeURIComponent(folder) + "/" + raw;
      var subtitle = t("design_detail.view_n_of_m", "View {n}/{m}")
        .replace("{n}", String(v.view_index || (i + 1)))
        .replace("{m}", String(total));
      return (
        '<button type="button" data-dd-zoom ' +
          'data-zoom-src="' + escapeHtml(url) + '" ' +
          'data-zoom-title="' + escapeHtml(titleStr) + '" ' +
          'data-zoom-subtitle="' + escapeHtml(subtitle) + '" ' +
          'class="aspect-square rounded-md overflow-hidden cursor-zoom-in transition-transform hover:scale-105" ' +
          'style="background:var(--color-bg-muted);border:0;padding:0">' +
          '<img src="' + escapeHtml(url) + '" alt="view ' + escapeHtml(String(v.view_index || "")) + '" ' +
          'class="w-full h-full object-contain pointer-events-none" loading="lazy" ' +
          'onerror="this.style.display=\'none\'" />' +
        '</button>'
      );
    }).join("");
  }

  function renderHolder(holder) {
    var el = $("dd-holder");
    if (!el) return;
    if (!holder) {
      el.textContent = "—";
      return;
    }
    var nameRaw = holder.name || "";
    var name = window._stripTurkishAddress
      ? window._stripTurkishAddress(nameRaw)
      : nameRaw;
    el.innerHTML = '<div class="font-medium" style="color:var(--color-text-primary)">' +
      escapeHtml(name) + '</div>' +
      (holder.country
        ? ('<div class="text-[10px]" style="color:var(--color-text-faint)">' +
           escapeHtml(holder.country) + '</div>')
        : "") +
      (holder.tpe_client_id
        ? ('<div class="text-[10px] font-mono mt-0.5" style="color:var(--color-text-faint)">TPE ' +
           escapeHtml(holder.tpe_client_id) + '</div>')
        : "");
  }

  function renderDesigners(d) {
    var el = $("dd-designers");
    if (!el) return;
    var designers = d.designers || [];
    if (!designers.length) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = designers.map(function (n) {
      var raw = String(n || "").trim();
      var disp = window._stripTurkishAddress ? window._stripTurkishAddress(raw) : raw;
      return '<div class="text-xs" style="color:var(--color-text-primary)">' +
             escapeHtml(disp) + '</div>';
    }).join("");
  }

  function renderAttorney(d) {
    var el = $("dd-attorney");
    if (!el) return;
    var name = (d.attorney_name || "").trim();
    var firm = (d.attorney_firm || "").trim();
    if (!name && !firm) {
      el.textContent = "—";
      return;
    }
    var nameDisp = window._stripTurkishAddress
      ? window._stripTurkishAddress(name)
      : name;
    var firmInName = firm && nameDisp.toLowerCase().indexOf(firm.toLowerCase()) !== -1;
    var text = (firm && !firmInName) ? (nameDisp + " — " + firm) : nameDisp;
    el.textContent = text;
  }

  function renderEvents(events) {
    var el = $("dd-events");
    if (!el) return;
    if (!events || events.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = events.map(function (e) {
      var label = e.event_type
        ? t("design_detail.event_" + e.event_type, e.event_type)
        : "—";
      var category = _eventCategory(e.event_type || "");
      var colors = _eventColors(category);
      // Surface design_indices when the event was a partial — gives
      // the user a sense of "this affected indices 1, 3" without
      // having to drill into the JSON.
      var indices = e.design_indices;
      var indicesChip = "";
      if (Array.isArray(indices) && indices.length) {
        indicesChip = ' <span class="ml-1 inline-block px-1 rounded text-[10px] font-mono" ' +
          'style="background:var(--color-bg-muted);color:var(--color-text-muted)">#' +
          escapeHtml(indices.join(", ")) + '</span>';
      }
      return (
        '<div class="text-xs flex items-center justify-between gap-2 py-1" ' +
        'style="border-bottom:1px solid var(--color-border-light, var(--color-border))">' +
          '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium" ' +
            'style="background:' + colors.bg + ';color:' + colors.color + '">' +
            escapeHtml(label) +
          '</span>' + indicesChip +
          '<span class="font-mono text-[10px]" style="color:var(--color-text-faint)">' +
            escapeHtml(e.bulletin_date || e.event_date || "") + '</span>' +
        '</div>'
      );
    }).join("");
  }

  function renderSource(d) {
    var el = $("dd-source");
    if (!el) return;
    el.textContent = d.source_issue_folder || "—";
  }

  function render(data) {
    var d = (data && data.design) || {};
    renderHeader(d);
    renderDates(d);
    renderLocarno(d);
    renderViews(data);
    renderHolder(data.holder);
    renderDesigners(d);
    renderAttorney(d);
    renderEvents(data.recent_events);
    renderSource(d);
  }

  function showError(msg) {
    var err = $("dd-error");
    var errMsg = $("dd-error-msg");
    if (errMsg) errMsg.textContent = msg || t("design_detail.error", "Failed to load.");
    show(err);
  }

  function openModal(designId) {
    var modal = $("design-detail-modal");
    if (!modal) {
      console.warn("design-detail-modal element not found");
      return;
    }
    show(modal);
    hide($("dd-body"));
    hide($("dd-error"));
    show($("dd-loading"));

    authFetch(API_DETAIL + encodeURIComponent(designId))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        hide($("dd-loading"));
        render(data);
        show($("dd-body"));
      })
      .catch(function (err) {
        hide($("dd-loading"));
        showError(err && err.message);
      });
  }

  function closeModal() {
    hide($("design-detail-modal"));
  }

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;

      // View thumbnail click → dispatch the shared lightbox event so
      // the existing modal (templates/dashboard/partials/_modals.html)
      // picks it up. Stops propagation so we don't accidentally close
      // the detail modal underneath.
      var zoom = t_.closest && t_.closest("[data-dd-zoom]");
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

      // Open from [data-dd-open="<design_id>"]. Skip when the click
      // landed on an interactive child (buttons inside the card
      // should keep their own behaviour).
      var trigger = t_.closest && t_.closest("[data-dd-open]");
      if (trigger) {
        var interactive = t_.closest(
          "button, a, input, select, textarea, " +
          "[data-abstract-toggle], [data-design-add-watchlist], " +
          "[data-portfolio-trigger]"
        );
        if (interactive && interactive !== trigger) return;
        var did = trigger.getAttribute("data-dd-open");
        if (did) {
          ev.preventDefault();
          openModal(did);
        }
        return;
      }
      if (t_.closest && t_.closest("#dd-close")) {
        ev.preventDefault();
        closeModal();
        return;
      }
      if (t_.id === "design-detail-modal") {
        closeModal();
      }
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        var modal = document.getElementById("design-detail-modal");
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

  window.AppDesignDetail = {
    open: openModal,
    close: closeModal,
  };
})();
