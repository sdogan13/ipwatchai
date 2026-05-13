/**
 * Patent detail modal — driver for the global #patent-detail-modal.
 *
 * Public entry: window.AppPatentDetail.open(patentId)
 *
 * Hydrates from /api/v1/patents/{id}; populates the modal sections;
 * shows loading/error states. All result cards on the four patent
 * surfaces (search/watchlist/alerts/leads) call into AppPatentDetail.open
 * so this single modal is the only detail UX.
 */
(function () {
  "use strict";

  var API_DETAIL = "/api/v1/patents/";

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

  function fmt(d) {
    if (!d) return "—";
    return String(d);
  }

  function authFetch(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    var tk = token();
    if (tk) opts.headers["Authorization"] = "Bearer " + tk;
    return fetch(url, opts);
  }

  // ---------------------------------------------------------------
  // Render sections
  // ---------------------------------------------------------------

  // Colour palette for the lifecycle status pill in the detail
  // header. Mirrors _statusColors() in static/js/dashboard/patent_search.js
  // so the card and detail modal stay visually consistent.
  function _statusColors(status) {
    var ENUM = {
      ACTIVE:             { bg: "rgba(34,197,94,0.12)", color: "#16a34a" },
      PENDING:            { bg: "rgba(59,130,246,0.12)", color: "#2563eb" },
      LAPSED_APPLICATION: { bg: "rgba(234,88,12,0.12)",  color: "#ea580c" },
      LAPSED_GRANT:       { bg: "rgba(217,119,6,0.12)",  color: "#d97706" },
      REJECTED:           { bg: "rgba(239,68,68,0.12)",  color: "#dc2626" },
      WITHDRAWN:          { bg: "rgba(239,68,68,0.12)",  color: "#dc2626" },
      EXPIRED:            { bg: "rgba(124,58,237,0.12)", color: "#7c3aed" },
      INVALIDATED:        { bg: "rgba(127,29,29,0.12)",  color: "#7f1d1d" },
      UNKNOWN:            { bg: "var(--color-bg-muted)", color: "var(--color-text-secondary)" },
    };
    return ENUM[status] || ENUM.UNKNOWN;
  }

  function renderHeader(p) {
    var titleEl = $("pd-title");
    if (titleEl) titleEl.textContent = p.title || "—";

    // Primary header badge: live lifecycle status (current_status).
    // Falls back to record_type when no events / no derivation has
    // run yet — same fallback the card uses.
    var rt = $("pd-record-type-badge");
    if (rt) {
      var status = p.current_status || p.record_type || "";
      if (p.current_status) {
        rt.textContent = t(
          "patent_search.status_" + String(p.current_status).toLowerCase(),
          p.current_status,
        );
        var colors = _statusColors(p.current_status);
        rt.style.background = colors.bg;
        rt.style.color = colors.color;
      } else if (p.record_type) {
        rt.textContent = t(
          "patent_detail.record_type_" + String(p.record_type).toLowerCase(),
          p.record_type,
        );
        // Muted neutral for the legacy record_type fallback.
        rt.style.background = "var(--color-bg-muted)";
        rt.style.color = "var(--color-text-muted)";
      }
      rt.style.display = status ? "" : "none";
    }

    // Secondary chip: bulletin classification (record_type). Visible
    // alongside the live status so users can still see what KIND of
    // bulletin entry produced this row — useful context when
    // current_status differs (e.g. lapsed grants whose record_type
    // is still "Granted UM Bulletin"). Injected inline so the
    // template stays untouched; idempotent across re-renders.
    if (rt && p.record_type && p.current_status) {
      var existing = $("pd-record-type-secondary");
      if (!existing) {
        existing = document.createElement("span");
        existing.id = "pd-record-type-secondary";
        existing.className = "inline-flex items-center px-1.5 py-0.5 rounded text-[10px]";
        existing.style.background = "var(--color-bg-muted)";
        existing.style.color = "var(--color-text-muted)";
        rt.parentNode.insertBefore(existing, rt.nextSibling);
      }
      var rtLabel = t(
        "patent_detail.record_type_" + String(p.record_type).toLowerCase(),
        p.record_type,
      );
      existing.textContent = rtLabel;
      existing.style.display = "";
    } else {
      var sec = $("pd-record-type-secondary");
      if (sec) sec.style.display = "none";
    }

    var kind = $("pd-kind-badge");
    if (kind) {
      kind.textContent = p.kind_code || "";
      kind.style.display = p.kind_code ? "" : "none";
    }
    var pubNo = $("pd-publication-no");
    if (pubNo) pubNo.textContent = p.publication_no || p.application_no || "";
  }

  function renderDates(p) {
    if ($("pd-application-date")) $("pd-application-date").textContent = fmt(p.application_date);
    if ($("pd-publication-date")) $("pd-publication-date").textContent = fmt(p.publication_date);
    if ($("pd-grant-date")) $("pd-grant-date").textContent = fmt(p.grant_date);
    var bul = (p.bulletin_no || "—") + (p.bulletin_date ? " · " + p.bulletin_date : "");
    if ($("pd-bulletin")) $("pd-bulletin").textContent = bul;
  }

  function renderAbstract(p) {
    var el = $("pd-abstract");
    if (el) el.textContent = p.abstract || t("patent_detail.no_abstract", "No abstract.");
  }

  function renderIpc(p) {
    var el = $("pd-ipc-chips");
    if (!el) return;
    var ipc = p.ipc_classes || [];
    if (ipc.length === 0) {
      el.innerHTML = '<span class="text-xs" style="color:var(--color-text-faint)">—</span>';
      return;
    }
    el.innerHTML = ipc.map(function (c) {
      return '<span class="inline-block px-2 py-0.5 rounded text-xs font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' +
             escapeHtml(c) + '</span>';
    }).join("");
  }

  // Figures grid — thumbnails with cursor-zoom-in, click dispatches
  // the shared open-lightbox event. Service pre-computes image_url
  // per row; rows with null URL are skipped so we never render a
  // broken <img>. Block stays hidden when there's nothing to show.
  function renderFigures(figures, patent) {
    var block = $("pd-figures-block");
    var grid = $("pd-figures");
    if (!grid || !block) return;
    var usable = (figures || []).filter(function (f) { return !!f.image_url; });
    if (usable.length === 0) {
      block.classList.add("hidden");
      grid.innerHTML = "";
      return;
    }
    block.classList.remove("hidden");
    var titleStr = (patent && patent.title) || (patent && patent.application_no) || "";
    var total = usable.length;
    grid.innerHTML = usable.map(function (f, i) {
      var subtitle = t("patent_detail.figure_n_of_m", "Figure {n}/{m}")
        .replace("{n}", String(f.seq || (i + 1)))
        .replace("{m}", String(total));
      return (
        '<button type="button" data-pd-zoom ' +
          'data-zoom-src="' + escapeHtml(f.image_url) + '" ' +
          'data-zoom-title="' + escapeHtml(titleStr) + '" ' +
          'data-zoom-subtitle="' + escapeHtml(subtitle) + '" ' +
          'class="aspect-square rounded-md overflow-hidden cursor-zoom-in transition-transform hover:scale-105" ' +
          'style="background:var(--color-bg-muted);border:0;padding:0">' +
          '<img src="' + escapeHtml(f.image_url) + '" alt="figure ' + escapeHtml(String(f.seq || "")) + '" ' +
          'class="w-full h-full object-contain pointer-events-none" loading="lazy" ' +
          'onerror="this.style.display=\'none\'" />' +
        '</button>'
      );
    }).join("");
  }

  function renderHolders(holders) {
    var el = $("pd-holders");
    if (!el) return;
    if (!holders || holders.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = holders.map(function (h) {
      var country = h.country || h.canonical_country || "";
      var tpe = h.tpe_client_id ? ' <span class="text-[10px] font-mono" style="color:var(--color-text-faint)">TPE ' + escapeHtml(h.tpe_client_id) + '</span>' : '';
      var addr = [h.city, h.state, h.country].filter(Boolean).join(", ");
      return (
        '<div class="text-xs">' +
          '<p class="font-medium" style="color:var(--color-text-primary)">' +
            escapeHtml(h.name || "—") +
            (country ? ' <span style="color:var(--color-text-faint)">(' + escapeHtml(country) + ')</span>' : '') +
            tpe +
          '</p>' +
          (addr ? ('<p style="color:var(--color-text-faint)">' + escapeHtml(addr) + '</p>') : '') +
        '</div>'
      );
    }).join("");
  }

  function renderInventors(inventors) {
    var el = $("pd-inventors");
    if (!el) return;
    if (!inventors || inventors.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = inventors.map(function (i) {
      var addr = [i.city, i.state, i.country].filter(Boolean).join(", ");
      return (
        '<div class="text-xs">' +
          '<p class="font-medium" style="color:var(--color-text-primary)">' +
            escapeHtml(i.name || "—") +
          '</p>' +
          (addr ? ('<p style="color:var(--color-text-faint)">' + escapeHtml(addr) + '</p>') : '') +
        '</div>'
      );
    }).join("");
  }

  function renderAttorneys(attorneys) {
    var el = $("pd-attorneys");
    if (!el) return;
    if (!attorneys || attorneys.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = attorneys.map(function (a) {
      var firm = a.firm ? ' <span style="color:var(--color-text-faint)">' + escapeHtml(a.firm) + '</span>' : '';
      var agent = a.agent_no ? ' <span class="text-[10px] font-mono" style="color:var(--color-text-faint)">#' + escapeHtml(a.agent_no) + '</span>' : '';
      return (
        '<div class="text-xs">' +
          '<p style="color:var(--color-text-primary)">' +
            escapeHtml(a.name || "—") + firm + agent +
          '</p>' +
        '</div>'
      );
    }).join("");
  }

  function renderPriorities(priorities) {
    var el = $("pd-priorities");
    if (!el) return;
    if (!priorities || priorities.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = priorities.map(function (p) {
      return (
        '<div class="text-xs">' +
          '<span class="font-mono" style="color:var(--color-text-primary)">' +
            escapeHtml(p.priority_no || "—") +
          '</span>' +
          (p.country ? ' <span style="color:var(--color-text-faint)">(' + escapeHtml(p.country) + ')</span>' : '') +
          (p.priority_date ? ' <span style="color:var(--color-text-faint)">· ' + escapeHtml(p.priority_date) + '</span>' : '') +
        '</div>'
      );
    }).join("");
  }

  // Event-type → semantic bucket. Drives the coloured pill in the
  // detail modal's "Recent events" list so a glance at the timeline
  // reveals whether the patent is healthy, in trouble, or just being
  // examined.
  //
  // Categories:
  //   positive    — grants, successful publications, revalidations,
  //                 lapse-cancellations, use declarations, upgrades
  //   negative    — rejections, withdrawals, fee lapses, expirations,
  //                 invalidations, abandonments, missing declarations
  //   opportunity — LICENSE_OFFER (this is a third-party lead, not
  //                 a status of the patent itself)
  //   neutral     — search reports, amendments, system choices,
  //                 neutral recordings (assignment / merger / division)
  function _eventCategory(eventType) {
    var POSITIVE = {
      // Synthetic milestone events from the patent row's own dates —
      // injected server-side so the timeline is complete even when
      // bulletin extraction missed the publication/grant events.
      APPLICATION_FILED: 1,
      APPLICATION_PUBLISHED: 1,
      APPLICATION_PUBLICATION_CORRECTED: 1,
      APPLICATION_FEE_REVALIDATION: 1,
      APPLICATION_FEE_LAPSE_CANCELLED: 1,
      APPLICATION_ABANDONED_CANCELLED: 1,
      GRANT_ANNOUNCED: 1,
      GRANT_ANNOUNCED_LEGACY_551: 1,
      GRANT_FINALIZED: 1,
      GRANT_CORRECTED: 1,
      GRANT_FEE_REVALIDATION: 1,
      GRANT_FEE_LAPSE_CANCELLED: 1,
      USE_DECLARATION_RECORDED: 1,
      CONVERSION_TO_PATENT: 1,
      EP_FASCICLE_ANNOUNCED: 1,
      EP_CLAIMS_PUBLISHED: 1,
      PCT_PHASE_II_ENTRY: 1,
      PROCEDURAL_REVALIDATION: 1,
    };
    var NEGATIVE = {
      APPLICATION_LAPSED_OR_REJECTED: 1,
      APPLICATION_REJECTED: 1,
      APPLICATION_WITHDRAWN: 1,
      APPLICATION_ABANDONED: 1,
      APPLICATION_FEE_LAPSE: 1,
      APPLICATION_PUBLICATION_CANCELLED: 1,
      GRANT_FEE_LAPSE: 1,
      GRANT_PROTECTION_EXPIRED: 1,
      GRANT_INVALIDATED_LEGACY_551: 1,
      USE_NONUSE_DECLARATION_MISSING: 1,
      NONUSE_DECLARATION_RECORDED: 1,
      SEARCH_REPORT_CANCELLED: 1,
      CONVERSION_TO_UM: 1,
    };
    if (eventType === "LICENSE_OFFER") return "opportunity";
    if (POSITIVE[eventType]) return "positive";
    if (NEGATIVE[eventType]) return "negative";
    return "neutral";
  }

  function _eventColors(category) {
    var map = {
      positive:    { bg: "#dcfce7", color: "#166534" },  // green
      negative:    { bg: "#fee2e2", color: "#991b1b" },  // red
      opportunity: { bg: "#ede9fe", color: "#5b21b6" },  // purple
      neutral:     { bg: "#f3f4f6", color: "#374151" },  // grey
    };
    return map[category] || map.neutral;
  }

  function renderEvents(events) {
    var el = $("pd-events");
    if (!el) return;
    if (!events || events.length === 0) {
      el.innerHTML = '<p class="text-xs" style="color:var(--color-text-faint)">—</p>';
      return;
    }
    el.innerHTML = events.map(function (e) {
      var label = e.event_type
        ? t("patent_detail.event_" + e.event_type.toLowerCase(), e.event_type)
        : "—";
      var category = _eventCategory(e.event_type || "");
      var colors = _eventColors(category);
      return (
        '<div class="text-xs flex items-center justify-between gap-2 py-1" ' +
        'style="border-bottom:1px solid var(--color-border-light, var(--color-border))">' +
          '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium" ' +
            'style="background:' + colors.bg + ';color:' + colors.color + '">' +
            escapeHtml(label) +
          '</span>' +
          '<span class="font-mono text-[10px]" style="color:var(--color-text-faint)">' +
            escapeHtml(e.bulletin_date || e.event_date || "") + '</span>' +
        '</div>'
      );
    }).join("");
  }

  function renderSource(p) {
    var el = $("pd-source");
    if (!el) return;
    var bits = [p.source_format, p.source_archive, p.source_pdf].filter(Boolean);
    el.textContent = bits.length ? bits.join(" / ") : "—";
  }

  function render(data) {
    var p = (data && data.patent) || {};
    renderHeader(p);
    renderDates(p);
    renderAbstract(p);
    renderIpc(p);
    renderFigures(data.figures, p);
    renderHolders(data.holders);
    renderInventors(data.inventors);
    renderAttorneys(data.attorneys);
    renderPriorities(data.priorities);
    renderEvents(data.recent_events);
    renderSource(p);
  }

  // ---------------------------------------------------------------
  // Open / close / fetch
  // ---------------------------------------------------------------

  function showError(msg) {
    var err = $("pd-error");
    var errMsg = $("pd-error-msg");
    if (errMsg) errMsg.textContent = msg || t("patent_detail.error", "Failed to load.");
    show(err);
  }

  function openModal(patentId) {
    var modal = $("patent-detail-modal");
    if (!modal) {
      console.warn("patent-detail-modal element not found");
      return;
    }
    show(modal);
    hide($("pd-body"));
    hide($("pd-error"));
    show($("pd-loading"));

    authFetch(API_DETAIL + encodeURIComponent(patentId))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        hide($("pd-loading"));
        render(data);
        show($("pd-body"));
      })
      .catch(function (err) {
        hide($("pd-loading"));
        showError(err && err.message);
      });
  }

  function closeModal() {
    hide($("patent-detail-modal"));
  }

  // ---------------------------------------------------------------
  // Event delegation: any element with [data-pd-open="<id>"] opens
  // ---------------------------------------------------------------

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;

      // Figure thumbnail click → shared lightbox (handled by
      // templates/dashboard/partials/_modals.html). Stops propagation
      // so we don't accidentally close the detail modal underneath.
      var zoom = t_.closest && t_.closest("[data-pd-zoom]");
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

      // Open from any [data-pd-open="<patent_id>"] click — but skip if
      // the actual click target is an interactive child (button, link,
      // input, or anything carrying its own data-* handler). This lets
      // the show-more toggles, IPC chip removes, and per-item Scan/Delete
      // buttons inside cards keep working without opening the modal.
      var trigger = t_.closest && t_.closest("[data-pd-open]");
      if (trigger) {
        var interactive = t_.closest(
          "button, a, input, select, textarea, " +
          "[data-abstract-toggle], [data-pwl-scan], [data-pwl-delete], " +
          "[data-pwl-alert-ack], [data-pwl-alert-resolve], [data-pwl-alert-dismiss], " +
          "[data-pl-category]"
        );
        if (interactive && interactive !== trigger) {
          return;  // let the inner handler take it
        }
        var pid = trigger.getAttribute("data-pd-open");
        if (pid) {
          ev.preventDefault();
          openModal(pid);
        }
        return;
      }
      // Close button
      if (t_.closest && t_.closest("#pd-close")) {
        ev.preventDefault();
        closeModal();
        return;
      }
      // Click on backdrop (the modal root itself, not its child card)
      if (t_.id === "patent-detail-modal") {
        closeModal();
      }
    });

    // ESC to close
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        var modal = document.getElementById("patent-detail-modal");
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

  window.AppPatentDetail = {
    open: openModal,
    close: closeModal,
  };
})();
