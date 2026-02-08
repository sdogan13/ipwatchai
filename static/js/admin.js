/**
 * admin.js - Admin Panel Alpine.js Components
 * All admin functionality: auth, API helper, toast, and per-tab components.
 */

// ============ AUTH & API HELPER ============

function getToken() {
    return localStorage.getItem('auth_token') || sessionStorage.getItem('auth_token') || '';
}

async function adminFetch(url, options = {}) {
    var token = getToken();
    if (!token) {
        window.location.href = '/dashboard';
        return null;
    }
    var headers = {
        'Authorization': 'Bearer ' + token,
    };
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }
    try {
        var response = await fetch(url, Object.assign({}, options, {
            headers: Object.assign(headers, options.headers || {}),
        }));
        if (response.status === 403) {
            showAdminToast('Superadmin access required. Redirecting...', 'error');
            setTimeout(function() { window.location.href = '/dashboard'; }, 1500);
            return null;
        }
        if (response.status === 401) {
            window.location.href = '/dashboard';
            return null;
        }
        return response;
    } catch (e) {
        showAdminToast('Network error', 'error');
        return null;
    }
}

async function adminAction(url, options, successMsg) {
    var res = await adminFetch(url, options);
    if (res && res.ok) {
        showAdminToast(successMsg || 'Done', 'success');
        try { return await res.json(); } catch(e) { return {}; }
    } else if (res) {
        try {
            var err = await res.json();
            showAdminToast(err.detail || 'Something went wrong', 'error');
        } catch(e) {
            showAdminToast('Request failed (' + res.status + ')', 'error');
        }
    }
    return null;
}

// ============ TOAST ============

function showAdminToast(message, type) {
    type = type || 'success';
    var container = document.getElementById('admin-toast-container');
    if (!container) return;
    var toast = document.createElement('div');
    var colors = {
        success: 'bg-green-600',
        error: 'bg-red-600',
        info: 'bg-blue-600',
        warning: 'bg-amber-600',
    };
    toast.className = 'px-4 py-2.5 rounded-lg text-white text-sm shadow-lg transition-all duration-300 ' + (colors[type] || colors.info);
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        setTimeout(function() { toast.remove(); }, 300);
    }, 3500);
}

// ============ MAIN ADMIN PANEL ============

function adminPanel() {
    return {
        activeTab: 'overview',
        currentUser: null,
        authorized: false,
        tabs: [
            { id: 'overview',      icon: '\u{1F4CA}', label: 'Overview' },
            { id: 'organizations', icon: '\u{1F3E2}', label: 'Organizations' },
            { id: 'users',         icon: '\u{1F465}', label: 'Users' },
            { id: 'plans',         icon: '\u{1F4B0}', label: 'Plans & Limits' },
            { id: 'credits',       icon: '\u{1F39F}', label: 'Credits' },
            { id: 'discounts',     icon: '\u{1F3F7}', label: 'Discount Codes' },
            { id: 'rate_limits',   icon: '\u{23F1}',  label: 'Rate Limits' },
            { id: 'features',      icon: '\u{1F527}', label: 'Feature Flags' },
            { id: 'analytics',     icon: '\u{1F4C8}', label: 'Analytics' },
            { id: 'audit',         icon: '\u{1F4CB}', label: 'Audit Log' },
            { id: 'settings',      icon: '\u{2699}',  label: 'All Settings' },
        ],

        async init() {
            var res = await adminFetch('/api/v1/admin/overview');
            if (!res || !res.ok) {
                window.location.href = '/dashboard';
                return;
            }
            // Decode JWT for user info
            var token = getToken();
            if (token) {
                try {
                    var payload = JSON.parse(atob(token.split('.')[1]));
                    this.currentUser = payload;
                } catch(e) {}
            }
            // Also try /me endpoint for more reliable data
            try {
                var meRes = await adminFetch('/api/v1/auth/me');
                if (meRes && meRes.ok) {
                    var profile = await meRes.json();
                    this.currentUser = Object.assign(this.currentUser || {}, {
                        email: profile.email,
                        first_name: profile.first_name,
                        is_superadmin: profile.is_superadmin
                    });
                }
            } catch(e) {}
            this.authorized = true;
        }
    };
}


