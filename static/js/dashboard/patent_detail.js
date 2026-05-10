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

  function renderHeader(p) {
    var titleEl = $("pd-title");
    if (titleEl) titleEl.textContent = p.title || "—";

    var rt = $("pd-record-type-badge");
    if (rt) {
      var rtLabel = p.record_type || "";
      rt.textContent = rtLabel ? t("patent_detail.record_type_" + rtLabel.toLowerCase(), rtLabel) : "";
      rt.style.display = rtLabel ? "" : "none";
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
      return (
        '<div class="text-xs flex items-center justify-between gap-2 py-1" ' +
        'style="border-bottom:1px solid var(--color-border-light, var(--color-border))">' +
          '<span style="color:var(--color-text-primary)">' + escapeHtml(label) + '</span>' +
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
