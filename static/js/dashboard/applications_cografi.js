/**
 * Coğrafi (GI) Applications driver — Phase 3.
 *
 * Hits /api/v1/applications?registry=cografi. GI-specific shape:
 *   - `brand_name` column holds the GI name (e.g. "Antep Baklavası")
 *   - `classification_codes` is intentionally always [] for GI — no
 *     classification system equivalent for geographical indications
 *   - `details.gi_type`           = 'mense' | 'mahrec'
 *   - `details.region`            = geographic region
 *   - `details.product_type`      = product category
 *   - `details.production_method` = traditional production description
 *
 * All DOM IDs prefixed with `ca-`.
 */
(function () {
    "use strict";

    var state = {
        initialized: false,
        page: 1,
        pageSize: 20,
        statusFilter: null,
        typeFilter: null,
    };

    function $(id) { return document.getElementById(id); }

    function t(key, fallback) {
        if (window.AppI18n && typeof window.AppI18n.t === "function") {
            var v = window.AppI18n.t(key);
            if (v && v !== key) return v;
        }
        return fallback || key;
    }

    function fetchAuth(url, opts) {
        if (typeof window._appFetch === "function") return window._appFetch(url, opts);
        var token = (window.AppAuth && window.AppAuth.getAuthToken && window.AppAuth.getAuthToken()) || "";
        opts = opts || {};
        opts.headers = opts.headers || {};
        if (token) opts.headers["Authorization"] = "Bearer " + token;
        return fetch(url, opts);
    }

    function showToast(msg, kind) {
        if (window.showToast) window.showToast(msg, kind || "info");
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    }

    // -----------------------------------------------------------------
    // Init + filter handlers
    // -----------------------------------------------------------------

    window.initCografiApplicationsTab = function () {
        if (!state.initialized) {
            state.initialized = true;
        }
        loadList();
    };

    function setActiveStatusFilter(status) {
        document.querySelectorAll(".ca-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("ca-filter-" + (status || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    function setActiveTypeFilter(appType) {
        document.querySelectorAll(".ca-type-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("ca-type-filter-" + (appType || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    window.filterCografiApplications = function (status) {
        state.statusFilter = status;
        state.page = 1;
        setActiveStatusFilter(status);
        loadList();
    };

    window.filterCografiApplicationsByType = function (appType) {
        state.typeFilter = appType;
        state.page = 1;
        setActiveTypeFilter(appType);
        loadList();
    };

    window.loadCografiApplicationsPage = function (page) {
        state.page = page;
        loadList();
    };

    // -----------------------------------------------------------------
    // List
    // -----------------------------------------------------------------

    function loadList() {
        var listEl = $("ca-list");
        var loadingEl = $("ca-list-loading");
        var emptyEl = $("ca-list-empty");
        var paginationEl = $("ca-pagination");
        if (!listEl) return;

        listEl.innerHTML = "";
        loadingEl.classList.remove("hidden");
        emptyEl.classList.add("hidden");
        paginationEl.classList.add("hidden");

        var url = "/api/v1/applications/?registry=cografi&page=" + state.page + "&page_size=" + state.pageSize;
        if (state.statusFilter) url += "&status=" + encodeURIComponent(state.statusFilter);
        if (state.typeFilter) url += "&application_type=" + encodeURIComponent(state.typeFilter);

        fetchAuth(url)
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function (data) {
                loadingEl.classList.add("hidden");
                var items = data.items || [];
                if (items.length === 0) {
                    emptyEl.classList.remove("hidden");
                    return;
                }
                listEl.innerHTML = items.map(renderCard).join("");
                if (data.total_pages > 1) {
                    paginationEl.classList.remove("hidden");
                    var html = "";
                    if (state.page > 1) {
                        html += '<button onclick="loadCografiApplicationsPage(' + (state.page - 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">&laquo; ' + t("applications.prev", "Prev") + "</button>";
                    }
                    html += '<span class="text-sm" style="color:var(--color-text-muted)">' + state.page + " / " + data.total_pages + "</span>";
                    if (state.page < data.total_pages) {
                        html += '<button onclick="loadCografiApplicationsPage(' + (state.page + 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' + t("applications.next", "Next") + " &raquo;</button>";
                    }
                    paginationEl.innerHTML = html;
                }
            })
            .catch(function (err) {
                loadingEl.classList.add("hidden");
                console.error("Failed to load cografi applications:", err);
                listEl.innerHTML = '<p class="text-center py-8" style="color:var(--color-text-faint)">' + t("applications.load_error", "Failed to load.") + "</p>";
            });
    }

    function renderCard(app) {
        var statusColors = {
            draft: "bg-gray-100 text-gray-700",
            submitted: "bg-blue-100 text-blue-700",
            under_review: "bg-yellow-100 text-yellow-700",
            approved: "bg-green-100 text-green-700",
            rejected: "bg-red-100 text-red-700",
            completed: "bg-emerald-100 text-emerald-700",
        };
        var statusLabel = t("applications.status_" + app.status) || app.status;
        var statusClass = statusColors[app.status] || "bg-gray-100 text-gray-700";
        var typeLabel = t("applications.type_" + app.application_type) || app.application_type;
        var title = escapeHtml(app.brand_name || "—");
        var d = app.details || {};
        var giType = d.gi_type || "mense";
        var giTypeLabel = t("applications.gi_type_" + giType) || giType;
        var region = d.region ? escapeHtml(d.region) : "";
        var regionHtml = region
            ? '<span class="text-xs px-2 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-muted)">' + region + "</span>"
            : "";
        var editable = app.status === "draft";

        return [
            '<div class="rounded-xl p-4 sm:p-5 transition-colors hover:shadow-sm" style="background:var(--color-bg-card);border:1px solid var(--color-border)">',
            '  <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">',
            '    <div class="min-w-0 flex-1">',
            '      <div class="flex items-center gap-2 mb-1 flex-wrap">',
            '        <h4 class="text-base font-semibold" style="color:var(--color-text-primary)">' + title + "</h4>",
            '        <span class="text-xs px-2 py-0.5 rounded ' + statusClass + '">' + escapeHtml(statusLabel) + "</span>",
            '        <span class="text-xs px-2 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-muted)">' + escapeHtml(typeLabel) + "</span>",
            '        <span class="text-xs px-2 py-0.5 rounded" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + escapeHtml(giTypeLabel) + "</span>",
            "      </div>",
            '      <div class="flex items-center gap-2 flex-wrap">',
            regionHtml,
            "      </div>",
            "    </div>",
            '    <div class="flex items-center gap-2 shrink-0">',
            editable
                ? '      <button onclick="editCografiApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + t("applications.edit", "Edit") + "</button>"
                : "",
            editable
                ? '      <button onclick="deleteCografiApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press text-red-600" style="background:rgba(239,68,68,0.1)">' + t("applications.delete", "Delete") + "</button>"
                : "",
            "    </div>",
            "  </div>",
            "</div>",
        ].join("");
    }

    // -----------------------------------------------------------------
    // Form view
    // -----------------------------------------------------------------

    function clearForm() {
        ["ca-form-id", "ca-applicant-name", "ca-applicant-id-no", "ca-applicant-phone",
         "ca-applicant-email", "ca-applicant-address", "ca-gi-name",
         "ca-region", "ca-product-type", "ca-production-method", "ca-notes"].forEach(function (id) {
            var el = $(id);
            if (el) el.value = "";
        });
        var idType = $("ca-applicant-id-type"); if (idType) idType.value = "tc_kimlik";
        var appType = $("ca-application-type"); if (appType) appType.value = "registration";
        var giType = $("ca-gi-type"); if (giType) giType.value = "mense";
    }

    window.showCografiApplicationForm = function (existing) {
        var listView = $("ca-list-view");
        var formView = $("ca-form-view");
        if (!listView || !formView) return;
        listView.classList.add("hidden");
        formView.classList.remove("hidden");
        clearForm();
        if (existing) {
            $("ca-form-id").value = existing.id || "";
            $("ca-applicant-name").value = existing.applicant_full_name || "";
            $("ca-applicant-id-type").value = existing.applicant_id_type || "tc_kimlik";
            $("ca-applicant-id-no").value = existing.applicant_id_no || "";
            $("ca-applicant-phone").value = existing.applicant_phone || "";
            $("ca-applicant-email").value = existing.applicant_email || "";
            $("ca-applicant-address").value = existing.applicant_address || "";
            $("ca-gi-name").value = existing.brand_name || "";
            $("ca-application-type").value = existing.application_type || "registration";
            var d = existing.details || {};
            $("ca-gi-type").value = d.gi_type || "mense";
            $("ca-region").value = d.region || "";
            $("ca-product-type").value = d.product_type || "";
            $("ca-production-method").value = d.production_method || "";
            $("ca-notes").value = existing.notes || "";
        }
    };

    window.showCografiApplicationsList = function () {
        var listView = $("ca-list-view");
        var formView = $("ca-form-view");
        if (listView) listView.classList.remove("hidden");
        if (formView) formView.classList.add("hidden");
        loadList();
    };

    function collectFormBody() {
        var idType = $("ca-applicant-id-type").value || "tc_kimlik";
        var details = {
            gi_type: $("ca-gi-type").value || "mense",
            region: $("ca-region").value.trim() || null,
            product_type: $("ca-product-type").value.trim() || null,
            production_method: $("ca-production-method").value.trim() || null,
        };
        return {
            registry_kind: "cografi",
            application_type: $("ca-application-type").value || "registration",
            brand_name: $("ca-gi-name").value.trim() || "—",
            mark_type: "word",  // unused for GI; column is NOT NULL
            classification_codes: [],  // GI has no classification equivalent
            nice_class_numbers: [],
            details: details,
            applicant_full_name: $("ca-applicant-name").value.trim() || null,
            applicant_id_type: idType,
            applicant_id_no: $("ca-applicant-id-no").value.trim() || null,
            applicant_address: $("ca-applicant-address").value.trim() || null,
            applicant_phone: $("ca-applicant-phone").value.trim() || null,
            applicant_email: $("ca-applicant-email").value.trim() || null,
            notes: $("ca-notes").value.trim() || null,
        };
    }

    window.saveCografiApplication = function (_mode) {
        var body = collectFormBody();
        if (!body.brand_name || body.brand_name === "—") {
            showToast(t("applications.gi_name_required", "GI name is required"), "error");
            return;
        }
        var appId = $("ca-form-id").value;
        var url = "/api/v1/applications/" + (appId ? appId : "");
        var method = appId ? "PUT" : "POST";
        if (appId) delete body.registry_kind;
        fetchAuth(url, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        })
            .then(function (r) {
                if (!r.ok) {
                    return r.json().catch(function () { return {}; }).then(function (data) {
                        var detail = (data && data.detail) || ("HTTP " + r.status);
                        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
                    });
                }
                return r.json();
            })
            .then(function (saved) {
                showToast(t("applications.saved_success", "Saved"), "success");
                $("ca-form-id").value = saved.id;
                window.showCografiApplicationsList();
            })
            .catch(function (err) {
                console.error("save cografi application failed:", err);
                showToast(err.message || t("applications.save_failed", "Save failed"), "error");
            });
    };

    window.submitCografiApplication = function () {
        var appId = $("ca-form-id").value;
        var save = appId
            ? Promise.resolve({ id: appId })
            : new Promise(function (resolve, reject) {
                var body = collectFormBody();
                if (!body.brand_name || body.brand_name === "—") {
                    reject(new Error(t("applications.gi_name_required", "GI name is required")));
                    return;
                }
                fetchAuth("/api/v1/applications/", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                }).then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { reject(new Error((d && d.detail) || ("HTTP " + r.status))); });
                    r.json().then(function (saved) {
                        $("ca-form-id").value = saved.id;
                        resolve(saved);
                    });
                });
            });
        save.then(function (savedRow) {
            return fetchAuth("/api/v1/applications/" + savedRow.id + "/submit", { method: "POST" })
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { throw new Error((d && d.detail) || ("HTTP " + r.status)); });
                    return r.json();
                });
        }).then(function () {
            showToast(t("applications.submitted_success", "Submitted"), "success");
            window.showCografiApplicationsList();
        }).catch(function (err) {
            console.error("submit cografi application failed:", err);
            showToast(err.message || t("applications.submit_failed", "Submit failed"), "error");
        });
    };

    window.editCografiApplication = function (appId) {
        fetchAuth("/api/v1/applications/" + appId)
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function (app) { window.showCografiApplicationForm(app); })
            .catch(function (err) {
                console.error("edit cografi application failed:", err);
                showToast(t("applications.load_error", "Load failed"), "error");
            });
    };

    window.deleteCografiApplication = function (appId) {
        if (!confirm(t("applications.confirm_delete", "Delete this draft?"))) return;
        fetchAuth("/api/v1/applications/" + appId, { method: "DELETE" })
            .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
            .then(function () {
                showToast(t("applications.deleted_success", "Deleted"), "success");
                loadList();
            })
            .catch(function (err) {
                console.error("delete cografi application failed:", err);
                showToast(t("applications.delete_failed", "Delete failed"), "error");
            });
    };
})();