// ============ OVERVIEW ============

function adminOverview() {
    return {
        stats: null,
        loading: true,

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/overview');
            if (res && res.ok) {
                this.stats = await res.json();
            }
            this.loading = false;
        }
    };
}


// ============ ORGANIZATIONS ============

function adminOrganizations() {
    return {
        orgs: [],
        total: 0,
        search: '',
        planFilter: '',
        activeFilter: '',
        page: 0,
        limit: 20,
        loading: true,
        detailOrg: null,

        async load() {
            this.loading = true;
            var params = new URLSearchParams({ limit: this.limit, offset: this.page * this.limit });
            if (this.search) params.set('search', this.search);
            if (this.planFilter) params.set('plan', this.planFilter);
            if (this.activeFilter !== '') params.set('is_active', this.activeFilter);

            var res = await adminFetch('/api/v1/admin/organizations?' + params);
            if (res && res.ok) {
                var data = await res.json();
                this.orgs = data.organizations || [];
                this.total = data.total || 0;
            }
            this.loading = false;
        },

        async showDetail(org) {
            var res = await adminFetch('/api/v1/admin/organizations/' + org.id);
            if (res && res.ok) {
                this.detailOrg = await res.json();
            }
        },

        async changePlan(orgId, planName) {
            if (!planName) return;
            if (!confirm('Change plan to ' + planName + '?')) return;
            var result = await adminAction('/api/v1/admin/organizations/' + orgId + '/plan', {
                method: 'PUT',
                body: JSON.stringify({ plan_name: planName }),
            }, 'Plan changed');
            if (result) this.load();
        },

        async toggleStatus(orgId, isActive) {
            var action = isActive ? 'activate' : 'deactivate';
            if (!confirm('Really ' + action + ' this organization?')) return;
            var result = await adminAction('/api/v1/admin/organizations/' + orgId + '/status', {
                method: 'PUT',
                body: JSON.stringify({ is_active: isActive }),
            }, 'Organization ' + action + 'd');
            if (result) this.load();
        },

        get totalPages() { return Math.ceil(this.total / this.limit) || 1; },
        nextPage() { if (this.page < this.totalPages - 1) { this.page++; this.load(); } },
        prevPage() { if (this.page > 0) { this.page--; this.load(); } },
    };
}


// ============ USERS ============

function adminUsers() {
    return {
        users: [],
        total: 0,
        search: '',
        roleFilter: '',
        activeFilter: '',
        page: 0,
        limit: 20,
        loading: true,

        async load() {
            this.loading = true;
            var params = new URLSearchParams({ limit: this.limit, offset: this.page * this.limit });
            if (this.search) params.set('search', this.search);
            if (this.roleFilter) params.set('role', this.roleFilter);
            if (this.activeFilter !== '') params.set('is_active', this.activeFilter);

            var res = await adminFetch('/api/v1/admin/users?' + params);
            if (res && res.ok) {
                var data = await res.json();
                this.users = data.users || [];
                this.total = data.total || 0;
            }
            this.loading = false;
        },

        async changeRole(userId, newRole) {
            if (!confirm('Change role to ' + newRole + '?')) return;
            var result = await adminAction('/api/v1/admin/users/' + userId + '/role', {
                method: 'PUT',
                body: JSON.stringify({ role: newRole }),
            }, 'Role changed');
            if (result) this.load();
        },

        async toggleActive(userId, isActive) {
            var action = isActive ? 'activate' : 'deactivate';
            if (!confirm('Really ' + action + ' this user?')) return;
            var result = await adminAction('/api/v1/admin/users/' + userId + '/status', {
                method: 'PUT',
                body: JSON.stringify({ is_active: isActive }),
            }, 'User ' + action + 'd');
            if (result) this.load();
        },

        async toggleSuperadmin(userId, isSuperadmin, email) {
            var action = isSuperadmin ? 'GRANT superadmin to' : 'REVOKE superadmin from';
            if (!confirm(action + ' ' + email + '? This is a critical action.')) return;
            var result = await adminAction('/api/v1/admin/users/' + userId + '/superadmin', {
                method: 'PUT',
                body: JSON.stringify({ is_superadmin: isSuperadmin }),
            }, 'Superadmin ' + (isSuperadmin ? 'granted' : 'revoked'));
            if (result) this.load();
        },

        get totalPages() { return Math.ceil(this.total / this.limit) || 1; },
        nextPage() { if (this.page < this.totalPages - 1) { this.page++; this.load(); } },
        prevPage() { if (this.page > 0) { this.page--; this.load(); } },
    };
}


