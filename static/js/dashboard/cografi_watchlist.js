/**
 * Coğrafi İşaret (GI) watchlist tab driver.
 *
 * Mirrors patent_watchlist.js patterns but tailored for cografi's
 * four watch types (vs patent's two):
 *   - holder     — alerts on every new GI by the watched holder
 *   - reference  — alerts on text+embedding similarity to a reference
 *   - region     — alerts on new GIs in a geographic area (trigram +
 *                  optional region_terms[] ANY-match)
 *   - lifecycle  — alerts on art42/correction events targeting a
 *                  specific registration_no
 *
 * Lazy-init via window.initCografiWatchlistTab so the parent watchlist
 * panel calls it on first activation (mirrors design / patent).
 */
(function () {
  "use strict";

  var API_WL    = "/api/v1/cografi-watchlist";
  var API_ALERT = "/api/v1/cografi-alerts";

  var state = {
    items: [],
    alerts: [],
    initialized: false,
    alertStatusFilter: "new",
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

  function fmtDate(s) {
    if (!s) return "";
    var d = new Date(s);
    if (isNaN(d.getTime())) return s;
    return d.toLocaleDateString();
  }

  function authFetch(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    var tk = token();
    if (tk) opts.headers["Authorization"] = "Bearer " + tk;
    return fetch(url, opts);
  }

  function toast(msg, kind) {
    if (window.AppToast && typeof window.AppToast.show === "function") {
      window.AppToast.show(msg, kind || "info");
    } else {
      console.log("[" + (kind || "info") + "] " + msg);
    }
  }

  // ---------------------------------------------------------------
  // Stats
  // ---------------------------------------------------------------

  function refreshStats() {
    var counts = { holder: 0, reference: 0, region: 0, lifecycle: 0 };
    state.items.forEach(function (i) {
      if (counts.hasOwnProperty(i.watch_type)) counts[i.watch_type]++;
    });
    var newAlerts = state.alerts.filter(function (a) { return a.status === "new"; }).length;

    var map = {
      "cwl-stat-total":      state.items.length,
      "cwl-stat-holder":     counts.holder,
      "cwl-stat-reference":  counts.reference,
      "cwl-stat-region":     counts.region,
      "cwl-stat-lifecycle":  counts.lifecycle,
      "cwl-stat-new-alerts": newAlerts,
    };
    Object.keys(map).forEach(function (id) {
      var el = $(id);
      if (el) el.textContent = String(map[id]);
    });
  }

  // ---------------------------------------------------------------
  // List rendering
  // ---------------------------------------------------------------

  function _typeBadge(watchType) {
    var palette = {
      holder:    { bg: "#ede9fe", fg: "#6d28d9" },
      reference: { bg: "#fef3c7", fg: "#92400e" },
      region:    { bg: "#dcfce7", fg: "#166534" },
      lifecycle: { bg: "#ccfbf1", fg: "#0f766e" },
    };
    var c = palette[watchType] || { bg: "#e5e7eb", fg: "#374151" };
    var label = t("cografi_watchlist.watch_type_" + watchType, watchType);
    return '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs" ' +
           'style="background:' + c.bg + ';color:' + c.fg + '">' + escapeHtml(label) + '</span>';
  }

  function _itemSubtitle(item) {
    switch (item.watch_type) {
      case "holder":
        return item.holder_name || item.holder_tpe_client_id ||
               t("cografi_watchlist.watch_type_holder", "Holder");
      case "reference":
        return item.reference_query
          ? (String(item.reference_query).length > 120
              ? String(item.reference_query).slice(0, 117) + "…"
              : item.reference_query)
          : ("Record #" + (item.reference_record_id || "").slice(0, 8));
      case "region":
        var terms = (item.region_terms || []).slice(0, 3).join(", ");
        var base = item.region_query || "";
        if (terms && base) return base + " · " + terms;
        return base || terms || "—";
      case "lifecycle":
        return "Tescil #" + (item.lifecycle_registration_no || "?");
      default:
        return "";
    }
  }

  function renderItem(item) {
    var typeBadge = _typeBadge(item.watch_type);
    var subtitle = escapeHtml(_itemSubtitle(item) || "");

    var giChip = item.gi_type
      ? '<span class="inline-block px-1.5 py-0.5 rounded text-[10px]" ' +
        'style="background:var(--color-primary-light);color:var(--color-primary)">' +
        escapeHtml(item.gi_type) + '</span>'
      : '';

    var sectionChips = (item.section_keys || []).slice(0, 3).map(function (sk) {
      var label = t("cografi_watchlist.section_" + sk.replace(/^article_/, "art").replace(/_modified$/, "_modified"), sk);
      return '<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono" ' +
             'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
             escapeHtml(label) + '</span>';
    }).join(" ");

    var lastScan = item.last_scan_at ? fmtDate(item.last_scan_at) :
      escapeHtml(t("cografi_watchlist.never_scanned", "never"));

    return (
      '<div class="rounded-lg border p-3 flex items-center justify-between gap-3" ' +
      'style="background:var(--color-bg-page);border-color:var(--color-border)">' +
        '<div class="min-w-0 flex-1">' +
          '<div class="flex items-center gap-2 mb-1 flex-wrap">' +
            typeBadge +
            (giChip ? giChip : '') +
            '<h4 class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' +
              escapeHtml(item.label || "—") + '</h4>' +
          '</div>' +
          '<p class="text-xs truncate" style="color:var(--color-text-muted)">' + subtitle + '</p>' +
          (sectionChips ? ('<div class="flex flex-wrap gap-1 mt-1">' + sectionChips + '</div>') : '') +
          '<p class="text-[10px] mt-1" style="color:var(--color-text-faint)">' +
            escapeHtml(t("cografi_watchlist.last_scan", "Last scan")) + ': ' + lastScan +
          '</p>' +
        '</div>' +
        '<div class="flex items-center gap-1 shrink-0">' +
          '<button type="button" data-cwl-scan="' + escapeHtml(item.id) + '" ' +
          'class="p-2 rounded hover:bg-indigo-50" style="color:var(--color-primary)" ' +
          'title="' + escapeHtml(t("cografi_watchlist.scan_btn", "Scan")) + '">' +
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
              'd="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>' +
            '</svg>' +
          '</button>' +
          '<button type="button" data-cwl-delete="' + escapeHtml(item.id) + '" ' +
          'class="p-2 rounded hover:bg-red-50 text-red-500" ' +
          'title="' + escapeHtml(t("cografi_watchlist.delete_btn", "Delete")) + '">' +
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
              '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
              'd="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>' +
            '</svg>' +
          '</button>' +
        '</div>' +
      '</div>'
    );
  }

  function renderList() {
    var container = $("cwl-list");
    var empty = $("cwl-empty");
    if (!container) return;
    if (state.items.length === 0) {
      container.innerHTML = "";
      show(empty);
      return;
    }
    hide(empty);
    container.innerHTML = state.items.map(renderItem).join("");
  }

  // ---------------------------------------------------------------
  // Alerts list rendering
  // ---------------------------------------------------------------

  function severityColor(sev) {
    return ({critical: "#dc2626", high: "#ea580c", medium: "#d97706", low: "#0891b2"})[sev] || "#6b7280";
  }

  function renderAlert(a) {
    var name = escapeHtml(a.conflicting_name || "—");
    var appNo = escapeHtml(a.conflicting_application_no || "");
    var regNo = a.conflicting_registration_no != null ? "#" + a.conflicting_registration_no : "";
    var region = escapeHtml(a.conflicting_geographical_boundary || "");

    var sevBadge =
      '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium" ' +
      'style="background:' + severityColor(a.severity) + ';color:white">' +
      escapeHtml(t("cografi_watchlist.severity_" + a.severity, a.severity)) + '</span>';

    var statusBadge =
      '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px]" ' +
      'style="background:var(--color-bg-muted);color:var(--color-text-muted)">' +
      escapeHtml(t("cografi_watchlist.status_" + a.status, a.status)) + '</span>';

    var matchBadge = a.match_type
      ? '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px]" ' +
        'style="background:var(--color-primary-light);color:var(--color-primary)">' +
        escapeHtml(t("cografi_watchlist.match_type_" + a.match_type, a.match_type)) + '</span>'
      : '';

    var score = a.overall_similarity_score != null
      ? Math.round(Number(a.overall_similarity_score) * 100) + "%"
      : "";

    var actionsHtml = "";
    if (a.status !== "resolved" && a.status !== "dismissed") {
      actionsHtml =
        '<button type="button" data-cwl-alert-ack="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-primary)">' +
          escapeHtml(t("cografi_watchlist.action_ack", "Acknowledge")) + '</button>' +
        '<button type="button" data-cwl-alert-resolve="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-success,#059669)">' +
          escapeHtml(t("cografi_watchlist.action_resolve", "Resolve")) + '</button>' +
        '<button type="button" data-cwl-alert-dismiss="' + escapeHtml(a.id) + '" ' +
        'class="text-xs px-2 py-1 rounded hover:underline" style="color:var(--color-text-faint)">' +
          escapeHtml(t("cografi_watchlist.action_dismiss", "Dismiss")) + '</button>';
    }

    var openAttr = a.conflicting_record_id
      ? ' data-cd-open="' + escapeHtml(a.conflicting_record_id) + '" class="rounded-lg border p-3 cursor-pointer"'
      : ' class="rounded-lg border p-3"';

    return (
      '<div' + openAttr + ' style="background:var(--color-bg-page);border-color:var(--color-border)">' +
        '<div class="flex items-start justify-between gap-2">' +
          '<div class="min-w-0 flex-1">' +
            '<div class="flex items-center gap-2 mb-1 flex-wrap">' +
              sevBadge + statusBadge + matchBadge +
              (score ? '<span class="text-xs font-mono" style="color:var(--color-text-muted)">' + score + '</span>' : '') +
            '</div>' +
            '<p class="text-sm font-semibold truncate" style="color:var(--color-text-primary)">' + name + '</p>' +
            (appNo || regNo
              ? '<p class="text-xs font-mono" style="color:var(--color-text-muted)">' +
                (appNo ? appNo : '') + (appNo && regNo ? ' · ' : '') + (regNo ? regNo : '') + '</p>'
              : '') +
            (region ? '<p class="text-xs" style="color:var(--color-text-faint)">' + region + '</p>' : '') +
          '</div>' +
        '</div>' +
        (actionsHtml
          ? '<div class="flex items-center gap-1 mt-2 pt-2" style="border-top:1px solid var(--color-border)">' + actionsHtml + '</div>'
          : '') +
      '</div>'
    );
  }

  function renderAlerts() {
    var box = $("cwl-alerts-list");
    var empty = $("cwl-alerts-empty");
    if (!box) return;
    if (state.alerts.length === 0) {
      box.innerHTML = "";
      show(empty);
      return;
    }
    hide(empty);
    box.innerHTML = state.alerts.map(renderAlert).join("");
  }

  // ---------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------

  function fetchItems() {
    // The watchlist list endpoint returns a raw array (unlike the
    // alerts list which returns {items: [...]}). Tolerate both
    // shapes so a future route normalization doesn't break the UI.
    return authFetch(API_WL + "?limit=200")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (d) {
        state.items = Array.isArray(d) ? d : (d.items || []);
      });
  }

  function fetchAlerts() {
    var url = API_ALERT + "?page_size=50";
    if (state.alertStatusFilter) url += "&status=" + encodeURIComponent(state.alertStatusFilter);
    return authFetch(url)
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (d) { state.alerts = d.items || []; });
  }

  function refreshAll() {
    return Promise.all([fetchItems(), fetchAlerts()]).then(function () {
      renderList();
      renderAlerts();
      refreshStats();
    });
  }

  // ---------------------------------------------------------------
  // CRUD actions
  // ---------------------------------------------------------------

  function openAddModal() {
    var modal = $("cwl-add-modal");
    if (!modal) return;
    // reset all per-type fields
    [
      "cwl-add-label", "cwl-add-holder-name", "cwl-add-reference-query",
      "cwl-add-region-query", "cwl-add-region-terms", "cwl-add-lifecycle-reg-no",
      "cwl-add-webhook-url",
    ].forEach(function (id) { var el = $(id); if (el) el.value = ""; });
    var giSel = $("cwl-add-gi-type"); if (giSel) giSel.value = "";
    var freq = $("cwl-add-frequency"); if (freq) freq.value = "daily";
    var emailCb = $("cwl-add-alert-email"); if (emailCb) emailCb.checked = true;
    var hookCb = $("cwl-add-alert-webhook"); if (hookCb) hookCb.checked = false;
    var hookUrl = $("cwl-add-webhook-url"); if (hookUrl) hookUrl.classList.add("hidden");
    document.querySelectorAll(".cwl-add-section-key").forEach(function (c) { c.checked = false; });

    var typeRadios = document.querySelectorAll('input[name="cwl-watch-type"]');
    typeRadios.forEach(function (r) { r.checked = (r.value === "holder"); });
    syncWatchTypeFields("holder");
    var err = $("cwl-add-error");
    if (err) { err.classList.add("hidden"); err.textContent = ""; }
    show(modal);
  }
  function closeAddModal() { hide($("cwl-add-modal")); }

  function syncWatchTypeFields(watchType) {
    var groups = {
      holder:    "cwl-holder-fields",
      reference: "cwl-reference-fields",
      region:    "cwl-region-fields",
      lifecycle: "cwl-lifecycle-fields",
    };
    Object.keys(groups).forEach(function (k) {
      var el = $(groups[k]);
      if (!el) return;
      el.classList.toggle("hidden", k !== watchType);
    });
    // Threshold only relevant for reference matches
    var thresh = $("cwl-threshold-row");
    if (thresh) thresh.classList.toggle("hidden", watchType !== "reference");
  }

  function readWatchType() {
    var checked = document.querySelector('input[name="cwl-watch-type"]:checked');
    return checked ? checked.value : "holder";
  }

  function readSelectedSectionKeys() {
    var nodes = document.querySelectorAll(".cwl-add-section-key:checked");
    var out = [];
    for (var i = 0; i < nodes.length; i++) out.push(nodes[i].value);
    return out;
  }

  function showAddError(msg) {
    var err = $("cwl-add-error");
    if (!err) return;
    err.textContent = msg;
    err.classList.remove("hidden");
  }

  function submitAdd() {
    var label = (($("cwl-add-label") || {}).value || "").trim();
    if (!label) {
      showAddError(t("cografi_watchlist.error_label_required", "Label is required"));
      return;
    }
    var watchType = readWatchType();
    var body = { watch_type: watchType, label: label };

    if (watchType === "holder") {
      var holderName = (($("cwl-add-holder-name") || {}).value || "").trim();
      if (!holderName) {
        showAddError(t("cografi_watchlist.error_holder_required", "Holder name is required"));
        return;
      }
      body.holder_name = holderName;
    } else if (watchType === "reference") {
      var refQ = (($("cwl-add-reference-query") || {}).value || "").trim();
      if (!refQ) {
        showAddError(t("cografi_watchlist.error_reference_required", "Reference text is required"));
        return;
      }
      body.reference_query = refQ;
      body.similarity_threshold = parseFloat((($("cwl-add-threshold") || {}).value || "0.50"));
    } else if (watchType === "region") {
      var regionQ = (($("cwl-add-region-query") || {}).value || "").trim();
      var regionTermsRaw = (($("cwl-add-region-terms") || {}).value || "").trim();
      var regionTerms = regionTermsRaw
        ? regionTermsRaw.split(",").map(function (s) { return s.trim(); }).filter(Boolean)
        : [];
      if (!regionQ && regionTerms.length === 0) {
        showAddError(t("cografi_watchlist.error_region_required", "Region is required"));
        return;
      }
      body.region_query = regionQ || null;
      body.region_terms = regionTerms;
    } else if (watchType === "lifecycle") {
      var rnRaw = (($("cwl-add-lifecycle-reg-no") || {}).value || "").trim();
      var rn = parseInt(rnRaw, 10);
      if (!rnRaw || isNaN(rn) || rn <= 0) {
        showAddError(t("cografi_watchlist.error_lifecycle_required", "Registration number is required"));
        return;
      }
      body.lifecycle_registration_no = rn;
    }

    var giType = (($("cwl-add-gi-type") || {}).value || "").trim();
    if (giType) body.gi_type = giType;

    var sectionKeys = readSelectedSectionKeys();
    if (sectionKeys.length) body.section_keys = sectionKeys;

    body.alert_frequency = (($("cwl-add-frequency") || {}).value || "daily");
    body.alert_email = !!($("cwl-add-alert-email") && $("cwl-add-alert-email").checked);
    body.alert_webhook = !!($("cwl-add-alert-webhook") && $("cwl-add-alert-webhook").checked);
    if (body.alert_webhook) {
      body.webhook_url = (($("cwl-add-webhook-url") || {}).value || "").trim();
    }

    authFetch(API_WL, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.ok) return r.json();
      return r.json().then(function (d) { throw d; });
    }).then(function () {
      closeAddModal();
      toast(t("cografi_watchlist.add_success", "Watchlist item added"), "success");
      refreshAll();
    }).catch(function (d) {
      var msg = (d && d.detail && (d.detail.message || d.detail)) || t("cografi_watchlist.error_generic", "Add failed");
      // Detect cross-registry quota error from server
      if (typeof msg === "string" && /quota/i.test(msg)) {
        msg = t("cografi_watchlist.error_quota_exceeded", msg);
      }
      showAddError(typeof msg === "string" ? msg : JSON.stringify(msg));
    });
  }

  function scanItem(id) {
    authFetch(API_WL + "/" + encodeURIComponent(id) + "/scan", { method: "POST" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function (d) {
        toast(
          t("cografi_watchlist.scan_done", "Scan complete") + ": " +
          (d.alerts_created || 0) + " " + t("cografi_watchlist.alerts_created", "new alerts"),
          "success",
        );
        refreshAll();
      }).catch(function () { toast(t("cografi_watchlist.scan_failed", "Scan failed"), "error"); });
  }

  function deleteItem(id) {
    if (!confirm(t("cografi_watchlist.confirm_delete", "Delete this watchlist item?"))) return;
    authFetch(API_WL + "/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () { toast(t("cografi_watchlist.delete_success", "Deleted"), "success"); refreshAll(); })
      .catch(function () { toast(t("cografi_watchlist.delete_failed", "Delete failed"), "error"); });
  }

  function scanAll() {
    if (state.items.length === 0) return;
    if (!confirm(t("cografi_watchlist.confirm_scan_all", "Scan all watchlist items?"))) return;
    var btn = $("cwl-btn-scan-all");
    if (btn) btn.disabled = true;
    Promise.all(state.items.map(function (it) {
      return authFetch(API_WL + "/" + encodeURIComponent(it.id) + "/scan", { method: "POST" })
        .then(function (r) { return r.ok ? r.json() : { alerts_created: 0 }; })
        .catch(function () { return { alerts_created: 0 }; });
    })).then(function (results) {
      var total = results.reduce(function (a, r) { return a + (r.alerts_created || 0); }, 0);
      toast(
        t("cografi_watchlist.scan_all_done", "Scan complete") + ": " + total + " " +
        t("cografi_watchlist.alerts_created", "new alerts"),
        "success",
      );
    }).finally(function () {
      if (btn) btn.disabled = false;
      refreshAll();
    });
  }

  function alertAction(id, action) {
    // The cografi alert PATCH endpoint takes the status as a body field
    // (acknowledged / resolved / dismissed) rather than separate verbs.
    var statusByAction = { acknowledge: "acknowledged", resolve: "resolved", dismiss: "dismissed" };
    var newStatus = statusByAction[action];
    if (!newStatus) return;
    authFetch(API_ALERT + "/" + encodeURIComponent(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus }),
    }).then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () { refreshAll(); })
      .catch(function () { toast(t("cografi_watchlist.alert_action_failed", "Action failed"), "error"); });
  }

  // ---------------------------------------------------------------
  // Wire-up
  // ---------------------------------------------------------------

  function wire() {
    document.addEventListener("click", function (ev) {
      var t_ = ev.target;
      if (!t_) return;
      if (t_.closest("#cwl-btn-add"))      { ev.preventDefault(); openAddModal(); return; }
      if (t_.closest("#cwl-add-close"))    { ev.preventDefault(); closeAddModal(); return; }
      if (t_.closest("#cwl-add-cancel"))   { ev.preventDefault(); closeAddModal(); return; }
      if (t_.closest("#cwl-add-submit"))   { ev.preventDefault(); submitAdd(); return; }
      if (t_.closest("#cwl-btn-scan-all")) { ev.preventDefault(); scanAll(); return; }

      var scanBtn = t_.closest("[data-cwl-scan]");
      if (scanBtn) { scanItem(scanBtn.getAttribute("data-cwl-scan")); return; }
      var delBtn = t_.closest("[data-cwl-delete]");
      if (delBtn) { deleteItem(delBtn.getAttribute("data-cwl-delete")); return; }

      var ackBtn = t_.closest("[data-cwl-alert-ack]");
      if (ackBtn) { alertAction(ackBtn.getAttribute("data-cwl-alert-ack"), "acknowledge"); return; }
      var resBtn = t_.closest("[data-cwl-alert-resolve]");
      if (resBtn) { alertAction(resBtn.getAttribute("data-cwl-alert-resolve"), "resolve"); return; }
      var disBtn = t_.closest("[data-cwl-alert-dismiss]");
      if (disBtn) { alertAction(disBtn.getAttribute("data-cwl-alert-dismiss"), "dismiss"); return; }
    });

    document.addEventListener("change", function (ev) {
      if (!ev.target) return;
      if (ev.target.name === "cwl-watch-type") {
        syncWatchTypeFields(ev.target.value);
      } else if (ev.target.id === "cwl-alert-status-filter") {
        state.alertStatusFilter = ev.target.value || "";
        fetchAlerts().then(renderAlerts).then(refreshStats);
      } else if (ev.target.id === "cwl-add-alert-webhook") {
        var hookUrl = $("cwl-add-webhook-url");
        if (hookUrl) hookUrl.classList.toggle("hidden", !ev.target.checked);
      }
    });
  }

  // ---------------------------------------------------------------
  // CSV export
  // ---------------------------------------------------------------

  function exportAlertsCsv() {
    var qs = state.alertStatusFilter
      ? ("?status=" + encodeURIComponent(state.alertStatusFilter))
      : "";
    // authFetch + blob — anchor href downloads can't carry the
    // Authorization header so we have to round-trip through fetch.
    authFetch(API_ALERT + "/export.csv" + qs)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "cografi_alerts.csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch(function () {
        toast(t("cografi_watchlist.export_failed", "Export failed"), "error");
      });
  }

  function init() {
    if (state.initialized) {
      refreshAll();
      return;
    }
    state.initialized = true;
    wire();
    var exportBtn = $("cwl-alerts-export-csv");
    if (exportBtn) {
      exportBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        exportAlertsCsv();
      });
    }
    refreshAll();
  }

  window.initCografiWatchlistTab = init;
})();
