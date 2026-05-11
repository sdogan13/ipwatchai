/**
 * Patent Applications driver — Phase 2.
 *
 * Hits /api/v1/applications?registry=patent on the shared applications
 * endpoint. Patent-specific shape:
 *   - `brand_name` column holds the invention title
 *   - `classification_codes` holds IPC codes (alphanumeric, e.g. "G06F 17/30")
 *   - `details.patent_kind`   = 'patent' | 'utility_model'
 *   - `details.abstract`      = invention abstract
 *   - `details.claims`        = patent claims text
 *   - `details.inventors`     = inventor full names (free-form string;
 *                               UI accepts comma-separated, but we don't
 *                               normalize beyond trim — preserves the
 *                               user's exact entry for later editing)
 *
 * All DOM IDs prefixed with `pa-`.
 */
(function () {
    "use strict";

    var state = {
        initialized: false,
        page: 1,
        pageSize: 20,
        statusFilter: null,
        typeFilter: null,
        selectedIpcClasses: [],  // free-form alphanumeric strings
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
    // Init + tab entry
    // -----------------------------------------------------------------

    window.initPatentApplicationsTab = function () {
        if (!state.initialized) {
            state.initialized = true;
            // No dropdown to populate — IPC codes are free-form text entry.
        }
        loadList();
    };

    // -----------------------------------------------------------------
    // Filter buttons
    // -----------------------------------------------------------------

    function setActiveStatusFilter(status) {
        document.querySelectorAll(".pa-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("pa-filter-" + (status || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    function setActiveTypeFilter(appType) {
        document.querySelectorAll(".pa-type-filter-btn").forEach(function (btn) {
            btn.classList.remove("bg-indigo-600", "text-white");
            btn.style.color = "var(--color-text-muted)";
            btn.style.background = "var(--color-bg-muted)";
        });
        var activeBtn = $("pa-type-filter-" + (appType || "all"));
        if (activeBtn) {
            activeBtn.classList.add("bg-indigo-600", "text-white");
            activeBtn.style.color = "";
            activeBtn.style.background = "";
        }
    }

    window.filterPatentApplications = function (status) {
        state.statusFilter = status;
        state.page = 1;
        setActiveStatusFilter(status);
        loadList();
    };

    window.filterPatentApplicationsByType = function (appType) {
        state.typeFilter = appType;
        state.page = 1;
        setActiveTypeFilter(appType);
        loadList();
    };

    window.loadPatentApplicationsPage = function (page) {
        state.page = page;
        loadList();
    };

    // -----------------------------------------------------------------
    // List
    // -----------------------------------------------------------------

    function loadList() {
        var listEl = $("pa-list");
        var loadingEl = $("pa-list-loading");
        var emptyEl = $("pa-list-empty");
        var paginationEl = $("pa-pagination");

        if (!listEl) return;

        listEl.innerHTML = "";
        loadingEl.classList.remove("hidden");
        emptyEl.classList.add("hidden");
        paginationEl.classList.add("hidden");

        var url = "/api/v1/applications/?registry=patent&page=" + state.page + "&page_size=" + state.pageSize;
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
                        html += '<button onclick="loadPatentApplicationsPage(' + (state.page - 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">&laquo; ' + t("applications.prev", "Prev") + "</button>";
                    }
                    html += '<span class="text-sm" style="color:var(--color-text-muted)">' + state.page + " / " + data.total_pages + "</span>";
                    if (state.page < data.total_pages) {
                        html += '<button onclick="loadPatentApplicationsPage(' + (state.page + 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' + t("applications.next", "Next") + " &raquo;</button>";
                    }
                    paginationEl.innerHTML = html;
                }
            })
            .catch(function (err) {
                loadingEl.classList.add("hidden");
                console.error("Failed to load patent applications:", err);
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
        var kind = (app.details && app.details.patent_kind) || "patent";
        var kindLabel = t("applications.patent_kind_" + kind) || kind;
        var ipc = (app.classification_codes && app.classification_codes.length)
            ? app.classification_codes.map(escapeHtml).join(", ")
            : "";
        var ipcHtml = ipc
            ? '<span class="text-xs px-2 py-0.5 rounded font-mono" style="background:var(--color-bg-muted);color:var(--color-text-muted)">IPC: ' + ipc + "</span>"
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
            '        <span class="text-xs px-2 py-0.5 rounded" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + escapeHtml(kindLabel) + "</span>",
            "      </div>",
            '      <div class="flex items-center gap-2 flex-wrap">',
            ipcHtml,
            "      </div>",
            "    </div>",
            '    <div class="flex items-center gap-2 shrink-0">',
            editable
                ? '      <button onclick="editPatentApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + t("applications.edit", "Edit") + "</button>"
                : "",
            editable
                ? '      <button onclick="deletePatentApplication(\'' + app.id + '\')" class="px-3 py-1.5 rounded-lg text-xs font-medium btn-press text-red-600" style="background:rgba(239,68,68,0.1)">' + t("applications.delete", "Delete") + "</button>"
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
        ["pa-form-id", "pa-applicant-name", "pa-applicant-id-no", "pa-applicant-phone",
         "pa-applicant-email", "pa-applicant-address", "pa-invention-title",
         "pa-abstract", "pa-claims", "pa-inventors", "pa-notes",
         "pa-ipc-class-input"].forEach(function (id) {
            var el = $(id);
            if (el) el.value = "";
        });
        var idType = $("pa-applicant-id-type"); if (idType) idType.value = "tc_kimlik";
        var appType = $("pa-application-type"); if (appType) appType.value = "registration";
        var kind = $("pa-patent-kind"); if (kind) kind.value = "patent";
        state.selectedIpcClasses = [];
        renderIpcChips();
    }

    function renderIpcChips() {
        var container = $("pa-ipc-classes-container");
        if (!container) return;
        if (state.selectedIpcClasses.length === 0) {
            container.innerHTML = '<span class="text-xs italic" style="color:var(--color-text-faint)">' + t("applications.no_classes", "No classes added") + "</span>";
            return;
        }
        container.innerHTML = state.selectedIpcClasses.map(function (cls) {
            return [
                '<span class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono" style="background:rgba(99,102,241,0.12);color:var(--color-primary)">',
                escapeHtml(cls),
                '<button onclick="(function(){ var s = window.__paRemoveIpc; s && s(\'' + cls.replace(/'/g, "\\'") + '\'); })()" class="hover:opacity-70 font-sans">&times;</button>',
                "</span>",
            ].join("");
        }).join("");
    }

    window.__paRemoveIpc = function (cls) {
        state.selectedIpcClasses = state.selectedIpcClasses.filter(function (c) { return c !== cls; });
        renderIpcChips();
    };

    window.addPatentIpcClass = function () {
        var input = $("pa-ipc-class-input");
        if (!input) return;
        var value = (input.value || "").trim();
        if (!value) return;
        if (state.selectedIpcClasses.indexOf(value) !== -1) {
            showToast(t("applications.class_already_added", "Already added"), "warning");
            return;
        }
        state.selectedIpcClasses.push(value);
        renderIpcChips();
        input.value = "";
        input.focus();
    };

    window.showPatentApplicationForm = function (existing) {
        var listView = $("pa-list-view");
        var formView = $("pa-form-view");
        if (!listView || !formView) return;
        listView.classList.add("hidden");
        formView.classList.remove("hidden");
        clearForm();
        if (existing) {
            $("pa-form-id").value = existing.id || "";
            $("pa-applicant-name").value = existing.applicant_full_name || "";
            $("pa-applicant-id-type").value = existing.applicant_id_type || "tc_kimlik";
            $("pa-applicant-id-no").value = existing.applicant_id_no || "";
            $("pa-applicant-phone").value = existing.applicant_phone || "";
            $("pa-applicant-email").value = existing.applicant_email || "";
            $("pa-applicant-address").value = existing.applicant_address || "";
            $("pa-invention-title").value = existing.brand_name || "";
            $("pa-application-type").value = existing.application_type || "registration";
            var d = existing.details || {};
            $("pa-patent-kind").value = d.patent_kind || "patent";
            $("pa-abstract").value = d.abstract || "";
            $("pa-claims").value = d.claims || "";
            $("pa-inventors").value = d.inventors || "";
            $("pa-notes").value = existing.notes || "";
            state.selectedIpcClasses = (existing.classification_codes || []).slice();
            renderIpcChips();
        }
    };

    window.showPatentApplicationsList = function () {
        var listView = $("pa-list-view");
        var formView = $("pa-form-view");
        if (listView) listView.classList.remove("hidden");
        if (formView) formView.classList.add("hidden");
        loadList();
    };

    function collectFormBody() {
        var idType = $("pa-applicant-id-type").value || "tc_kimlik";
        var details = {
            patent_kind: $("pa-patent-kind").value || "patent",
            abstract: $("pa-abstract").value.trim() || null,
            claims: $("pa-claims").value.trim() || null,
            inventors: $("pa-inventors").value.trim() || null,
        };
        return {
            registry_kind: "patent",
            application_type: $("pa-application-type").value || "registration",
            brand_name: $("pa-invention-title").value.trim() || "—",
            mark_type: "word",  // unused for patent; column is NOT NULL
            classification_codes: state.selectedIpcClasses.slice(),
            nice_class_numbers: [],
            details: details,
            applicant_full_name: $("pa-applicant-name").value.trim() || null,
            applicant_id_type: idType,
            applicant_id_no: $("pa-applicant-id-no").value.trim() || null,
            applicant_address: $("pa-applicant-address").value.trim() || null,
            applicant_phone: $("pa-applicant-phone").value.trim() || null,
            applicant_email: $("pa-applicant-email").value.trim() || null,
            notes: $("pa-notes").value.trim() || null,
        };
    }

    window.savePatentApplication = function (_mode) {
        var body = collectFormBody();
        if (!body.brand_name || body.brand_name === "—") {
            showToast(t("applications.invention_title_required", "Invention title is required"), "error");
            return;
        }
        var appId = $("pa-form-id").value;
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
                $("pa-form-id").value = saved.id;
                window.showPatentApplicationsList();
            })
            .catch(function (err) {
                console.error("save patent application failed:", err);
                showToast(err.message || t("applications.save_failed", "Save failed"), "error");
            });
    };

    window.submitPatentApplication = function () {
        var appId = $("pa-form-id").value;
        var save = appId
            ? Promise.resolve({ id: appId })
            : new Promise(function (resolve, reject) {
                var body = collectFormBody();
                if (!body.brand_name || body.brand_name === "—") {
                    reject(new Error(t("applications.invention_title_required", "Invention title is required")));
                    return;
                }
                fetchAuth("/api/v1/applications/", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                }).then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { reject(new Error((d && d.detail) || ("HTTP " + r.status))); });
                    r.json().then(function (saved) {
                        $("pa-form-id").value = saved.id;
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
            window.showPatentApplicationsList();
        }).catch(function (err) {
            console.error("submit patent application failed:", err);
            showToast(err.message || t("applications.submit_failed", "Submit failed"), "error");
        });
    };

    window.editPatentApplication = function (appId) {
        fetchAuth("/api/v1/applications/" + appId)
            .then(function (r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function (app) {
                window.showPatentApplicationForm(app);
            })
            .catch(function (err) {
                console.error("edit patent application failed:", err);
                showToast(t("applications.load_error", "Load failed"), "error");
            });
    };

    window.deletePatentApplication = function (appId) {
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
                console.error("delete patent application failed:", err);
                showToast(t("applications.delete_failed", "Delete failed"), "error");
            });
    };
})();