// ============ PLANS & LIMITS ============

function adminPlans() {
    return {
        plans: [],
        loading: true,

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/plans');
            if (res && res.ok) {
                var data = await res.json();
                this.plans = data.plans || [];
            }
            this.loading = false;
        },

        async saveLimit(planName, feature, value) {
            // Parse value: try number, then boolean, then string
            var parsed = value;
            if (value === 'true') parsed = true;
            else if (value === 'false') parsed = false;
            else if (!isNaN(value) && value !== '') parsed = Number(value);

            var key = 'plan.' + planName + '.' + feature;
            await adminAction('/api/v1/admin/settings/' + key, {
                method: 'PUT',
                body: JSON.stringify({
                    value: parsed,
                    category: 'plan_limits',
                    value_type: typeof parsed === 'number' ? 'integer' : typeof parsed === 'boolean' ? 'boolean' : 'string',
                }),
            }, 'Saved ' + feature);
            this.load();
        },

        async resetLimit(planName, feature) {
            var key = 'plan.' + planName + '.' + feature;
            await adminAction('/api/v1/admin/settings/' + key, {
                method: 'DELETE',
            }, 'Reset to default');
            this.load();
        },

        async updatePricing(planName, updates) {
            await adminAction('/api/v1/admin/plans/' + planName + '/pricing', {
                method: 'PUT',
                body: JSON.stringify(updates),
            }, 'Pricing updated');
        },
    };
}


// ============ CREDITS ============

function adminCredits() {
    return {
        orgSearch: '',
        orgResults: [],
        selectedOrg: null,
        credits: null,
        adjustForm: { credit_type: 'logo_purchased', operation: 'add', amount: 0, reason: '' },
        bulkForm: { plan_filter: 'all', credit_type: 'logo_purchased', operation: 'add', amount: 0, reason: '' },

        async load() {
            // No initial load needed — user searches for an org
        },

        async searchOrgs() {
            if (this.orgSearch.length < 2) { this.orgResults = []; return; }
            var res = await adminFetch('/api/v1/admin/organizations?search=' + encodeURIComponent(this.orgSearch) + '&limit=10');
            if (res && res.ok) {
                var data = await res.json();
                this.orgResults = data.organizations || [];
            }
        },

        async selectOrg(org) {
            this.selectedOrg = org;
            this.orgResults = [];
            this.orgSearch = org.name;
            await this.loadCredits();
        },

        async loadCredits() {
            if (!this.selectedOrg) return;
            var res = await adminFetch('/api/v1/admin/organizations/' + this.selectedOrg.id + '/credits');
            if (res && res.ok) {
                this.credits = await res.json();
            }
        },

        async adjustCredits() {
            if (!this.selectedOrg) return;
            var result = await adminAction('/api/v1/admin/organizations/' + this.selectedOrg.id + '/credits', {
                method: 'PUT',
                body: JSON.stringify(this.adjustForm),
            }, 'Credits adjusted');
            if (result) this.loadCredits();
        },

        async bulkAdjust() {
            if (!confirm('Apply bulk credit operation to ' + this.bulkForm.plan_filter + ' plans?')) return;
            await adminAction('/api/v1/admin/credits/bulk', {
                method: 'POST',
                body: JSON.stringify(this.bulkForm),
            }, 'Bulk credits applied');
        },
    };
}


