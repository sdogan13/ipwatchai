/**
 * Design (Tasarım) Applications driver — Phase 1.
 *
 * Hits /api/v1/applications?registry=design on the shared applications
 * endpoint. Mirrors the trademark applications workflow's shape but
 * with design-specific fields:
 *   - `brand_name` column holds the design title
 *   - `classification_codes` holds Locarno classes ("1"-"32" as strings)
 *   - `details.design_description` holds the design description
 *
 * All DOM IDs prefixed with `da-` so the trademark form's IDs stay
 * isolated and both subviews can coexist in the DOM.
 *
 * Globals exposed (called from inline onclick handlers in
 * _applications_design_subview.html):
 *   - showDesignApplicationForm()
 *   - showDesignApplicationsList()
 *   - filterDesignApplications(status)
 *   - filterDesignApplicationsByType(appType)
 *   - addDesignLocarnoClass()
 *   - saveDesignApplication(mode)
 *   - submitDesignApplication()
 *   - editDesignApplication(appId)
 *   - deleteDesignApplication(appId)
 *   - loadDesignApplicationsPage(page)
 *
 * Init entry point: window.initDesignApplicationsTab() — call from the
 * Tasarım registry switcher's @click in _applications_panel.html.
 */
(function () {
    "use strict";

    var LOCARNO_MAX_CLASS = 32;

    var state = {
        initialized: false,
        page: 1,
        pageSize: 20,
        statusFilter: null,
        typeFilter: null,
        selectedLocarnoClasses: [],  // strings: "1"..."32"
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
        // Reuse the trademark _appFetch — already on window via app.js
        if (typeof window._appFetch === "function") {
            return window._appFetch(url, opts);
        }
        // Fallback (shouldn't happen because app.js loads first)
        var token = (window.AppAuth && window.AppAuth.getAuthToken && window.AppAuth.getAuthToken()) || "";
        opts = opts || {};
        opts.headers = opts.headers || {};
        if (token) opts.headers["Authorization"] = "Bearer " + token;
        return fetch(url, opts);
    }

    function showToast(msg, kind) {
        if (window.showToast) window.showToast(msg, kind || "info");
    }

    // -----------------------------------------------------------------
    // Init + tab entry
    // -----------------------------------------------------------------

    window.initDesignApplicationsTab = function () {
        if (!state.initialized) {
            state.initialized = true;
            var select = $("da-locarno-class-select");
            if (select && select.options.length <= 1) {
                for (var i = 1; i <= LOCARNO_MAX_CLASS; i++) {
                    var opt = document.createElement("option");
                    opt.value = String(i);
                    opt.textContent = t("applications.locarno_class", "Locarno") + " " + i;
                    select.appendChild(opt);
                }
            }
        }
        loadList();
    };

    // -----------------------------------------------------------------
    // Filter buttons
    // -----------------------------------------------------------------

    function setActiveStatusFilter(status) {
        document.querySelectorAll(".da-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("da-filter-" + (status || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    function setActiveTypeFilter(appType) {
        document.querySelectorAll(".da-type-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("da-type-filter-" + (appType || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    window.filterDesignApplications = function (status) {
        state.statusFilter = status;
        state.page = 1;
        setActiveStatusFilter(status);
        loadList();
    };

    window.filterDesignApplicationsByType = function (appType) {
        state.typeFilter = appType;
        state.page = 1;
        setActiveTypeFilter(appType);
        loadList();
    };

    window.loadDesignApplicationsPage = function (page) {
        state.page = page;
        loadList();
    };

    // -----------------------------------------------------------------
    // List
    // -----------------------------------------------------------------

    function loadList() {
        var listEl = $("da-list");
        var loadingEl = $("da-list-loading");
        var emptyEl = $("da-list-empty");
        var paginationEl = $("da-pagination");

        if (!listEl) return;  // subview not in DOM (e.g., before tab open)

        listEl.innerHTML = "";
        loadingEl.classList.remove("hidden");
        emptyEl.classList.add("hidden");
        paginationEl.classList.add("hidden");

        var url = "/api/v1/applications/?registry=design&page=" + state.page + "&page_size=" + state.pageSize;
        if (state.statusFilter) url += "&status=" + encodeURIComponent(state.statusFilter);
        if (state.typeFilter) url += "&application_type=" + encodeURIComponent(state.typeFilter);

        fetchAuth(url)
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
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
                        html += '<button onclick="loadDesignApplicationsPage(' + (state.page - 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">&laquo; ' + t("applications.prev", "Prev") + "</button>";
                    }
                    html += '<span class="text-sm" style="color:var(--color-text-muted)">' + state.page + " / " + data.total_pages + "</span>";
                    if (state.page < data.total_pages) {
                        html += '<button onclick="loadDesignApplicationsPage(' + (state.page + 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' + t("applications.next", "Next") + " &raquo;</button>";
                    }
                    paginationEl.innerHTML = html;
                }
            })
            .catch(function (err) {
                loadingEl.classList.add("hidden");
                console.error("Failed to load design applications:", err);
                listEl.innerHTML = '<p class="text-center py-8" style="color:var(--color-text-faint)">' + t("applications.load_error", "Failed to load.") + "</p>";
            });
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
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
        var classes = (app.classification_codes && app.classification_codes.length)
            ? app.classification_codes.map(function (c) { return escapeHtml(c); }).join(", ")
            : "";
        var classesHtml = classes
            ? '<span class="text-xs px-2 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-muted)">' + t("applications.locarno_classes", "Locarno") + ": " + classes + "</span>"
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
            "      </div>",
            '      <div class="flex items-center gap-2 flex-wrap">',
            classesHtml,
            "      </div>",
            "    </div>",
            '    <div class="flex items-center gap-2 shrink-0">',
            editable
                ? '      <button onclick="editDesignApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + t("applications.edit", "Edit") + "</button>"
                : "",
            editable
                ? '      <button onclick="deleteDesignApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press text-red-600" style="background:rgba(239,68,68,0.1)">' + t("applications.delete", "Delete") + "</button>"
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
        ["da-form-id", "da-applicant-name", "da-applicant-id-no", "da-applicant-phone",
         "da-applicant-email", "da-applicant-address", "da-design-title",
         "da-design-description", "da-notes"].forEach(function (id) {
            var el = $(id);
            if (el) el.value = "";
        });
        var idType = $("da-applicant-id-type"); if (idType) idType.value = "tc_kimlik";
        var appType = $("da-application-type"); if (appType) appType.value = "registration";
        state.selectedLocarnoClasses = [];
        renderLocarnoChips();
    }

    function renderLocarnoChips() {
        var container = $("da-locarno-classes-container");
        if (!container) return;
        if (state.selectedLocarnoClasses.length === 0) {
            container.innerHTML = '<span class="text-xs italic" style="color:var(--color-text-faint)">' + t("applications.no_classes", "No classes selected") + "</span>";
            return;
        }
        container.innerHTML = state.selectedLocarnoClasses.map(function (cls) {
            return [
                '<span class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs" style="background:rgba(99,102,241,0.12);color:var(--color-primary)">',
                t("applications.locarno_class", "Locarno") + " " + escapeHtml(cls),
                '<button onclick="(function(){ var s = window.__daRemoveLocarno; s && s(\'' + cls + '\'); })()" class="hover:opacity-70">&times;</button>',
                "</span>",
            ].join("");
        }).join("");
    }

    window.__daRemoveLocarno = function (cls) {
        state.selectedLocarnoClasses = state.selectedLocarnoClasses.filter(function (c) { return c !== cls; });
        renderLocarnoChips();
    };

    window.addDesignLocarnoClass = function () {
        var sel = $("da-locarno-class-select");
        if (!sel) return;
        var value = sel.value;
        if (!value) return;
        if (state.selectedLocarnoClasses.indexOf(value) !== -1) {
            showToast(t("applications.class_already_added", "Class already added"), "warning");
            return;
        }
        state.selectedLocarnoClasses.push(value);
        renderLocarnoChips();
        sel.value = "";
    };

    window.showDesignApplicationForm = function (existing) {
        var listView = $("da-list-view");
        var formView = $("da-form-view");
        if (!listView || !formView) return;
        listView.classList.add("hidden");
        formView.classList.remove("hidden");
        clearForm();
        if (existing) {
            $("da-form-id").value = existing.id || "";
            $("da-applicant-name").value = existing.applicant_full_name || "";
            $("da-applicant-id-type").value = existing.applicant_id_type || "tc_kimlik";
            $("da-applicant-id-no").value = existing.applicant_id_no || "";
            $("da-applicant-phone").value = existing.applicant_phone || "";
            $("da-applicant-email").value = existing.applicant_email || "";
            $("da-applicant-address").value = existing.applicant_address || "";
            $("da-design-title").value = existing.brand_name || "";
            $("da-application-type").value = existing.application_type || "registration";
            $("da-design-description").value = (existing.details && existing.details.design_description) || "";
            $("da-notes").value = existing.notes || "";
            state.selectedLocarnoClasses = (existing.classification_codes || []).slice();
            renderLocarnoChips();
        }
    };

    window.showDesignApplicationsList = function () {
        var listView = $("da-list-view");
        var formView = $("da-form-view");
        if (listView) listView.classList.remove("hidden");
        if (formView) formView.classList.add("hidden");
        loadList();
    };

    function collectFormBody() {
        var idType = $("da-applicant-id-type").value || "tc_kimlik";
        return {
            registry_kind: "design",
            application_type: $("da-application-type").value || "registration",
            brand_name: $("da-design-title").value.trim() || "—",  // primary title; required
            mark_type: "word",  // ignored for design but the column is NOT NULL
            classification_codes: state.selectedLocarnoClasses.slice(),
            nice_class_numbers: [],
            details: {
                design_description: $("da-design-description").value.trim() || null,
            },
            applicant_full_name: $("da-applicant-name").value.trim() || null,
            applicant_id_type: idType,
            applicant_id_no: $("da-applicant-id-no").value.trim() || null,
            applicant_address: $("da-applicant-address").value.trim() || null,
            applicant_phone: $("da-applicant-phone").value.trim() || null,
            applicant_email: $("da-applicant-email").value.trim() || null,
            notes: $("da-notes").value.trim() || null,
        };
    }

    window.saveDesignApplication = function (_mode) {
        var body = collectFormBody();
        if (!body.brand_name || body.brand_name === "—") {
            showToast(t("applications.design_title_required", "Design title is required"), "error");
            return;
        }
        var appId = $("da-form-id").value;
        var url = "/api/v1/applications/" + (appId ? appId : "");
        var method = appId ? "PUT" : "POST";
        // PUT body shouldn't include registry_kind (immutable post-create)
        if (appId) {
            delete body.registry_kind;
        }
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
                $("da-form-id").value = saved.id;
                window.showDesignApplicationsList();
            })
            .catch(function (err) {
                console.error("save design application failed:", err);
                showToast(err.message || t("applications.save_failed", "Save failed"), "error");
            });
    };

    window.submitDesignApplication = function () {
        // Save as draft first, then POST /submit
        var appId = $("da-form-id").value;
        var save = appId
            ? Promise.resolve({ id: appId })
            : new Promise(function (resolve, reject) {
                var body = collectFormBody();
                if (!body.brand_name || body.brand_name === "—") {
                    reject(new Error(t("applications.design_title_required", "Design title is required")));
                    return;
                }
                fetchAuth("/api/v1/applications/", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                }).then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { reject(new Error((d && d.detail) || ("HTTP " + r.status))); });
                    r.json().then(function (saved) {
                        $("da-form-id").value = saved.id;
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
            window.showDesignApplicationsList();
        }).catch(function (err) {
            console.error("submit design application failed:", err);
            showToast(err.message || t("applications.submit_failed", "Submit failed"), "error");
        });
    };

    window.editDesignApplication = function (appId) {
        fetchAuth("/api/v1/applications/" + appId)
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (app) {
                window.showDesignApplicationForm(app);
            })
            .catch(function (err) {
                console.error("edit design application failed:", err);
                showToast(t("applications.load_error", "Load failed"), "error");
            });
    };

    window.deleteDesignApplication = function (appId) {
        if (!confirm(t("applications.confirm_delete", "Delete this draft?"))) return;
        fetchAuth("/api/v1/applications/" + appId, { method: "DELETE" })
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function () {
                showToast(t("applications.deleted_success", "Deleted"), "success");
                loadList();
            })
            .catch(function (err) {
                console.error("delete design application failed:", err);
                showToast(t("applications.delete_failed", "Delete failed"), "error");
            });
    };
})();