// ============ DISCOUNT CODES ============

function adminDiscounts() {
    return {
        codes: [],
        loading: true,
        showCreate: false,
        editingCode: null,
        form: { code: '', description: '', discount_type: 'percentage', discount_value: 10, applies_to_plan: '', max_uses: null, valid_until: '' },

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/discount-codes');
            if (res && res.ok) {
                var data = await res.json();
                this.codes = data.discount_codes || [];
            }
            this.loading = false;
        },

        async createCode() {
            var payload = Object.assign({}, this.form);
            if (!payload.applies_to_plan) delete payload.applies_to_plan;
            if (!payload.max_uses) delete payload.max_uses;
            if (!payload.valid_until) delete payload.valid_until;
            else payload.valid_until = payload.valid_until + 'T23:59:59';

            var result = await adminAction('/api/v1/admin/discount-codes', {
                method: 'POST',
                body: JSON.stringify(payload),
            }, 'Code created');
            if (result) {
                this.showCreate = false;
                this.resetForm();
                this.load();
            }
        },

        editCode(code) {
            this.editingCode = code;
            this.form = {
                description: code.description || '',
                discount_type: code.discount_type,
                discount_value: code.discount_value,
                applies_to_plan: code.applies_to_plan || '',
                max_uses: code.max_uses,
                valid_until: code.valid_until ? code.valid_until.split('T')[0] : '',
            };
        },

        async updateCode() {
            var updates = {};
            if (this.form.description !== (this.editingCode.description || '')) updates.description = this.form.description;
            if (this.form.discount_value !== this.editingCode.discount_value) updates.discount_value = this.form.discount_value;
            if (this.form.max_uses !== this.editingCode.max_uses) updates.max_uses = this.form.max_uses;
            if (this.form.valid_until) updates.valid_until = this.form.valid_until + 'T23:59:59';

            if (Object.keys(updates).length === 0) {
                this.editingCode = null;
                return;
            }
            var result = await adminAction('/api/v1/admin/discount-codes/' + this.editingCode.id, {
                method: 'PUT',
                body: JSON.stringify(updates),
            }, 'Code updated');
            if (result) {
                this.editingCode = null;
                this.resetForm();
                this.load();
            }
        },

        async deactivateCode(codeId) {
            if (!confirm('Deactivate this discount code?')) return;
            var result = await adminAction('/api/v1/admin/discount-codes/' + codeId, {
                method: 'DELETE',
            }, 'Code deactivated');
            if (result) this.load();
        },

        resetForm() {
            this.form = { code: '', description: '', discount_type: 'percentage', discount_value: 10, applies_to_plan: '', max_uses: null, valid_until: '' };
        },
    };
}


// ============ RATE LIMITS ============

function adminRateLimits() {
    return {
        items: [],
        loading: true,

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/settings/rate_limits');
            if (res && res.ok) {
                var data = await res.json();
                this.items = Object.entries(data).map(function(entry) {
                    return Object.assign({ key: entry[0] }, entry[1]);
                }).filter(function(item) {
                    return item.key.startsWith('rate_limit.');
                });
            }
            this.loading = false;
        },

        async saveSetting(key, value) {
            var parsed = !isNaN(value) && value !== '' ? Number(value) : value;
            await adminAction('/api/v1/admin/settings/' + key, {
                method: 'PUT',
                body: JSON.stringify({ value: parsed, category: 'rate_limits', value_type: 'integer' }),
            }, 'Rate limit saved');
        },

        async deleteSetting(key) {
            if (!confirm('Reset ' + key + ' to code default?')) return;
            await adminAction('/api/v1/admin/settings/' + key, {
                method: 'DELETE',
            }, 'Reset to default');
            this.load();
        },
    };
}


// ============ FEATURE FLAGS ============

function adminFeatures() {
    return {
        items: [],
        loading: true,

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/settings/feature_flags');
            if (res && res.ok) {
                var data = await res.json();
                this.items = Object.entries(data).map(function(entry) {
                    return Object.assign({ key: entry[0] }, entry[1]);
                }).filter(function(item) {
                    return item.key.startsWith('feature.');
                });
            }
            this.loading = false;
        },

        async toggleFeature(item) {
            var newValue = !item.value;
            var result = await adminAction('/api/v1/admin/settings/' + item.key, {
                method: 'PUT',
                body: JSON.stringify({ value: newValue, category: 'feature_flags', value_type: 'boolean' }),
            }, item.key.replace('feature.', '') + (newValue ? ' enabled' : ' disabled'));
            if (result) {
                item.value = newValue;
            }
        },
    };
}


// ============ ANALYTICS ============

function adminAnalytics() {
    return {
        data: null,
        days: 30,
        loading: true,
        usageChart: null,
        planChart: null,

        get totalSearches() {
            if (!this.data || !this.data.daily_usage) return 0;
            return this.data.daily_usage.reduce(function(s, d) {
                return s + (d.quick_searches || 0) + (d.live_searches || 0);
            }, 0);
        },

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/analytics/usage?days=' + this.days);
            if (res && res.ok) {
                this.data = await res.json();
                this.$nextTick(function() { this.renderCharts(); }.bind(this));
            }
            this.loading = false;
        },

        renderCharts() {
            this.renderUsageChart();
            this.renderPlanChart();
        },

        renderUsageChart() {
            var canvas = document.getElementById('admin-usage-chart');
            if (!canvas || !this.data) return;
            if (this.usageChart) this.usageChart.destroy();

            var daily = (this.data.daily_usage || []).slice().reverse();
            var labels = daily.map(function(d) { return d.date ? d.date.split('T')[0] : ''; });

            this.usageChart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Quick Searches',
                            data: daily.map(function(d) { return d.quick_searches || 0; }),
                            borderColor: '#6366f1',
                            backgroundColor: 'rgba(99,102,241,0.1)',
                            fill: true,
                            tension: 0.3,
                        },
                        {
                            label: 'Live Searches',
                            data: daily.map(function(d) { return d.live_searches || 0; }),
                            borderColor: '#f59e0b',
                            backgroundColor: 'rgba(245,158,11,0.1)',
                            fill: true,
                            tension: 0.3,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { position: 'top' } },
                    scales: { y: { beginAtZero: true } },
                },
            });
        },

        renderPlanChart() {
            var canvas = document.getElementById('admin-plan-chart');
            if (!canvas || !this.data) return;
            if (this.planChart) this.planChart.destroy();

            var byPlan = this.data.usage_by_plan || {};
            var planLabels = Object.keys(byPlan);
            var planColors = {
                free: '#9ca3af',
                starter: '#60a5fa',
                professional: '#a78bfa',
                enterprise: '#fbbf24',
            };

            this.planChart = new Chart(canvas, {
                type: 'doughnut',
                data: {
                    labels: planLabels,
                    datasets: [{
                        data: planLabels.map(function(p) { return byPlan[p] || 0; }),
                        backgroundColor: planLabels.map(function(p) { return planColors[p] || '#d1d5db'; }),
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { position: 'bottom' } },
                },
            });
        },

        async exportCsv() {
            var res = await adminFetch('/api/v1/admin/analytics/export?days=' + this.days);
            if (res && res.ok) {
                var blob = await res.blob();
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'usage_export_' + this.days + 'd.csv';
                a.click();
                URL.revokeObjectURL(url);
                showAdminToast('CSV exported', 'success');
            }
        },
    };
}


// ============ AUDIT LOG ============

function adminAuditLog() {
    return {
        entries: [],
        actionFilter: '',
        page: 0,
        limit: 50,
        loading: true,

        async load() {
            this.loading = true;
            var params = new URLSearchParams({ limit: this.limit, offset: this.page * this.limit });
            if (this.actionFilter) params.set('action', this.actionFilter);

            var res = await adminFetch('/api/v1/admin/audit-log?' + params);
            if (res && res.ok) {
                var data = await res.json();
                this.entries = (data.entries || []).map(function(e) {
                    e._expanded = false;
                    return e;
                });
            }
            this.loading = false;
        },

        nextPage() { this.page++; this.load(); },
        prevPage() { if (this.page > 0) { this.page--; this.load(); } },
    };
}


// ============ ALL SETTINGS ============

function adminSettings() {
    return {
        allItems: [],
        filteredItems: [],
        categories: [],
        categoryFilter: '',
        loading: true,
        showAdd: false,
        addForm: { key: '', value: '', category: 'general', value_type: 'string', description: '' },

        async load() {
            this.loading = true;
            var res = await adminFetch('/api/v1/admin/settings');
            if (res && res.ok) {
                var data = await res.json();
                this.allItems = Object.entries(data).map(function(entry) {
                    return Object.assign({ key: entry[0] }, entry[1]);
                });
                // Extract unique categories
                var cats = {};
                this.allItems.forEach(function(item) {
                    if (item.category) cats[item.category] = true;
                });
                this.categories = Object.keys(cats).sort();
                this.filterSettings();
            }
            this.loading = false;
        },

        filterSettings() {
            if (!this.categoryFilter) {
                this.filteredItems = this.allItems;
            } else {
                var cat = this.categoryFilter;
                this.filteredItems = this.allItems.filter(function(item) {
                    return item.category === cat;
                });
            }
        },

        async saveItem(key, value, category, valueType) {
            // Try to parse JSON values
            var parsed = value;
            if (value === 'true') parsed = true;
            else if (value === 'false') parsed = false;
            else if (!isNaN(value) && value !== '') parsed = Number(value);
            else {
                try { parsed = JSON.parse(value); } catch(e) { /* keep as string */ }
            }

            await adminAction('/api/v1/admin/settings/' + key, {
                method: 'PUT',
                body: JSON.stringify({
                    value: parsed,
                    category: category || 'general',
                    value_type: valueType || 'string',
                }),
            }, 'Setting saved');
        },

        async deleteItem(key) {
            if (!confirm('Delete setting "' + key + '"? This reverts to code default.')) return;
            var result = await adminAction('/api/v1/admin/settings/' + key, {
                method: 'DELETE',
            }, 'Setting deleted');
            if (result) this.load();
        },

        async addSetting() {
            var parsed = this.addForm.value;
            if (this.addForm.value_type === 'boolean') parsed = this.addForm.value === 'true';
            else if (this.addForm.value_type === 'integer') parsed = Number(this.addForm.value);
            else if (this.addForm.value_type === 'json') {
                try { parsed = JSON.parse(this.addForm.value); } catch(e) {
                    showAdminToast('Invalid JSON value', 'error');
                    return;
                }
            }

            var result = await adminAction('/api/v1/admin/settings/' + this.addForm.key, {
                method: 'PUT',
                body: JSON.stringify({
                    value: parsed,
                    category: this.addForm.category,
                    value_type: this.addForm.value_type,
                    description: this.addForm.description,
                }),
            }, 'Setting added');
            if (result) {
                this.showAdd = false;
                this.addForm = { key: '', value: '', category: 'general', value_type: 'string', description: '' };
                this.load();
            }
        },
    };
}
