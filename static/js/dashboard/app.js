/**
 * app.js - Main Alpine.js app initialization + remaining UI functions
 * Loaded last - depends on all other JS files
 */

// ============================================
// MOBILE HELPERS
// ============================================
var _scrollY = 0;
function lockBodyScroll() {
    _scrollY = window.scrollY;
    document.body.classList.add('modal-open');
    document.body.style.top = '-' + _scrollY + 'px';
}
function unlockBodyScroll() {
    document.body.classList.remove('modal-open');
    document.body.style.top = '';
    window.scrollTo(0, _scrollY);
}

function populateNiceClassOptions(selectEl) {
    if (!selectEl) return;
    for (var i = 1; i <= 45; i++) {
        var option = document.createElement('option');
        option.value = i;
        option.textContent = i + ' - ' + t('nice_classes.' + i);
        selectEl.appendChild(option);
    }
}

function getEffectiveTextScoreForResult(result) {
    if (window.AppComponents && window.AppComponents.getOriginalTextScore) {
        return window.AppComponents.getOriginalTextScore(result);
    }
    var scores = result && result.scores ? result.scores : (result || {});
    if (scores.path_a_score !== undefined && scores.path_a_score !== null) return scores.path_a_score;
    return scores.text_similarity || 0;
}

// ============================================
// GLOBAL STATE
// ============================================
var agenticSearchAborted = false;
var currentLeadPage = 1;
var currentLeadId = null;
var radarInitialized = false;
var studioInitialized = false;
var studioActiveMode = 'name';
var studioNameLoading = false;
var studioLogoLoading = false;
var studioStatus = null;
var studioCredits = null;
var studioHistoryFilter = 'all';
var studioLastNameResult = null;
var studioLastLogoResult = null;
var studioActiveLogoProjectId = null;
var studioSelectedLogoImageId = null;
var studioLogoPollTimer = null;
var LEADS_PER_PAGE = 20;
var DASHBOARD_SEARCH_RESULT_LIMIT = 20;
var currentHolderTpeId = null;
var _storedSearchResults = [];
var _lastSearchBannerHtml = '';
var currentSearchTotal = 0;
var currentSearchType = 'quick';

// Watchlist cache — tracks which application_nos are already monitored
var userWatchlistAppNos = {};  // using object as Set for IE compat

// ============================================
// ALPINE.JS DASHBOARD COMPONENT
// ============================================
function dashboard() {
    return {
        userId: '...',
        stats: {},
        alerts: [],
        alertsSummary: null,
        watchlist: [],
        deadlines: [],
        renewalDeadlines: [],
        chartInstance: null,
        chartLibraryWaits: 0,
        lang_code: window.AppI18n ? window.AppI18n._locale : 'tr',
        currentLang: window.AppI18n ? window.AppI18n._locale : 'tr',

        // ===== Search state =====
        dbCount: 0,
        designCount: 0,
        patentCount: 0,
        cografiCount: 0,
        searchQuery: '',
        searchResults: [],
        searchLoading: false,
        searchError: '',
        searchType: 'quick',
        searchMeta: {},
        expandedResult: null,
        lightboxImage: '',
        sortMode: 'risk_desc',
        riskReportLoading: false,
        riskReportError: '',
        riskReport: null,

        // ===== Search history state =====
        searchHistory: [],
        showSearchHistory: false,

        // ===== Image upload state =====
        selectedImage: null,
        imagePreview: '',
        imageName: '',
        dragOver: false,

        // ===== Class selection state =====
        selectedClasses: [],
        classInput: '',
        suggestedClasses: [],
        suggesting: false,
        classError: '',
        allClasses: [],
        browseLoading: false,
        browseFilter: '',

        // ===== Portfolio state =====
        portfolioResults: [],
        portfolioName: '',
        portfolioType: '',
        portfolioLoading: false,
        showPortfolio: false,

        // ===== Profile state =====
        showProfileModal: false,
        profileEmail: '',
        profileAvatar: '',
        profileData: { first_name: '', last_name: '', email: '', phone: '', title: '', department: '', plan: '', created_at: null, avatar_url: '' },
        profilePassword: { current: '', newPw: '', confirm: '' },
        showProfileCurrentPw: false,
        showProfileNewPw: false,
        showProfileConfirmPw: false,
        profileSaving: false,
        profileMessage: '',
        profileMessageType: 'success',
        avatarUploading: false,

        // ===== Email verification state =====
        showEmailVerification: false,
        verificationCode: '',
        verificationLoading: false,
        verificationError: '',
        verificationSuccess: '',
        verificationResendCooldown: 0,

        // Reactive t() wrapper — Alpine tracks lang_code dependency,
        // so all x-text="t('key')" re-render when locale changes
        t(key, params) {
            void this.lang_code;
            return window.AppI18n.t(key, params);
        },

        init() {
            this.loadData();
            this.loadSearchHistory();

            // Re-render all i18n bindings when locale finishes loading
            var self = this;
            window.addEventListener('locale-changed', function (e) {
                self.lang_code = e.detail.locale + '_' + Date.now();
                self.currentLang = e.detail.locale;
                // Reload Nice classes in new language
                if (self.allClasses.length > 0) {
                    self.allClasses = [];
                    self.loadAllClasses();
                }
                refreshStudioDynamicTranslations();
                // Re-render watchlist grid (innerHTML-based, not Alpine-reactive)
                if (_watchlistItemsCache && _watchlistItemsCache.length > 0) {
                    renderPortfolioGrid(_watchlistItemsCache);
                    // Re-open the expanded conflict panel in the new language
                    if (_expandedWatchlistId) {
                        var panel = document.getElementById('wl-alerts-' + _expandedWatchlistId);
                        if (panel) {
                            panel.classList.remove('hidden');
                            loadInlineAlerts(_expandedWatchlistId, panel);
                        }
                    }
                }
                // Re-render right-panel alerts list (innerHTML-based)
                if (_watchlistAlertsCache && _watchlistAlertsCache.length > 0) {
                    renderWatchlistAlerts(_watchlistAlertsCache);
                }
                if (typeof _updateScanBanner === 'function') {
                    _updateScanBanner();
                }
            });

            // If locale already loaded, trigger a re-render
            if (window.AppI18n._ready) {
                this.lang_code = window.AppI18n._locale + '_ready';
            }

            // Poll for real username from auth profile
            var attempts = 0;
            var pollName = setInterval(function () {
                attempts++;
                if (window.AppAuth && window.AppAuth.currentUserName) {
                    self.userId = window.AppAuth.currentUserName;
                    // Also preload profile email for user menu dropdown
                    self.loadProfile();
                    clearInterval(pollName);
                } else if (attempts >= 50) {
                    clearInterval(pollName);
                }
            }, 200);
        },

        // ==================== IMAGE UPLOAD ====================
        onImageSelected(event) {
            var file = event.target.files && event.target.files[0];
            if (!file) return;
            this._setImage(file);
        },

        handleDrop(event) {
            this.dragOver = false;
            var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
            if (!file) return;
            if (!file.type.match(/^image\/(png|jpeg|webp)$/)) {
                this.searchError = this.t('search.invalid_image_type');
                return;
            }
            if (file.size > 5 * 1024 * 1024) {
                this.searchError = this.t('landing.image_too_large');
                return;
            }
            this._setImage(file);
        },

        _setImage(file) {
            this.selectedImage = file;
            this.imageName = file.name;
            this.searchError = '';
            this.clearRiskReportState();
            var self = this;
            var reader = new FileReader();
            reader.onload = function (e) {
                self.imagePreview = e.target.result;
            };
            reader.readAsDataURL(file);
        },

        clearImage() {
            this.selectedImage = null;
            this.imagePreview = '';
            this.imageName = '';
            this.clearRiskReportState();
            if (this.$refs.dashImageInput) {
                this.$refs.dashImageInput.value = '';
            }
            if (!this.searchQuery.trim()) {
                this.searchResults = [];
                this.searchError = '';
                this.expandedResult = null;
            }
        },

        // ==================== CLASS FINDER ====================
        _syncClassInput() {
            this.classInput = this.selectedClasses.join(', ');
        },

        submitClassInput() {
            var input = this.classInput.trim();
            if (!input) return;
            this.classError = '';

            var parts = input.split(/[,\s]+/).filter(function (p) { return p.length > 0; });
            var allNumbers = parts.every(function (p) {
                var n = parseInt(p, 10);
                return !isNaN(n) && n >= 1 && n <= 45 && String(n) === p.trim();
            });

            if (allNumbers) {
                var self = this;
                var added = 0;
                parts.forEach(function (part) {
                    var num = parseInt(part, 10);
                    if (self.selectedClasses.indexOf(num) === -1) {
                        self.selectedClasses.push(num);
                        added++;
                    }
                });
                if (added === 0) {
                    this.classError = this.t('search.invalid_class_number');
                }
                this._syncClassInput();
            } else {
                this.suggestClasses();
            }
        },

        suggestClasses() {
            var desc = this.classInput.trim();
            if (!desc) return;
            if (desc.length < 3) {
                this.classError = this.t('search.description_too_short');
                return;
            }

            this.suggesting = true;
            this.classError = '';
            this.suggestedClasses = [];
            var self = this;
            var token = getAuthToken();
            var headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = 'Bearer ' + token;

            fetch('/api/suggest-classes', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ description: desc, top_k: 5, lang: self.currentLang || 'tr' })
            })
                .then(function (res) {
                    if (res.ok) return res.json().then(function (d) { return { ok: true, data: d }; });
                    return res.json().catch(function () { return {}; }).then(function (d) {
                        return { ok: false, status: res.status, detail: d && d.detail };
                    });
                })
                .then(function (r) {
                    if (r.ok) {
                        self.suggestedClasses = r.data.suggestions || [];
                        if (self.suggestedClasses.length === 0) {
                            self.classError = self.t('search.no_class_suggestions');
                        }
                        return;
                    }
                    self.suggestedClasses = [];
                    if (r.status === 401 || r.status === 402 || r.status === 403) {
                        var d = r.detail || {};
                        var handled = window.AppUpgradeModal
                            && typeof window.AppUpgradeModal.maybeHandle === 'function'
                            && window.AppUpgradeModal.maybeHandle(d, 'class_suggestions');
                        if (!handled) {
                            var msg = (window.AppI18n && window.AppI18n.locale === 'en')
                                ? (d.message_en || d.message)
                                : (d.message || d.message_en);
                            self.classError = msg || self.t('search.class_suggestion_upgrade_required');
                        } else {
                            // Modal is showing — clear inline error so it's not duplicated.
                            self.classError = '';
                        }
                    } else {
                        self.classError = self.t('search.class_suggestion_failed');
                    }
                })
                .catch(function () {
                    self.suggestedClasses = [];
                    self.classError = self.t('search.class_suggestion_failed');
                })
                .finally(function () {
                    self.suggesting = false;
                });
        },

        selectClass(cls) {
            var num = cls.class_number;
            var idx = this.selectedClasses.indexOf(num);
            if (idx === -1) {
                this.selectedClasses.push(num);
            } else {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        loadAllClasses() {
            if (this.browseLoading) return;
            this.browseLoading = true;
            var self = this;
            fetch('/api/nice-classes?lang=' + (this.currentLang || 'tr'))
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (data) {
                    if (data && data.classes) {
                        self.allClasses = data.classes;
                    }
                })
                .catch(function () { /* silent */ })
                .finally(function () {
                    self.browseLoading = false;
                });
        },

        toggleBrowseClass(num) {
            var idx = this.selectedClasses.indexOf(num);
            if (idx === -1) {
                this.selectedClasses.push(num);
            } else {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        get filteredClasses() {
            if (!this.browseFilter.trim()) return this.allClasses;
            var q = this.browseFilter.trim().toLowerCase();
            return this.allClasses.filter(function (cls) {
                return String(cls.number).indexOf(q) !== -1 ||
                    cls.name.toLowerCase().indexOf(q) !== -1;
            });
        },

        removeClass(num) {
            var idx = this.selectedClasses.indexOf(num);
            if (idx !== -1) {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        clearAllClasses() {
            this.selectedClasses = [];
            this._syncClassInput();
        },

        // ==================== SEARCH HISTORY ====================
        loadSearchHistory() {
            try {
                var raw = localStorage.getItem('search_history');
                this.searchHistory = raw ? JSON.parse(raw) : [];
            } catch (e) { this.searchHistory = []; }
        },

        saveSearchQuery(query) {
            if (!query || !query.trim()) return;
            var q = query.trim();
            // Remove duplicate if exists
            this.searchHistory = this.searchHistory.filter(function (h) { return h.toLowerCase() !== q.toLowerCase(); });
            // Add to front
            this.searchHistory.unshift(q);
            // Keep max 20
            if (this.searchHistory.length > 20) this.searchHistory = this.searchHistory.slice(0, 20);
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch (e) { }
        },

        removeSearchHistoryItem(query) {
            this.searchHistory = this.searchHistory.filter(function (h) { return h !== query; });
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch (e) { }
        },

        clearSearchHistory() {
            this.searchHistory = [];
            try { localStorage.removeItem('search_history'); } catch (e) { }
            this.showSearchHistory = false;
        },

        filteredSearchHistory() {
            var q = (this.searchQuery || '').trim().toLowerCase();
            if (!q) return this.searchHistory.slice(0, 10);
            return this.searchHistory.filter(function (h) { return h.toLowerCase().indexOf(q) !== -1; }).slice(0, 10);
        },

        selectSearchHistoryItem(item) {
            this.searchQuery = item;
            this.showSearchHistory = false;
        },

        // ==================== SEARCH ====================
        async dashboardQuickSearch() {
            var query = this.searchQuery.trim();
            if (!query && !this.selectedImage) {
                showToast(this.t('search.enter_brand_name'), 'error');
                return;
            }

            this.searchLoading = true;
            this.searchError = '';
            this.searchType = 'quick';
            this.searchResults = [];
            this.expandedResult = null;
            this.clearRiskReportState();

            var classes = this.selectedClasses;
            var token = getAuthToken();

            try {
                var res;
                if (this.selectedImage) {
                    // POST with FormData for image
                    var formData = new FormData();
                    if (query) formData.append('query', query);
                    formData.append('image', this.selectedImage);
                    if (classes.length) formData.append('classes', classes.join(','));

                    res = await fetch('/api/v1/search/quick', {
                        method: 'POST',
                        headers: { 'Authorization': 'Bearer ' + token },
                        body: formData
                    });
                } else {
                    // GET text-only
                    var url = '/api/v1/search/quick?query=' + encodeURIComponent(query);
                    if (classes.length) url += '&classes=' + classes.join(',');

                    res = await fetch(url, {
                        headers: { 'Authorization': 'Bearer ' + token }
                    });
                }

                if (res.status === 401) { showToast(this.t('auth.session_expired'), 'error'); return; }
                if (res.status === 429) {
                    var errData = await res.json().catch(function () { return {}; });
                    var detail = errData.detail || errData;
                    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.maybeHandle === 'function'
                        && window.AppUpgradeModal.maybeHandle(detail, 'quick_search')) {
                        return;
                    }
                    this.searchError = this.t('search.rate_limited');
                    showToast(this.searchError, 'warning');
                    return;
                }
                if (!res.ok) throw new Error(this.t('search.search_failed'));

                var data = await res.json();
                this.searchResults = (data.results || []).slice(0, DASHBOARD_SEARCH_RESULT_LIMIT);
                this.searchMeta = {
                    total: Math.min(data.total || 0, DASHBOARD_SEARCH_RESULT_LIMIT),
                    scrape_triggered: false,
                    image_used: data.image_used || false,
                    elapsed_seconds: data.elapsed_seconds || null,
                    source: 'database'
                };
                this.expandedResult = null;
                this.sortResults();

                currentSearchTotal = data.total || 0;
                currentSearchType = 'quick';

                this.saveSearchQuery(query);
                this.showSearchHistory = false;
                showToast(this.t('search.results_found_db', { count: Math.min(data.total || 0, DASHBOARD_SEARCH_RESULT_LIMIT) }), 'success');
            } catch (e) {
                console.error('Quick search error:', e);
                this.searchError = e.message || this.t('search.search_failed');
            } finally {
                this.searchLoading = false;
            }
        },

        async dashboardLiveSearch() {
            var query = this.searchQuery.trim();
            if (!query) {
                showToast(this.t('search.live_search_name_required'), 'warning');
                return;
            }

            this.searchLoading = true;
            this.searchError = '';
            this.searchType = 'intelligent';
            this.searchResults = [];
            this.expandedResult = null;
            this.clearRiskReportState();
            agenticSearchAborted = false;
            _agenticAbortController = new AbortController();
            showAgenticLoadingModal();

            var classes = this.selectedClasses;
            var token = getAuthToken();
            var signal = _agenticAbortController ? _agenticAbortController.signal : undefined;

            try {
                var res;
                if (this.selectedImage) {
                    var formData = new FormData();
                    if (query) formData.append('query', query);
                    formData.append('image', this.selectedImage);
                    if (classes.length) formData.append('classes', classes.join(','));

                    res = await fetch('/api/v1/search/intelligent', {
                        method: 'POST',
                        headers: { 'Authorization': 'Bearer ' + token },
                        body: formData,
                        signal: signal
                    });
                } else {
                    var url = '/api/v1/search/intelligent?query=' + encodeURIComponent(query);
                    if (classes.length) url += '&classes=' + classes.join(',');

                    res = await fetch(url, {
                        headers: { 'Authorization': 'Bearer ' + token },
                        signal: signal
                    });
                }

                if (agenticSearchAborted) return;
                var data = await res.json();

                if (res.status === 403) { hideAgenticLoadingModal(); showUpgradeModal(data.detail || data, 'live_search'); return; }
                if (res.status === 402) { hideAgenticLoadingModal(); showUpgradeModal(data.detail || data, 'live_search'); return; }
                if (res.status === 401) { hideAgenticLoadingModal(); showToast(this.t('auth.session_expired'), 'error'); return; }
                if (!res.ok) throw new Error((data.detail && data.detail.message) || data.detail || this.t('search.search_failed'));

                hideAgenticLoadingModal();

                this.searchResults = (data.results || []).slice(0, DASHBOARD_SEARCH_RESULT_LIMIT);
                this.searchMeta = {
                    total: Math.min(data.total || 0, DASHBOARD_SEARCH_RESULT_LIMIT),
                    scrape_triggered: data.scrape_triggered || false,
                    image_used: data.image_used || false,
                    elapsed_seconds: data.elapsed_seconds || null,
                    source: data.scrape_triggered ? 'live' : 'database',
                    credits_remaining: data.credits_remaining
                };
                this.expandedResult = null;
                this.sortResults();

                currentSearchTotal = data.total || 0;
                currentSearchType = 'intelligent';

                this.saveSearchQuery(query);
                this.showSearchHistory = false;

                var creditsMsg = data.scrape_triggered
                    ? this.t('search.credits_remaining', { count: data.credits_remaining })
                    : this.t('search.from_database');
                var resultMsg = data.image_used
                    ? this.t('search.results_found_image', { count: Math.min(data.total || 0, DASHBOARD_SEARCH_RESULT_LIMIT), credits: creditsMsg })
                    : this.t('search.results_found', { count: Math.min(data.total || 0, DASHBOARD_SEARCH_RESULT_LIMIT) }) + '. ' + creditsMsg;
                showToast(resultMsg, 'success');
            } catch (e) {
                if (!agenticSearchAborted) {
                    hideAgenticLoadingModal();
                    console.error('Agentic Search error:', e);
                    this.searchError = e.message || this.t('search.search_failed');
                }
            } finally {
                this.searchLoading = false;
            }
        },

        buildRiskReportCandidate(r) {
            return {
                name: getTrademarkDisplayName(r) || r.name || r.trademark_name || '',
                application_no: r.application_no || null,
                status: r.status || null,
                status_code: r.status_code || null,
                nice_classes: r.nice_classes || r.classes || [],
                owner: r.owner || r.holder_name || null,
                attorney: r.attorney || r.attorney_name || null,
                image_url: r.image_url || r.image_path || null
            };
        },

        getRiskReportLanguage() {
            var locale = window.AppI18n && window.AppI18n.getLocale
                ? window.AppI18n.getLocale()
                : (this.currentLang || this.lang_code || 'tr');
            return ['tr', 'en', 'ar'].indexOf(locale) !== -1 ? locale : 'tr';
        },

        clearRiskReportOrdering() {
            if (this.searchResults && this.searchResults.length) {
                this.searchResults.forEach(function (result) {
                    delete result._riskReportScore;
                    delete result._riskReportRank;
                    delete result._riskReportApplicationNo;
                });
                this.searchResults = this.searchResults.slice();
            }
            if (this.sortMode === 'risk_report_desc') {
                this.sortMode = 'risk_desc';
            }
        },

        clearRiskReportState() {
            this.riskReport = null;
            this.riskReportError = '';
            this.clearRiskReportOrdering();
        },

        applyRiskReportOrdering(report, visibleResults) {
            if (!report || !Array.isArray(report.results)) return;
            this.clearRiskReportOrdering();
            report.results.forEach(function (candidate, index) {
                var source = visibleResults[candidate.input_index - 1] || null;
                if (!source && candidate.application_no) {
                    source = this.searchResults.find(function (result) {
                        return result.application_no === candidate.application_no;
                    }) || null;
                }
                if (!source) return;
                source._riskReportScore = candidate.llm_risk_score;
                source._riskReportRank = index + 1;
                source._riskReportApplicationNo = candidate.application_no || source.application_no || null;
            }, this);
            this.sortMode = 'risk_report_desc';
            this.searchResults = this.searchResults.slice();
        },

        openRiskReportPdf(report) {
            var reportId = report && report.report_id;
            if (!reportId) {
                showToast(this.t('search.risk_report_failed'), 'error');
                return;
            }
            var viewer = window.open('', '_blank');
            if (viewer) viewer.opener = null;
            var downloadFn = typeof downloadReportAPI === 'function'
                ? downloadReportAPI
                : (window.AppAPI && window.AppAPI.downloadReport);
            if (typeof downloadFn !== 'function') {
                if (viewer) viewer.close();
                showToast(this.t('reports.download_failed'), 'error');
                return;
            }
            downloadFn(reportId).then(function (blob) {
                var url = window.URL.createObjectURL(blob);
                if (viewer) {
                    viewer.location.href = url;
                } else {
                    window.open(url, '_blank', 'noopener');
                }
                setTimeout(function () { window.URL.revokeObjectURL(url); }, 60000);
            }).catch(function (err) {
                if (viewer) viewer.close();
                showToast(t('reports.download_failed') + ': ' + ((err && err.message) || ''), 'error');
            });
        },

        showRiskReportReadyNotification(report) {
            var self = this;
            showToast(this.t('search.risk_report_ready_view'), 'success', {
                actionLabel: this.t('search.risk_report_open'),
                duration: 9000,
                onAction: function () {
                    self.openRiskReportPdf(report);
                }
            });
        },

        async generateRiskReport() {
            var query = (this.searchQuery || '').trim();
            if (!query && !this.selectedImage) {
                showToast(this.t('search.live_search_name_required'), 'warning');
                return;
            }
            if (!this.selectedClasses || this.selectedClasses.length === 0) {
                showToast(this.t('search.risk_report_classes_required'), 'warning');
                return;
            }

            this.riskReportLoading = true;
            this.searchLoading = true;
            this.searchType = 'intelligent';
            this.searchResults = [];
            this.expandedResult = null;
            this.searchError = '';
            this.riskReportError = '';
            this.riskReport = null;
            agenticSearchAborted = false;
            _agenticAbortController = new AbortController();
            showAgenticLoadingModal();

            var token = getAuthToken();
            var language = this.getRiskReportLanguage();
            var classes = this.selectedClasses || [];
            var signal = _agenticAbortController ? _agenticAbortController.signal : undefined;

            var body = new FormData();
            if (query) body.append('query', query);
            if (classes.length) body.append('classes', classes.join(','));
            body.append('language', language);
            if (this.selectedImage) {
                body.append('image', this.selectedImage, this.selectedImage.name || 'query-logo');
            }

            try {
                var res = await fetch('/api/v1/search/intelligent-risk-report', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token },
                    body: body,
                    signal: signal,
                });
                if (agenticSearchAborted) return;
                var data = await res.json().catch(function () { return {}; });

                if (res.status === 401) { hideAgenticLoadingModal(); showToast(this.t('auth.session_expired'), 'error'); return; }
                if (res.status === 402 || res.status === 403) { hideAgenticLoadingModal(); showUpgradeModal(data.detail || data, 'reports'); return; }
                if (!res.ok) {
                    var detail = data.detail || data;
                    throw new Error((detail && (detail['message_' + language] || detail.message)) || this.t('search.risk_report_failed'));
                }

                hideAgenticLoadingModal();

                // Cancellation came back as a 200 with cancelled flag — bail without populating state.
                if (data && data.cancelled) {
                    return;
                }

                // Populate the search panel from the bundled agentic search results.
                var searchPayload = data.search || {};
                var resultsArray = (searchPayload.results || []).slice(0, DASHBOARD_SEARCH_RESULT_LIMIT);
                this.searchResults = resultsArray;
                this.searchMeta = {
                    total: Math.min(searchPayload.total || resultsArray.length, DASHBOARD_SEARCH_RESULT_LIMIT),
                    scrape_triggered: !!searchPayload.scrape_triggered,
                    image_used: !!searchPayload.image_used,
                    elapsed_seconds: searchPayload.elapsed_seconds || null,
                    source: searchPayload.scrape_triggered ? 'live' : 'database',
                };
                this.expandedResult = null;
                this.sortResults();

                // The combined endpoint nests the report fields at the top level alongside `search`.
                this.applyRiskReportOrdering(data, resultsArray);
                this.riskReport = data;
                this.loadUsageData({ 'Authorization': 'Bearer ' + token });
                window._reportsInitialized = false;
                var reportsPanel = document.getElementById('tab-content-reports');
                if (reportsPanel && !reportsPanel.classList.contains('hidden') && typeof loadReportsTab === 'function') {
                    loadReportsTab();
                }
                this.showRiskReportReadyNotification(data);
                this.saveSearchQuery(query);
                this.showSearchHistory = false;
            } catch (e) {
                if (!agenticSearchAborted) {
                    hideAgenticLoadingModal();
                    console.error('Risk report error:', e);
                    this.riskReportError = e.message || this.t('search.risk_report_failed');
                }
            } finally {
                this.riskReportLoading = false;
                this.searchLoading = false;
            }
        },

        // ==================== SORT ====================
        sortResults() {
            // Sorting is handled by the sortedResults getter
            // This method just forces reactivity by touching sortMode
            void this.sortMode;
        },

        get sortedResults() {
            var mode = this.sortMode;
            var sorted = this.searchResults.slice();

            if (mode === 'risk_desc') {
                sorted.sort(function (a, b) { return (getResultScore(b) || 0) - (getResultScore(a) || 0); });
            } else if (mode === 'risk_asc') {
                sorted.sort(function (a, b) { return (getResultScore(a) || 0) - (getResultScore(b) || 0); });
            } else if (mode === 'risk_report_desc') {
                sorted.sort(function (a, b) {
                    var rankA = a._riskReportRank || 999999;
                    var rankB = b._riskReportRank || 999999;
                    if (rankA !== rankB) return rankA - rankB;
                    return (getResultScore(b) || 0) - (getResultScore(a) || 0);
                });
            } else if (mode === 'date_desc') {
                sorted.sort(function (a, b) { return parseResultDate(b.application_date) - parseResultDate(a.application_date); });
            } else if (mode === 'date_asc') {
                sorted.sort(function (a, b) { return parseResultDate(a.application_date) - parseResultDate(b.application_date); });
            }

            return sorted;
        },

        // ==================== SCORE HELPERS ====================
        getScore(r) {
            // Extract risk score from result (handles both public + authenticated API formats)
            if (r.scores && r.scores.total !== undefined && r.scores.total !== null) return r.scores.total;
            if (r.risk_score !== undefined && r.risk_score !== null) return r.risk_score;
            return null;
        },

        getTextScore(r) {
            return getEffectiveTextScoreForResult(r);
        },

        hasHighRisk() {
            if (!this.searchResults || this.searchResults.length === 0) return false;
            for (var i = 0; i < this.searchResults.length; i++) {
                var score = this.getScore(this.searchResults[i]);
                if (score !== null && score >= 0.65) return true;
            }
            return false;
        },

        getStudioCtaTitle() {
            var hasImage = !!this.selectedImage;
            var hasText = !!(this.searchQuery || '').trim();
            if (hasImage && !hasText) return this.t('landing.studio_cta_title_image');
            if (hasImage && hasText) return this.t('landing.studio_cta_title_both');
            return this.t('landing.studio_cta_title_text');
        },

        getStudioCtaDesc() {
            var hasImage = !!this.selectedImage;
            var hasText = !!(this.searchQuery || '').trim();
            if (hasImage && !hasText) return this.t('landing.studio_cta_desc_image');
            if (hasImage && hasText) return this.t('landing.studio_cta_desc_both');
            return this.t('landing.studio_cta_desc_text');
        },

        getRiskBg(score) {
            if (score >= 0.9) return 'var(--color-risk-critical-bg)';
            if (score >= 0.7) return 'var(--color-risk-high-bg)';
            if (score >= 0.5) return 'var(--color-risk-medium-bg)';
            return 'var(--color-risk-low-bg)';
        },

        getRiskColor(score) {
            if (score >= 0.9) return 'var(--color-risk-critical-text)';
            if (score >= 0.7) return 'var(--color-risk-high-text)';
            if (score >= 0.5) return 'var(--color-risk-medium-text)';
            return 'var(--color-risk-low-text)';
        },

        getRiskReportScoreFraction(score) {
            var numeric = parseFloat(score);
            if (isNaN(numeric)) return 0;
            return Math.max(0, Math.min(100, numeric)) / 100;
        },

        getStatusColor(status) {
            return window.AppUtils.getStatusColor(status);
        },
        getStatusBg(status) {
            return window.AppUtils.getStatusBg(status);
        },

        // ==================== PORTFOLIO ====================
        portfolioTotalCount: 0,
        _portfolioAllResults: [],
        _portfolioEntityId: '',
        portfolioBulkAdding: false,

        loadPortfolio(type, id, name) {
            if (!id) return;
            this.portfolioLoading = true;
            this.portfolioResults = [];
            this._portfolioAllResults = [];
            this.portfolioTotalCount = 0;
            this.portfolioName = name || id;
            this.portfolioType = type;
            this._portfolioEntityId = id;
            this.showPortfolio = true;

            var self = this;
            // Pick the right backend endpoint per portfolio type. Design
            // holders hit /portfolio/public/designs; design designers
            // hit /portfolio/public/designers (matched by normalized
            // name via the functional GIN index). Trademark holder +
            // attorney keep the existing /portfolio/public.
            var url;
            if (type === 'design-holder') {
                url = '/api/v1/portfolio/public/designs?holder_id=' + encodeURIComponent(id);
            } else if (type === 'design-designer') {
                url = '/api/v1/portfolio/public/designers?name=' + encodeURIComponent(id);
            } else if (type === 'design-attorney') {
                // id is a JSON blob {name, firm} — unpack into separate
                // query params so the backend can match the pair.
                var parsed = (function () {
                    try { return JSON.parse(id); } catch (_) { return null; }
                })();
                var nm = parsed && parsed.name ? String(parsed.name) : '';
                var fm = parsed && parsed.firm ? String(parsed.firm) : '';
                url = '/api/v1/portfolio/public/attorneys?name=' + encodeURIComponent(nm);
                if (fm) url += '&firm=' + encodeURIComponent(fm);
            } else {
                var param = type === 'holder' ? 'holder_id' : 'attorney_no';
                url = '/api/v1/portfolio/public?' + param + '=' + encodeURIComponent(id);
            }
            fetch(url)
                .then(function (res) {
                    if (res.status === 429) {
                        self.searchError = self.t('search.rate_limited');
                        self.showPortfolio = false;
                        return null;
                    }
                    if (!res.ok) throw new Error('Portfolio failed: ' + res.status);
                    return res.json();
                })
                .then(function (data) {
                    if (data) {
                        var all = data.results || [];
                        self._portfolioAllResults = all;
                        self.portfolioTotalCount = (data.total_count != null) ? data.total_count : all.length;
                        self.portfolioResults = all.slice(0, 5);
                        var rawName = data.entity_name || name || id;
                        // For design holder + designer entity names,
                        // strip any address that was concatenated at
                        // ingest. Trademark/attorney sides are clean.
                        self.portfolioName = (type === 'design-holder' || type === 'design-designer')
                            ? (window._stripTurkishAddress
                                ? window._stripTurkishAddress(rawName)
                                : rawName)
                            : rawName;
                    }
                })
                .catch(function () {
                    self.searchError = self.t('search.search_failed');
                    self.showPortfolio = false;
                })
                .finally(function () {
                    self.portfolioLoading = false;
                });
        },

        closePortfolio() {
            this.showPortfolio = false;
            this.portfolioResults = [];
            this._portfolioAllResults = [];
            this.portfolioTotalCount = 0;
            this.portfolioName = '';
            this.portfolioType = '';
            this._portfolioEntityId = '';
        },

        downloadPortfolioCsv() {
            var id = this._portfolioEntityId;
            var type = this.portfolioType;
            if (!id || !type) return;
            var self = this;
            var token = getAuthToken();
            if (!token) {
                this.showLoginModal = false;
                showUpgradeModal({
                    error: 'upgrade_required',
                    current_plan: 'free',
                    upgrade_context: 'portfolio_download',
                    message: this.t('upgrade.portfolio_download_description')
                }, 'portfolio_download');
                return;
            }
            // Branch CSV endpoint per portfolio type. Design holders +
            // designers each have their own CSV route; trademark
            // holder + attorney share the existing /portfolio/public/csv.
            var csvUrl;
            var fileLabel;
            if (type === 'design-holder') {
                csvUrl = '/api/v1/portfolio/public/designs/csv?holder_id=' + encodeURIComponent(id);
                fileLabel = 'tasarim_sahibi';
            } else if (type === 'design-designer') {
                csvUrl = '/api/v1/portfolio/public/designers/csv?name=' + encodeURIComponent(id);
                fileLabel = 'tasarimci';
            } else if (type === 'design-attorney') {
                var parsed = (function () {
                    try { return JSON.parse(id); } catch (_) { return null; }
                })();
                var nm = parsed && parsed.name ? String(parsed.name) : '';
                var fm = parsed && parsed.firm ? String(parsed.firm) : '';
                csvUrl = '/api/v1/portfolio/public/attorneys/csv?name=' + encodeURIComponent(nm);
                if (fm) csvUrl += '&firm=' + encodeURIComponent(fm);
                fileLabel = 'tasarim_vekili';
            } else {
                var param = type === 'holder' ? 'holder_id' : 'attorney_no';
                csvUrl = '/api/v1/portfolio/public/csv?' + param + '=' + encodeURIComponent(id);
                fileLabel = type === 'holder' ? 'sahip' : 'vekil';
            }
            fetch(csvUrl, { headers: { 'Authorization': 'Bearer ' + token } })
                .then(function (res) {
                    if (res.status === 401) {
                        self.showLoginModal = true;
                        throw { handled: true };
                    }
                    if (res.status === 403) {
                        return res.json().catch(function () { return {}; }).then(function (data) {
                            showUpgradeModal((data && data.detail) || data || {}, 'portfolio_download');
                            throw { handled: true };
                        });
                    }
                    if (!res.ok) throw new Error('CSV export failed');
                    return res.blob();
                })
                .then(function (blob) {
                    var url = URL.createObjectURL(blob);
                    var a = document.createElement('a');
                    a.href = url;
                    a.download = fileLabel + '_' + id + '_portfolio.csv';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                })
                .catch(function (err) {
                    if (err && err.handled) return;
                    if (window.AppToast) AppToast.show('CSV indirilemedi', 'error');
                });
        },

        addPortfolioToWatchlist() {
            var id = this._portfolioEntityId;
            var type = this.portfolioType;
            if (!id || !type) {
                showToast('Portfolio bilgisi eksik, tekrar deneyin', 'error');
                return;
            }
            var token = getAuthToken();
            if (!token) {
                this.showLoginModal = true;
                return;
            }
            window.dispatchEvent(new CustomEvent('open-bulk-watchlist', {
                detail: {
                    type: type,
                    id: id,
                    name: this.portfolioName || '',
                    totalCount: this.portfolioTotalCount || 0
                }
            }));
        },

        // ==================== WATCHLIST ====================
        async addToWatchlistFromResult(r) {
            var appNo = r.application_no;
            if (!appNo) return;

            var brandName = r.trademark_name || r.name || '';
            if (!brandName) brandName = appNo;
            var classes = r.nice_classes || r.classes || r.nice_class_numbers || [];
            if (!classes.length) classes = [1];

            try {
                if (!(window.AppAPI && typeof window.AppAPI.addWatchlistItem === 'function')) {
                    throw new Error(this.t('watchlist.add_failed'));
                }
                if (!getAuthToken()) {
                    showToast(this.t('auth.session_expired'), 'error');
                    return;
                }

                await window.AppAPI.addWatchlistItem({
                    application_no: appNo,
                    brand_name: brandName,
                    nice_class_numbers: classes
                });

                showToast(this.t('watchlist.added_toast'), 'success');
                if (typeof _setScanInProgress === 'function' && typeof _getScanDurationMs === 'function') {
                    _setScanInProgress(_getScanDurationMs(1), 'item');
                }
                if (typeof refreshWatchlistAndStats === 'function') refreshWatchlistAndStats();
                // Land the user on the Watchlist tab so they immediately see
                // the new row (toast persists across tab switches).
                if (typeof showDashboardTab === 'function') showDashboardTab('watchlist');
            } catch (e) {
                if (e.status === 409) {
                    showToast(this.t('watchlist.already_watching'), 'info');
                    return;
                }
                if (e.status === 403) {
                    var errData = (e.data && (e.data.detail || e.data)) || e;
                    showUpgradeModal(errData, 'watchlist_items');
                    return;
                }
                if (e.status === 401) {
                    showToast(this.t('auth.session_expired'), 'error');
                    return;
                }
                showToast(this.t('common.error') + ': ' + e.message, 'error');
            }
        },

        getScoreColor(score) {
            // Returns Tailwind classes for Alpine :class bindings
            return window.AppComponents.getScoreColorClass(score);
        },
        getScoreStyle(score) {
            // Returns inline style string for CSS variable-based coloring
            return window.AppComponents.getScoreColor(score);
        },

        async loadData() {
            try {
                var token = getAuthToken();
                var headers = token ? { 'Authorization': 'Bearer ' + token } : {};
                var fetchTimeout = typeof AbortSignal.timeout === 'function'
                    ? { headers: headers, signal: AbortSignal.timeout(15000) }
                    : { headers: headers };

                var results = await Promise.allSettled([
                    fetch('/api/v1/dashboard/stats', fetchTimeout),
                    fetch('/api/v1/alerts?page=1&page_size=10', fetchTimeout),
                    fetch('/api/v1/alerts/summary', fetchTimeout),
                    fetch('/api/v1/watchlist?page=1&page_size=10&renewal_only=true', fetchTimeout)
                ]);

                // Dashboard stats
                if (results[0].status === 'fulfilled' && results[0].value.ok) {
                    var statsData = await results[0].value.json();
                    this.stats = {
                        total_watched: statsData.active_watchlist || 0,
                        high_risk_count: statsData.critical_alerts || 0,
                        pending_deadlines: statsData.active_deadline_count || 0,
                        recent_activity_count: statsData.alerts_this_week || 0,
                        watchlist_count: statsData.watchlist_count || statsData.active_watchlist || 0,
                        total_alerts: statsData.total_alerts || 0,
                        pre_publication_count: statsData.pre_publication_count || 0
                    };
                }

                // Recent alerts for list + chart
                if (results[1].status === 'fulfilled' && results[1].value.ok) {
                    var alertsData = await results[1].value.json();
                    var items = alertsData.items || [];
                    this.alerts = items.map(function (a) {
                        var c = a.conflicting || {};
                        var sc = a.scores || {};
                        return {
                            alert_id: a.id,
                            conflicting_brand: c.name || 'N/A',
                            conflicting_app_no: c.application_no || '',
                            conflicting_image_path: c.image_path || '',
                            brand_watched: a.watched_brand_name || '',
                            risk_score: Math.round((sc.total || 0) * 100),
                            scores: sc,
                            date: a.detected_at || '',
                            appeal_deadline: a.appeal_deadline || null,
                            conflict_bulletin_date: a.conflict_bulletin_date || null,
                            deadline_status: a.deadline_status || null,
                            deadline_days_remaining: a.deadline_days_remaining,
                            deadline_label: a.deadline_label || '',
                            deadline_urgency: a.deadline_urgency || '',
                            overlapping_classes: a.overlapping_classes || [],
                            watchlist_application_no: a.watchlist_application_no || '',
                            watchlist_bulletin_no: a.watchlist_bulletin_no || '',
                            has_extracted_goods: c.has_extracted_goods || false,
                            severity: a.severity || null
                        };
                    });
                }

                // Alerts summary (severity breakdown for chart)
                if (results[2].status === 'fulfilled' && results[2].value.ok) {
                    var summaryData = await results[2].value.json();
                    this.alertsSummary = summaryData;
                }

                // Deadlines: use backend-computed deadline_status fields (no client-side date math)
                var derivedDeadlines = this.alerts
                    .filter(function (a) { return a.deadline_status && a.deadline_status.indexOf('active') === 0; })
                    .sort(function (a, b) { return (a.deadline_days_remaining || 999) - (b.deadline_days_remaining || 999); })
                    .slice(0, 10)
                    .map(function (a) {
                        return {
                            alert_id: a.alert_id,
                            conflicting_brand: a.conflicting_brand,
                            app_no: a.conflicting_app_no,
                            days_left: a.deadline_days_remaining,
                            appeal_deadline: a.appeal_deadline ? formatDateTRShort(a.appeal_deadline) : '',
                            risk_score: a.risk_score,
                            brand_watched: a.brand_watched,
                            scores: a.scores
                        };
                    });
                this.deadlines = derivedDeadlines;

                // Renewal deadlines
                if (results[3] && results[3].status === 'fulfilled' && results[3].value.ok) {
                    var renewalData = await results[3].value.json();
                    this.renewalDeadlines = (renewalData.items || []).map(function (item) {
                        return {
                            watchlist_id: item.id,
                            brand_name: item.brand_name,
                            app_no: item.customer_application_no || '',
                            days_left: item.days_until_expiry !== null && item.days_until_expiry !== undefined ? item.days_until_expiry : null,
                            expiry_date: item.expiry_date ? formatDateTRShort(item.expiry_date) : '',
                            needs_renewal: item.needs_renewal
                        };
                    });
                }

                // Chart is in overview tab — render only if tab is visible
                var overviewPanel = document.getElementById('tab-content-overview');
                if (overviewPanel && !overviewPanel.classList.contains('hidden')) {
                    this.renderChart();
                }

                // Load usage summary & system stats (non-blocking)
                this.loadUsageData(headers);

            } catch (error) {
                console.error("API Error:", error);
            }
        },

        showOppositionModal(deadline) {
            var modal = document.getElementById('opposition-modal');
            var content = document.getElementById('opposition-content');
            if (!modal || !content) return;
            var displayConflictingBrand = getTrademarkDisplayName(deadline.conflicting_brand);
            var displayWatchedBrand = getTrademarkDisplayName(deadline.brand_watched);

            var urgencyStyle = deadline.days_left < 10
                ? 'color:var(--color-risk-critical-text)'
                : 'color:var(--color-risk-high-text)';
            var urgencyBgStyle = deadline.days_left < 10
                ? 'background:var(--color-risk-critical-bg);border-color:var(--color-risk-critical-border)'
                : 'background:var(--color-risk-high-bg);border-color:var(--color-risk-high-border)';

            var subject = encodeURIComponent(t('opposition.email_subject', { brand: displayConflictingBrand || '' }));
            var body = encodeURIComponent(
                t('opposition.email_greeting') + '\n\n'
                + t('opposition.email_body_intro') + '\n\n'
                + t('opposition.email_conflicting') + ' ' + (displayConflictingBrand || t('common.na')) + '\n'
                + t('opposition.email_app_no') + ' ' + (deadline.app_no || t('common.na')) + '\n'
                + t('opposition.email_watched') + ' ' + (displayWatchedBrand || t('common.na')) + '\n'
                + t('opposition.email_deadline') + ' ' + (deadline.appeal_deadline || t('common.na')) + '\n'
                + t('opposition.email_risk') + ' %' + (deadline.risk_score || 0) + '\n\n'
                + t('opposition.email_closing') + '\n'
            );

            content.innerHTML = '<div class="space-y-4">'
                + '<div class="text-center border rounded-xl p-4" style="' + urgencyBgStyle + '">'
                + '<div class="text-3xl font-bold" style="' + urgencyStyle + '">' + t('opposition.days_label', { count: deadline.days_left }) + '</div>'
                + '<div class="text-sm" style="color:var(--color-text-secondary)">' + t('opposition.approaching') + '</div>'
                + '<div class="text-xs mt-1" style="color:var(--color-text-faint)">' + t('opposition.last_date', { date: escapeHtml(deadline.appeal_deadline || '') }) + '</div></div>'
                + '<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">'
                + '<div class="rounded-lg p-3 border" style="background:var(--color-bg-muted);border-color:var(--color-border)"><div class="text-xs mb-1" style="color:var(--color-text-muted)">' + t('opposition.conflicting_brand') + '</div>'
                + '<div class="font-semibold" style="color:var(--color-text-primary)">' + escapeHtml(displayConflictingBrand || t('common.na')) + '</div>'
                + '<div class="text-xs font-mono-id" style="color:var(--color-text-faint)">' + escapeHtml(deadline.app_no || '') + '</div></div>'
                + '<div class="rounded-lg p-3 border" style="background:var(--color-bg-muted);border-color:var(--color-border)"><div class="text-xs mb-1" style="color:var(--color-text-muted)">' + t('opposition.watched_brand') + '</div>'
                + '<div class="font-semibold" style="color:var(--color-text-primary)">' + escapeHtml(displayWatchedBrand || t('common.na')) + '</div>'
                + '<div class="text-xs" style="color:var(--color-text-faint)">' + t('opposition.risk_label', { score: deadline.risk_score || 0 }) + '</div></div></div>'
                + '<div class="border rounded-xl p-4 text-sm" style="background:var(--color-risk-medium-bg);border-color:var(--color-risk-medium-border)">'
                + '<div class="font-semibold mb-2" style="color:var(--color-risk-medium-text)">' + t('opposition.about_process') + '</div>'
                + '<ul class="space-y-1" style="color:var(--color-text-secondary)">'
                + '<li>&bull; ' + t('opposition.legal_1') + '</li>'
                + '<li>&bull; ' + t('opposition.legal_2') + '</li>'
                + '<li>&bull; ' + t('opposition.legal_3') + '</li></ul></div>'
                + '<div class="flex flex-col sm:flex-row gap-3">'
                + '<a href="https://epats.turkpatent.gov.tr/" target="_blank" rel="noopener" '
                + 'class="flex-1 px-4 py-2.5 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-center text-sm font-medium btn-press min-h-[44px]">'
                + t('opposition.turkpatent_portal') + '</a>'
                + '<a href="mailto:?subject=' + subject + '&body=' + body + '" '
                + 'class="flex-1 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-center text-sm font-medium btn-press min-h-[44px]">'
                + t('opposition.send_lawyer') + '</a></div></div>';

            modal.classList.remove('hidden');
            lockBodyScroll();
        },

        async showAlertDetail(alertId) {
            var modal = document.getElementById('alert-detail-modal');
            var content = document.getElementById('alert-detail-content');
            var actionsContainer = document.getElementById('alert-detail-actions');
            if (!modal || !content) return;

            modal.classList.remove('hidden');
            lockBodyScroll();
            content.innerHTML = '<div class="text-center py-8"><div class="animate-spin inline-block w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full"></div></div>';
            window._currentAlertId = alertId;

            try {
                var token = getAuthToken();
                var res = await fetch('/api/v1/alerts/' + alertId, {
                    headers: token ? { 'Authorization': 'Bearer ' + token } : {}
                });
                if (!res.ok) throw new Error(t('alerts.load_failed'));
                var alert = await res.json();

                var scorePercent = Math.round((alert.scores.total || 0) * 100);
                var scoreColor = window.AppComponents.getScoreColor(scorePercent);
                var c = alert.conflicting || {};
                var s = alert.scores || {};

                var badgesHtml = window.AppComponents.renderSimilarityBadges(s);

                var imageHtml = window.AppComponents.renderThumbnail(c.image_path, c.name, c.application_no, 'w-20 h-20');

                var overlappingHtml = '';
                if (alert.overlapping_classes && alert.overlapping_classes.length > 0) {
                    overlappingHtml = '<div class="mt-3"><span class="text-xs font-medium text-gray-500">' + t('alerts.overlapping_classes') + '</span> '
                        + window.AppComponents.renderNiceClassBadges(alert.overlapping_classes)
                        + '</div>';
                }

                // Deadline section at the top
                var deadlineSection = renderAlertDetailDeadlineSection(alert);

                // Score ring instead of flat badge
                var scoreRingHtml = window.AppComponents.renderScoreRing(scorePercent, 64);
                var severityHtml = renderSeverityBadge(alert.severity);

                content.innerHTML = '<div class="space-y-4">'
                    + deadlineSection
                    + '<div class="text-center">'
                    + '<div class="mx-auto mb-1" style="width:64px">' + scoreRingHtml + '</div>'
                    + '<div class="text-sm" style="color:var(--color-text-muted)">' + t('scores.overall_risk') + '</div>'
                    + (severityHtml ? '<div class="mt-1">' + severityHtml + '</div>' : '')
                    + '<div class="flex justify-center mt-2">' + badgesHtml + '</div>'
                    + (s.phonetic_match ? '<div class="mt-1"><span class="text-xs font-semibold px-2 py-0.5 rounded-full" style="background:var(--color-risk-high-bg);color:var(--color-risk-high-text)">' + t('alerts.phonetic_match') + '</span></div>' : '')
                    + '</div>'
                    + '<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">'
                    + '<div class="rounded-xl p-4 border" style="background:rgba(79,70,229,0.05);border-color:rgba(79,70,229,0.15)">'
                    + '<div class="text-indigo-600 font-semibold text-sm mb-2">' + t('alerts.watched_brand') + '</div>'
                    + '<div class="font-medium" style="color:var(--color-text-primary)">' + escapeHtml(getTrademarkDisplayName(alert.watched_brand_name)) + '</div>'
                    + (alert.watchlist_application_no ? window.AppComponents.renderTurkpatentButton(alert.watchlist_application_no) : '')
                    + (alert.watchlist_bulletin_no ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('common.bulletin_label') + ' ' + escapeHtml(alert.watchlist_bulletin_no) + '</div>' : '')
                    + (alert.watchlist_classes && alert.watchlist_classes.length > 0
                        ? '<div class="mt-1"><span class="text-xs" style="color:var(--color-text-muted)">' + t('alerts.watched_classes') + ':</span> '
                        + window.AppComponents.renderNiceClassBadges(alert.watchlist_classes) + '</div>'
                        : '')
                    + '</div>'
                    + '<div class="rounded-xl p-4 border" style="background:var(--color-risk-critical-bg);border-color:var(--color-risk-critical-border)">'
                    + '<div class="font-semibold text-sm mb-2" style="color:var(--color-risk-critical-text)">' + t('alerts.conflicting_brand') + '</div>'
                    + '<div class="flex items-center gap-3">' + imageHtml
                    + '<div><div class="font-medium flex items-center gap-2" style="color:var(--color-text-primary)">' + escapeHtml(getTrademarkDisplayName(c.name))
                    + (c.status ? ' <span class="text-xs px-2 py-0.5 rounded-full font-medium" style="color:' + getStatusColor(c.status) + ';background:' + getStatusBg(c.status) + '">' + escapeHtml(translateStatus(c.status)) + '</span>' : '')
                    + '</div>'
                    + window.AppComponents.renderTurkpatentButton(c.application_no)
                    + window.AppComponents.renderRegistrationNo(c.registration_no)
                    + (c.application_date ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('common.application_date') + ' ' + formatDateTRShort(c.application_date) + '</div>' : '')
                    + window.AppComponents.renderHolderLink(c.holder, c.holder_tpe_client_id)
                    + window.AppComponents.renderAttorneyLink(c.attorney_name, c.attorney_no)
                    + '<div class="mt-1">' + window.AppComponents.renderEventsButton(c.application_no) + '</div>'
                    + '</div></div></div></div>'
                    + overlappingHtml
                    + (c.classes && c.classes.length > 0
                        ? '<div class="mt-2"><span class="text-xs font-medium" style="color:var(--color-text-muted)">' + t('alerts.conflict_classes') + ':</span> '
                        + window.AppComponents.renderNiceClassBadges(c.classes) + '</div>'
                        : '')
                    + (c.has_extracted_goods
                        ? '<div class="mt-3 text-center"><button onclick="showExtractedGoods(\'' + (c.application_no || '').replace(/'/g, "\\'") + '\', this)" '
                        + 'class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold cursor-pointer transition-colors btn-press min-h-[28px]" '
                        + 'style="' + window.AppComponents.getScoreColor(70) + '">'
                        + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
                        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>'
                        + '</svg>' + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span></button></div>'
                        : '')
                    + '<div class="text-xs text-center mt-2" style="color:var(--color-text-faint)">'
                    + t('alerts.status_label') + ' ' + escapeHtml(alert.status || '') + ' &bull; ' + t('alerts.source_label') + ' ' + escapeHtml(alert.source_type || '') + ' &bull; ' + (alert.detected_at || '')
                    + (alert.conflict_bulletin_no ? ' &bull; ' + t('alerts.bulletin') + ': ' + escapeHtml(alert.conflict_bulletin_no) : '')
                    + (alert.source_reference ? ' &bull; ' + t('alerts.source_ref') + ': ' + escapeHtml(alert.source_reference) : '')
                    + '</div>'
                    + (alert.acknowledged_at || alert.resolved_at
                        ? '<div class="mt-4 pt-3" style="border-top:1px solid var(--color-border)">'
                        + '<div class="text-xs font-semibold mb-2" style="color:var(--color-text-secondary)">' + t('alerts.resolution_timeline') + '</div>'
                        + (alert.acknowledged_at
                            ? '<div class="flex items-center gap-2 mb-1"><span class="w-2 h-2 rounded-full" style="background:var(--color-risk-medium-text)"></span>'
                            + '<span class="text-xs" style="color:var(--color-text-muted)">' + t('alerts.acknowledged_at') + ': ' + new Date(alert.acknowledged_at).toLocaleString() + '</span></div>'
                            : '')
                        + (alert.resolved_at
                            ? '<div class="flex items-center gap-2 mb-1"><span class="w-2 h-2 rounded-full" style="background:var(--color-risk-low-text)"></span>'
                            + '<span class="text-xs" style="color:var(--color-text-muted)">' + t('alerts.resolved_at') + ': ' + new Date(alert.resolved_at).toLocaleString() + '</span></div>'
                            : '')
                        + (alert.resolution_notes
                            ? '<div class="text-xs mt-1 p-2 rounded" style="background:var(--color-bg-page);color:var(--color-text-secondary)">' + escapeHtml(alert.resolution_notes) + '</div>'
                            : '')
                        + '</div>'
                        : '')
                    + '</div>';

                // Update action buttons based on deadline status
                if (actionsContainer) {
                    var actionsHtml = '';

                    // Opposition button — only if deadline is active
                    if (alert.deadline_status && alert.deadline_status.indexOf('active') === 0) {
                        var urgentBtnClass = alert.deadline_urgency === 'critical'
                            ? 'bg-red-600 hover:bg-red-700 animate-pulse'
                            : 'bg-orange-600 hover:bg-orange-700';
                        var daysText = alert.deadline_days_remaining !== null ? ' (' + t('deadline.days_remaining', { count: alert.deadline_days_remaining }) + ')' : '';
                        actionsHtml += '<button onclick="document.getElementById(\'alert-detail-modal\').classList.add(\'hidden\'); '
                            + 'dashboard().showOppositionModal({conflicting_brand: \'' + escapeHtml(c.name || '').replace(/'/g, "\\'") + '\', '
                            + 'app_no: \'' + escapeHtml(c.application_no || '').replace(/'/g, "\\'") + '\', '
                            + 'appeal_deadline: \'' + (alert.appeal_deadline || '') + '\', '
                            + 'days_left: ' + (alert.deadline_days_remaining || 0) + ', '
                            + 'risk_score: ' + scorePercent + ', '
                            + 'brand_watched: \'' + escapeHtml(alert.watched_brand_name || '').replace(/'/g, "\\'") + '\'})" '
                            + 'class="px-3 py-2.5 ' + urgentBtnClass + ' text-white text-sm rounded-lg font-medium">'
                            + t('opposition.title') + daysText + '</button>';
                    }

                    // Pre-publication note
                    if (alert.deadline_status === 'pre_publication') {
                        actionsHtml += '<div class="px-3 py-2.5 bg-blue-100 text-blue-800 text-sm rounded-lg">'
                            + t('deadline.bulletin_published_notify') + '</div>';
                    }

                    // Standard actions
                    actionsHtml += '<button onclick="acknowledgeAlert()" class="flex-1 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium">' + t('alerts.acknowledge') + '</button>';
                    actionsHtml += '<button onclick="resolveAlert()" class="flex-1 px-4 py-2.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium">' + t('alerts.resolved') + '</button>';
                    actionsHtml += '<button onclick="dismissAlert()" class="px-4 py-2.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg text-sm font-medium border border-gray-200">' + t('alerts.dismiss') + '</button>';

                    actionsContainer.innerHTML = actionsHtml;
                }

            } catch (e) {
                content.innerHTML = '<div class="text-center py-8 text-red-500">' + escapeHtml(e.message) + '</div>';
            }
        },

        async loadUsageData(headers) {
            try {
                var usageOpts = typeof AbortSignal.timeout === 'function'
                    ? { headers: headers, signal: AbortSignal.timeout(15000) }
                    : { headers: headers };
                var usageResults = await Promise.allSettled([
                    fetch('/api/v1/usage/summary', usageOpts),
                    fetch('/api/v1/status', usageOpts),
                    fetch('/api/v1/watchlist/scan-status', usageOpts)
                ]);

                // Usage summary
                if (usageResults[0].status === 'fulfilled' && usageResults[0].value.ok) {
                    var usage = (await usageResults[0].value.json()).usage || {};

                    // Helper: format limit display (show "Limitsiz" for 999999+)
                    function _fmtLimit(val) { return val >= 999999 ? '∞' : val; }
                    function _fmtPct(used, limit) { return limit >= 999999 ? 0 : (limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0); }

                    // Quick searches
                    var qs = usage.daily_quick_searches || {};
                    var qsEl = document.getElementById('usage-quick-text');
                    var qsBar = document.getElementById('usage-quick-bar');
                    var qsRing = document.getElementById('usage-quick-ring');
                    if (qsEl) qsEl.textContent = (qs.used || 0) + ' / ' + _fmtLimit(qs.limit || 0);
                    if (qsBar && qs.limit) qsBar.style.width = _fmtPct(qs.used || 0, qs.limit) + '%';
                    if (qsRing && qs.limit) qsRing.innerHTML = window.AppComponents.renderUsageRing(qs.used || 0, qs.limit >= 999999 ? 1 : qs.limit, 'var(--color-primary)');

                    // Agentic Search credits
                    var ls = usage.monthly_live_searches || {};
                    var lsEl = document.getElementById('usage-live-text');
                    var lsBar = document.getElementById('usage-live-bar');
                    var lsRing = document.getElementById('usage-live-ring');
                    if (lsEl) lsEl.textContent = (ls.used || 0) + ' / ' + _fmtLimit(ls.limit || 0);
                    if (lsBar && ls.limit) lsBar.style.width = _fmtPct(ls.used || 0, ls.limit) + '%';
                    if (lsRing && ls.limit) lsRing.innerHTML = window.AppComponents.renderUsageRing(ls.used || 0, ls.limit >= 999999 ? 1 : ls.limit, '#f59e0b');

                    // Watchlist
                    var wl = usage.watchlist_items || {};
                    var wlEl = document.getElementById('usage-watchlist-text');
                    var wlBar = document.getElementById('usage-watchlist-bar');
                    var wlRing = document.getElementById('usage-watchlist-ring');
                    if (wlEl) wlEl.textContent = (wl.used || 0) + ' / ' + _fmtLimit(wl.limit || 0);
                    if (wlBar && wl.limit) wlBar.style.width = _fmtPct(wl.used || 0, wl.limit) + '%';
                    if (wlRing && wl.limit) wlRing.innerHTML = window.AppComponents.renderUsageRing(wl.used || 0, wl.limit >= 999999 ? 1 : wl.limit, '#22c55e');

                    // Reports (saved reports + inline search risk reports)
                    var reports = usage.monthly_reports || {};
                    var reportsEl = document.getElementById('usage-reports-text');
                    var reportsBar = document.getElementById('usage-reports-bar');
                    var reportsRing = document.getElementById('usage-reports-ring');
                    if (reportsEl) reportsEl.textContent = (reports.used || 0) + ' / ' + _fmtLimit(reports.limit || 0);
                    if (reportsBar && reports.limit) reportsBar.style.width = _fmtPct(reports.used || 0, reports.limit) + '%';
                    if (reportsRing && reports.limit) reportsRing.innerHTML = window.AppComponents.renderUsageRing(reports.used || 0, reports.limit >= 999999 ? 1 : reports.limit, '#10b981');

                    // C3: Unified AI Credits (name gen + logo gen share a pool).
                    // Prefer the API's explicit `used` count (cost-weighted, accurate for
                    // every plan including superadmin); fall back to limit - remaining.
                    var ai = usage.monthly_ai_credits || {};
                    if (ai.limit && ai.limit > 0) {
                        var aiCard = document.getElementById('usage-ai-card');
                        var aiEl = document.getElementById('usage-ai-text');
                        var aiBar = document.getElementById('usage-ai-bar');
                        var aiRing = document.getElementById('usage-ai-ring');
                        var aiUsed = (typeof ai.used === 'number')
                            ? Math.max(0, ai.used)
                            : Math.max(0, (ai.limit || 0) - (ai.remaining || 0));
                        if (aiCard) aiCard.classList.remove('hidden');
                        if (ai.limit >= 999999) {
                            // Unlimited plan — show actual used count, denominator as "∞".
                            if (aiEl) aiEl.textContent = aiUsed + ' / ∞';
                            if (aiBar) aiBar.style.width = '0%';
                            if (aiRing) aiRing.innerHTML = window.AppComponents.renderUsageRing(0, 1, '#8b5cf6');
                        } else {
                            if (aiEl) aiEl.textContent = aiUsed + ' / ' + ai.limit;
                            if (aiBar) aiBar.style.width = _fmtPct(aiUsed, ai.limit) + '%';
                            if (aiRing) aiRing.innerHTML = window.AppComponents.renderUsageRing(aiUsed, ai.limit, '#8b5cf6');
                        }
                    }
                }

                // C1+C2: Search credits (resets_on, display_name)
                try {
                    var credRes = await fetch('/api/v1/search/credits', usageOpts);
                    if (credRes.ok) {
                        var credData = await credRes.json();
                        // C1: Credit reset date — format the month name in the app's
                        // selected locale, not the browser default (otherwise "May"
                        // stays English even when the UI is Turkish/Arabic).
                        var resetEl = document.getElementById('credit-reset-date');
                        if (resetEl && credData.resets_on) {
                            var rd = new Date(credData.resets_on);
                            var appLoc = (window.AppI18n && window.AppI18n.getLocale) ? window.AppI18n.getLocale() : 'tr';
                            var bcp47 = { tr: 'tr-TR', en: 'en-US', ar: 'ar-SA' }[appLoc] || 'tr-TR';
                            resetEl.textContent = t('usage.resets') + ' ' + rd.toLocaleDateString(bcp47, { month: 'short', day: 'numeric' });
                        }
                        // C2: Plan display name
                        var planBadgeEl = document.getElementById('plan-display-badge');
                        if (planBadgeEl && credData.display_name) {
                            planBadgeEl.innerHTML = '<span class="px-2 py-0.5 rounded-full text-xs font-medium" style="background:rgba(79,70,229,0.1);color:var(--color-primary)">' + escapeHtml(credData.display_name) + '</span>';
                        }
                    }
                } catch (e) { /* non-critical */ }

                // System stats
                if (usageResults[1].status === 'fulfilled' && usageResults[1].value.ok) {
                    var statusData = await usageResults[1].value.json();
                    var sysStats = statusData.statistics || {};
                    this.dbCount = sysStats.total_trademarks || 0;
                    this.designCount = sysStats.total_designs || 0;
                    this.patentCount = sysStats.total_patents || 0;
                    this.cografiCount = sysStats.total_cografi || 0;
                    var tmEl = document.getElementById('sys-total-trademarks');
                    if (tmEl) tmEl.textContent = this.dbCount.toLocaleString();
                    var bulletinEl = document.getElementById('sys-last-bulletin');
                    if (bulletinEl && sysStats.last_bulletin_date) {
                        try {
                            var bd = new Date(sysStats.last_bulletin_date);
                            bulletinEl.textContent = bd.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' });
                        } catch (e) { bulletinEl.textContent = sysStats.last_bulletin_date; }
                    }
                }

                // Scan status
                if (usageResults[2].status === 'fulfilled' && usageResults[2].value.ok) {
                    var scanData = await usageResults[2].value.json();
                    var scanEl = document.getElementById('sys-next-scan');
                    if (scanEl && scanData.next_scan_at) {
                        try {
                            var d = new Date(scanData.next_scan_at);
                            scanEl.textContent = d.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' }) + ' ' + d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
                        } catch (e) { scanEl.textContent = scanData.schedule || '-'; }
                    }
                    // D4: Auto-scan indicator
                    var autoScanEl = document.getElementById('auto-scan-badge');
                    if (autoScanEl && scanData.auto_scan_enabled != null) {
                        var asCls = scanData.auto_scan_enabled
                            ? 'background:rgba(34,197,94,0.1);color:var(--color-risk-low-text)'
                            : 'background:rgba(239,68,68,0.1);color:var(--color-risk-critical-text)';
                        var asLabel = scanData.auto_scan_enabled ? t('watchlist.auto_scan_on') : t('watchlist.auto_scan_off');
                        autoScanEl.innerHTML = '<span class="px-2 py-0.5 rounded text-xs font-medium" style="' + asCls + '">' + asLabel + '</span>';
                    }
                }

            } catch (e) {
                console.error('Usage data load error:', e);
            }
        },

        renderChart() {
            var ctx = document.getElementById('riskChart');
            if (!ctx) return;
            if (typeof Chart === 'undefined') {
                if (this.chartLibraryWaits < 10) {
                    this.chartLibraryWaits += 1;
                    var self = this;
                    setTimeout(function () { self.renderChart(); }, 200);
                } else {
                    console.warn('Chart.js not available after retries');
                }
                return;
            }
            this.chartLibraryWaits = 0;

            // Use alerts summary from backend if available (full dataset), else compute from page
            var critical, veryHigh, high, medium, low;
            var sev = this.alertsSummary && this.alertsSummary.by_severity;
            if (sev) {
                critical = sev.critical || 0;
                veryHigh = sev.very_high || 0;
                high = sev.high || 0;
                medium = sev.medium || 0;
                low = sev.low || 0;
            } else {
                critical = this.alerts.filter(function (a) { return a.risk_score >= 90; }).length;
                veryHigh = this.alerts.filter(function (a) { return a.risk_score >= 80 && a.risk_score < 90; }).length;
                high = this.alerts.filter(function (a) { return a.risk_score >= 70 && a.risk_score < 80; }).length;
                medium = this.alerts.filter(function (a) { return a.risk_score >= 50 && a.risk_score < 70; }).length;
                low = this.alerts.filter(function (a) { return a.risk_score < 50; }).length;
            }

            if (critical + veryHigh + high + medium + low === 0) { critical = 1; high = 2; low = 5; }

            if (this.chartInstance) this.chartInstance.destroy();

            this.chartInstance = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: [t('chart.critical'), t('chart.very_high'), t('chart.high'), t('chart.medium'), t('chart.low')],
                    datasets: [{
                        data: [critical, veryHigh, high, medium, low],
                        backgroundColor: ['#EF4444', '#F97316', '#F59E0B', '#EAB308', '#22C55E'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        // Bottom legend keeps the doughnut readable in the
                        // narrower 1/3-width chart column.
                        legend: { position: 'bottom', labels: { boxWidth: 10, padding: 8, font: { size: 11 } } }
                    },
                    cutout: '70%'
                }
            });

            // Render by_status breakdown below chart
            var statusContainer = document.getElementById('alert-status-breakdown');
            var byStatus = this.alertsSummary && this.alertsSummary.by_status;
            if (statusContainer && byStatus) {
                var statusLabels = {
                    'new': { label: t('alerts.status_new'), color: 'var(--color-primary)' },
                    acknowledged: { label: t('alerts.status_acknowledged'), color: 'var(--color-risk-medium-text)' },
                    resolved: { label: t('alerts.status_resolved'), color: 'var(--color-risk-low-text)' },
                    dismissed: { label: t('alerts.status_dismissed'), color: 'var(--color-text-muted)' }
                };
                var statusHtml = '';
                Object.keys(statusLabels).forEach(function (key) {
                    var count = byStatus[key] || 0;
                    if (count > 0) {
                        var cfg = statusLabels[key];
                        statusHtml += '<span class="text-xs px-2 py-0.5 rounded-full" style="color:' + cfg.color + ';border:1px solid ' + cfg.color + '">' + cfg.label + ': ' + count + '</span>';
                    }
                });
                statusContainer.innerHTML = statusHtml;
                statusContainer.classList.toggle('hidden', !statusHtml);
            }
        },

        // ==================== PROFILE ====================
        openProfile() {
            this.showProfileModal = true;
            this.profileMessage = '';
            this.profilePassword = { current: '', newPw: '', confirm: '' };
            this.loadProfile();
        },

        async loadProfile() {
            try {
                var token = getAuthToken();
                if (!token) return;
                var profileOpts = { headers: { 'Authorization': 'Bearer ' + token } };
                if (typeof AbortSignal.timeout === 'function') profileOpts.signal = AbortSignal.timeout(15000);
                var res = await fetch('/api/v1/user/profile', profileOpts);
                if (!res.ok) throw new Error('Failed to load profile');
                var data = await res.json();
                this.profileData = {
                    first_name: data.first_name || '',
                    last_name: data.last_name || '',
                    email: data.email || '',
                    phone: data.phone || '',
                    title: data.title || '',
                    department: data.department || '',
                    plan: (window.AppAuth && window.AppAuth.currentUserPlan) || 'free',
                    created_at: data.created_at || null,
                    avatar_url: data.avatar_url || ''
                };
                this.profileEmail = data.email || '';
                this.profileAvatar = data.avatar_url || '';

                // Show verification modal if email not verified
                if (data.is_email_verified === false) {
                    this.showEmailVerification = true;
                }
            } catch (e) {
                // silent
            }
        },

        async submitVerificationCode() {
            if (this.verificationCode.length !== 6) return;
            this.verificationLoading = true;
            this.verificationError = '';
            this.verificationSuccess = '';
            try {
                var token = getAuthToken();
                var res = await fetch('/api/v1/auth/verify-email', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code: this.verificationCode })
                });
                if (!res.ok) {
                    var err = await res.json().catch(function () { return {}; });
                    throw new Error(err.detail || 'Verification failed');
                }
                this.verificationSuccess = this.t('verification.success');
                var self = this;
                setTimeout(function () {
                    self.showEmailVerification = false;
                    self.verificationCode = '';
                    self.verificationSuccess = '';
                    showToast(self.t('verification.success'), 'success');
                }, 1200);
            } catch (e) {
                this.verificationError = e.message || this.t('verification.failed');
            } finally {
                this.verificationLoading = false;
            }
        },

        async resendVerificationCode() {
            if (this.verificationResendCooldown > 0) return;
            this.verificationError = '';
            this.verificationSuccess = '';
            try {
                var token = getAuthToken();
                var res = await fetch('/api/v1/auth/resend-verification', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
                });
                if (!res.ok) {
                    var err = await res.json().catch(function () { return {}; });
                    throw new Error(err.detail || 'Resend failed');
                }
                this.verificationSuccess = this.t('verification.code_resent');
                // Start 60-second cooldown
                this.verificationResendCooldown = 60;
                var self = this;
                var cooldownInterval = setInterval(function () {
                    self.verificationResendCooldown--;
                    if (self.verificationResendCooldown <= 0) {
                        clearInterval(cooldownInterval);
                    }
                }, 1000);
            } catch (e) {
                this.verificationError = e.message || this.t('verification.failed');
            }
        },

        async saveProfile() {
            this.profileSaving = true;
            this.profileMessage = '';
            try {
                var token = getAuthToken();
                var payload = {
                    first_name: this.profileData.first_name,
                    last_name: this.profileData.last_name,
                    email: this.profileData.email,
                    phone: this.profileData.phone,
                    title: this.profileData.title,
                    department: this.profileData.department
                };

                // Include password change if filled in
                if (this.profilePassword.newPw) {
                    if (this.profilePassword.newPw !== this.profilePassword.confirm) {
                        this.profileMessage = this.t('profile.password_mismatch');
                        this.profileMessageType = 'error';
                        this.profileSaving = false;
                        return;
                    }
                    payload.current_password = this.profilePassword.current;
                    payload.new_password = this.profilePassword.newPw;
                }

                var res = await fetch('/api/v1/user/profile', {
                    method: 'PUT',
                    headers: {
                        'Authorization': 'Bearer ' + token,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    var err = await res.json().catch(function () { return {}; });
                    throw new Error(err.detail || 'Failed');
                }

                this.profileMessage = this.t('profile.saved');
                this.profileMessageType = 'success';

                // Update displayed name
                if (this.profileData.first_name) {
                    this.userId = this.profileData.first_name;
                    window.AppAuth.currentUserName = this.profileData.first_name;
                }
                this.profileEmail = this.profileData.email;
                this.profilePassword = { current: '', newPw: '', confirm: '' };

                if (typeof showToast === 'function') {
                    showToast(this.t('profile.saved'), 'success');
                }
            } catch (e) {
                this.profileMessage = e.message || this.t('profile.save_failed');
                this.profileMessageType = 'error';
            } finally {
                this.profileSaving = false;
            }
        },

        // ==================== AVATAR UPLOAD ====================
        async uploadAvatar(event) {
            var file = event.target.files && event.target.files[0];
            if (!file) { console.warn('[avatar] No file selected'); return; }
            console.log('[avatar] File selected:', file.name, file.type, file.size);
            if (!file.type.match(/^image\/(png|jpe?g|gif|webp)$/)) {
                console.warn('[avatar] Invalid type:', file.type);
                this.profileMessage = this.t('search.invalid_image_type') || 'Invalid image type';
                this.profileMessageType = 'error';
                return;
            }
            if (file.size > 5 * 1024 * 1024) {
                this.profileMessage = this.t('landing.image_too_large') || 'Image too large (max 5MB)';
                this.profileMessageType = 'error';
                return;
            }
            this.avatarUploading = true;
            this.profileMessage = '';
            try {
                var token = getAuthToken();
                if (!token) throw new Error('Not authenticated');
                var formData = new FormData();
                formData.append('file', file);
                console.log('[avatar] Uploading...');
                var res = await fetch('/api/v1/user/avatar', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token },
                    body: formData
                });
                console.log('[avatar] Response status:', res.status);
                if (!res.ok) {
                    var errText = await res.text();
                    console.error('[avatar] Upload failed:', res.status, errText);
                    throw new Error('Upload failed (' + res.status + ')');
                }
                var result = await res.json();
                console.log('[avatar] Upload success:', result);
                var url = result.avatar_url + '?t=' + Date.now();
                this.profileAvatar = url;
                this.profileData.avatar_url = url;
                console.log('[avatar] profileAvatar set to:', url);
                if (typeof showToast === 'function') {
                    showToast(this.t('profile.saved') || 'Saved', 'success');
                }
            } catch (e) {
                console.error('[avatar] Error:', e);
                this.profileMessage = e.message || 'Upload failed';
                this.profileMessageType = 'error';
            } finally {
                this.avatarUploading = false;
                event.target.value = '';
            }
        },

        // ==================== LOGOUT ====================
        doLogout() {
            if (window.AppAuth && window.AppAuth.logout) {
                window.AppAuth.logout({ redirectTo: '/' });
                return;
            }
            ['auth_token', 'access_token', 'token', 'refresh_token'].forEach(function (key) {
                localStorage.removeItem(key);
                sessionStorage.removeItem(key);
            });
            window.location.href = '/';
        }
    };
}

// ============================================
// SEVERITY BADGE RENDERING
// ============================================
function renderSeverityBadge(severity) {
    if (!severity) return '';
    var colors = {
        critical: { bg: 'var(--color-risk-critical-bg)', text: 'var(--color-risk-critical-text)' },
        high: { bg: 'var(--color-risk-high-bg)', text: 'var(--color-risk-high-text)' },
        medium: { bg: 'var(--color-risk-medium-bg)', text: 'var(--color-risk-medium-text)' },
        low: { bg: 'var(--color-risk-low-bg)', text: 'var(--color-risk-low-text)' }
    };
    var c = colors[severity] || colors.medium;
    return '<span class="text-xs font-semibold px-2 py-0.5 rounded-full uppercase" style="background:' + c.bg + ';color:' + c.text + '">' + (t('alerts.severity_' + severity) || severity) + '</span>';
}

// ============================================
// DEADLINE STATUS BADGE RENDERING
// ============================================
function renderDeadlineStatusBadge(alert) {
    if (!alert.deadline_status) return '';

    var statusConfig = {
        'pre_publication': { bg: 'bg-blue-100', text: 'text-blue-800', border: 'border-blue-300', label: t('deadline.pre_publication') },
        'active_critical': { bg: 'bg-red-100', text: 'text-red-800', border: 'border-red-300', label: alert.deadline_label || t('deadline.active_critical'), pulse: true },
        'active_urgent': { bg: 'bg-orange-100', text: 'text-orange-800', border: 'border-orange-300', label: alert.deadline_label || t('deadline.active_urgent') },
        'active': { bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-300', label: alert.deadline_label || t('deadline.active') },
        'expired': { bg: 'bg-gray-100', text: 'text-gray-500', border: 'border-gray-200', label: t('deadline.expired') },
        'registered': { bg: 'bg-gray-100', text: 'text-gray-500', border: 'border-gray-200', label: t('deadline.registered') },
        'opposed': { bg: 'bg-purple-100', text: 'text-purple-800', border: 'border-purple-300', label: t('deadline.opposed') },
        'resolved': { bg: 'bg-green-100', text: 'text-green-800', border: 'border-green-300', label: t('deadline.resolved') }
    };

    var config = statusConfig[alert.deadline_status];
    if (!config) return '';

    var pulseClass = config.pulse ? ' animate-pulse' : '';
    return '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold '
        + config.bg + ' ' + config.text + ' ' + config.border + ' border' + pulseClass + '">'
        + config.label + '</span>';
}

function renderPrePublicationBanner(alert) {
    if (!alert.deadline_status || alert.deadline_status !== 'pre_publication') return '';
    return '<div class="mt-2 p-2 bg-blue-50 border border-blue-200 rounded-lg">'
        + '<div class="flex items-center gap-2">'
        + '<svg class="w-4 h-4 text-blue-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
        + '<div class="text-xs text-blue-800">'
        + '<span class="font-semibold">' + t('deadline.early_detection') + '</span> ' + t('deadline.early_detection_desc') + '</div></div></div>';
}

function renderAlertDetailDeadlineSection(alert) {
    var badge = renderDeadlineStatusBadge(alert);
    var prePubBanner = renderPrePublicationBanner(alert);
    if (!badge && !prePubBanner) return '';

    var bgClass = 'bg-gray-50 border-gray-200';
    if (alert.deadline_urgency === 'critical') bgClass = 'bg-red-50 border-red-200';
    else if (alert.deadline_urgency === 'urgent') bgClass = 'bg-orange-50 border-orange-200';
    else if (alert.deadline_status === 'pre_publication') bgClass = 'bg-blue-50 border-blue-200';

    var html = '<div class="mb-4 p-3 rounded-lg border ' + bgClass + '">';
    html += '<div class="flex items-center justify-between mb-1">'
        + '<span class="text-sm font-semibold text-gray-700">' + t('deadline.status_label') + '</span>'
        + badge + '</div>';

    if (alert.conflict_bulletin_date) {
        html += '<div class="text-xs text-gray-600">' + t('deadline.bulletin_date') + ' ' + formatDateTRShort(alert.conflict_bulletin_date) + '</div>';
    }
    if (alert.appeal_deadline && alert.deadline_days_remaining !== null && alert.deadline_days_remaining >= 0) {
        html += '<div class="text-xs text-gray-600">' + t('deadline.last_opposition_date') + ' ' + formatDateTRShort(alert.appeal_deadline) + '</div>';
    }

    html += prePubBanner;
    html += '</div>';
    return html;
}

function formatDateTRShort(dateStr) {
    if (!dateStr) return '';
    try {
        var d = new Date(dateStr);
        return d.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric' });
    } catch (e) { return dateStr; }
}

// ============================================
// SEARCH INPUT UX - CLEAR & FEEDBACK
// ============================================
(function initSearchInputHandlers() {
    var input = document.getElementById('search-input');
    if (!input) return;

    // Populate Nice class filter for search panel
    var niceSelect = document.getElementById('nice-class-select');
    if (niceSelect && niceSelect.options.length <= 1) {
        // Remove the disabled placeholder
        niceSelect.innerHTML = '';
        for (var i = 1; i <= 45; i++) {
            var opt = document.createElement('option');
            opt.value = i;
            opt.textContent = i + ' - ' + t('nice_classes.' + i);
            niceSelect.appendChild(opt);
        }
    }

    input.addEventListener('input', function () {
        var val = input.value.trim();
        var clearBtn = document.getElementById('clear-search-btn');

        if (clearBtn) {
            if (val.length > 0) clearBtn.classList.remove('hidden');
            else clearBtn.classList.add('hidden');
        }

        if (val.length === 0) {
            clearSearchResults();
        }
    });

    input.addEventListener('search', function () {
        if (input.value.trim() === '') clearSearchResults();
    });
})();

function clearSearchResults() {
    var container = document.getElementById('search-results');
    if (container) {
        container.innerHTML = '';
        container.classList.add('hidden');
    }
    _storedSearchResults = [];
    currentSearchTotal = 0;
}

function clearSearchInput() {
    var input = document.getElementById('search-input');
    if (input) {
        input.value = '';
        input.focus();
    }
    var clearBtn = document.getElementById('clear-search-btn');
    if (clearBtn) clearBtn.classList.add('hidden');

    clearSearchImage();
    clearSearchResults();
}

// ============================================
// CLASS FINDER (AI SUGGESTION)
// ============================================
function toggleClassFinder() {
    var section = document.getElementById('class-finder-section');
    var chevron = document.getElementById('class-finder-chevron');
    if (!section) return;
    var isHidden = section.classList.contains('hidden');
    section.classList.toggle('hidden');
    if (chevron) {
        chevron.style.transform = isHidden ? 'rotate(90deg)' : '';
    }
    if (isHidden) {
        var inp = document.getElementById('goods-description-input');
        if (inp) inp.focus();
    }
}

async function suggestNiceClasses() {
    var input = document.getElementById('goods-description-input');
    var btn = document.getElementById('suggest-classes-btn');
    if (!input || !input.value.trim()) return;

    var description = input.value.trim();
    var originalText = btn ? btn.textContent : '';

    // Loading state
    if (btn) {
        btn.disabled = true;
        btn.textContent = t('search.suggesting');
        btn.classList.add('opacity-70');
    }

    try {
        var token = getAuthToken();
        var headers = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = 'Bearer ' + token;

        var resp = await fetch('/api/suggest-classes', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ description: description, top_k: 5 })
        });

        if (!resp.ok) throw new Error('Request failed');

        var data = await resp.json();
        renderClassSuggestions(data.suggestions || []);
    } catch (e) {
        console.error('Class suggestion failed:', e);
        if (window.AppToast) window.AppToast.show(t('search.search_failed'), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText;
            btn.classList.remove('opacity-70');
        }
    }
}

function renderClassSuggestions(suggestions) {
    var container = document.getElementById('class-suggestion-results');
    var chips = document.getElementById('class-suggestion-chips');
    if (!container || !chips) return;

    if (!suggestions || suggestions.length === 0) {
        container.classList.add('hidden');
        return;
    }

    var html = '';
    for (var i = 0; i < suggestions.length; i++) {
        var s = suggestions[i];
        var pct = Math.round(s.similarity * 100);
        var className = t('nice_classes.' + s.class_number) || s.class_name;
        html += '<button type="button" onclick="selectSuggestedClass(' + s.class_number + ', this)" '
            + 'class="class-suggestion-chip inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium border transition-all cursor-pointer hover:shadow-sm" '
            + 'style="border-color:var(--color-border);background:var(--color-bg-card);color:var(--color-text-primary)" '
            + 'data-class="' + s.class_number + '">'
            + '<span class="chip-checkbox inline-flex items-center justify-center w-3.5 h-3.5 rounded border flex-shrink-0" style="border-color:var(--color-text-faint)"></span>'
            + '<span class="font-bold text-indigo-600">' + s.class_number + '</span>'
            + '<span>' + escapeHtml(className) + '</span>'
            + '<span class="text-xs px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">' + pct + '% ' + t('search.class_match') + '</span>'
            + '</button>';
    }
    chips.innerHTML = html;
    container.classList.remove('hidden');
}

function selectSuggestedClass(classNum, chipEl) {
    var select = document.getElementById('nice-class-select');
    if (!select) return;

    // Find and select the option
    for (var i = 0; i < select.options.length; i++) {
        if (parseInt(select.options[i].value) === classNum) {
            select.options[i].selected = true;
            break;
        }
    }

    // Visual feedback on chip
    if (chipEl) {
        chipEl.style.background = 'var(--color-bg-muted)';
        chipEl.style.borderColor = '#6366f1';
        chipEl.style.opacity = '0.7';
        // Replace empty checkbox with checked one
        var cb = chipEl.querySelector('.chip-checkbox');
        if (cb) {
            cb.style.background = '#6366f1';
            cb.style.borderColor = '#6366f1';
            cb.innerHTML = '<svg class="w-2.5 h-2.5" fill="none" stroke="white" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>';
        }
        chipEl.onclick = null;
        chipEl.style.cursor = 'default';
    }
}

// ============================================
// SEARCH IMAGE UPLOAD
// ============================================
function onSearchImageSelected(input) {
    var file = input.files && input.files[0];
    showImagePreview(file);
}

function handleDroppedImage(event) {
    var files = event.dataTransfer && event.dataTransfer.files;
    if (!files || files.length === 0) return;
    var file = files[0];
    if (!file.type.match(/^image\/(png|jpeg|webp)$/)) {
        showToast(t('search.invalid_image_type'), 'error');
        return;
    }
    // Set the file on the hidden input for form submission
    var input = document.getElementById('search-image');
    if (input) {
        var dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
    }
    showImagePreview(file);
}

function showImagePreview(file) {
    var wrapper = document.getElementById('search-image-preview-wrapper');
    var defaultEl = document.getElementById('search-image-default');
    var preview = document.getElementById('search-image-preview');
    var nameEl = document.getElementById('search-image-name');

    if (file) {
        var reader = new FileReader();
        reader.onload = function (e) {
            preview.src = e.target.result;
            nameEl.textContent = file.name;
            wrapper.classList.remove('hidden');
            if (defaultEl) defaultEl.classList.add('hidden');
        };
        reader.readAsDataURL(file);
    }
}

function clearSearchImage() {
    var input = document.getElementById('search-image');
    if (input) input.value = '';
    var wrapper = document.getElementById('search-image-preview-wrapper');
    if (wrapper) wrapper.classList.add('hidden');
    var preview = document.getElementById('search-image-preview');
    if (preview) preview.src = '';
    var defaultEl = document.getElementById('search-image-default');
    if (defaultEl) defaultEl.classList.remove('hidden');
}

// ============================================
// AGENTIC SEARCH CANCEL
// ============================================
var _agenticAbortController = null;

function cancelAgenticSearch() {
    agenticSearchAborted = true;
    // Abort the in-flight fetch
    if (_agenticAbortController) {
        try { _agenticAbortController.abort(); } catch (e) { }
        _agenticAbortController = null;
    }
    // Tell backend to stop the pipeline
    var token = window.AppAuth && window.AppAuth.getToken ? window.AppAuth.getToken() : (typeof getAuthToken === 'function' ? getAuthToken() : '');
    if (token) {
        fetch('/api/v1/search/cancel', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token }
        }).catch(function () { });
    }
    hideAgenticLoadingModal();
    showToast(t('search.cancelled'), 'info');
}

// ============================================
// LOADING MODAL — removed; show/hide are kept as no-ops so existing
// call sites in dashboardLiveSearch / generateRiskReport keep working.
// ============================================
function showAgenticLoadingModal() { /* no-op: progress modal removed */ }
function hideAgenticLoadingModal() { /* no-op: progress modal removed */ }

// ============================================
// UPGRADE MODAL
// ============================================
function showUpgradeModal(detail, fallbackContext) {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.show === 'function') {
        return window.AppUpgradeModal.show(detail, fallbackContext);
    }
    return null;
}
function hideUpgradeModal() {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.hide === 'function') {
        window.AppUpgradeModal.hide();
    }
}
function redirectToUpgrade() {
    if (window.AppUpgradeModal && typeof window.AppUpgradeModal.redirect === 'function') {
        window.AppUpgradeModal.redirect();
        return;
    }
    hideUpgradeModal();
    window.location.href = '/pricing';
}

// ============================================
// CREDITS MODAL
// ============================================
function showCreditsModal(detail) {
    if (showUpgradeModal(detail || {}, 'ai_credits')) return;
    var msg = document.getElementById('credits-message');
    if (detail && detail.message) msg.textContent = detail.message;
    document.getElementById('credits-modal').classList.remove('hidden');
    lockBodyScroll();
}
function hideCreditsModal() {
    document.getElementById('credits-modal').classList.add('hidden');
    unlockBodyScroll();
}
function buyCredits(amount) {
    hideCreditsModal();
    window.location.href = 'mailto:sales@ipwatchdog.com.tr?subject='
        + encodeURIComponent(t('credits.buy_subject', { amount: amount }))
        + '&body=' + encodeURIComponent(t('credits.buy_body', { amount: amount }));
}

// ============================================
// TAB SWITCHING
// ============================================
function showDashboardTab(tabId) {
    // Backwards-compat: 'design-watchlist' is now a sub-view of the unified
    // Watchlist tab. Redirect callers (deep-links, post-add navigation in
    // design_search.js) to the merged tab and select the Tasarım sub-view
    // via two cooperating mechanisms:
    //   * cold-load / deep-link path — set localStorage so Alpine consumes
    //     it on init (cleared after read in _watchlist_panel.html x-data),
    //   * same-page navigation path — Alpine is already inited, so flip
    //     watchlistView on the live state directly. Either path lands on
    //     Tasarım without persisting the choice for the next app open.
    if (tabId === 'design-watchlist') {
        try { localStorage.setItem('watchlistView', 'design'); } catch (_) {}
        tabId = 'watchlist';
        var wlEl = document.getElementById('tab-content-watchlist');
        if (wlEl && wlEl._x_dataStack && wlEl._x_dataStack[0]) {
            try { wlEl._x_dataStack[0].watchlistView = 'design'; } catch (_) {}
        }
    }

    // Hide ALL tab content panels
    var panels = ['overview', 'watchlist', 'search', 'opposition-radar', 'ai-studio', 'reports', 'applications'];
    panels.forEach(function (id) {
        var el = document.getElementById('tab-content-' + id);
        if (el) el.classList.add('hidden');
    });

    // Reset ALL tab buttons to inactive style
    document.querySelectorAll('.dashboard-tab-btn').forEach(function (btn) {
        btn.classList.remove('bg-indigo-600', 'text-white');
        btn.style.color = 'var(--color-text-muted)';
    });

    // Show selected tab content with entrance animation
    var content = document.getElementById('tab-content-' + tabId);
    if (content) {
        content.classList.remove('hidden');
        content.classList.add('tab-panel-enter');
        setTimeout(function () { content.classList.remove('tab-panel-enter'); }, 200);
    }

    // Activate the matching tab button
    var btn = document.getElementById('tab-btn-' + tabId);
    if (btn) {
        btn.classList.add('bg-indigo-600', 'text-white');
        btn.style.color = '';
    }

    // Update mobile bottom bar active state
    if (typeof updateBottomTabActive === 'function') updateBottomTabActive(tabId);

    // Update page title
    var tabTitles = { 'overview': 'Dashboard', 'watchlist': 'Watchlist', 'search': 'Search', 'opposition-radar': 'Opposition Radar', 'ai-studio': 'AI Studio', 'reports': 'Reports', 'applications': 'Applications' };
    document.title = 'IPWatchAI' + (tabTitles[tabId] ? ' \u2014 ' + tabTitles[tabId] : '');

    // Only clear search results when leaving the search tab. Patent
    // search now lives inside the unified Search tab as a sub-view, so
    // the same check covers it.
    if (tabId !== 'search') {
        clearSearchResults();
    }

    // Lazy-initialize tab content on first visit
    if (tabId === 'opposition-radar') {
        initOppositionRadar();
    }
    if (tabId === 'ai-studio') {
        initAIStudio();
    } else {
        stopLogoProjectPolling();
    }
    if (tabId === 'watchlist') {
        initWatchlistTab();
        // Lazy-init the Tasarım list when the Watchlist tab opens with the
        // design sub-view active. Read from the live Alpine state — that's
        // the source of truth (the redirect above + x-data init both write
        // to it).
        var wlEl2 = document.getElementById('tab-content-watchlist');
        var liveView = wlEl2 && wlEl2._x_dataStack && wlEl2._x_dataStack[0]
            ? wlEl2._x_dataStack[0].watchlistView
            : '';
        if (liveView === 'design' && typeof window.initDesignWatchlistTab === 'function') {
            window.initDesignWatchlistTab();
        }
    }
    if (tabId === 'overview') {
        // Pull fresh stats so any change made elsewhere in the dashboard
        // (alerts, watchlist, search/report/AI credit usage) is reflected
        // when the user lands here. Then re-render the chart.
        var data = _alpineRoot();
        if (data && typeof data.loadData === 'function') data.loadData();
        if (data && typeof data.renderChart === 'function') {
            setTimeout(function () { data.renderChart(); }, 50);
        }
    }
    if (tabId === 'reports') {
        if (!window._reportsInitialized) {
            window._reportsInitialized = true;
            loadReportsTab();
        }
    }
    if (tabId === 'applications') {
        initApplicationsTab();
    }
    // Focus search input when switching to search tab
    if (tabId === 'search') {
        setTimeout(function () {
            var input = document.getElementById('search-input');
            if (input) input.focus();
        }, 100);
    }
    // Design search now lives inside the Search tab — wire it up alongside Marka.
    if (tabId === 'search' && typeof window.initDesignSearchTab === 'function') {
        window.initDesignSearchTab();
    }
    // (Legacy 'design-watchlist' tab id is redirected to the merged
    // Watchlist tab above; the design init runs from the watchlist branch
    // when the saved sub-view is 'design'.)
}

// ============================================
// OPPOSITION RADAR INIT
// ============================================
function initOppositionRadar() {
    if (radarInitialized) return;
    radarInitialized = true;
    loadLeadStats();
    loadLeadCredits();
    loadLeadFeed(1);
}

// ============================================
// URGENCY SUMMARY BAR
// ============================================
function renderUrgencySummary(stats) {
    var wrapper = document.getElementById('urgency-summary-bar');
    var bar = document.getElementById('urgency-stacked-bar');
    var legend = document.getElementById('urgency-legend');
    var totalLabel = document.getElementById('urgency-total-label');
    if (!wrapper || !bar || !legend) return;

    var critical = stats.critical_leads || 0;
    var urgent = stats.urgent_leads || 0;
    var total = stats.total_leads || 0;
    var converted = stats.converted_leads || 0;
    var remaining = Math.max(0, total - critical - urgent - converted);

    if (total === 0) {
        wrapper.classList.add('hidden');
        return;
    }

    wrapper.classList.remove('hidden');
    totalLabel.textContent = t('leads.total_leads_label', { count: total });

    var segments = [
        { count: critical, color: 'var(--color-deadline-critical)', label: t('leads.stat_critical'), onclick: "document.getElementById('filter-urgency').value='critical';loadLeadFeed()" },
        { count: urgent, color: 'var(--color-deadline-warning)', label: t('leads.stat_urgent'), onclick: "document.getElementById('filter-urgency').value='urgent';loadLeadFeed()" },
        { count: remaining, color: 'var(--color-primary)', label: t('leads.stat_active'), onclick: "document.getElementById('filter-urgency').value='';loadLeadFeed()" },
        { count: converted, color: 'var(--color-deadline-safe)', label: t('leads.stat_converted'), onclick: "document.getElementById('filter-status').value='all';loadLeadFeed()" }
    ];

    var barHtml = '';
    var legendHtml = '';

    segments.forEach(function (seg) {
        if (seg.count <= 0) return;
        var pct = Math.max(2, Math.round((seg.count / total) * 100));
        barHtml += '<div class="h-full cursor-pointer hover:opacity-80 transition-opacity" '
            + 'style="width:' + pct + '%;background:' + seg.color + '" '
            + 'onclick="' + seg.onclick + '" '
            + 'title="' + seg.label + ': ' + seg.count + '"></div>';

        legendHtml += '<div class="flex items-center gap-1 cursor-pointer hover:opacity-70" onclick="' + seg.onclick + '">'
            + '<span class="w-2.5 h-2.5 rounded-full inline-block" style="background:' + seg.color + '"></span>'
            + '<span style="color:var(--color-text-muted)">' + seg.label + '</span>'
            + '<span class="font-semibold" style="color:var(--color-text-primary)">' + seg.count + '</span>'
            + '</div>';
    });

    bar.innerHTML = barHtml;
    legend.innerHTML = legendHtml;
}

// ============================================
// LEAD DETAIL MODAL HANDLERS
// ============================================
function hideLeadDetailModal() {
    document.getElementById('lead-detail-modal').classList.add('hidden');
    unlockBodyScroll();
    currentLeadId = null;
}

function markLeadContacted() { if (currentLeadId) updateLeadStatus(currentLeadId, 'contact'); }
function markLeadConverted() { if (currentLeadId) updateLeadStatus(currentLeadId, 'convert'); }
function dismissLead() { if (currentLeadId) updateLeadStatus(currentLeadId, 'dismiss'); }

// ============================================
// ALERT DETAIL MODAL HANDLERS
// ============================================
function hideAlertDetailModal() {
    document.getElementById('alert-detail-modal').classList.add('hidden');
    unlockBodyScroll();
    window._currentAlertId = null;
}

async function acknowledgeAlert() {
    if (!window._currentAlertId) return;
    try {
        var res = await fetch('/api/v1/alerts/' + window._currentAlertId + '/acknowledge', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: null })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.acknowledged_toast'), 'success');
        hideAlertDetailModal();
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

async function resolveAlert() {
    if (!window._currentAlertId) return;
    try {
        var res = await fetch('/api/v1/alerts/' + window._currentAlertId + '/resolve', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ resolution_notes: 'Resolved from dashboard' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.resolved_toast'), 'success');
        hideAlertDetailModal();
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

async function dismissAlert() {
    if (!window._currentAlertId) return;
    try {
        var res = await fetch('/api/v1/alerts/' + window._currentAlertId + '/dismiss', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'Dismissed from dashboard' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.dismissed_toast'), 'success');
        hideAlertDetailModal();
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

// ============================================
// QUICK ALERT ACTIONS (inline from threat cards)
// ============================================
async function quickResolveAlert(alertId) {
    try {
        var res = await fetch('/api/v1/alerts/' + alertId + '/resolve', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ resolution_notes: 'Resolved from watchlist' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.resolved_toast'), 'success');
        // Remove card from list and refresh stats
        _watchlistAlertsCache = _watchlistAlertsCache.filter(function (a) { return a.alert_id !== alertId; });
        renderWatchlistAlerts(_watchlistAlertsCache);
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

async function quickDismissAlert(alertId) {
    try {
        var res = await fetch('/api/v1/alerts/' + alertId + '/dismiss', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'Dismissed from watchlist' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.dismissed_toast'), 'success');
        // Remove card from list and refresh stats
        _watchlistAlertsCache = _watchlistAlertsCache.filter(function (a) { return a.alert_id !== alertId; });
        renderWatchlistAlerts(_watchlistAlertsCache);
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

// ============================================
// INLINE ALERT ACTIONS (expandable watchlist cards)
// ============================================
async function inlineResolveAlert(alertId, watchlistItemId) {
    try {
        var res = await fetch('/api/v1/alerts/' + alertId + '/resolve', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ resolution_notes: 'Resolved from watchlist' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.resolved_toast'), 'success');
        _updateInlineConflictCount(watchlistItemId);
        // Reload inline alerts for this watchlist item
        var panel = document.getElementById('wl-alerts-' + watchlistItemId);
        if (panel) loadInlineAlerts(watchlistItemId, panel);
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

async function inlineDismissAlert(alertId, watchlistItemId) {
    try {
        var res = await fetch('/api/v1/alerts/' + alertId + '/dismiss', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'Dismissed from watchlist' })
        });
        if (!res.ok) throw new Error(t('alerts.operation_failed'));
        showToast(t('alerts.dismissed_toast'), 'success');
        _updateInlineConflictCount(watchlistItemId);
        // Reload inline alerts for this watchlist item
        var panel = document.getElementById('wl-alerts-' + watchlistItemId);
        if (panel) loadInlineAlerts(watchlistItemId, panel);
        refreshWatchlistAndStats();
    } catch (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    }
}

function _updateInlineConflictCount(watchlistItemId) {
    var countEl = document.getElementById('wl-conflict-count-' + watchlistItemId);
    if (!countEl) return;
    var numEl = countEl.querySelector('span');
    if (!numEl) return;
    var cur = parseInt(numEl.textContent, 10) || 0;
    var next = Math.max(0, cur - 1);
    if (next === 0) {
        countEl.remove();
    } else {
        var ccColor = next >= 5 ? '#dc2626' : next >= 2 ? '#ea580c' : '#ca8a04';
        numEl.style.color = ccColor;
        numEl.textContent = next;
    }
}

// ============================================
// ITIRAZ (OPPOSITION) FORM NAVIGATION
// ============================================
var _itirazContextMap = {};

function openItirazForm(alertId) {
    var ctx = _itirazContextMap[alertId] || {};
    // Build the appeal context from stored conflict data
    _applicationsState.appealContext = {
        targetAppNo: ctx.appNo || null,
        targetBrand: ctx.name || null,
        targetHolder: ctx.holder || null,
        targetBulletinNo: ctx.bulletinNo || null,
        targetBulletinDate: ctx.bulletinDate || null,
        targetClasses: ctx.classes || [],
        targetRiskScore: ctx.riskScore || null,
        deadline: ctx.deadline || null,
        watchedBrand: ctx.watchedBrand || null
    };
    showDashboardTab('applications');
    setTimeout(function () {
        showApplicationForm({ applicationType: 'appeal', brandName: ctx.watchedBrand || '' });
    }, 150);
}

function onAppTypeChange() {
    var type = document.getElementById('app-application-type').value;
    var appealSection = document.getElementById('app-appeal-section');
    var titleEl = document.getElementById('app-form-title');
    var subtitleEl = document.getElementById('app-form-subtitle');
    if (type === 'appeal') {
        if (appealSection) appealSection.classList.remove('hidden');
        if (titleEl) titleEl.textContent = t('applications.form_title_appeal');
        if (subtitleEl) subtitleEl.textContent = t('applications.form_subtitle_appeal');
        _renderAppealConflictCard();
    } else {
        if (appealSection) appealSection.classList.add('hidden');
        if (titleEl) titleEl.textContent = t('applications.form_title');
        if (subtitleEl) subtitleEl.textContent = t('applications.form_subtitle');
    }
}

function _renderAppealConflictCard() {
    var ctx = _applicationsState.appealContext;
    var card = document.getElementById('app-appeal-conflict-card');
    if (!card || !ctx) return;

    var hasAny = ctx.targetBrand || ctx.targetAppNo || ctx.targetHolder || ctx.targetBulletinNo;
    if (!hasAny) { card.classList.add('hidden'); return; }
    card.classList.remove('hidden');

    function _setRow(rowId, valId, value) {
        var row = document.getElementById(rowId);
        var el = document.getElementById(valId);
        if (!row || !el) return;
        if (value) { el.textContent = value; row.classList.remove('hidden'); }
        else { row.classList.add('hidden'); }
    }

    _setRow('app-conflict-brand-row', 'app-conflict-brand', ctx.targetBrand);
    _setRow('app-conflict-appno-row', 'app-conflict-appno', ctx.targetAppNo);
    _setRow('app-conflict-holder-row', 'app-conflict-holder', ctx.targetHolder);
    _setRow('app-conflict-bulletin-row', 'app-conflict-bulletin', ctx.targetBulletinNo);
    _setRow('app-conflict-bulletindate-row', 'app-conflict-bulletindate', ctx.targetBulletinDate);
    _setRow('app-conflict-deadline-row', 'app-conflict-deadline', ctx.deadline);
    _setRow('app-conflict-risk-row', 'app-conflict-risk', ctx.targetRiskScore ? ctx.targetRiskScore + '%' : null);

    var classesRow = document.getElementById('app-conflict-classes-row');
    var classesEl = document.getElementById('app-conflict-classes');
    if (classesRow && classesEl && ctx.targetClasses && ctx.targetClasses.length > 0) {
        classesEl.innerHTML = ctx.targetClasses.map(function (c) {
            return '<span class="px-2 py-0.5 rounded text-xs font-medium" style="background:rgba(99,102,241,0.1);color:var(--color-primary)">' + c + '</span>';
        }).join('');
        classesRow.classList.remove('hidden');
    } else if (classesRow) {
        classesRow.classList.add('hidden');
    }
}

function _collectApplicationBody() {
    var formIdEl = document.getElementById('app-form-id');
    var appType = document.getElementById('app-application-type').value;
    var body = {
        brand_name: document.getElementById('app-brand-name').value.trim(),
        application_type: appType,
        mark_type: document.getElementById('app-mark-type').value,
        nice_class_numbers: _applicationsState.selectedClasses,
        goods_services_description: document.getElementById('app-goods-services').value.trim() || null,
        applicant_full_name: document.getElementById('app-applicant-name').value.trim() || null,
        applicant_id_type: document.getElementById('app-applicant-id-type').value,
        applicant_id_no: document.getElementById('app-applicant-id-no').value.trim() || null,
        applicant_phone: document.getElementById('app-applicant-phone').value.trim() || null,
        applicant_email: document.getElementById('app-applicant-email').value.trim() || null,
        applicant_address: document.getElementById('app-applicant-address').value.trim() || null,
        notes: document.getElementById('app-notes').value.trim() || null,
        source_search_query: formIdEl.dataset.sourceQuery || null,
        source_risk_score: formIdEl.dataset.sourceRisk ? parseFloat(formIdEl.dataset.sourceRisk) : null
    };
    if (appType === 'appeal') {
        var ctx = _applicationsState.appealContext || {};
        body.opposition_target_app_no = ctx.targetAppNo || null;
        body.opposition_target_brand = ctx.targetBrand || null;
        body.opposition_target_holder = ctx.targetHolder || null;
        body.opposition_target_bulletin_no = ctx.targetBulletinNo || null;
        body.opposition_target_bulletin_date = ctx.targetBulletinDate || null;
        body.opposition_target_classes = ctx.targetClasses || [];
        body.opposition_grounds = document.getElementById('app-appeal-grounds').value.trim() || null;
    }
    return body;
}

// Global wrapper for showAlertDetail (Alpine method called from innerHTML)
function showAlertDetail(alertId) {
    var data = _alpineRoot();
    if (data && typeof data.showAlertDetail === 'function') {
        data.showAlertDetail(alertId);
    }
}

// ============================================
// OPPOSITION MODAL HANDLER
// ============================================
function hideOppositionModal() {
    document.getElementById('opposition-modal').classList.add('hidden');
    unlockBodyScroll();
}

function showLeadUpgradePrompt(detail) {
    document.getElementById('lead-feed-loading').classList.add('hidden');
    document.getElementById('lead-feed-container').classList.add('hidden');
    document.getElementById('lead-stats-cards').classList.add('hidden');
    document.getElementById('lead-upgrade-prompt').classList.add('hidden');
    showUpgradeModal(detail || { current_plan: (window.AppAuth && window.AppAuth.currentUserPlan) || 'free' }, 'leads');
}

// ============================================
// GENERIC ENTITY PORTFOLIO MODAL
// Supports: 'holder' and 'attorney' entity types
// ============================================
var _entityPortfolioType = null;  // 'holder' | 'attorney'
var _entityPortfolioId = null;
var _entityPortfolioName = null;
var _entityPortfolioTotalCount = 0;
window._entitySearchPreviousState = null;

// Entity type config — maps entity type to API paths and i18n keys
var _entityConfig = {
    holder: {
        loadFn: function (id, page) { return loadHolderTrademarks(id, page); },
        searchFn: function (q) { return searchHolders(q); },
        nameKey: 'holder_name',
        idKey: 'holder_tpe_client_id',
        i18nPrefix: 'holder',
        subtitleParam: 'tpeId'
    },
    attorney: {
        loadFn: function (id, page) { return loadAttorneyTrademarks(id, page); },
        searchFn: function (q) { return searchAttorneys(q); },
        nameKey: 'attorney_name',
        idKey: 'attorney_no',
        i18nPrefix: 'attorney',
        subtitleParam: 'attorneyNo'
    }
};

function showEntityPortfolio(entityType, entityId, entityName) {
    if (!entityId) return;
    _entityPortfolioType = entityType;
    _entityPortfolioId = entityId;
    _entityPortfolioName = entityName;
    currentHolderTpeId = entityId; // backward compat

    var cfg = _entityConfig[entityType];
    var modal = document.getElementById('entityPortfolioModal');
    modal.classList.remove('hidden');

    document.getElementById('entityModalTitle').textContent = entityName || t(cfg.i18nPrefix + '.title');
    var subtitleParams = { count: '...' };
    subtitleParams[cfg.subtitleParam] = entityId;
    document.getElementById('entityModalSubtitle').textContent = t(cfg.i18nPrefix + '.loading_subtitle', subtitleParams);

    document.getElementById('entitySearchInput').placeholder = t(cfg.i18nPrefix + '.search_placeholder') || '';
    document.getElementById('entityPortfolioLoading').classList.remove('hidden');
    document.getElementById('entityPortfolioResults').classList.add('hidden');
    document.getElementById('entityPortfolioError').classList.add('hidden');
    var footer = document.getElementById('entityPortfolioFooter');
    if (footer) footer.classList.add('hidden');

    cfg.loadFn(entityId, 1);
}

// Thin wrappers for backward compatibility (holder click handlers call these)
function showHolderPortfolio(tpeClientId, holderName) {
    showEntityPortfolio('holder', tpeClientId, holderName);
}
function showAttorneyPortfolio(attorneyNo, attorneyName) {
    showEntityPortfolio('attorney', attorneyNo, attorneyName);
}

function renderEntityTrademarks(trademarks) {
    var cfg = _entityConfig[_entityPortfolioType] || _entityConfig.holder;
    var container = document.getElementById('entityTrademarksList');
    if (!trademarks || trademarks.length === 0) {
        container.innerHTML = '<div class="text-center py-8 text-gray-500">' + t(cfg.i18nPrefix + '.no_trademarks') + '</div>';
        return;
    }
    var html = '';
    trademarks.forEach(function (tm) {
        var egIndicator = '';
        if (tm.has_extracted_goods) {
            var safeAppNo = (tm.application_no || '').replace(/'/g, "\\'");
            egIndicator = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeAppNo + '\', this)" '
                + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1">'
                + t('extracted_goods.label') + ' <span class="underline">' + t('extracted_goods.yes') + '</span></button>';
        }
        html += '<div class="flex items-center gap-4 p-4 bg-gray-50 hover:bg-gray-100 rounded-xl transition-colors">'
            + window.AppComponents.renderThumbnail(tm.image_path, tm.name, tm.application_no, 'w-12 h-12')
            + '<div class="flex-1 min-w-0">'
            + '<div class="font-semibold text-gray-900 truncate">' + (escapeHtml(tm.name) || t('holder.unnamed')) + '</div>'
            + (tm.application_date ? '<div class="text-xs text-gray-400">' + formatHolderDate(tm.application_date) + '</div>' : '')
            + (tm.registration_date ? '<div class="text-xs" style="color:var(--color-text-faint)">' + t('holder.registration_date') + ': ' + formatHolderDate(tm.registration_date) + '</div>' : '')
            + window.AppComponents.renderEventDerivedBadges(tm)
            + window.AppComponents.renderLastEventLine(tm)
            + window.AppComponents.renderTurkpatentButton(tm.application_no)
            + egIndicator + '</div>'
            + '<div class="flex-shrink-0"><span class="' + getStatusBadgeClass(tm.status) + ' px-2 py-1 rounded text-xs font-medium">'
            + getStatusText(tm.status) + '</span></div>'
            + '<div class="flex-shrink-0 hidden sm:block">'
            + window.AppComponents.renderNiceClassBadges(tm.classes, 3)
            + '</div></div>';
    });
    container.innerHTML = html;
}

// Keep old name as alias for backward compat (api.js calls this)
function renderHolderTrademarks(trademarks) { renderEntityTrademarks(trademarks); }

function renderEntityPagination(currentPage, totalPages, entityId) {
    var container = document.getElementById('entityPagination');
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    var type = _entityPortfolioType || 'holder';
    var loadFnName = type === 'attorney' ? 'loadAttorneyTrademarks' : 'loadHolderTrademarks';

    var html = '<button onclick="' + loadFnName + '(\'' + escapeHtml(entityId) + '\', ' + (currentPage - 1) + ')" '
        + 'class="px-3 py-2 rounded-lg ' + (currentPage === 1 ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (currentPage === 1 ? 'disabled' : '') + '>' + t('pagination.prev') + '</button>'
        + '<span class="px-4 py-2 text-gray-600">' + t('pagination.page_of', { current: currentPage, total: totalPages }) + '</span>'
        + '<button onclick="' + loadFnName + '(\'' + escapeHtml(entityId) + '\', ' + (currentPage + 1) + ')" '
        + 'class="px-3 py-2 rounded-lg ' + (currentPage === totalPages ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (currentPage === totalPages ? 'disabled' : '') + '>' + t('pagination.next') + '</button>';
    container.innerHTML = html;
}

// Keep old name as alias
function renderHolderPagination(currentPage, totalPages, tpeClientId) { renderEntityPagination(currentPage, totalPages, tpeClientId); }

function closeEntityPortfolio() {
    document.getElementById('entityPortfolioModal').classList.add('hidden');
    _entityPortfolioType = null;
    _entityPortfolioId = null;
    _entityPortfolioName = null;
    _entityPortfolioTotalCount = 0;
    currentHolderTpeId = null;
    document.getElementById('entitySearchInput').value = '';
    document.getElementById('entitySearchResults').innerHTML = '';
    document.getElementById('entitySearchResults').classList.add('hidden');
    document.getElementById('entitySearchClearBtn').classList.add('hidden');
    document.getElementById('entitySearchBtn').classList.remove('hidden');
    var footer = document.getElementById('entityPortfolioFooter');
    if (footer) footer.classList.add('hidden');
    window._entitySearchPreviousState = null;
}

// Keep old name as alias
function closeHolderPortfolio() { closeEntityPortfolio(); }

// Entity portfolio: Watch All button
function entityBulkWatchlist() {
    var type = _entityPortfolioType;
    var id = _entityPortfolioId;
    if (!type || !id) {
        showToast('Portfolio bilgisi eksik, tekrar deneyin', 'error');
        return;
    }
    var token = getAuthToken();
    if (!token) {
        var data = _alpineRoot();
        if (data) {
            data.showLoginModal = true;
        } else {
            showToast(t('auth.session_expired'), 'error');
        }
        return;
    }
    window.dispatchEvent(new CustomEvent('open-bulk-watchlist', {
        detail: {
            type: type,
            id: id,
            name: _entityPortfolioName || '',
            totalCount: _entityPortfolioTotalCount || 0
        }
    }));
}

// Persistent result banner for bulk watchlist operations — only closable by X or timeout
function _showBulkResultBanner(created, total, type, skipped) {
    var existing = document.getElementById('bulk-result-banner');
    if (existing) existing.remove();

    var isInfo = (type === 'info' || created === 0);
    var bg = isInfo ? 'linear-gradient(135deg,#3b82f6,#2563eb)' : 'linear-gradient(135deg,#16a34a,#15803d)';
    var icon = isInfo
        ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>'
        : '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>';

    var msg = '';
    skipped = skipped || 0;

    if (isInfo && skipped > 0) {
        msg = skipped + ' marka zaten takip listenizde.';
    } else if (isInfo) {
        msg = (t('watchlist.all_already_added') || 'Bu markalar zaten takip listenizde');
    } else {
        msg = (t('holder.bulk_watchlist_result', { created: created, total: total }) || (created + '/' + total + ' eklendi'));
        if (skipped > 0) {
            msg += ' (' + skipped + ' marka atlandı, zaten listede)';
        }
    }

    var banner = document.createElement('div');
    banner.id = 'bulk-result-banner';
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;padding:16px 20px;background:' + bg + ';color:#fff;font-size:15px;font-weight:600;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;gap:12px;animation:slideDown 0.3s ease-out';
    banner.innerHTML = '<svg class="w-6 h-6 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">' + icon + '</svg>'
        + '<span>' + msg + '</span>'
        + '<button onclick="this.parentElement.remove()" style="background:none;border:none;color:white;font-size:20px;cursor:pointer;padding:0 8px;line-height:1;margin-left:8px">&times;</button>';
    document.body.prepend(banner);

    // Auto-hide the success banner after 5 seconds matching default toast duration
    setTimeout(function () {
        if (document.body.contains(banner)) {
            banner.style.animation = 'slideUp 0.3s ease-in forwards';
            setTimeout(function () { banner.remove(); }, 300);
        }
    }, 5000);
}

// Very visible upgrade banner for limit reached — persistent until user clicks X
function _showUpgradeBanner(message) {
    showUpgradeModal({
        current_plan: (window.AppAuth && window.AppAuth.currentUserPlan) || 'free',
        message: message || ''
    }, 'watchlist_items');
}

// Entity portfolio: CSV Download button
function entityDownloadCsv() {
    var type = _entityPortfolioType;
    var id = _entityPortfolioId;
    if (!type || !id) return;
    var token = getAuthToken();
    if (!token) {
        showUpgradeModal({
            error: 'upgrade_required',
            current_plan: 'free',
            upgrade_context: 'portfolio_download',
            message: t('upgrade.portfolio_download_description')
        }, 'portfolio_download');
        return;
    }
    var csvUrl = type === 'holder'
        ? '/api/v1/holders/' + encodeURIComponent(id) + '/trademarks/csv'
        : '/api/v1/attorneys/' + encodeURIComponent(id) + '/trademarks/csv';
    fetch(csvUrl, { headers: { 'Authorization': 'Bearer ' + token } })
        .then(function (res) {
            if (res.status === 401) {
                showToast(t('auth.session_expired'), 'error');
                throw { handled: true };
            }
            if (res.status === 403) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    showUpgradeModal((data && data.detail) || data || {}, 'portfolio_download');
                    throw { handled: true };
                });
            }
            if (!res.ok) throw new Error('CSV download failed');
            return res.blob();
        })
        .then(function (blob) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = (type === 'holder' ? 'holder_' : 'attorney_') + id + '_trademarks.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        })
        .catch(function (err) {
            if (err && err.handled) return;
            showToast(t('common.error'), 'error');
        });
}

// ============================================
// ENTITY SEARCH FUNCTIONS (generic)
// ============================================
function handleEntitySearchKeydown(event) {
    if (event.key === 'Enter') performEntitySearch();
}

function performEntitySearch() {
    var input = document.getElementById('entitySearchInput');
    var query = (input.value || '').trim();
    var cfg = _entityConfig[_entityPortfolioType] || _entityConfig.holder;
    if (query.length < 2) { showToast(t(cfg.i18nPrefix + '.search_min_chars'), 'warning'); return; }

    var searchResults = document.getElementById('entitySearchResults');
    searchResults.innerHTML = '<div class="flex flex-col items-center justify-center py-12">'
        + '<div class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"></div>'
        + '<p class="text-gray-500 mt-4">' + t(cfg.i18nPrefix + '.searching') + '</p></div>';
    document.getElementById('entityPortfolioBody').classList.add('hidden');
    searchResults.classList.remove('hidden');
    document.getElementById('entitySearchClearBtn').classList.remove('hidden');
    document.getElementById('entitySearchBtn').classList.add('hidden');

    window._entitySearchPreviousState = {
        id: _entityPortfolioId,
        name: _entityPortfolioName,
        type: _entityPortfolioType
    };

    cfg.searchFn(query).then(function (data) {
        renderEntitySearchResults(data.results || []);
    }).catch(function (err) {
        if (err.status === 403) {
            searchResults.classList.add('hidden');
            document.getElementById('entityPortfolioBody').classList.remove('hidden');
            showUpgradeModal(err, 'portfolio_download');
        } else {
            showToast(t(cfg.i18nPrefix + '.search_error'), 'error');
            searchResults.innerHTML = '<div class="text-center py-8 text-red-500">' + t(cfg.i18nPrefix + '.search_failed') + '</div>';
        }
    });
}

function renderEntitySearchResults(results) {
    var cfg = _entityConfig[_entityPortfolioType] || _entityConfig.holder;
    var container = document.getElementById('entitySearchResults');
    if (!results || results.length === 0) {
        container.innerHTML = '<div class="text-center py-12">'
            + '<svg class="mx-auto h-12 w-12 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>'
            + '<p class="text-gray-400">' + t(cfg.i18nPrefix + '.no_results') + '</p></div>';
        return;
    }

    var html = '<div class="mb-3 text-sm text-gray-500">' + t(cfg.i18nPrefix + '.results_found', { count: results.length }) + '</div>';
    results.forEach(function (result) {
        var escapedName = escapeHtml(result[cfg.nameKey] || '');
        var escapedId = escapeHtml(result[cfg.idKey] || '');
        var showFn = _entityPortfolioType === 'attorney' ? 'selectEntityFromSearch' : 'selectEntityFromSearch';
        html += '<div onclick="selectEntityFromSearch(\'' + escapedId + '\', \'' + escapedName.replace(/'/g, "\\'") + '\')" '
            + 'class="flex items-center justify-between p-3 border border-gray-200 rounded-lg hover:bg-blue-50 cursor-pointer transition-colors mb-2">'
            + '<div>'
            + '<div class="font-medium text-gray-900">' + escapedName
            + ' <span class="text-gray-400 text-sm">(' + escapedId + ')</span></div>'
            + '<div class="text-sm text-gray-500">' + t(cfg.i18nPrefix + '.trademarks_count', { count: result.trademark_count }) + '</div>'
            + '</div>'
            + '<svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>'
            + '</svg></div>';
    });
    container.innerHTML = html;
}

function selectEntityFromSearch(entityId, entityName) {
    document.getElementById('entitySearchResults').classList.add('hidden');
    document.getElementById('entityPortfolioBody').classList.remove('hidden');
    _entityPortfolioId = entityId;
    _entityPortfolioName = entityName;
    showEntityPortfolio(_entityPortfolioType || 'holder', entityId, entityName);
}

// Keep old name as alias
function selectHolderFromSearch(tpeClientId, holderName) { selectEntityFromSearch(tpeClientId, holderName); }

function clearEntitySearch() {
    document.getElementById('entitySearchInput').value = '';
    document.getElementById('entitySearchResults').classList.add('hidden');
    document.getElementById('entitySearchResults').innerHTML = '';
    document.getElementById('entityPortfolioBody').classList.remove('hidden');
    document.getElementById('entitySearchClearBtn').classList.add('hidden');
    document.getElementById('entitySearchBtn').classList.remove('hidden');

    if (window._entitySearchPreviousState && window._entitySearchPreviousState.id) {
        document.getElementById('entityPortfolioResults').classList.remove('hidden');
    }
    window._entitySearchPreviousState = null;
}

// Keep old name as alias
function clearHolderSearch() { clearEntitySearch(); }

// Escape key to close modals
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        var reportModal = document.getElementById('report-generate-modal');
        if (reportModal && !reportModal.classList.contains('hidden')) { hideReportGenerateModal(); return; }
        var alertModal = document.getElementById('alert-detail-modal');
        if (alertModal && !alertModal.classList.contains('hidden')) { hideAlertDetailModal(); return; }
        var oppositionModal = document.getElementById('opposition-modal');
        if (oppositionModal && !oppositionModal.classList.contains('hidden')) { hideOppositionModal(); return; }
        var entityModal = document.getElementById('entityPortfolioModal');
        if (entityModal && !entityModal.classList.contains('hidden')) closeEntityPortfolio();
    }
});

// ============================================
// SEARCH RESULTS SORTING
// ============================================
function buildSortBarHtml(count, data) {
    data = data || {};
    var elapsed = data.elapsed_seconds ? (data.elapsed_seconds).toFixed(1) + 's' : '';
    var modeLabel = data.scrape_triggered ? t('search.mode_intelligent') : t('search.mode_quick');
    var modeStyle = data.scrape_triggered
        ? 'background:rgba(245,158,11,0.15);color:var(--color-risk-medium-text)'
        : 'background:rgba(79,70,229,0.15);color:var(--color-primary)';

    // Risk badge
    var riskHtml = '';
    if (data.risk_level) {
        var rlStyle = window.AppComponents.getRiskBadgeColor(data.risk_level);
        riskHtml = '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full font-bold border" style="' + rlStyle + '">'
            + data.risk_level + '</span>';
    }

    // Max score (text category, no percentage)
    var maxHtml = '';
    if (data.max_score !== null && data.max_score !== undefined) {
        var maxPct = Math.round(data.max_score * 100);
        var maxCategory = window.AppComponents.getSimilarityCategory(maxPct);
        maxHtml = '<span class="text-xs font-medium" style="color:var(--color-text-secondary)">'
            + t('scores.max_risk') + ' ' + t('risk_level.' + maxCategory) + '</span>';
    }

    // Candidates
    var candidatesHtml = '';
    if (data.total_candidates && data.total_candidates !== count) {
        candidatesHtml = '<span class="text-xs" style="color:var(--color-text-faint)">'
            + t('scores.total_candidates', { count: data.total_candidates }) + '</span>';
    }

    // Image used badge
    var imageHtml = '';
    if (data.image_used) {
        imageHtml = '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" style="background:rgba(168,85,247,0.15);color:#9333ea">'
            + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
            + t('search.visual_analysis') + '</span>';
    }

    return '<div class="rounded-xl p-3 mb-3 flex flex-wrap items-center gap-x-4 gap-y-2" style="background:var(--color-bg-muted);border:1px solid var(--color-border)">'
        // Left: count + mode + badges
        + '<div class="flex flex-wrap items-center gap-2">'
        + '<span class="text-sm font-semibold" style="color:var(--color-text-primary)">' + t('sort.results_count', { count: count }) + '</span>'
        + '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium" style="' + modeStyle + '">' + modeLabel + '</span>'
        + (data.source ? '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium" style="' + (data.source === 'database' ? 'background:var(--color-bg-muted);color:var(--color-text-secondary)' : 'background:rgba(168,85,247,0.15);color:#9333ea') + '">' + (data.source === 'database' ? t('search.source_db') : t('search.source_live')) + '</span>' : '')
        + riskHtml + imageHtml
        + '</div>'
        // Center: meta stats
        + '<div class="flex items-center gap-2 flex-wrap">'
        + (maxHtml ? maxHtml : '')
        + (candidatesHtml ? '<span style="color:var(--color-text-faint)">&middot;</span>' + candidatesHtml : '')
        + (elapsed ? '<span style="color:var(--color-text-faint)">&middot;</span><span class="text-xs" style="color:var(--color-text-faint)">' + elapsed + '</span>' : '')
        + '</div>'
        // Right: sort
        + '<div class="flex items-center gap-2 ml-auto">'
        + '<span class="text-xs" style="color:var(--color-text-muted)">' + t('sort.label') + '</span>'
        + '<select id="sortSelect" onchange="sortSearchResults()" '
        + 'class="text-sm rounded-lg px-2 py-1.5 pr-7 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500" '
        + 'style="border:1px solid var(--color-border-input);background:var(--color-bg-input);color:var(--color-text-secondary)">'
        + '<option value="risk_desc">' + t('sort.risk_desc') + '</option>'
        + '<option value="risk_asc">' + t('sort.risk_asc') + '</option>'
        + '<option value="date_desc">' + t('sort.date_desc') + '</option>'
        + '<option value="date_asc">' + t('sort.date_asc') + '</option>'
        + '</select>'
        + '<button onclick="resetSort()" class="text-xs hover:opacity-70" style="color:var(--color-text-faint)" title="' + t('sort.reset') + '">&#x21ba;</button>'
        + '</div></div>';
}

function sortSearchResults() {
    var sel = document.getElementById('sortSelect');
    var mode = sel ? sel.value : 'risk_desc';
    var sorted = _storedSearchResults.slice();

    if (mode === 'risk_desc') {
        sorted.sort(function (a, b) { return getResultScore(b) - getResultScore(a); });
    } else if (mode === 'risk_asc') {
        sorted.sort(function (a, b) { return getResultScore(a) - getResultScore(b); });
    } else if (mode === 'date_desc') {
        sorted.sort(function (a, b) { return parseResultDate(b.application_date) - parseResultDate(a.application_date); });
    } else if (mode === 'date_asc') {
        sorted.sort(function (a, b) { return parseResultDate(a.application_date) - parseResultDate(b.application_date); });
    }

    var cardsContainer = document.getElementById('search-results-cards');
    if (cardsContainer) {
        cardsContainer.innerHTML = sorted.map(renderResultCard).join('');
        // Add staggered entrance animation
        var cards = cardsContainer.children;
        for (var ci = 0; ci < cards.length; ci++) {
            cards[ci].style.animationDelay = (ci * 50) + 'ms';
            cards[ci].classList.add('card-enter');
        }
    }
}

function resetSort() {
    var sel = document.getElementById('sortSelect');
    if (sel) sel.value = 'risk_desc';
    sortSearchResults();
}

// ============================================
// DISPLAY AGENTIC RESULTS
// ============================================
function displayAgenticResults(data) {
    var container = document.getElementById('search-results');
    if (!container) return;

    container.classList.remove('hidden');
    currentSearchTotal = data.total || 0;

    var html = '';

    // Source banner (compact)
    var bannerIcon = data.scrape_triggered ? '&#x1f575;&#xfe0f;' : '&#x1f50d;';
    var bannerText = data.scrape_triggered ? t('search.live_results') : t('search.db_results');
    var bannerBg = data.scrape_triggered
        ? 'background:var(--color-risk-medium-bg);border-color:var(--color-risk-medium-border)'
        : 'background:var(--color-primary-light);border-color:rgba(79,70,229,0.2)';
    var bannerTxtColor = data.scrape_triggered
        ? 'color:var(--color-risk-medium-text)' : 'color:var(--color-primary)';

    html += '<div class="mb-3 px-4 py-2 rounded-xl border flex items-center gap-2 flex-wrap" style="' + bannerBg + '">'
        + '<span class="text-lg">' + bannerIcon + '</span>'
        + '<span class="text-sm font-semibold" style="' + bannerTxtColor + '">' + bannerText + '</span>';
    if (data.scraped_count) {
        html += '<span class="text-xs" style="color:var(--color-text-faint)">' + t('search.new_records', { count: data.scraped_count }) + '</span>';
    }
    html += '</div>';

    var results = (data.results || []).slice(0, DASHBOARD_SEARCH_RESULT_LIMIT);
    // Attach query context to each result for AI Studio CTA
    var searchInput = document.getElementById('search-input');
    var queryName = searchInput ? searchInput.value.trim() : '';
    var queryClasses = getSelectedNiceClasses();
    results.forEach(function (r) {
        r._query_name = queryName;
        r._query_classes = queryClasses;
    });
    _storedSearchResults = results.slice();

    if (results.length === 0) {
        html += '<div class="text-center py-8 text-gray-400"><div class="text-4xl mb-2">&#x1f50d;</div><p>' + t('search.no_results') + '</p></div>';
    } else {
        // Consolidated search stats bar with count, meta, and sort
        html += buildSortBarHtml(Math.min(data.total || results.length, DASHBOARD_SEARCH_RESULT_LIMIT), data);
        html += '<div id="search-results-cards">';
        results.forEach(function (r) { html += renderResultCard(r); });
        html += '</div>';
        // Risk/Safe banner CTA (same style as landing page)
        html += buildSearchResultBannerHtml(results, queryName, queryClasses);
    }

    container.innerHTML = html;
}

// ============================================
// SEARCH RESULTS BANNER CTA (Risk/Safe)
// ============================================
function buildSearchResultBannerHtml(results, queryName, queryClasses) {
    if (!results || results.length === 0 || !queryName) return '';

    // Check if any result has high risk (>=65%)
    var hasHigh = false;
    var hasHighVisual = false;
    var hasImage = false;
    for (var i = 0; i < results.length; i++) {
        var score = getResultScore(results[i]);
        var pct = Math.round(score * 100);
        if (pct >= 65) hasHigh = true;
        var visSim = results[i].scores ? (results[i].scores.visual_similarity || 0) : 0;
        if (visSim > 0.65) hasHighVisual = true;
        if (results[i].image_path) hasImage = true;
    }

    var ctx = encodeURIComponent(JSON.stringify({ query: queryName, nice_classes: queryClasses || [] }));

    if (hasHigh) {
        // Determine search type for title
        var searchInput = document.getElementById('search-input');
        var dashData = Alpine.$data(document.querySelector('[x-data]'));
        var hasImageUpload = dashData && dashData.selectedImage;
        var titleKey = hasImageUpload && !queryName ? 'landing.studio_cta_title_image'
            : hasImageUpload ? 'landing.studio_cta_title_both'
                : 'landing.studio_cta_title_text';
        var descKey = hasImageUpload && !queryName ? 'landing.studio_cta_desc_image'
            : hasImageUpload ? 'landing.studio_cta_desc_both'
                : 'landing.studio_cta_desc_text';

        var html = '<div class="mt-4 rounded-xl overflow-hidden" style="background:linear-gradient(135deg, rgba(239,68,68,0.08) 0%, rgba(99,102,241,0.12) 100%);border:1px solid var(--color-border)">'
            + '<div class="p-4">'
            + '<div class="flex items-start gap-3">'
            + '<div class="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0" style="background:rgba(239,68,68,0.12)">'
            + '<svg class="w-5 h-5" style="color:#ef4444" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>'
            + '</div>'
            + '<div class="flex-1 min-w-0">'
            + '<h4 class="text-sm font-bold mb-1" style="color:var(--color-text-primary)">' + t(titleKey) + '</h4>'
            + '<p class="text-xs leading-relaxed mb-3" style="color:var(--color-text-secondary)">' + t(descKey) + '</p>'
            + '<div class="flex flex-wrap gap-2">'
            + '<button onclick="openStudioWithContext(\'name\', JSON.parse(decodeURIComponent(\'' + ctx + '\')))" '
            + 'class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90 btn-press" '
            + 'style="background:linear-gradient(135deg, #6366f1, #8b5cf6);box-shadow:0 2px 8px rgba(99,102,241,0.3)">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>'
            + t('landing.studio_cta_name_btn') + '</button>';

        if (hasHighVisual) {
            html += '<button onclick="openStudioWithContext(\'logo\', JSON.parse(decodeURIComponent(\'' + ctx + '\')))" '
                + 'class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-all hover:opacity-90 btn-press" '
                + 'style="background:var(--color-bg-card);color:var(--color-primary);border:1px solid var(--color-primary)">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
                + t('landing.studio_cta_logo_btn') + '</button>';
        }

        html += '</div></div></div></div></div>';
        return html;

    } else {
        // Safe — encourage registration
        var appCtx = encodeURIComponent(JSON.stringify({ name: queryName, classes: queryClasses || [] }));
        var html = '<div class="mt-4 rounded-xl overflow-hidden" style="background:linear-gradient(135deg, rgba(34,197,94,0.08) 0%, rgba(16,185,129,0.12) 100%);border:1px solid rgba(34,197,94,0.3)">'
            + '<div class="p-4">'
            + '<div class="flex items-start gap-3">'
            + '<div class="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0" style="background:rgba(34,197,94,0.12)">'
            + '<svg class="w-5 h-5" style="color:#22c55e" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
            + '</div>'
            + '<div class="flex-1 min-w-0">'
            + '<h4 class="text-sm font-bold mb-1" style="color:var(--color-text-primary)">' + t('landing.safe_cta_title') + '</h4>'
            + '<p class="text-xs leading-relaxed mb-3" style="color:var(--color-text-secondary)">' + t('landing.safe_cta_desc') + '</p>'
            + '<button onclick="var _c=JSON.parse(decodeURIComponent(\'' + appCtx + '\'));openApplicationWithContext(_c.name,_c.classes)" '
            + 'class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90 btn-press" '
            + 'style="background:linear-gradient(135deg, #22c55e, #16a34a);box-shadow:0 2px 8px rgba(34,197,94,0.3)">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
            + t('landing.safe_cta_button') + '</button>'
            + '</div></div></div></div>';
        return html;
    }
}

// ============================================
// AI STUDIO
// ============================================

function initAIStudio() {
    if (studioInitialized) return;
    studioInitialized = true;
    populateStudioNiceClasses('studio-name-classes');
    populateStudioNiceClasses('studio-logo-classes');
    updateStudioClassSummary('studio-name-classes');
    updateStudioClassSummary('studio-logo-classes');
    bindStudioNameRequiredInputs();
    initStudioColorSwatches();
    updateStudioCredits();
    updateStudioModeMeta();
    updateStudioNameButtonState();
    checkCreativeSuiteStatus();
    loadStudioUsageSummary();
    loadStudioHistory();
}

function checkCreativeSuiteStatus() {
    return fetch('/api/v1/tools/status')
        .then(function (res) { return res.json(); })
        .then(function (data) {
            studioStatus = data || {};
            applyStudioAvailability('name', studioStatus.name_generator);
            applyStudioAvailability('logo', studioStatus.logo_studio);
            updateStudioModeMeta();
        })
        .catch(function () {
            // Status endpoint unreachable — don't block usage, just log
            console.warn('Creative Suite status check failed');
        });
}

function applyStudioAvailability(mode, toolStatus) {
    var btn = document.getElementById(mode === 'name' ? 'studio-name-btn' : 'studio-logo-btn');
    var panel = document.getElementById(mode === 'name' ? 'studio-name-panel' : 'studio-logo-panel');
    var bannerId = mode === 'name' ? 'name-unavailable-banner' : 'logo-unavailable-banner';
    var existing = document.getElementById(bannerId);
    var available = !toolStatus || toolStatus.available !== false;
    var translatedReason = translateStudioStatusReason(toolStatus && toolStatus.reason);

    if (mode === 'name') {
        updateStudioNameButtonState();
    } else if (btn) {
        btn.disabled = !available;
        btn.classList.toggle('opacity-50', !available);
        btn.classList.toggle('cursor-not-allowed', !available);
        btn.title = available ? '' : (translatedReason || t('studio.service_unavailable'));
    }

    if (available) {
        if (existing) existing.remove();
        updateStudioModeMeta();
        return;
    }

    if (panel && !existing) {
        var banner = document.createElement('div');
        banner.id = bannerId;
        banner.className = 'studio-unavailable-banner';
        var label = mode === 'name' ? t('studio.service_unavailable_now') : t('studio.logo_studio_unavailable');
        banner.innerHTML = '<svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M12 18a9 9 0 110-18 9 9 0 010 18z"/></svg>'
            + '<div><strong>' + escapeHtml(label) + '</strong> ' + escapeHtml(translatedReason || '') + '</div>';
        panel.insertBefore(banner, panel.firstChild);
    }
    updateStudioModeMeta();
}

function translateStudioStatusReason(reason) {
    if (!reason) return '';
    var normalized = String(reason).toLowerCase();
    if (normalized.indexOf('openai') !== -1 && normalized.indexOf('gemini') !== -1 && (normalized.indexOf('anahtar') !== -1 || normalized.indexOf('api key') !== -1 || normalized.indexOf('key') !== -1)) {
        return t('studio.reason_logo_providers_not_configured');
    }
    if (normalized.indexOf('gemini') !== -1 && (normalized.indexOf('anahtar') !== -1 || normalized.indexOf('api key') !== -1 || normalized.indexOf('key') !== -1)) {
        return t('studio.reason_gemini_not_configured');
    }
    if (normalized.indexOf('clip') !== -1 && normalized.indexOf('fonksiyonu') !== -1) {
        return t('studio.reason_clip_function_missing');
    }
    if (normalized.indexOf('clip') !== -1 && (normalized.indexOf('model') !== -1 || normalized.indexOf('modeli') !== -1)) {
        return t('studio.reason_clip_not_loaded');
    }
    return reason;
}

function refreshStudioAvailabilityText() {
    ['name-unavailable-banner', 'logo-unavailable-banner'].forEach(function (id) {
        var existing = document.getElementById(id);
        if (existing) existing.remove();
    });
    if (studioStatus) {
        applyStudioAvailability('name', studioStatus.name_generator);
        applyStudioAvailability('logo', studioStatus.logo_studio);
    } else {
        updateStudioModeMeta();
    }
}

function populateStudioNiceClasses(selectId) {
    var select = document.getElementById(selectId);
    if (!select || select.dataset.populated === '1') return;
    if (select.tagName === 'SELECT' && select.options.length > 5) return;
    for (var i = 1; i <= 45; i++) {
        if (select.tagName === 'SELECT') {
            var opt = document.createElement('option');
            opt.value = i;
            opt.textContent = i + ' - ' + t('nice_classes.' + i);
            select.appendChild(opt);
        } else {
            var chip = document.createElement('button');
            chip.type = 'button';
            chip.dataset.value = String(i);
            chip.className = 'studio-class-chip';
            chip.title = t('nice_classes.' + i);
            chip.setAttribute('aria-pressed', 'false');
            chip.textContent = i + ' - ' + t('nice_classes.' + i);
            chip.onclick = function () {
                var selected = this.getAttribute('aria-pressed') === 'true';
                setStudioClassChipSelected(this, !selected);
                updateStudioClassSummary(selectId);
                if (selectId === 'studio-name-classes') updateStudioNameButtonState();
            };
            select.appendChild(chip);
        }
    }
    select.dataset.populated = '1';
    updateStudioClassSummary(selectId);
}

function setStudioClassChipSelected(chip, selected) {
    chip.setAttribute('aria-pressed', selected ? 'true' : 'false');
    chip.classList.toggle('is-selected', selected);
}

function getStudioNiceClasses(selectId) {
    var select = document.getElementById(selectId);
    if (!select) return [];
    if (select.tagName === 'SELECT') {
        return Array.from(select.selectedOptions).map(function (o) { return parseInt(o.value); }).filter(function (v) { return !isNaN(v); });
    }
    return Array.from(select.querySelectorAll('.studio-class-chip.is-selected'))
        .map(function (chip) { return parseInt(chip.dataset.value); })
        .filter(function (v) { return !isNaN(v); });
}

function refreshStudioClassPickers() {
    ['studio-name-classes', 'studio-logo-classes'].forEach(function (selectId) {
        var select = document.getElementById(selectId);
        if (!select || select.tagName === 'SELECT') return;
        var selected = getStudioNiceClasses(selectId);
        select.innerHTML = '';
        select.dataset.populated = '0';
        populateStudioNiceClasses(selectId);
        setStudioSelectValues(selectId, selected);
        updateStudioClassSummary(selectId);
    });
}

function refreshStudioClassToggleLabels() {
    ['studio-name-classes', 'studio-logo-classes'].forEach(function (selectId) {
        var picker = document.getElementById(selectId);
        var toggle = document.getElementById(selectId + '-toggle');
        if (!picker || !toggle) return;
        var expanded = !picker.classList.contains('hidden');
        toggle.textContent = expanded ? t('studio.done_classes') : t('studio.edit_classes');
    });
}

function updateStudioClassSummary(selectId) {
    var summary = document.getElementById(selectId + '-summary');
    if (!summary) return;
    var values = getStudioNiceClasses(selectId);
    if (!values.length) {
        summary.textContent = t('studio.no_classes_selected');
        if (selectId === 'studio-name-classes') updateStudioNameButtonState();
        return;
    }
    var preview = values.slice(0, 4).join(', ');
    if (values.length > 4) preview += ' +' + (values.length - 4);
    summary.textContent = t('studio.classes_selected', { count: values.length }) + ' (' + preview + ')';
    if (selectId === 'studio-name-classes') updateStudioNameButtonState();
}

function toggleStudioClassPicker(selectId) {
    var picker = document.getElementById(selectId);
    var toggle = document.getElementById(selectId + '-toggle');
    if (!picker || !toggle) return;
    var expanded = picker.classList.toggle('hidden') === false;
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    toggle.textContent = expanded ? t('studio.done_classes') : t('studio.edit_classes');
}

function initStudioColorSwatches() {
    var input = document.getElementById('studio-logo-colors');
    var selected = document.querySelector('.studio-color-swatch.is-selected');
    if (input && input.value && !selected) {
        Array.from(document.querySelectorAll('.studio-color-swatch')).some(function (btn) {
            if (getStudioColorSwatchValue(btn) === input.value || btn.dataset.value === input.value) {
                btn.classList.add('is-selected');
                return true;
            }
            return false;
        });
    }
}

function getStudioColorSwatchValue(button) {
    if (!button) return '';
    var key = button.dataset.i18nValue;
    return key ? t(key) : (button.dataset.value || '');
}

function selectStudioColorPalette(button) {
    if (!button) return;
    document.querySelectorAll('.studio-color-swatch').forEach(function (swatch) {
        swatch.classList.remove('is-selected');
    });
    button.classList.add('is-selected');
    var input = document.getElementById('studio-logo-colors');
    if (input) input.value = getStudioColorSwatchValue(button);
}

function clearStudioColorPalette() {
    document.querySelectorAll('.studio-color-swatch.is-selected').forEach(function (swatch) {
        swatch.classList.remove('is-selected');
    });
}

function refreshStudioColorSwatches() {
    var selected = document.querySelector('.studio-color-swatch.is-selected');
    var input = document.getElementById('studio-logo-colors');
    if (selected && input) {
        input.value = getStudioColorSwatchValue(selected);
    }
}

function bindStudioNameRequiredInputs() {
    ['studio-name-query', 'studio-name-industry', 'studio-name-language', 'studio-name-style'].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el || el.dataset.studioRequiredBound === '1') return;
        el.dataset.studioRequiredBound = '1';
        el.addEventListener('input', updateStudioNameButtonState);
        el.addEventListener('change', updateStudioNameButtonState);
    });
}

function getStudioNameRequiredMissingFields() {
    var queryEl = document.getElementById('studio-name-query');
    var industryEl = document.getElementById('studio-name-industry');
    var languageEl = document.getElementById('studio-name-language');
    var styleEl = document.getElementById('studio-name-style');
    var missing = [];
    if (!queryEl || !(queryEl.value || '').trim()) missing.push('query');
    if (!getStudioNiceClasses('studio-name-classes').length) missing.push('nice_classes');
    if (!industryEl || !(industryEl.value || '').trim()) missing.push('industry');
    if (!languageEl || !(languageEl.value || '').trim()) missing.push('language');
    if (!styleEl || !(styleEl.value || '').trim()) missing.push('style');
    return missing;
}

function updateStudioNameButtonState() {
    var btn = document.getElementById('studio-name-btn');
    if (!btn) return;
    var toolStatus = studioStatus && studioStatus.name_generator;
    var available = !toolStatus || toolStatus.available !== false;
    var formComplete = getStudioNameRequiredMissingFields().length === 0;
    var disabled = studioNameLoading || !available;
    btn.disabled = disabled;
    btn.classList.toggle('opacity-50', disabled);
    btn.classList.toggle('cursor-not-allowed', disabled);
    if (!available) {
        btn.title = translateStudioStatusReason(toolStatus && toolStatus.reason) || t('studio.service_unavailable');
    } else if (!formComplete) {
        btn.title = t('studio.complete_required_fields');
    } else {
        btn.title = '';
    }
}

function updateStudioModeMeta() {
    var statusEl = document.getElementById('studio-active-status');
    var costEl = document.getElementById('studio-run-cost');
    var toolStatus = studioActiveMode === 'logo'
        ? (studioStatus && studioStatus.logo_studio)
        : (studioStatus && studioStatus.name_generator);
    var checking = studioStatus === null;
    var available = checking || !toolStatus || toolStatus.available !== false;
    if (statusEl) {
        statusEl.classList.toggle('is-available', !checking && available);
        statusEl.classList.toggle('is-unavailable', !checking && !available);
        statusEl.textContent = checking ? t('studio.status_checking') : (available ? t('studio.status_available') : t('studio.status_unavailable'));
        statusEl.title = available ? '' : (translateStudioStatusReason(toolStatus && toolStatus.reason) || t('studio.service_unavailable'));
    }
    if (costEl) {
        costEl.textContent = t('studio.run_cost', { cost: getStudioRunCost(studioActiveMode) });
    }
}

function getStudioRunCost(mode) {
    var toolStatus = mode === 'logo'
        ? (studioStatus && studioStatus.logo_studio)
        : (studioStatus && studioStatus.name_generator);
    var fallback = mode === 'logo' ? 5 : 2;
    var parsed = Number(toolStatus && toolStatus.cost);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function updateStudioCredits() {
    var el = document.getElementById('studio-credits-display');
    if (!el) return;
    if (!studioCredits) {
        el.textContent = '-';
        return;
    }
    el.textContent = t('studio.credits_remaining_short', {
        remaining: studioCredits.total_remaining || 0,
        limit: studioCredits.monthly_limit || 0
    });
}

function normalizeStudioCredits(raw, cost) {
    raw = raw || {};
    return {
        monthly_remaining: raw.monthly_remaining != null ? raw.monthly_remaining : (raw.monthly || 0),
        purchased_remaining: raw.purchased_remaining != null ? raw.purchased_remaining : (raw.purchased || 0),
        total_remaining: raw.total_remaining != null ? raw.total_remaining : ((raw.monthly || 0) + (raw.purchased || 0)),
        monthly_limit: raw.monthly_limit != null ? raw.monthly_limit : (raw.limit || 0),
        cost: raw.cost || cost || 1
    };
}

async function loadStudioUsageSummary() {
    try {
        var res = await fetch('/api/v1/usage/summary', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!res.ok) return;
        var data = await res.json();
        var usage = data.usage || {};
        var credits = usage.monthly_ai_credits || {};
        studioCredits = normalizeStudioCredits({
            total_remaining: credits.remaining || 0,
            monthly_remaining: credits.remaining || 0,
            purchased_remaining: 0,
            monthly_limit: credits.limit || 0,
            cost: getStudioRunCost(studioActiveMode)
        }, getStudioRunCost(studioActiveMode));
        updateStudioCredits();
        updateLogoCreditsDisplay();
    } catch (e) {
        console.warn('AI Studio usage summary failed');
    }
}

function switchStudioMode(mode) {
    studioActiveMode = mode;

    document.getElementById('studio-name-panel').classList.toggle('hidden', mode !== 'name');
    document.getElementById('studio-logo-panel').classList.toggle('hidden', mode !== 'logo');

    document.querySelectorAll('.studio-mode-btn').forEach(function (btn) {
        btn.classList.remove('is-active');
        btn.setAttribute('aria-selected', 'false');
    });

    var activeBtn = document.getElementById('studio-mode-' + mode);
    if (activeBtn) {
        activeBtn.classList.add('is-active');
        activeBtn.setAttribute('aria-selected', 'true');
    }

    // Update credits display for the active mode
    if (mode === 'logo') {
        updateLogoCreditsDisplay();
    }
    updateStudioCredits();
    updateStudioModeMeta();
    updateStudioNameButtonState();
}

// ============================================
// NAME LAB: GENERATE
// ============================================
async function generateNames() {
    var query = (document.getElementById('studio-name-query').value || '').trim();
    if (getStudioNameRequiredMissingFields().length) {
        updateStudioNameButtonState();
        showToast(t('studio.complete_required_fields'), 'error');
        return;
    }
    if (studioStatus && studioStatus.name_generator && studioStatus.name_generator.available === false) {
        showToast(translateStudioStatusReason(studioStatus.name_generator.reason) || t('studio.service_unavailable'), 'error');
        return;
    }

    if (studioNameLoading) return;
    studioNameLoading = true;
    updateStudioNameButtonState();

    var classes = getStudioNiceClasses('studio-name-classes');
    var industry = (document.getElementById('studio-name-industry').value || '').trim();
    var languageEl = document.getElementById('studio-name-language');
    var language = languageEl ? (languageEl.value || 'mixed') : 'mixed';
    var style = document.getElementById('studio-name-style').value || 'modern';

    // Show loading, hide others
    document.getElementById('studio-name-loading').classList.remove('hidden');
    var nameIdleEl = document.getElementById('studio-name-idle');
    if (nameIdleEl) nameIdleEl.classList.add('hidden');
    document.getElementById('studio-name-results').classList.add('hidden');
    document.getElementById('studio-name-empty').classList.add('hidden');
    document.getElementById('studio-name-error').classList.add('hidden');

    try {
        var data = await generateNamesAPI({
            query: query,
            nice_classes: classes,
            industry: industry,
            style: style,
            language: language,
            avoid_names: []
        });
        studioLastNameResult = data;

        document.getElementById('studio-name-loading').classList.add('hidden');

        renderStudioNameResults(data);

        if (data.credits_remaining) {
            studioCredits = normalizeStudioCredits(data.credits_remaining, 1);
            updateStudioCredits();
        }
        _refreshDashboardUsage();
        loadStudioHistory();

    } catch (e) {
        document.getElementById('studio-name-loading').classList.add('hidden');
        if (e.message !== 'upgrade_required' && e.message !== 'credits_exhausted' && e.message !== 'unauthorized') {
            document.getElementById('studio-name-error').classList.remove('hidden');
            document.getElementById('studio-name-error-msg').textContent = e.message || t('studio.name_gen_failed');
        }
    } finally {
        studioNameLoading = false;
        updateStudioNameButtonState();
        if (studioStatus && studioStatus.name_generator) {
            applyStudioAvailability('name', studioStatus.name_generator);
        }
    }
}

// ============================================
// NAME LAB: USE FOR LOGO
// ============================================
function renderStudioNameResults(data) {
    data = data || {};
    var safeNames = data.safe_names || [];
    if (safeNames.length === 0) {
        document.getElementById('studio-name-empty').classList.remove('hidden');
        return;
    }

    document.getElementById('studio-name-results').classList.remove('hidden');
    document.getElementById('studio-name-meta').textContent =
        t('studio.safe_count_meta', { safe: safeNames.length, total: data.total_generated, filtered: data.filtered_count })
        + (data.cached ? ' ' + t('studio.from_cache') : '');

    var cardsHtml = '';
    safeNames.forEach(function (name, i) {
        cardsHtml += renderNameCard(name, i);
    });
    document.getElementById('studio-name-cards').innerHTML = cardsHtml;
}

function refreshStudioNameResultsText() {
    var resultsEl = document.getElementById('studio-name-results');
    if (!studioLastNameResult || !resultsEl || resultsEl.classList.contains('hidden')) return;
    renderStudioNameResults(studioLastNameResult);
}

function useNameForLogo(name) {
    switchStudioMode('logo');
    document.getElementById('studio-logo-name').value = name;
    document.getElementById('studio-logo-name').focus();
    showToast(t('studio.brand_transferred_to_logo'), 'info');
}

// ============================================
// LOGO STUDIO: GENERATE
// ============================================
async function generateLogos() {
    var brandName = (document.getElementById('studio-logo-name').value || '').trim();
    if (!brandName) { showToast(t('search.enter_brand_name'), 'error'); return; }
    if (studioStatus && studioStatus.logo_studio && studioStatus.logo_studio.available === false) {
        showToast(translateStudioStatusReason(studioStatus.logo_studio.reason) || t('studio.service_unavailable'), 'error');
        return;
    }

    if (studioLogoLoading) return;
    studioLogoLoading = true;
    stopLogoProjectPolling();

    var description = (document.getElementById('studio-logo-desc').value || '').trim();
    var colors = (document.getElementById('studio-logo-colors').value || '').trim();
    var classes = getStudioNiceClasses('studio-logo-classes');

    // Show loading, hide others
    var firstGenLoadingEl = document.getElementById('studio-logo-loading');
    if (firstGenLoadingEl) firstGenLoadingEl.classList.remove('is-revising');
    firstGenLoadingEl.classList.remove('hidden');
    var logoIdleEl = document.getElementById('studio-logo-idle');
    if (logoIdleEl) logoIdleEl.classList.add('hidden');
    document.getElementById('studio-logo-results').classList.add('hidden');
    document.getElementById('studio-logo-error').classList.add('hidden');
    var logoEmptyEl = document.getElementById('studio-logo-empty');
    if (logoEmptyEl) logoEmptyEl.classList.add('hidden');

    var btn = document.getElementById('studio-logo-btn');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }

    try {
        var data = await generateLogosAPI({
            brand_name: brandName,
            description: description,
            color_preferences: colors,
            nice_classes: classes
        });
        studioActiveLogoProjectId = data.project_id || null;
        studioSelectedLogoImageId = null;
        studioLastLogoResult = data;

        document.getElementById('studio-logo-loading').classList.add('hidden');

        renderStudioLogoResults(data);
        startLogoProjectPollingIfNeeded(data);

        if (data.credits_remaining) {
            studioCredits = normalizeStudioCredits(data.credits_remaining, 5);
            updateStudioCredits();
            updateLogoCreditsFromData(data.credits_remaining);
        }
        _refreshDashboardUsage();
        loadStudioHistory();

    } catch (e) {
        document.getElementById('studio-logo-loading').classList.add('hidden');
        if (e.message !== 'upgrade_required' && e.message !== 'credits_exhausted' && e.message !== 'unauthorized') {
            document.getElementById('studio-logo-error').classList.remove('hidden');
            document.getElementById('studio-logo-error-msg').textContent = e.message || t('studio.logo_failed_msg');
        }
    } finally {
        studioLogoLoading = false;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
        if (studioStatus && studioStatus.logo_studio) {
            applyStudioAvailability('logo', studioStatus.logo_studio);
        }
    }
}

function getLogoFormPayload(extra) {
    extra = extra || {};
    var payload = {
        brand_name: (document.getElementById('studio-logo-name').value || '').trim(),
        description: (document.getElementById('studio-logo-desc').value || '').trim(),
        color_preferences: (document.getElementById('studio-logo-colors').value || '').trim(),
        nice_classes: getStudioNiceClasses('studio-logo-classes')
    };
    Object.keys(extra).forEach(function (key) {
        payload[key] = extra[key];
    });
    return payload;
}

function hasPendingLogoAudits(logos) {
    return (logos || []).some(function (logo) {
        var status = logo.audit_status || 'completed';
        return status === 'pending' || status === 'running';
    });
}

function stopLogoProjectPolling() {
    if (studioLogoPollTimer) {
        clearInterval(studioLogoPollTimer);
        studioLogoPollTimer = null;
    }
}

function startLogoProjectPollingIfNeeded(data) {
    var projectId = (data && (data.project_id || data.id)) || studioActiveLogoProjectId;
    var logos = (data && data.logos) || [];
    if (!projectId || !hasPendingLogoAudits(logos)) {
        stopLogoProjectPolling();
        return;
    }
    studioActiveLogoProjectId = projectId;
    stopLogoProjectPolling();
    studioLogoPollTimer = setInterval(function () {
        fetchAndRenderLogoProject(projectId, true);
    }, 2500);
}

async function fetchAndRenderLogoProject(projectId, fromPoll) {
    if (!projectId) return null;
    try {
        var project = await getLogoProjectAPI(projectId);
        studioActiveLogoProjectId = project.id;
        studioLastLogoResult = {
            logos: project.logos || [],
            project_id: project.id,
            selected_image_id: project.selected_image_id,
            project: project
        };
        renderStudioLogoResults(studioLastLogoResult);
        if (hasPendingLogoAudits(project.logos || [])) {
            startLogoProjectPollingIfNeeded(project);
        } else {
            stopLogoProjectPolling();
        }
        if (!fromPoll) loadStudioHistory();
        return project;
    } catch (e) {
        if (!fromPoll) showToast(e.message || t('studio.project_load_failed'), 'error');
        return null;
    }
}

async function reviseSelectedLogo() {
    var revisionTargetId = studioSelectedLogoImageId
        || (studioLastLogoResult && studioLastLogoResult.selected_image_id)
        || null;
    if (revisionTargetId && !_studioLogos[revisionTargetId] && studioLastLogoResult && studioLastLogoResult.logos) {
        var match = studioLastLogoResult.logos.find(function (l) { return l && l.image_id === revisionTargetId; });
        if (match) _studioLogos[revisionTargetId] = match;
    }
    if (!revisionTargetId || !_studioLogos[revisionTargetId]) {
        showToast(t('studio.select_logo_to_revise'), 'error');
        return;
    }
    studioSelectedLogoImageId = revisionTargetId;
    var selectedLogo = _studioLogos[revisionTargetId];
    var projectId = selectedLogo.project_id || studioActiveLogoProjectId || null;
    if (projectId) studioActiveLogoProjectId = projectId;
    var prompt = (document.getElementById('studio-logo-revision-prompt').value || '').trim();
    if (!prompt) {
        showToast(t('studio.enter_revision_prompt'), 'error');
        return;
    }
    if (studioLogoLoading) return;
    studioLogoLoading = true;
    stopLogoProjectPolling();

    // Show the same breathing-skeleton loading state as first-gen, but with
    // the .is-revising modifier so only one centered panel is visible — one
    // logo in, one logo out.
    var loadingEl = document.getElementById('studio-logo-loading');
    if (loadingEl) {
        loadingEl.classList.add('is-revising');
        loadingEl.classList.remove('hidden');
    }
    var resultsEl = document.getElementById('studio-logo-results');
    if (resultsEl) resultsEl.classList.add('hidden');
    var logoErrorEl = document.getElementById('studio-logo-error');
    if (logoErrorEl) logoErrorEl.classList.add('hidden');

    var btn = document.getElementById('studio-logo-revise-btn');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }
    try {
        var data = await generateLogosAPI(getLogoFormPayload({
            project_id: projectId,
            parent_image_id: studioSelectedLogoImageId,
            revision_prompt: prompt
        }));
        studioActiveLogoProjectId = data.project_id || studioActiveLogoProjectId;
        if (data.credits_remaining) {
            studioCredits = normalizeStudioCredits(data.credits_remaining, 5);
            updateStudioCredits();
            updateLogoCreditsFromData(data.credits_remaining);
        }
        _refreshDashboardUsage();
        if (loadingEl) loadingEl.classList.add('hidden');
        await fetchAndRenderLogoProject(studioActiveLogoProjectId, false);
        startLogoProjectPollingIfNeeded(data);
        showToast(t('studio.revision_started'), 'success');
    } catch (e) {
        if (loadingEl) loadingEl.classList.add('hidden');
        if (e.message !== 'upgrade_required' && e.message !== 'credits_exhausted' && e.message !== 'unauthorized') {
            if (logoErrorEl) {
                logoErrorEl.classList.remove('hidden');
                var errorMsgEl = document.getElementById('studio-logo-error-msg');
                if (errorMsgEl) errorMsgEl.textContent = e.message || t('studio.logo_failed_msg');
            } else {
                showToast(e.message || t('studio.logo_failed_msg'), 'error');
            }
        }
    } finally {
        if (loadingEl) loadingEl.classList.remove('is-revising');
        studioLogoLoading = false;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
    }
}

function chooseLogoForRevision(imageId) {
    var logo = _studioLogos[imageId];
    if (!logo) {
        showToast(t('studio.logo_data_not_found'), 'error');
        return;
    }
    var status = logo.audit_status || 'completed';
    if (status === 'pending' || status === 'running') {
        showToast(t('studio.audit_wait_to_revise'), 'info');
        return;
    }
    studioSelectedLogoImageId = imageId;
    if (logo.project_id) studioActiveLogoProjectId = logo.project_id;
    updateLogoRevisionPanel();
    renderStudioLogoResults(studioLastLogoResult);
}

function clearLogoCandidateSelection() {
    studioSelectedLogoImageId = null;
    updateLogoRevisionPanel();
    renderStudioLogoResults(studioLastLogoResult);
}

async function retryLogoAudit(imageId) {
    try {
        await retryLogoAuditAPI(imageId);
        showToast(t('studio.audit_retry_started'), 'success');
        if (studioActiveLogoProjectId) {
            await fetchAndRenderLogoProject(studioActiveLogoProjectId, false);
        }
    } catch (e) {
        showToast(e.message || t('studio.audit_retry_failed'), 'error');
    }
}

function updateLogoRevisionPanel() {
    var panel = document.getElementById('studio-logo-revision-panel');
    var summary = document.getElementById('studio-logo-selected-summary');
    var projectChip = document.getElementById('studio-logo-project-id');
    if (!panel) return;
    var selectedLogo = studioSelectedLogoImageId ? _studioLogos[studioSelectedLogoImageId] : null;
    var projectId = (selectedLogo && selectedLogo.project_id) || studioActiveLogoProjectId || null;
    if (!selectedLogo && !projectId) {
        panel.classList.add('hidden');
        return;
    }
    panel.classList.remove('hidden');
    if (projectChip) {
        projectChip.textContent = projectId ? t('studio.project_chip', { id: projectId.substring(0, 8) }) : '';
    }
    if (!summary) return;
    if (!studioSelectedLogoImageId) {
        summary.textContent = t('studio.revision_select_hint');
        return;
    }
    var logo = selectedLogo || {};
    var logosList = (studioLastLogoResult && studioLastLogoResult.logos) || [];
    var idx = logosList.findIndex(function (l) {
        return l && l.image_id === studioSelectedLogoImageId;
    });
    var positionLabel = t('studio.logo_label_position', { n: idx >= 0 ? idx + 1 : 1 });
    if (idx >= 0 && logosList.length === 2) {
        var sideKey = idx === 0 ? 'studio.logo_position_side_left' : 'studio.logo_position_side_right';
        positionLabel = positionLabel + ' (' + t(sideKey) + ')';
    }
    summary.textContent = t('studio.revision_selected', {
        target: positionLabel,
        status: t('studio.audit_status_' + (logo.audit_status || 'completed'))
    });
}

function updateLogoCreditsDisplay() {
    var el = document.getElementById('studio-logo-credit-info');
    if (!el) return;
    if (!studioCredits) {
        el.textContent = '';
        return;
    }
    el.textContent = t('studio.ai_credits_info', {
        total: studioCredits.total_remaining || 0,
        monthly: studioCredits.monthly_remaining || 0,
        purchased: studioCredits.purchased_remaining || 0,
        cost: 5
    });
}

function updateLogoCreditsFromData(credits) {
    credits = normalizeStudioCredits(credits, 5);
    var infoEl = document.getElementById('studio-logo-credit-info');
    if (infoEl) {
        infoEl.textContent = t('studio.ai_credits_info', {
            total: credits.total_remaining || 0,
            monthly: credits.monthly_remaining || 0,
            purchased: credits.purchased_remaining || 0,
            cost: 5
        });
    }
    var badgeEl = document.getElementById('studio-credits-display');
    if (badgeEl) {
        badgeEl.textContent = t('studio.credits_remaining_short', {
            remaining: credits.total_remaining || 0,
            limit: credits.monthly_limit || 0
        });
    }
}

function renderStudioLogoResults(data) {
    data = data || {};
    var logos = data.logos || [];
    if (data.project_id) studioActiveLogoProjectId = data.project_id;
    if (logos.length === 0) {
        var emptyEl = document.getElementById('studio-logo-empty');
        if (emptyEl) emptyEl.classList.remove('hidden');
        else {
            document.getElementById('studio-logo-error').classList.remove('hidden');
            document.getElementById('studio-logo-error-msg').textContent = t('studio.logo_create_failed');
        }
        return;
    }

    document.getElementById('studio-logo-results').classList.remove('hidden');
    var pendingCount = logos.filter(function (logo) {
        var status = logo.audit_status || 'completed';
        return status === 'pending' || status === 'running';
    }).length;
    var safeCount = logos.filter(function (logo) {
        return (logo.audit_status || 'completed') === 'completed' && logo.is_safe;
    }).length;
    var riskyCount = logos.filter(function (logo) {
        return (logo.audit_status || 'completed') === 'completed' && !logo.is_safe;
    }).length;
    var metaEl = document.getElementById('studio-logo-project-meta');
    if (metaEl) {
        metaEl.innerHTML = '<div class="studio-audit-summary">'
            + '<span>' + t('studio.logo_project_summary', { count: logos.length }) + '</span>'
            + '<span>' + t('studio.audit_safe_count', { count: safeCount }) + '</span>'
            + '<span>' + t('studio.audit_risky_count', { count: riskyCount }) + '</span>'
            + (pendingCount ? '<span class="is-pending">' + t('studio.audit_pending_count', { count: pendingCount }) + '</span>' : '')
            + '</div>';
    }
    var cardsHtml = '';
    logos.forEach(function (logo) {
        logo.is_revision_target = studioSelectedLogoImageId && studioSelectedLogoImageId === logo.image_id;
        cardsHtml += renderLogoCard(logo);
    });
    var logoCardsEl = document.getElementById('studio-logo-cards');
    Array.from(logoCardsEl.querySelectorAll('[data-blob-url]')).forEach(function (el) {
        window.URL.revokeObjectURL(el.dataset.blobUrl);
    });
    logoCardsEl.innerHTML = cardsHtml;
    storeLogoData(logos);
    loadLogoImages(logos);
    updateLogoRevisionPanel();
}

function refreshStudioLogoResultsText() {
    var resultsEl = document.getElementById('studio-logo-results');
    if (!studioLastLogoResult || !resultsEl || resultsEl.classList.contains('hidden')) return;
    renderStudioLogoResults(studioLastLogoResult);
}

var _studioLogos = {};

function storeLogoData(logos) {
    if (!logos) return;
    logos.forEach(function (logo) {
        if (logo.image_id) _studioLogos[logo.image_id] = logo;
    });
}

function toggleLogoDetail(imageId) {
    var existingPanel = document.getElementById('logo-detail-' + imageId);
    if (existingPanel) {
        existingPanel.remove();
        return;
    }

    var logo = _studioLogos[imageId];
    if (!logo) {
        showToast(t('studio.logo_data_not_found'), 'error');
        return;
    }
    if ((logo.audit_status || 'completed') !== 'completed') {
        showToast(t('studio.audit_details_pending'), 'info');
        return;
    }

    function normalizeLogoDetailPct(value, fallback) {
        var raw = value;
        if (raw === undefined || raw === null || raw === '') raw = fallback;
        var n = Number(raw || 0);
        if (!isFinite(n)) n = 0;
        if (n > 0 && n <= 1) n = n * 100;
        return Math.round(n);
    }
    var simPct = normalizeLogoDetailPct(
        logo.llm_risk_score != null ? logo.llm_risk_score : logo.similarity_score,
        logo.similarity_score || 0
    );

    function makeBar(label, value) {
        var pct = Math.round((value || 0) * 100);
        var color = pct >= 70 ? 'var(--color-risk-critical-text)' : pct >= 50 ? 'var(--color-risk-medium-text)' : 'var(--color-risk-low-text)';
        return '<div class="flex items-center gap-2 text-xs">'
            + '<span class="w-14" style="color:var(--color-text-muted)">' + label + '</span>'
            + '<div class="flex-1 h-2 rounded-full overflow-hidden" style="background:var(--color-border)">'
            + '<div class="h-full rounded-full" style="width:' + pct + '%;background:' + color + '"></div></div>'
            + '<span class="w-8 text-right font-medium" style="color:var(--color-text-primary)">' + pct + '%</span></div>';
    }

    var barsHtml = '';
    barsHtml += makeBar(t('studio.ai_risk_score'), simPct / 100);

    if (!barsHtml) {
        barsHtml = '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('studio.no_visual_data') + '</div>';
    }

    var closestHtml = logo.closest_match_name
        ? '<div class="text-xs mt-2" style="color:var(--color-text-muted)">' + t('studio.closest_label') + ' <span class="font-medium" style="color:var(--color-text-secondary)">' + escapeHtml(logo.closest_match_name) + '</span></div>'
        : '';

    var panelHtml = '<div id="logo-detail-' + imageId + '" class="studio-logo-detail-panel">'
        + '<div class="text-xs font-semibold mb-2" style="color:var(--color-text-secondary)">' + t('studio.ai_risk_score') + '</div>'
        + '<div class="space-y-1.5">' + barsHtml + '</div>'
        + closestHtml
        + '</div>';

    // Find the logo card by its image placeholder
    var imgContainer = document.getElementById('logo-img-' + imageId);
    if (imgContainer) {
        var card = imgContainer.closest('[data-studio-logo-card="true"]');
        if (card) {
            card.insertAdjacentHTML('beforeend', panelHtml);
            return;
        }
    }
    showToast(t('studio.card_not_found'), 'error');
}

// ============================================
// LOGO CREDITS EXHAUSTED MODAL
// ============================================
function showLogoCreditsExhausted(detail) {
    if (typeof openBuyCreditsModal === 'function') {
        openBuyCreditsModal(detail || { error: 'credits_exhausted' });
    } else {
        showUpgradeModal(detail || { error: 'credits_exhausted' }, 'ai_credits');
    }
}

// ============================================
// BUY CREDITS MODAL (one-shot AI credit packs)
// ============================================
var _buyCreditsState = { selectedPackId: null, packs: [], loading: false, detail: null };

// Plans shown in the left "Upgrade plan" column of the combined modal.
// Prices and monthly credit allowances mirror PLAN_FEATURES on the server
// (utils/subscription.py). Free is intentionally omitted — the modal only
// opens for users who already need more credits.
var _BUY_CREDITS_UPGRADE_PLANS = [
    { id: 'starter',      price_try:  499, monthly_ai_credits:  10 },
    { id: 'professional', price_try: 1999, monthly_ai_credits:  50 },
    { id: 'enterprise',   price_try: 4999, monthly_ai_credits: 500 }
];

function openBuyCreditsModal(detail) {
    var modal = document.getElementById('buy-credits-modal');
    if (!modal) return;
    _buyCreditsState.selectedPackId = null;
    _buyCreditsState.detail = detail || null;
    var submit = document.getElementById('buy-credits-submit');
    if (submit) submit.disabled = true;
    var err = document.getElementById('buy-credits-error');
    if (err) { err.classList.add('hidden'); err.textContent = ''; }
    var discount = document.getElementById('buy-credits-discount');
    if (discount) discount.value = '';
    var picker = document.getElementById('buy-credits-picker');
    if (picker) picker.classList.remove('hidden');
    var iyzico = document.getElementById('buy-credits-iyzico');
    if (iyzico) { iyzico.classList.add('hidden'); iyzico.innerHTML = ''; }
    modal.classList.remove('hidden');
    if (typeof lockBodyScroll === 'function') lockBodyScroll();
    _renderBuyCreditsPlans();
    _loadBuyCreditsPacks();
}

function closeBuyCreditsModal() {
    var modal = document.getElementById('buy-credits-modal');
    if (modal) modal.classList.add('hidden');
    if (typeof unlockBodyScroll === 'function') unlockBodyScroll();
}

function _renderBuyCreditsPlans() {
    var container = document.getElementById('buy-credits-plans');
    if (!container) return;
    var detail = _buyCreditsState.detail || {};
    var currentPlan = String(detail.current_plan || '').toLowerCase();
    var plans = _BUY_CREDITS_UPGRADE_PLANS.filter(function (p) {
        return p.id !== currentPlan;
    });
    container.innerHTML = plans.map(function (plan) {
        var name = t('pricing.' + plan.id + '_name');
        var creditsLine = plan.monthly_ai_credits + ' '
            + t('studio.buy_credits.monthly_credits');
        var price = '₺' + plan.price_try + ' / '
            + t('studio.buy_credits.per_month');
        return '<button type="button"'
            + ' onclick="upgradeFromBuyCredits(\'' + plan.id + '\')"'
            + ' class="w-full text-left rounded-lg p-3 transition hover:opacity-90"'
            + ' style="border:1px solid var(--color-border);background:var(--color-bg-card)">'
            + '<div class="flex items-center justify-between gap-3">'
            + '<div class="min-w-0">'
            + '<div class="font-semibold" style="color:var(--color-text-primary)">' + name + '</div>'
            + '<div class="text-xs mt-0.5" style="color:var(--color-text-secondary)">' + creditsLine + '</div>'
            + '</div>'
            + '<div class="text-sm font-medium whitespace-nowrap" style="color:var(--color-text-primary)">' + price + '</div>'
            + '</div>'
            + '</button>';
    }).join('');
}

function upgradeFromBuyCredits(planId) {
    closeBuyCreditsModal();
    window.location.href = '/checkout?plan=' + encodeURIComponent(planId) + '&billing=monthly';
}

async function _loadBuyCreditsPacks() {
    var container = document.getElementById('buy-credits-packs');
    if (!container) return;
    container.innerHTML = '<div class="text-sm" style="color:var(--color-text-muted)">' + t('common.loading') + '</div>';
    try {
        var res = await fetch('/api/v1/billing/credit-packs', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!res.ok) throw new Error('failed');
        var data = await res.json();
        _buyCreditsState.packs = data.packs || [];
        _renderBuyCreditsPacks();
    } catch (_) {
        container.innerHTML = '<div class="text-sm" style="color:#dc2626">'
            + t('studio.buy_credits.load_failed') + '</div>';
    }
}

function _renderBuyCreditsPacks() {
    var container = document.getElementById('buy-credits-packs');
    if (!container) return;
    var packs = _buyCreditsState.packs;
    if (!packs.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = packs.map(function (pack) {
        var selected = _buyCreditsState.selectedPackId === pack.id;
        var label = t(pack.label_key);
        var priceLine = '₺' + pack.price_try + ' · ' + pack.credits + ' '
            + t('studio.buy_credits.credits_word');
        return '<button type="button" data-pack-id="' + pack.id + '"'
            + ' onclick="selectBuyCreditsPack(\'' + pack.id + '\')"'
            + ' class="w-full text-left rounded-lg p-3 transition"'
            + ' style="border:1px solid '
            + (selected ? 'var(--color-primary, #4f46e5)' : 'var(--color-border)')
            + ';background:' + (selected ? 'var(--color-bg-muted)' : 'transparent') + '">'
            + '<div class="flex items-center justify-between">'
            + '<div class="font-semibold" style="color:var(--color-text-primary)">' + label + '</div>'
            + '<div class="text-sm font-medium" style="color:var(--color-text-primary)">' + priceLine + '</div>'
            + '</div>'
            + '</button>';
    }).join('');
}

function selectBuyCreditsPack(packId) {
    _buyCreditsState.selectedPackId = packId;
    _renderBuyCreditsPacks();
    var submit = document.getElementById('buy-credits-submit');
    if (submit) submit.disabled = false;
}

async function submitBuyCredits() {
    if (_buyCreditsState.loading) return;
    var packId = _buyCreditsState.selectedPackId;
    if (!packId) return;
    var discountEl = document.getElementById('buy-credits-discount');
    var discountCode = (discountEl && discountEl.value || '').trim();
    var errEl = document.getElementById('buy-credits-error');
    if (errEl) { errEl.classList.add('hidden'); errEl.textContent = ''; }

    _buyCreditsState.loading = true;
    var submit = document.getElementById('buy-credits-submit');
    if (submit) submit.disabled = true;

    try {
        var body = { pack_id: packId };
        if (discountCode) body.discount_code = discountCode;
        var res = await fetch('/api/v1/billing/purchase-credits', {
            method: 'POST',
            headers: {
                'Authorization': 'Bearer ' + getAuthToken(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(body)
        });
        var data = await res.json();
        if (!res.ok) {
            var msg = (data && data.detail) || t('studio.buy_credits.failure');
            if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
            if (submit) submit.disabled = false;
            return;
        }
        var html = data.checkout_form_content || '';
        if (!html) {
            if (errEl) { errEl.textContent = t('studio.buy_credits.failure'); errEl.classList.remove('hidden'); }
            if (submit) submit.disabled = false;
            return;
        }
        var picker = document.getElementById('buy-credits-picker');
        var iyzico = document.getElementById('buy-credits-iyzico');
        if (picker) picker.classList.add('hidden');
        if (iyzico) {
            iyzico.classList.remove('hidden');
            iyzico.innerHTML = html;
            // iyzico's embedded <script> tags don't auto-execute when set via
            // innerHTML — replace each one to trigger execution.
            iyzico.querySelectorAll('script').forEach(function (oldScript) {
                var newScript = document.createElement('script');
                if (oldScript.src) {
                    newScript.src = oldScript.src;
                } else {
                    newScript.textContent = oldScript.textContent;
                }
                oldScript.parentNode.replaceChild(newScript, oldScript);
            });
        }
    } catch (_) {
        if (errEl) { errEl.textContent = t('studio.buy_credits.failure'); errEl.classList.remove('hidden'); }
        if (submit) submit.disabled = false;
    } finally {
        _buyCreditsState.loading = false;
    }
}

function refreshStudioDynamicTranslations() {
    if (!studioInitialized) return;
    refreshStudioClassPickers();
    refreshStudioClassToggleLabels();
    refreshStudioColorSwatches();
    refreshStudioAvailabilityText();
    updateStudioCredits();
    updateLogoCreditsDisplay();
    updateStudioModeMeta();
    refreshStudioNameResultsText();
    refreshStudioLogoResultsText();
    loadStudioHistory();
}

async function loadStudioHistory(featureType) {
    if (featureType === undefined) {
        featureType = studioHistoryFilter === 'logo' ? 'LOGO' : (studioHistoryFilter === 'name' ? 'NAME' : undefined);
    }
    var listEl = document.getElementById('studio-history-list');
    var loadingEl = document.getElementById('studio-history-loading');
    var emptyEl = document.getElementById('studio-history-empty');
    if (!listEl) return;

    if (loadingEl) loadingEl.classList.remove('hidden');
    if (emptyEl) {
        emptyEl.textContent = t('studio.history_empty');
        emptyEl.classList.add('hidden');
    }
    Array.from(listEl.querySelectorAll('[data-blob-url]')).forEach(function (el) {
        window.URL.revokeObjectURL(el.dataset.blobUrl);
    });
    listEl.innerHTML = '';

    try {
        var data = await getGenerationHistory(1, featureType);
        var items = data.items || [];
        if (loadingEl) loadingEl.classList.add('hidden');
        if (!items.length) {
            if (emptyEl) emptyEl.classList.remove('hidden');
            return;
        }
        storeStudioHistoryItems(items);
        listEl.innerHTML = items.map(renderStudioHistoryItem).join('');
        loadStudioHistoryImages(items);
    } catch (e) {
        if (loadingEl) loadingEl.classList.add('hidden');
        if (emptyEl) {
            emptyEl.classList.remove('hidden');
            emptyEl.textContent = t('studio.history_failed');
        }
    }
}

var _studioHistoryItems = {};
var _studioHistoryImages = {};

function getCurrentStudioHistoryFeatureType() {
    return studioHistoryFilter === 'logo' ? 'LOGO' : (studioHistoryFilter === 'name' ? 'NAME' : undefined);
}

function storeStudioHistoryItems(items) {
    _studioHistoryItems = {};
    _studioHistoryImages = {};
    (items || []).forEach(function (item) {
        if (item.id) _studioHistoryItems[item.id] = item;
        if (item.feature_type === 'LOGO' && item.images) {
            item.images.forEach(function (image) {
                if (!image.image_id) return;
                _studioHistoryImages[image.image_id] = {
                    image: image,
                    item: item
                };
                _studioLogos[image.image_id] = image;
            });
        }
    });
}

function setStudioHistoryFilter(filter) {
    studioHistoryFilter = filter || 'all';
    document.querySelectorAll('.studio-history-filter').forEach(function (btn) {
        var active = btn.dataset.studioHistoryFilter === studioHistoryFilter;
        btn.classList.toggle('is-active', active);
    });
    var featureType = studioHistoryFilter === 'logo' ? 'LOGO' : (studioHistoryFilter === 'name' ? 'NAME' : undefined);
    loadStudioHistory(featureType);
}

function renderStudioHistoryItem(item) {
    var params = item.input_params || {};
    var output = item.output_data || {};
    var isLogo = item.feature_type === 'LOGO';
    var pendingImages = isLogo ? (item.images || []).filter(function (image) {
        var status = image.audit_status || 'completed';
        return status === 'pending' || status === 'running';
    }).length : 0;
    var title = isLogo
        ? (params.brand_name || t('studio.logo_studio'))
        : (params.query || t('studio.name_lab'));
    var subtitle = isLogo
        ? t('studio.history_logo_meta', { count: (item.images || []).length }) + (pendingImages ? ' - ' + t('studio.audit_pending_count', { count: pendingImages }) : '')
        : t('studio.history_name_meta', {
            safe: output.safe_count || 0,
            filtered: output.filtered_count || 0
        });
    var created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
    var imagesHtml = '';

    if (isLogo && item.images && item.images.length) {
        imagesHtml = '<div class="studio-history-thumbs">'
            + item.images.map(function (image) {
                return '<button type="button" id="studio-history-img-' + escapeHtml(image.image_id) + '" class="studio-history-thumb" '
                    + 'onclick="openStudioHistoryLogo(\'' + escapeHtml(image.image_id) + '\')" '
                    + 'title="' + t('studio.history_view_logo') + ' - ' + t('studio.audit_status_' + (image.audit_status || 'completed')) + '">'
                    + t('common.loading')
                    + '</button>';
            }).join('')
            + '</div>';
    }
    var canOpen = !isLogo || ((item.images || []).length > 0);
    var openActionHtml = canOpen
        ? '<button type="button" onclick="openStudioHistoryItem(\'' + escapeHtml(item.id) + '\')" class="studio-history-action-button">'
            + t(isLogo ? 'studio.history_open_logos' : 'studio.history_open_names')
            + '</button>'
        : '';
    var actionsHtml = '<div class="studio-history-actions">'
        + openActionHtml
        + '<button type="button" onclick="deleteStudioHistoryItem(\'' + escapeHtml(item.id) + '\')" class="studio-history-action-button is-danger">'
        + t('common.delete')
        + '</button>'
        + '</div>';

    return '<div class="studio-history-item">'
        + '<div class="studio-history-item-header">'
        + '<div class="min-w-0">'
        + '<span class="studio-history-type">' + (isLogo ? t('studio.logo_studio') : t('studio.name_lab')) + '</span>'
        + '<div class="studio-history-title">' + escapeHtml(title) + '</div>'
        + '<div class="studio-history-subtitle">' + escapeHtml(subtitle) + '</div>'
        + '</div>'
        + '<div class="text-right flex-shrink-0">'
        + '<div class="studio-history-date">' + escapeHtml(created) + '</div>'
        + '<div class="studio-history-credit">' + t('studio.credits_used', { count: item.credits_used || 0 }) + '</div>'
        + '</div>'
        + '</div>'
        + imagesHtml
        + actionsHtml
        + '</div>';
}

function loadStudioHistoryImages(items) {
    items.forEach(function (item) {
        if (item.feature_type !== 'LOGO' || !item.images) return;
        item.images.forEach(function (image) {
            var el = document.getElementById('studio-history-img-' + image.image_id);
            if (!el || !image.image_url) return;
            fetch(image.image_url, {
                headers: { 'Authorization': 'Bearer ' + getAuthToken() }
            }).then(function (res) {
                if (!res.ok) throw new Error('Failed');
                return res.blob();
            }).then(function (blob) {
                var url = window.URL.createObjectURL(blob);
                el.innerHTML = '<img src="' + url + '" alt="' + t('studio.logo_studio') + '" class="w-full h-full object-contain">';
                el.dataset.blobUrl = url;
            }).catch(function () {
                el.textContent = t('dashboard.load_failed_short');
            });
        });
    });
}

async function openStudioHistoryLogo(imageId) {
    var ref = _studioHistoryImages[imageId] || {};
    var image = ref.image || _studioLogos[imageId];
    if (!image || !image.image_url) {
        showToast(t('studio.logo_data_not_found'), 'error');
        return;
    }
    var el = document.getElementById('studio-history-img-' + imageId);
    var blobUrl = el && el.dataset ? el.dataset.blobUrl : '';
    if (!blobUrl) {
        try {
            var res = await fetch(image.image_url, {
                headers: { 'Authorization': 'Bearer ' + getAuthToken() }
            });
            if (!res.ok) throw new Error('Failed');
            var blob = await res.blob();
            blobUrl = window.URL.createObjectURL(blob);
            if (el && el.dataset) el.dataset.blobUrl = blobUrl;
        } catch (e) {
            showToast(t('dashboard.load_failed_short'), 'error');
            return;
        }
    }
    var item = ref.item || {};
    var params = item.input_params || {};
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: {
            src: blobUrl,
            title: params.brand_name || t('studio.logo_studio'),
            subtitle: t('studio.audit_status_' + (image.audit_status || 'completed'))
        }
    }));
}

function applyNameHistoryToForm(item) {
    var params = item.input_params || {};
    if (document.getElementById('studio-name-query')) document.getElementById('studio-name-query').value = params.query || '';
    if (document.getElementById('studio-name-industry')) document.getElementById('studio-name-industry').value = params.industry || '';
    if (document.getElementById('studio-name-style') && params.style) document.getElementById('studio-name-style').value = params.style;
    if (document.getElementById('studio-name-language') && params.language) document.getElementById('studio-name-language').value = params.language;
    setStudioSelectValues('studio-name-classes', params.nice_classes || []);
    updateStudioNameButtonState();
}

function applyLogoHistoryToForm(item) {
    var params = item.input_params || {};
    if (document.getElementById('studio-logo-name')) document.getElementById('studio-logo-name').value = params.brand_name || '';
    if (document.getElementById('studio-logo-desc')) document.getElementById('studio-logo-desc').value = params.description || '';
    if (document.getElementById('studio-logo-style') && params.style) document.getElementById('studio-logo-style').value = params.style;
    if (document.getElementById('studio-logo-colors')) document.getElementById('studio-logo-colors').value = params.color_preferences || '';
    setStudioSelectValues('studio-logo-classes', params.nice_classes || []);
    refreshStudioColorSwatches();
}

function historyNameToCard(name) {
    if (typeof name === 'string') {
        return {
            name: name,
            risk_score: 0,
            llm_risk_score: 0,
            text_similarity: 0,
            semantic_similarity: 0,
            phonetic_match: false,
            is_safe: true,
            risk_level: 'low'
        };
    }
    var riskScore = name.risk_score != null ? name.risk_score : (name.llm_risk_score != null ? name.llm_risk_score : 0);
    return {
        name: name.name,
        risk_score: riskScore,
        llm_risk_score: name.llm_risk_score != null ? name.llm_risk_score : riskScore,
        text_similarity: name.text_similarity || 0,
        semantic_similarity: name.semantic_similarity || 0,
        phonetic_match: !!name.phonetic_match,
        closest_match: name.closest_match || null,
        is_safe: name.is_safe !== false,
        risk_level: name.risk_level || window.AppComponents.getScoreRiskLevel(Math.round(riskScore || 0))
    };
}

async function openStudioHistoryItem(historyId) {
    var item = _studioHistoryItems[historyId];
    if (!item) {
        showToast(t('studio.history_item_missing'), 'error');
        return;
    }
    if (item.feature_type === 'LOGO') {
        switchStudioMode('logo');
        applyLogoHistoryToForm(item);
        document.getElementById('studio-logo-idle').classList.add('hidden');
        document.getElementById('studio-logo-loading').classList.add('hidden');
        document.getElementById('studio-logo-error').classList.add('hidden');
        document.getElementById('studio-logo-empty').classList.add('hidden');
        var images = item.images || [];
        if (!images.length) {
            showToast(t('studio.no_logos'), 'error');
            return;
        }
        var projectId = images.length ? images[0].project_id : null;
        if (projectId) {
            var project = await fetchAndRenderLogoProject(projectId, false);
            if (!project) return;
        } else {
            studioActiveLogoProjectId = null;
            studioLastLogoResult = { logos: images, project_id: null, selected_image_id: null };
            renderStudioLogoResults(studioLastLogoResult);
        }
        showToast(t('studio.history_loaded'), 'success');
        return;
    }

    switchStudioMode('name');
    applyNameHistoryToForm(item);
    document.getElementById('studio-name-idle').classList.add('hidden');
    document.getElementById('studio-name-loading').classList.add('hidden');
    document.getElementById('studio-name-error').classList.add('hidden');
    document.getElementById('studio-name-empty').classList.add('hidden');
    var output = item.output_data || {};
    var scored = (output.scored_names || []).filter(function (name) { return name && name.is_safe !== false; });
    var safeNames = scored.length ? scored.map(historyNameToCard) : (output.safe_names || []).map(historyNameToCard);
    studioLastNameResult = {
        safe_names: safeNames,
        total_generated: output.total_generated || safeNames.length,
        filtered_count: output.filtered_count || 0,
        cached: false
    };
    renderStudioNameResults(studioLastNameResult);
    showToast(t('studio.history_loaded'), 'success');
}

async function deleteStudioHistoryItem(historyId) {
    if (!window.confirm(t('studio.history_delete_confirm'))) return;
    try {
        await deleteGenerationHistoryItemAPI(historyId);
        showToast(t('studio.history_deleted'), 'success');
        loadStudioHistory();
    } catch (e) {
        showToast(e.message || t('studio.history_delete_failed'), 'error');
    }
}

async function clearStudioHistory() {
    var featureType = getCurrentStudioHistoryFeatureType();
    var confirmKey = featureType === 'LOGO'
        ? 'studio.history_clear_logos_confirm'
        : featureType === 'NAME'
            ? 'studio.history_clear_names_confirm'
            : 'studio.history_clear_all_confirm';
    if (!window.confirm(t(confirmKey))) return;
    try {
        var result = await clearGenerationHistoryAPI(featureType);
        showToast(t('studio.history_cleared', { count: result.deleted || 0 }), 'success');
        loadStudioHistory();
    } catch (e) {
        showToast(e.message || t('studio.history_delete_failed'), 'error');
    }
}

// ============================================
// STUDIO CONTEXT TRIGGER (from search results)
// ============================================
function openStudioWithContext(mode, context) {
    // Switch to AI Studio tab
    showDashboardTab('ai-studio');

    // Switch to the appropriate mode
    switchStudioMode(mode);

    if (mode === 'name' && context.query) {
        document.getElementById('studio-name-query').value = context.query;
        if (context.nice_classes && context.nice_classes.length > 0) {
            setStudioSelectValues('studio-name-classes', context.nice_classes);
        }
        updateStudioNameButtonState();
    } else if (mode === 'logo' && context.query) {
        document.getElementById('studio-logo-name').value = context.query;
        if (context.nice_classes && context.nice_classes.length > 0) {
            setStudioSelectValues('studio-logo-classes', context.nice_classes);
        }
    }
}

function setStudioSelectValues(selectId, values) {
    var select = document.getElementById(selectId);
    if (!select) return;
    if (select.tagName === 'SELECT') {
        Array.from(select.options).forEach(function (opt) {
            opt.selected = values.indexOf(parseInt(opt.value)) !== -1;
        });
        updateStudioClassSummary(selectId);
        return;
    }
    Array.from(select.querySelectorAll('.studio-class-chip')).forEach(function (chip) {
        setStudioClassChipSelected(chip, values.indexOf(parseInt(chip.dataset.value)) !== -1);
    });
    updateStudioClassSummary(selectId);
}

// ============================================
// PIPELINE STATUS (admin/owner only)
// ============================================
var pipelineRunning = false;
var pipelineCurrentStep = null;
var pipelineLastRun = null;
var pipelineNextScheduled = null;
var pipelineInitDone = false;

function initPipelineStatus() {
    if (pipelineInitDone) return;
    pipelineInitDone = true;

    // Show the panel
    var panel = document.getElementById('pipeline-status-panel');
    if (panel) panel.classList.remove('hidden');

    // Enable buttons
    var btnFull = document.getElementById('pipeline-btn-full');
    var btnSkip = document.getElementById('pipeline-btn-skip');
    if (btnFull) btnFull.disabled = false;
    if (btnSkip) btnSkip.disabled = false;

    // Load initial status
    refreshPipelineStatus();
}

async function refreshPipelineStatus() {
    try {
        var data = await AppAPI.getPipelineStatus();
        if (!data) return;
        updatePipelineUI(data);
    } catch (e) {
        // Silent fail - pipeline table may not exist yet
    }
}

function updatePipelineUI(data) {
    pipelineRunning = data.is_running;
    pipelineCurrentStep = data.current_step;
    pipelineNextScheduled = data.next_scheduled;
    pipelineLastRun = (data.recent_runs && data.recent_runs.length > 0) ? data.recent_runs[0] : null;

    var stepNames = ['download', 'extract', 'metadata', 'embeddings', 'ingest', 'event_ingest'];

    // Update step cards from last run
    stepNames.forEach(function (name) {
        var stepEl = document.getElementById('pipeline-step-' + name);
        var countEl = document.getElementById('pipeline-count-' + name);
        var statusEl = document.getElementById('pipeline-status-' + name);
        if (!stepEl) return;

        var stepData = pipelineLastRun ? pipelineLastRun['step_' + name] : null;

        // Reset classes
        stepEl.className = 'text-center p-3 rounded-lg ' + AppUtils.stepStatusClass(stepData);
        countEl.textContent = (stepData && stepData.processed != null) ? stepData.processed : '-';
        statusEl.textContent = AppUtils.stepStatusText(stepData);
    });

    // Running indicator
    var runIndicator = document.getElementById('pipeline-running-indicator');
    if (pipelineRunning) {
        runIndicator.classList.remove('hidden');
        runIndicator.classList.add('flex');
        document.getElementById('pipeline-running-step').textContent =
            pipelineCurrentStep ? stepDisplayName(pipelineCurrentStep) : '...';
    } else {
        runIndicator.classList.add('hidden');
        runIndicator.classList.remove('flex');
    }

    // Buttons state
    var btnFull = document.getElementById('pipeline-btn-full');
    var btnSkip = document.getElementById('pipeline-btn-skip');
    if (btnFull) btnFull.disabled = pipelineRunning;
    if (btnSkip) btnSkip.disabled = pipelineRunning;

    // Footer: last run info
    var lastInfo = document.getElementById('pipeline-last-run-info');
    if (pipelineLastRun && pipelineLastRun.completed_at) {
        lastInfo.textContent = t('pipeline.last_run_prefix') + ' ' + AppUtils.formatDateTR(pipelineLastRun.completed_at)
            + ' (' + AppUtils.formatDuration(pipelineLastRun.duration_seconds) + ')';
    } else if (pipelineLastRun && pipelineLastRun.status === 'running') {
        lastInfo.textContent = t('pipeline.currently_running');
    } else {
        lastInfo.textContent = t('pipeline.not_run_yet');
    }

    // Footer: next scheduled
    var nextInfo = document.getElementById('pipeline-next-run-info');
    if (pipelineNextScheduled) {
        nextInfo.textContent = t('pipeline.next_run_prefix') + ' ' + AppUtils.formatDateTR(pipelineNextScheduled);
    } else {
        nextInfo.textContent = '';
    }
}

function stepDisplayName(step) {
    var names = {
        'starting': t('pipeline.starting_name'),
        'download': t('pipeline.download_name'),
        'extract': t('pipeline.extract_name'),
        'metadata': t('pipeline.metadata_name'),
        'embeddings': t('pipeline.embeddings_name'),
        'ingest': t('pipeline.ingest_name'),
        'event_ingest': t('pipeline.event_ingest_name'),
        'conflict_scan': t('pipeline.conflict_scan_name'),
        'final_status_repair': t('pipeline.final_status_repair_name')
    };
    return names[step] || step;
}

async function triggerPipeline(skipDownload) {
    if (pipelineRunning) return;

    var btnFull = document.getElementById('pipeline-btn-full');
    var btnSkip = document.getElementById('pipeline-btn-skip');
    if (btnFull) btnFull.disabled = true;
    if (btnSkip) btnSkip.disabled = true;

    try {
        await AppAPI.triggerPipeline(skipDownload);
        showToast(t('pipeline.started'), 'success');
        pipelineRunning = true;
        pollPipelineStatus();
    } catch (e) {
        showToast(t('pipeline.start_failed_detail', { error: e.message }), 'error');
        if (btnFull) btnFull.disabled = false;
        if (btnSkip) btnSkip.disabled = false;
    }
}

function pollPipelineStatus() {
    var errorCount = 0;
    var maxErrors = 30; // Stop after 30 consecutive errors (~5 minutes)
    var poll = function () {
        AppAPI.getPipelineStatus().then(function (data) {
            if (!data) return;
            errorCount = 0; // Reset on success
            updatePipelineUI(data);
            if (data.is_running) {
                setTimeout(poll, 5000);
            } else {
                showToast(t('pipeline.completed'), 'success');
            }
        }).catch(function () {
            errorCount++;
            if (errorCount < maxErrors) {
                setTimeout(poll, 10000);
            }
        });
    };
    setTimeout(poll, 2000); // First poll after 2s
}

// ============================================
// WATCHLIST CACHE — tracks which app_nos are already monitored
// ============================================
function isInWatchlist(applicationNo) {
    return applicationNo && userWatchlistAppNos.hasOwnProperty(applicationNo);
}

function openQuickWatchlistAdd(data) {
    if (isInWatchlist(data.application_no)) {
        showToast(t('watchlist.already_in_list'), 'info');
        return;
    }
    window.dispatchEvent(new CustomEvent('open-quick-watchlist', { detail: data }));
}

function loadWatchlistCache() {
    AppAPI.getWatchlistItems(1, 2000).then(function (data) {
        var items = data.items || [];
        userWatchlistAppNos = {};
        items.forEach(function (item) {
            if (item.application_no) {
                userWatchlistAppNos[item.application_no] = true;
            }
        });
    }).catch(function () { /* silent */ });
}

function refreshWatchlistButtons() {
    document.querySelectorAll('[data-watchlist-appno]').forEach(function (el) {
        if (isInWatchlist(el.getAttribute('data-watchlist-appno'))) {
            el.outerHTML = '<span class="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-green-700 bg-green-50 rounded">'
                + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
                + t('watchlist.already_watching') + '</span>';
        }
    });
}

// ============================================
// WATCHLIST TAB INIT
// ============================================
var watchlistTabInitialized = false;
var _wlCurrentPage = 1;
var _wlCurrentSearch = '';
var _wlCurrentSort = 'conflicts_desc';
var _wlPageSize = 20;
var _wlDebounceTimer = null;
var _wlRightPanelMode = 'selected'; // 'selected' | 'all'
var _wlAggPage = 1;
var WATCHLIST_THRESHOLD_STORAGE_KEY = 'ipwatch.watchlist.risk_threshold_pct';

function _normalizeWatchlistThresholdPct(value) {
    var pct = parseInt(value, 10);
    return [50, 60, 70, 80, 90].indexOf(pct) >= 0 ? pct : 70;
}

function _loadWatchlistThresholdPct() {
    try {
        return _normalizeWatchlistThresholdPct(localStorage.getItem(WATCHLIST_THRESHOLD_STORAGE_KEY));
    } catch (e) {
        return 70;
    }
}

function _persistWatchlistThresholdPct(pct) {
    try {
        localStorage.setItem(WATCHLIST_THRESHOLD_STORAGE_KEY, String(_normalizeWatchlistThresholdPct(pct)));
    } catch (e) { /* ignore storage failures */ }
}

function _syncWatchlistThresholdControl() {
    var sel = document.getElementById('wl-threshold-slider');
    if (sel) sel.value = String(Math.round(_activeThreshold * 100));
}

var _activeThreshold = _loadWatchlistThresholdPct() / 100; // single source of truth for display filter (0.0-1.0 decimal)
var _wlView = 'all'; // 'all' | 'renewal' | 'appeals'
var _wlStatusFilter = '';
var _wlTmStatusFilter = ''; // trademark final_status filter (e.g. 'Tescil Edildi')
var _wlTotalCount = 0; // total active watchlist items (used for scan time estimate)
var _wlLoadRequestSeq = 0;
var _wlSilentRefresh = false;

function _normalizeWatchlistView(view) {
    return ['all', 'appeals', 'renewal'].indexOf(view) >= 0 ? view : 'all';
}

function _syncWatchlistViewControl() {
    var sel = document.getElementById('wl-view-select');
    if (sel) sel.value = _wlView;
}

function initWatchlistTab() {
    if (watchlistTabInitialized) return;
    watchlistTabInitialized = true;
    _syncWatchlistThresholdControl();
    setWatchlistView(_wlView);
    loadWatchlistStats();
    _initScanButtonState(); // restore scan-in-progress state if page was refreshed mid-scan
}

function loadWatchlistStats() {
    refreshAllStats();
}

function refreshAllStats() {
    console.log('[Stats] Fetching stats with threshold:', _activeThreshold);
    AppAPI.getWatchlistStats(_activeThreshold).then(function (s) {
        var el = function (id) { return document.getElementById(id); };
        if (el('wl-stat-total')) el('wl-stat-total').textContent = s.total_items || 0;
        if (el('wl-stat-threatened')) el('wl-stat-threatened').textContent = s.items_with_threats || 0;
        if (el('wl-stat-critical')) el('wl-stat-critical').textContent = s.critical_threats || 0;
        if (el('wl-stat-new-alerts')) el('wl-stat-new-alerts').textContent = s.new_alerts || 0;
        if (el('wl-stat-deadline')) {
            if (s.nearest_deadline_days !== null && s.nearest_deadline_days !== undefined) {
                el('wl-stat-deadline').textContent = s.nearest_deadline_days + ' ' + t('common.days');
                el('wl-stat-deadline').style.color = s.nearest_deadline_days <= 7 ? 'var(--color-risk-critical-text, #dc2626)' : s.nearest_deadline_days <= 30 ? '#ea580c' : '';
            } else {
                el('wl-stat-deadline').textContent = '-';
            }
        }
        if (el('wl-stat-renewal')) {
            var rc = s.renewal_count || 0;
            el('wl-stat-renewal').textContent = rc;
            el('wl-stat-renewal').style.color = rc > 0 ? '#d97706' : '';
        }
    }).catch(function () { });
}

// Alpine v3 root accessor. Alpine v3 removed `el.__x`; `Alpine.$data(el)` is the
// supported accessor. Returns the reactive proxy for the dashboard root, or null.
function _alpineRoot() {
    var el = document.querySelector('[x-data]');
    if (!el || typeof window.Alpine === 'undefined' || typeof window.Alpine.$data !== 'function') return null;
    try { return window.Alpine.$data(el); } catch (e) { return null; }
}

// Pulls fresh /api/v1/usage/summary into the overview's KPI/usage rings.
// Called from mutating actions (AI generation, etc.) so the overview reflects
// the new state immediately, without waiting for a tab switch.
function _refreshDashboardUsage() {
    var data = _alpineRoot();
    if (!data || typeof data.loadUsageData !== 'function') return;
    var token = getAuthToken();
    data.loadUsageData(token ? { 'Authorization': 'Bearer ' + token } : {});
}

function refreshWatchlistAndStats() {
    loadWatchlistStats();
    loadPortfolio();
    // Refresh stats (total_watched, usage rings, etc.).
    var data = _alpineRoot();
    if (data && typeof data.loadData === 'function') data.loadData();
}

// ============================================
// PORTFOLIO / WATCHLIST WITH LOGO UPLOAD
// ============================================
function loadPortfolio() {
    console.log('[Portfolio] Fetching portfolio with threshold:', _activeThreshold);
    var grid = document.getElementById('portfolio-grid');
    if (!_wlSilentRefresh && grid && grid.innerHTML.indexOf('animate-pulse') === -1) {
        grid.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">' + t('dashboard.loading') + '</div>';
    }
    var requestSeq = ++_wlLoadRequestSeq;
    AppAPI.getWatchlistItems(_wlCurrentPage, _wlPageSize, _wlCurrentSearch || undefined, _wlCurrentSort || undefined, _wlView === 'renewal', _wlView === 'appeals', _wlStatusFilter || undefined, _activeThreshold, _wlTmStatusFilter || undefined).then(function (data) {
        if (requestSeq !== _wlLoadRequestSeq) return;
        var items = data.items || [];
        var total = data.total || items.length;
        var totalPages = data.total_pages || 1;
        _wlTotalCount = data.total || 0; // keep for scan time estimate
        if (_wlTotalCount === 0 && typeof _getScanState === 'function' && typeof _clearScanInProgress === 'function') {
            var scanState = _getScanState();
            if (scanState && scanState.active) {
                _clearScanInProgress(false);
            }
        }
        var countEl = document.getElementById('portfolio-count');
        if (countEl) countEl.textContent = t('holder.trademarks_count', { count: total });
        userWatchlistAppNos = {};
        items.forEach(function (item) {
            if (item.application_no) {
                userWatchlistAppNos[item.application_no] = true;
            }
        });
        if (_wlView === 'renewal') {
            renderRenewalGrid(items);
        } else if (_wlView === 'appeals') {
            renderAppealsGrid(items);
        } else {
            renderPortfolioGrid(items);
        }
        renderWatchlistPagination(total, totalPages, _wlCurrentPage);
        if (_expandedWatchlistId) {
            setTimeout(_restoreExpandedWatchlistPanel, 0);
        }
    }).catch(function (e) {
        if (requestSeq !== _wlLoadRequestSeq) return;
        if (grid) grid.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">' + t('dashboard.load_failed_short') + '</div>';
    });
}

function debounceWatchlistSearch(query) {
    var clearBtn = document.getElementById('wl-search-clear');
    if (clearBtn) clearBtn.classList.toggle('hidden', !query);
    if (_wlDebounceTimer) clearTimeout(_wlDebounceTimer);
    _wlDebounceTimer = setTimeout(function () {
        _wlCurrentSearch = query;
        _wlCurrentPage = 1;
        loadPortfolio();
    }, 300);
}

function clearWatchlistSearch() {
    var input = document.getElementById('wl-search-input');
    if (input) input.value = '';
    var clearBtn = document.getElementById('wl-search-clear');
    if (clearBtn) clearBtn.classList.add('hidden');
    _wlCurrentSearch = '';
    _wlCurrentPage = 1;
    loadPortfolio();
}

function onWatchlistSortChange() {
    var sel = document.getElementById('wl-sort-select');
    _wlCurrentSort = sel ? sel.value : 'date_desc';
    _wlCurrentPage = 1;
    loadPortfolio();
}

function onWatchlistViewFilterChange() {
    var sel = document.getElementById('wl-view-select');
    setWatchlistView(sel ? sel.value : 'all');
}

function onWatchlistStatusChange() {
    var sel = document.getElementById('wl-status-select');
    _wlStatusFilter = sel ? sel.value : '';
    _wlCurrentPage = 1;
    loadPortfolio();
}

function onWatchlistTmStatusChange() {
    var sel = document.getElementById('wl-tm-status-select');
    _wlTmStatusFilter = sel ? sel.value : '';
    _wlCurrentPage = 1;
    loadPortfolio();
}

function onWatchlistThresholdChange(pct) {
    pct = _normalizeWatchlistThresholdPct(pct);
    _activeThreshold = pct / 100;
    _persistWatchlistThresholdPct(pct);
    _syncWatchlistThresholdControl();
    console.log('[Threshold] Dropdown changed. New threshold:', _activeThreshold);
    _wlCurrentPage = 1;
    loadPortfolio();
    refreshAllStats();
}

function renderWatchlistPagination(total, totalPages, currentPage) {
    var container = document.getElementById('wl-pagination');
    var infoEl = document.getElementById('wl-page-info');
    if (!container) return;
    if (totalPages <= 1) {
        container.classList.add('hidden');
        if (infoEl) infoEl.classList.add('hidden');
        return;
    }
    container.classList.remove('hidden');
    if (infoEl) {
        infoEl.classList.remove('hidden');
        infoEl.textContent = t('watchlist.page_info', { current: currentPage, total: totalPages, count: total });
    }
    var prevBtn = document.getElementById('wl-prev-btn');
    var nextBtn = document.getElementById('wl-next-btn');
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;

    var btnsEl = document.getElementById('wl-page-buttons');
    if (btnsEl) {
        var html = '';
        var start = Math.max(1, currentPage - 2);
        var end = Math.min(totalPages, start + 4);
        if (end - start < 4) start = Math.max(1, end - 4);
        for (var p = start; p <= end; p++) {
            var active = p === currentPage;
            html += '<button onclick="wlGoToPage(' + p + ')" class="w-8 h-8 text-xs rounded-lg font-medium transition-colors" style="'
                + (active ? 'background:var(--color-primary);color:white' : 'color:var(--color-text-secondary)')
                + '">' + p + '</button>';
        }
        btnsEl.innerHTML = html;
    }
}

function wlPrevPage() { if (_wlCurrentPage > 1) { _wlCurrentPage--; loadPortfolio(); } }
function wlNextPage() { _wlCurrentPage++; loadPortfolio(); }
function wlGoToPage(p) { _wlCurrentPage = p; loadPortfolio(); }

// ============================================
// RIGHT PANEL TOGGLE: Selected vs All Threats
// ============================================
function switchWlRightPanel(mode) {
    _wlRightPanelMode = mode;
    var selBtn = document.getElementById('wl-view-selected-btn');
    var allBtn = document.getElementById('wl-view-all-btn');
    if (mode === 'selected') {
        if (selBtn) { selBtn.style.background = 'var(--color-primary)'; selBtn.style.color = 'white'; }
        if (allBtn) { allBtn.style.background = 'var(--color-bg-muted)'; allBtn.style.color = 'var(--color-text-secondary)'; }
        var header = document.getElementById('watchlist-alert-header');
        if (header) {
            header.innerHTML = '<h3 class="font-semibold" style="color:var(--color-text-primary)">' + t('dashboard.recent_threats') + '</h3>'
                + '<p class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('empty.watchlist_desc') + '</p>';
        }
        var alertList = document.getElementById('watchlist-alert-list');
        if (alertList) alertList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('empty.alerts_desc') + '</div>';
        var filters = document.getElementById('alert-filters');
        if (filters) filters.classList.add('hidden');
    } else {
        if (allBtn) { allBtn.style.background = 'var(--color-primary)'; allBtn.style.color = 'white'; }
        if (selBtn) { selBtn.style.background = 'var(--color-bg-muted)'; selBtn.style.color = 'var(--color-text-secondary)'; }
        var filters2 = document.getElementById('alert-filters');
        if (filters2) filters2.classList.add('hidden');
        _wlAggPage = 1;
        loadAggregateAlerts();
    }
}

function loadAggregateAlerts() {
    var alertList = document.getElementById('watchlist-alert-list');
    if (!alertList) return;
    alertList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('dashboard.loading') + '</div>';
    var header = document.getElementById('watchlist-alert-header');
    if (header) {
        header.innerHTML = '<h3 class="font-semibold" style="color:var(--color-text-primary)">' + t('watchlist.view_all_threats') + '</h3>';
    }

    AppAPI.getAggregateAlerts(_wlAggPage, 20).then(function (data) {
        var items = data.items || [];
        if (items.length === 0) {
            alertList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('watchlist.no_threats') + '</div>';
            return;
        }
        var esc = window.AppUtils.escapeHtml;
        var html = items.map(function (a) {
            var severityColors = { critical: '#dc2626', very_high: '#dc2626', high: '#ea580c', medium: '#ca8a04', low: '#6b7280' };
            var sevColor = severityColors[a.severity] || '#6b7280';
            var deadlineHtml = '';
            if (a.deadline_days !== null && a.deadline_days !== undefined) {
                var dColor = a.deadline_days <= 7 ? '#dc2626' : a.deadline_days <= 30 ? '#ea580c' : '#ca8a04';
                deadlineHtml = '<span class="text-xs font-bold" style="color:' + dColor + '">' + a.deadline_days + ' ' + t('common.days') + '</span>';
            }
            var isEvent = a.alert_type === 'event';
            var leadPillHtml = '';
            if (isEvent && a.source_type) {
                var typeKey = 'events.type_' + a.source_type;
                var typeLabel = t(typeKey);
                if (typeLabel === typeKey) typeLabel = a.source_type;
                leadPillHtml = '<span class="text-xs font-bold px-1.5 py-0.5 rounded" '
                    + 'style="background:' + sevColor + '20;color:' + sevColor + '" '
                    + 'data-alert-pill="event-type">' + esc(typeLabel) + '</span>';
            } else {
                var riskPct = a.risk_score !== null && a.risk_score !== undefined ? Math.round(a.risk_score * 100) + '%' : '';
                if (riskPct) {
                    leadPillHtml = '<span class="text-xs font-bold px-1.5 py-0.5 rounded" '
                        + 'style="background:' + sevColor + '20;color:' + sevColor + '" '
                        + 'data-alert-pill="risk">' + riskPct + '</span>';
                }
            }
            var dataAttrs = ' data-alert-type="' + (isEvent ? 'event' : 'similarity') + '"'
                + ' data-severity="' + esc(a.severity || '') + '"';
            return '<div class="px-4 py-3 hover:bg-gray-50 transition-colors"' + dataAttrs
                + ' style="border-left:3px solid ' + sevColor + '">'
                + '<div class="flex items-center justify-between">'
                + '<div class="flex-1 min-w-0">'
                + '<div class="flex items-center gap-2">'
                + leadPillHtml
                + '<span class="text-sm font-medium truncate" style="color:var(--color-text-primary)">' + esc(getTrademarkDisplayName(a.conflicting_brand_name)) + '</span>'
                + '</div>'
                + '<div class="text-xs mt-0.5" style="color:var(--color-text-muted)">'
                + '<span style="color:var(--color-text-faint)">' + t('alerts.watched_brand') + ':</span> '
                + esc(getTrademarkDisplayName(a.watched_brand_name))
                + '</div>'
                + (a.overlapping_classes ? '<div class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('alerts.overlapping_classes') + ' ' + a.overlapping_classes + '</div>' : '')
                + '</div>'
                + '<div class="flex-shrink-0 text-right ml-3">'
                + deadlineHtml
                + '</div>'
                + '</div>'
                + '</div>';
        }).join('');

        // Simple prev/next for aggregate
        if (data.total_pages > 1) {
            html += '<div class="flex items-center justify-center gap-3 py-3 border-t" style="border-color:var(--color-border)">';
            html += '<button onclick="_wlAggPage--;loadAggregateAlerts()" class="text-xs px-3 py-1 rounded border" style="border-color:var(--color-border);color:var(--color-text-secondary)"' + (_wlAggPage <= 1 ? ' disabled style="opacity:0.4"' : '') + '>' + t('common.previous') + '</button>';
            html += '<span class="text-xs" style="color:var(--color-text-faint)">' + _wlAggPage + '/' + data.total_pages + '</span>';
            html += '<button onclick="_wlAggPage++;loadAggregateAlerts()" class="text-xs px-3 py-1 rounded border" style="border-color:var(--color-border);color:var(--color-text-secondary)"' + (_wlAggPage >= data.total_pages ? ' disabled style="opacity:0.4"' : '') + '>' + t('common.next') + '</button>';
            html += '</div>';
        }
        alertList.innerHTML = html;
    }).catch(function () {
        alertList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('dashboard.threats_load_failed') + '</div>';
    });
}

function _thresholdFilteredConflictCount(summary) {
    // The API summary is already filtered by the active watchlist-tab threshold.
    if (!summary) return 0;
    return (summary.sev_critical  || 0)
         + (summary.sev_very_high || 0)
         + (summary.sev_high      || 0)
         + (summary.sev_medium    || 0)
         + (summary.sev_low       || 0);
}

function renderPortfolioGrid(items) {
    var grid = document.getElementById('portfolio-grid');
    if (!grid) return;

    if (!items || items.length === 0) {
        grid.innerHTML = '<div class="text-sm text-center py-4" style="color:var(--color-text-faint)">' + t('dashboard.watchlist_empty') + '</div>';
        return;
    }

    // Cache items — backend already applies global ORDER BY before LIMIT/OFFSET.
    // Do NOT re-sort locally; that would corrupt pagination across pages.
    _watchlistItemsCache = items;
    grid.innerHTML = items.map(function (item, idx) {
        var esc = window.AppUtils.escapeHtml;
        var classes = window.AppComponents.renderNiceClassBadges(item.nice_class_numbers, 3);

        // Logo with larger size for card layout
        // Priority: custom uploaded logo > trademark-linked image > placeholder
        var logoHtml;
        var customLogoUrl = item.custom_logo_url || (item.has_custom_logo ? item.logo_url : null);
        if (item.has_custom_logo && customLogoUrl) {
            // Manually-uploaded logo - takes priority over trademark image
            var escapedBrand = esc(item.brand_name).replace(/'/g, "\\'");
            var escapedLogoUrl = customLogoUrl.replace(/'/g, "\\'");
            var _thumbPlaceholderEsc = window.AppComponents.IMG_PLACEHOLDER_SVG.replace(/"/g, '&quot;').replace(/'/g, "\\'");
            logoHtml = '<div class="w-12 h-12 rounded-lg overflow-hidden flex-shrink-0 cursor-pointer hover:ring-2 hover:ring-blue-300 transition" '
                + 'style="border:1px solid var(--color-border);background:var(--color-bg-muted)" '
                + 'onclick="event.stopPropagation(); openLogoPreview(\'' + escapedLogoUrl + '\', \'' + escapedBrand + '\')" title="' + t('watchlist.view_logo') + '">'
                + '<img src="' + customLogoUrl + '" class="w-full h-full object-contain" '
                + 'onerror="this.style.display=\'none\'; this.parentElement.innerHTML=\'' + _thumbPlaceholderEsc + '\'; this.parentElement.style.cursor=\'default\'; this.parentElement.onclick=null;">'
                + '</div>';
        } else if (item.trademark_image_path) {
            // Logo from linked trademark record
            var _thumbWrapper = '<div class="flex-shrink-0" onclick="event.stopPropagation()" title="' + t('watchlist.view_logo') + '">'
                + window.AppComponents.renderThumbnail(item.trademark_image_path, item.brand_name, item.application_no, 'w-12 h-12')
                + '</div>';
            logoHtml = _thumbWrapper;
        } else {
            // No image available — non-clickable placeholder
            var _thumbWrapper2 = '<div class="flex-shrink-0">'
                + window.AppComponents.renderThumbnail(null, item.brand_name, null, 'w-12 h-12')
                + '</div>';
            logoHtml = _thumbWrapper2;
        }

        // Monitoring status indicator
        var monitorDot = item.is_active !== false
            ? '<span class="w-2 h-2 rounded-full inline-block" style="background:var(--color-deadline-safe)" title="' + t('watchlist.monitoring_active') + '"></span>'
            : '<span class="w-2 h-2 rounded-full inline-block" style="background:var(--color-deadline-expired)" title="' + t('watchlist.monitoring_paused') + '"></span>';

        var escapedName = esc(item.brand_name).replace(/'/g, "\\'");

        // Application no & bulletin no instead of scan date
        var metaLine = '';
        if (item.application_no) {
            metaLine += '<span class="text-xs" style="color:var(--color-text-faint)">' + t('watchlist.application_no') + ': ' + esc(item.application_no) + '</span>';
        }
        if (item.bulletin_no) {
            metaLine += (metaLine ? '<span class="text-xs" style="color:var(--color-text-faint)"> · </span>' : '');
            metaLine += '<span class="text-xs" style="color:var(--color-text-faint)">' + t('watchlist.filter_bulletin') + ': ' + esc(item.bulletin_no) + '</span>';
        }

        // Conflict count filtered by the active risk threshold
        var totalConflicts = _thresholdFilteredConflictCount(item.conflict_summary);
        var chevronHtml = totalConflicts > 0
            ? '<span id="wl-chevron-' + item.id + '" class="text-xs transition-transform inline-block" style="color:var(--color-text-faint)">&#9660;</span>'
            : '';

        // Severity-based left border color
        var severity = item.conflict_summary && item.conflict_summary.highest_severity;
        var severityColors = { critical: '#dc2626', very_high: '#f97316', high: '#ea580c', medium: '#ca8a04', low: '#d1d5db' };
        var borderColor = severityColors[severity] || 'var(--color-border)';

        // Compact conflict count badge — rendered inline in the button row
        var ccColor = totalConflicts >= 5 ? '#dc2626' : totalConflicts >= 2 ? '#ea580c' : '#ca8a04';
        var ccBg   = totalConflicts >= 5 ? 'rgba(220,38,38,0.08)' : totalConflicts >= 2 ? 'rgba(234,88,12,0.08)' : 'rgba(202,138,4,0.08)';
        var conflictBadgeHtml = totalConflicts > 0
            ? '<div id="wl-conflict-count-' + item.id + '" class="flex flex-col items-center justify-center px-1.5 py-0.5 rounded mr-0.5" style="background:' + ccBg + ';min-width:26px">'
                + '<span class="text-sm font-bold leading-none" style="color:' + ccColor + '">' + totalConflicts + '</span>'
                + '<span class="text-[9px] leading-none mt-0.5" style="color:' + ccColor + ';opacity:0.75">' + t('watchlist.conflicts') + '</span>'
                + '</div>'
            : '<div id="wl-conflict-count-' + item.id + '" style="display:none"></div>';

        // Compact T/V/P monitoring badge (T=Text, V=Visual, P=Phonetic)
        var tvpHtml = '<span class="hidden sm:inline text-[10px] font-mono px-1 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-faint)" title="' + t('watchlist.score_text') + ' / ' + t('watchlist.score_logo') + ' / ' + t('watchlist.score_phonetic') + '">'
            + (item.monitor_text !== false ? '<span title="' + t('watchlist.score_text') + '" style="color:var(--color-primary)">T</span>' : '<span title="' + t('watchlist.score_text') + '" style="opacity:0.3">T</span>')
            + (item.monitor_visual !== false ? '<span title="' + t('watchlist.score_logo') + '" style="color:var(--color-primary)">V</span>' : '<span title="' + t('watchlist.score_logo') + '" style="opacity:0.3">V</span>')
            + (item.monitor_phonetic !== false ? '<span title="' + t('watchlist.score_phonetic') + '" style="color:var(--color-primary)">P</span>' : '<span title="' + t('watchlist.score_phonetic') + '" style="opacity:0.3">P</span>')
            + '</span>';

        // Renewal Badge (overdue = red, ≤90d = orange, ≤365d = yellow)
        var renewalBadge = '';
        if (item.needs_renewal || item.renewal_approaching) {
            var days = item.days_until_expiry;
            var daysLabel = '';
            if (days !== null && days !== undefined) {
                if (days < 0) {
                    daysLabel = ' · ' + Math.abs(days) + ' ' + t('watchlist.renewal_days_ago');
                } else if (days === 0) {
                    daysLabel = ' · ' + t('watchlist.renewal_today');
                } else {
                    daysLabel = ' · ' + days + ' ' + t('watchlist.renewal_days_left');
                }
            }
            var badgeBg, badgeColor, badgeBorder;
            if (item.needs_renewal) {
                badgeBg = 'var(--color-risk-critical-bg)'; badgeColor = 'var(--color-risk-critical-text)'; badgeBorder = 'var(--color-risk-critical-border)';
            } else if (days <= 90) {
                badgeBg = 'rgba(234,88,12,0.1)'; badgeColor = '#c2410c'; badgeBorder = 'rgba(234,88,12,0.3)';
            } else {
                badgeBg = 'rgba(217,119,6,0.1)'; badgeColor = '#b45309'; badgeBorder = 'rgba(217,119,6,0.3)';
            }
            renewalBadge = '<div class="mt-1"><span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold"'
                + ' style="background:' + badgeBg + ';color:' + badgeColor + ';border:1px solid ' + badgeBorder + '"'
                + ' title="' + (item.expiry_date ? t('watchlist.renewal_expiry_title') + ' ' + item.expiry_date : '') + '">'
                + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>'
                + t('watchlist.renewal_badge') + daysLabel
                + '</span></div>';
        }

        // Trademark status badge — mirrors search result cards
        var statusBadge = '';
        if (item.trademark_status) {
            statusBadge = '<div class="mt-1">'
                + '<span class="' + getStatusBadgeClass(item.trademark_status) + ' px-1.5 py-0.5 rounded text-[10px] font-medium">'
                + getStatusText(item.trademark_status) + '</span>'
                + '</div>';
        }

        return '<div class="card-base px-3 py-2 mb-1.5 cursor-pointer hover:border-indigo-300 transition-all" '
            + 'style="background:var(--color-bg-card);border-color:var(--color-border);border-left:4px solid ' + borderColor + '" '
            + 'onclick="toggleWatchlistAlerts(\'' + item.id + '\', \'' + escapedName + '\')">'
            + '<div class="flex items-start gap-3">'
            + logoHtml
            + '<div class="flex-1 min-w-0">'
            + '<div class="flex items-center gap-2">'
            + monitorDot
            + '<span class="font-semibold text-sm truncate" style="color:var(--color-text-primary)">' + esc(item.brand_name) + '</span>'
            + tvpHtml
            + chevronHtml
            + '</div>'
            + classes
            + statusBadge
            + renewalBadge
            + (metaLine ? '<div class="hidden sm:block mt-0.5">' + metaLine + '</div>' : '')
            + (item.description ? '<div class="hidden sm:block text-xs mt-0.5 truncate" style="color:var(--color-text-muted)" title="' + esc(item.description) + '">' + esc(item.description) + '</div>' : '')
            + '</div>'
            + '<div class="flex-shrink-0 flex flex-col items-end gap-1">'
            + '<div class="flex items-center gap-0.5">'
            + conflictBadgeHtml
            + '<button onclick="event.stopPropagation(); scanWatchlistItem(\'' + item.id + '\')" class="p-1 rounded hover:bg-blue-50 transition-colors" title="' + t('watchlist.scan_now') + '">'
            + '<svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>'
            + '</button>'
            + (item.has_custom_logo
                ? '<button onclick="event.stopPropagation(); deleteWatchlistLogo(\'' + item.id + '\')" class="p-1 rounded hover:bg-red-50 transition-colors group" title="' + t('watchlist.delete_logo_title') + '">'
                + '<svg class="w-3.5 h-3.5 text-gray-400 group-hover:text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/><line x1="4" y1="4" x2="20" y2="20" stroke-width="2" stroke-linecap="round"/></svg>'
                + '</button>'
                : '')
            + '<label onclick="event.stopPropagation();" class="p-1 rounded hover:bg-green-50 transition-colors cursor-pointer" title="' + t('watchlist.upload_logo_title') + '">'
                + '<svg class="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/><circle cx="12" cy="13" r="3"/></svg>'
                + '<input type="file" accept="image/*" class="hidden" onchange="handleWatchlistLogoUpload(\'' + item.id + '\', this)">'
                + '</label>'
            + '<button onclick="event.stopPropagation(); openEditWatchlistModal(' + idx + ')" class="p-1 rounded hover:bg-yellow-50 transition-colors" title="' + t('watchlist.edit_item') + '">'
            + '<svg class="w-3.5 h-3.5 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>'
            + '</button>'
            + '<button onclick="event.stopPropagation(); deleteWatchlistItem(\'' + item.id + '\', \'' + escapedName + '\')" class="p-1 rounded hover:bg-red-50 transition-colors" title="' + t('watchlist.delete_item') + '">'
            + '<svg class="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>'
            + '</button>'
            + '</div>'
            + '</div>'
            + '</div>'
            + '<div id="wl-alerts-' + item.id + '" class="hidden mt-2 border-t pt-2" style="border-color:var(--color-border)">'
            + '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('dashboard.loading') + '</div>'
            + '</div>'
            + '</div>';
    }).join('');
}

// ============================================
// WATCHLIST FILTERS (server-side via loadPortfolio)
// ============================================
function applyWatchlistFilters() {
    // Legacy compat — now handled by server-side search/sort
    _wlCurrentPage = 1;
    loadPortfolio();
}

function clearWatchlistFilters() {
    var sortEl = document.getElementById('wl-sort-select');
    if (sortEl) sortEl.value = 'date_desc';
    _wlCurrentSort = 'date_desc';
    clearWatchlistSearch();
}

function setWatchlistView(view) {
    view = _normalizeWatchlistView(view);
    _wlView = view;
    _wlCurrentPage = 1;
    _syncWatchlistViewControl();
    // Per-tab colour palette
    var tabColors = {
        appeals: { solid: '#dc2626', tintBg: 'rgba(220,38,38,0.07)', tintBorder: 'rgba(220,38,38,0.3)' },
        renewal: { solid: '#d97706', tintBg: 'rgba(217,119,6,0.07)',  tintBorder: 'rgba(217,119,6,0.3)'  }
    };
    ['appeals', 'renewal'].forEach(function (v) {
        var btn = document.getElementById('wl-view-tab-' + v);
        if (!btn) return;
        var c = tabColors[v];
        if (v === view) {
            btn.style.background = c.solid;
            btn.style.color = 'white';
            btn.style.borderColor = c.solid;
        } else {
            btn.style.background = c.tintBg;
            btn.style.color = c.solid;
            btn.style.borderColor = c.tintBorder;
        }
    });
    // Highlight renewal stat card when in renewal view
    var renewalCard = document.getElementById('wl-stat-renewal-card');
    if (renewalCard) {
        renewalCard.style.outline = (view === 'renewal') ? '2px solid #d97706' : '';
        renewalCard.style.outlineOffset = (view === 'renewal') ? '2px' : '';
    }
    // Reset + repopulate status filter dropdowns
    _wlStatusFilter = '';
    _wlTmStatusFilter = '';
    var tmStatusSel = document.getElementById('wl-tm-status-select');
    if (tmStatusSel) tmStatusSel.value = '';
    var statusWrap = document.getElementById('wl-status-filter-wrap');
    var statusSel = document.getElementById('wl-status-select');
    if (statusWrap) statusWrap.style.display = (view === 'appeals' || view === 'renewal') ? 'flex' : 'none';
    if (statusSel) {
        statusSel.value = '';
        if (view === 'appeals') {
            statusSel.innerHTML =
                '<option value="">' + t('watchlist.status_all') + '</option>' +
                '<option value="new">' + t('watchlist.status_new') + '</option>' +
                '<option value="acknowledged">' + t('watchlist.status_acknowledged') + '</option>' +
                '<option value="resolved">' + t('watchlist.status_resolved') + '</option>' +
                '<option value="dismissed">' + t('watchlist.status_dismissed') + '</option>';
        } else if (view === 'renewal') {
            statusSel.innerHTML =
                '<option value="">' + t('watchlist.status_all') + '</option>' +
                '<option value="overdue">' + t('watchlist.renewal_status_overdue') + '</option>' +
                '<option value="critical">' + t('watchlist.renewal_status_critical') + '</option>' +
                '<option value="approaching">' + t('watchlist.renewal_status_approaching') + '</option>';
        }
    }
    loadPortfolio();
}

function renderRenewalGrid(items) {
    var grid = document.getElementById('portfolio-grid');
    if (!grid) return;
    var esc = window.AppUtils.escapeHtml;
    if (!items.length) {
        grid.innerHTML = '<div class="text-sm text-center py-10" style="color:var(--color-text-muted)">' + t('watchlist.renewal_empty') + '</div>';
        return;
    }
    var html = '<div class="rounded-xl border overflow-hidden divide-y" style="border-color:var(--color-border)">'
        + '<div class="grid px-4 py-2 text-xs font-semibold" style="grid-template-columns:1fr auto;background:var(--color-bg-muted,rgba(0,0,0,0.03));color:var(--color-text-muted)">'
        + '<span>' + t('watchlist.col_mark') + '</span>'
        + '<span class="text-right">' + t('watchlist.col_renewal_status') + '</span>'
        + '</div>';
    items.forEach(function (item) {
        var days = item.days_until_expiry;
        var pillBg, pillColor, pillText;
        if (item.needs_renewal) {
            pillBg = 'var(--color-risk-critical-bg)'; pillColor = 'var(--color-risk-critical-text)';
            pillText = Math.abs(days) + ' ' + t('watchlist.renewal_days_ago');
        } else if (days !== null && days <= 90) {
            pillBg = 'rgba(234,88,12,0.12)'; pillColor = '#c2410c';
            pillText = days + ' ' + t('watchlist.renewal_days_left');
        } else {
            pillBg = 'rgba(217,119,6,0.12)'; pillColor = '#b45309';
            pillText = (days !== null ? days : '?') + ' ' + t('watchlist.renewal_days_left');
        }
        var classes = (item.nice_class_numbers || []).slice(0, 3).map(function (c) { return t('watchlist.nice_class_short') + ' ' + c; }).join(', ');
        var appDateStr = item.true_application_date ? new Date(item.true_application_date).toLocaleDateString() : '-';
        var expDateStr = item.expiry_date ? new Date(item.expiry_date).toLocaleDateString() : '-';
        var escapedName = esc(item.brand_name).replace(/'/g, "\\'");
        html += '<div class="grid px-4 py-3 items-center gap-4 cursor-pointer transition-colors hover:opacity-80" style="grid-template-columns:1fr auto;background:var(--color-bg-card)" onclick="toggleWatchlistAlerts(\'' + item.id + '\', \'' + escapedName + '\')">'
            + '<div>'
            + '<div class="font-semibold text-sm" style="color:var(--color-text-primary)">' + esc(item.brand_name) + '</div>'
            + '<div class="text-xs mt-0.5" style="color:var(--color-text-muted)">'
            + (classes ? classes + ' · ' : '') + t('watchlist.col_app_date') + ': ' + appDateStr
            + '</div>'
            + '</div>'
            + '<div class="text-right ml-4 flex-shrink-0">'
            + '<span class="inline-block px-2 py-0.5 rounded-full text-xs font-bold" style="background:' + pillBg + ';color:' + pillColor + '">' + pillText + '</span>'
            + '<div class="text-xs mt-1" style="color:var(--color-text-muted)">' + t('watchlist.col_expiry') + ': ' + expDateStr + '</div>'
            + '</div>'
            + '</div>';
        html += '<div id="wl-alerts-' + item.id + '" class="hidden px-4 pb-3 pt-2" style="background:var(--color-bg-card)">'
            + '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('dashboard.loading') + '</div>'
            + '</div>';
    });
    html += '</div>';
    grid.innerHTML = html;
}

function renderAppealsGrid(items) {
    var grid = document.getElementById('portfolio-grid');
    if (!grid) return;
    var esc = window.AppUtils.escapeHtml;
    var appealItems = items.filter(function (item) {
        return item.conflict_summary && (item.conflict_summary.total || 0) > 0;
    });
    if (!appealItems.length) {
        grid.innerHTML = '<div class="text-sm text-center py-10" style="color:var(--color-text-muted)">' + t('watchlist.appeals_empty') + '</div>';
        return;
    }
    var html = '<div class="rounded-xl border overflow-hidden divide-y" style="border-color:var(--color-border)">'
        + '<div class="grid px-4 py-2 text-xs font-semibold" style="grid-template-columns:1fr auto;background:var(--color-bg-muted,rgba(0,0,0,0.03));color:var(--color-text-muted)">'
        + '<span>' + t('watchlist.col_mark') + '</span>'
        + '<span class="text-right">' + t('watchlist.col_appeal_deadline') + '</span>'
        + '</div>';
    appealItems.forEach(function (item) {
        var summary = item.conflict_summary;
        var days = summary.nearest_deadline_days;
        var hasDeadline = summary.nearest_deadline && days !== null && days !== undefined;
        var deadlineStr = summary.nearest_deadline ? new Date(summary.nearest_deadline).toLocaleDateString() : '-';
        var pillBg, pillColor;
        if (!hasDeadline) {
            pillBg = 'rgba(107,114,128,0.12)'; pillColor = 'var(--color-text-muted)';
        } else if (days <= 7) {
            pillBg = 'var(--color-risk-critical-bg)'; pillColor = 'var(--color-risk-critical-text)';
        } else if (days <= 30) {
            pillBg = 'rgba(234,88,12,0.12)'; pillColor = '#c2410c';
        } else {
            pillBg = 'rgba(217,119,6,0.12)'; pillColor = '#b45309';
        }
        var conflicts = summary.total || 0;
        var brandAttr = esc(item.brand_name || '');
        var statusAttr = esc(item.trademark_status || '');
        var escapedName = brandAttr.replace(/'/g, "\\'");
        html += '<div class="grid px-4 py-3 items-center gap-4 cursor-pointer transition-colors hover:opacity-80" data-watchlist-appeal-row="true" data-watchlist-id="' + item.id + '" data-watchlist-brand="' + brandAttr + '" data-watchlist-tm-status="' + statusAttr + '" data-watchlist-deadline-days="' + (days !== null && days !== undefined ? days : '') + '" style="grid-template-columns:1fr auto;background:var(--color-bg-card)" onclick="toggleWatchlistAlerts(\'' + item.id + '\', \'' + escapedName + '\')">'
            + '<div>'
            + '<div class="font-semibold text-sm" style="color:var(--color-text-primary)">' + brandAttr + '</div>'
            + '<div class="text-xs mt-0.5" style="color:var(--color-text-muted)">' + conflicts + ' ' + t('watchlist.col_active_conflicts') + '</div>'
            + '</div>'
            + '<div class="text-right ml-4 flex-shrink-0">'
            + '<span class="inline-block px-2 py-0.5 rounded-full text-xs font-bold" style="background:' + pillBg + ';color:' + pillColor + '">' + (hasDeadline ? (days + ' ' + t('watchlist.renewal_days_left')) : '-') + '</span>'
            + '<div class="text-xs mt-1" style="color:var(--color-text-muted)">' + t('watchlist.col_deadline') + ': ' + deadlineStr + '</div>'
            + '</div>'
            + '</div>';
        html += '<div id="wl-alerts-' + item.id + '" class="hidden px-4 pb-3 pt-2" style="background:var(--color-bg-card)">'
            + '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('dashboard.loading') + '</div>'
            + '</div>';
    });
    html += '</div>';
    grid.innerHTML = html;
}

// NOTE: onWatchlistThresholdChange is defined earlier (display filter + stats refresh).
// _applyGlobalThreshold persists the threshold to the DB for all watchlist items.
// It is NOT wired to the dropdown — the dropdown is a UI display filter only.

async function _applyGlobalThreshold(threshold) {
    var slider = document.getElementById('wl-threshold-slider');
    if (slider) slider.disabled = true;
    try {
        await AppAPI.updateAllThreshold(threshold);
        showToast(t('watchlist.threshold_saved'), 'success');
    } catch (e) {
        showToast(t('watchlist.threshold_error'), 'error');
    } finally {
        if (slider) slider.disabled = false;
    }
}

// ============================================
// WATCHLIST CARD → INLINE EXPANDABLE ALERTS
// ============================================
window._alertFilterWatchlistId = null;
var _expandedWatchlistId = null;

function toggleWatchlistAlerts(watchlistItemId, brandName) {
    var panel = document.getElementById('wl-alerts-' + watchlistItemId);
    var chevron = document.getElementById('wl-chevron-' + watchlistItemId);

    // If already expanded, collapse it
    if (_expandedWatchlistId === watchlistItemId) {
        if (panel) panel.classList.add('hidden');
        if (chevron) chevron.style.transform = '';
        _expandedWatchlistId = null;
        return;
    }

    // Collapse previously expanded
    if (_expandedWatchlistId) {
        var oldPanel = document.getElementById('wl-alerts-' + _expandedWatchlistId);
        var oldChevron = document.getElementById('wl-chevron-' + _expandedWatchlistId);
        if (oldPanel) oldPanel.classList.add('hidden');
        if (oldChevron) oldChevron.style.transform = '';
    }

    // Expand this one
    _expandedWatchlistId = watchlistItemId;
    if (panel) panel.classList.remove('hidden');
    if (chevron) chevron.style.transform = 'rotate(180deg)';

    // Load inline alerts
    loadInlineAlerts(watchlistItemId, panel);
}

function _inlineRiskColor(score) {
    if (score >= 0.9) return 'var(--color-risk-critical-text)';
    if (score >= 0.8) return '#f97316';
    if (score >= 0.7) return 'var(--color-risk-high-text)';
    if (score >= 0.5) return 'var(--color-risk-medium-text)';
    return 'var(--color-risk-low-text)';
}

// Map raw Turkish DB status values (final_status column) to translated strings.
// DB stores Turkish labels; this converts them to the active locale via landing.status_* keys.
var _STATUS_KEY_MAP = {
    'Başvuruldu':   'landing.status_pending',
    'Yayında':      'landing.status_published',
    'Yayınlandı':   'landing.status_published',
    'Tescil Edildi':'landing.status_registered',
    'Reddedildi':   'landing.status_rejected',
    'Geri Çekildi': 'landing.status_withdrawn',
    'İtiraz Edildi':'landing.status_opposed',
    'Süresi Doldu': 'landing.status_expired',
    'Kısmi Red':    'landing.status_partial_refusal',
    'Yenilendi':    'landing.status_renewed',
    'Devredildi':   'landing.status_transferred',
    'İptal Edildi': 'landing.status_cancelled',
};
function translateStatus(rawStatus) {
    if (!rawStatus) return '';
    var key = _STATUS_KEY_MAP[rawStatus];
    return key ? t(key) : rawStatus;
}

function toggleInlineAlertDetail(alertId) {
    var detail = document.getElementById('inline-alert-detail-' + alertId);
    var chevron = document.getElementById('inline-alert-chevron-' + alertId);
    if (!detail) return;
    var isHidden = detail.classList.contains('hidden');
    detail.classList.toggle('hidden');
    if (chevron) chevron.style.transform = isHidden ? 'rotate(180deg)' : '';
}

async function loadInlineAlerts(watchlistItemId, panel) {
    if (!panel) return;
    panel.innerHTML = '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('dashboard.loading') + '</div>';
    try {
        var token = getAuthToken();
        var res = await fetch('/api/v1/alerts?watchlist_id=' + watchlistItemId + '&page=1&page_size=20&min_score=' + _activeThreshold, {
            headers: token ? { 'Authorization': 'Bearer ' + token } : {}
        });
        if (!res.ok) throw new Error('Failed');
        var data = await res.json();
        // Show alerts that match the persisted watchlist-tab display threshold.
        var items = data.items || [];
        // Sync parent row badge to match actual alert count
        var badgeEl = document.getElementById('wl-conflict-count-' + watchlistItemId);
        if (badgeEl) {
            var filteredCount = items.length;
            if (filteredCount === 0) {
                badgeEl.style.display = 'none';
            } else {
                var newColor = filteredCount >= 5 ? '#dc2626' : filteredCount >= 2 ? '#ea580c' : '#ca8a04';
                var newBg    = filteredCount >= 5 ? 'rgba(220,38,38,0.08)' : filteredCount >= 2 ? 'rgba(234,88,12,0.08)' : 'rgba(202,138,4,0.08)';
                badgeEl.style.display = '';
                badgeEl.style.background = newBg;
                var numSpan = badgeEl.querySelector('span');
                if (numSpan) { numSpan.textContent = filteredCount; numSpan.style.color = newColor; }
                var subSpan = badgeEl.querySelectorAll('span')[1];
                if (subSpan) subSpan.style.color = newColor;
            }
        }
        if (items.length === 0) {
            panel.innerHTML = '<div class="text-xs text-center py-2" style="color:var(--color-text-faint)">' + t('empty.alerts_desc') + '</div>';
            return;
        }
        var countColor = items.length >= 5 ? '#dc2626' : items.length >= 2 ? '#ea580c' : '#ca8a04';
        var html = '<div class="flex items-center justify-between mb-2 px-0.5">'
            + '<span class="text-xs font-semibold" style="color:' + countColor + '">'
            + items.length + ' ' + t('watchlist.conflicts')
            + '</span>'
            + '</div>'
            + '<div class="space-y-1 max-h-64 overflow-y-auto pr-1" style="scrollbar-width:thin">';
        items.forEach(function (a) {
            var c = a.conflicting || {};
            var sc = a.scores || {};
            var risk = Math.round((sc.total || 0) * 100);
            var textSim = getEffectiveTextScoreForResult(sc);
            var semanticSim = sc.semantic_similarity || 0;
            var phoneticMatch = sc.phonetic_match || false;
            var visualSim = sc.visual_similarity || 0;
            var transSim = sc.path_b_score !== undefined && sc.path_b_score !== null
                ? parseFloat(sc.path_b_score)
                : (sc.translation_similarity || 0);
            if (isNaN(transSim)) transSim = 0;
            var scoreStyle = window.AppComponents && window.AppComponents.getScoreColorStyle
                ? window.AppComponents.getScoreColorStyle(sc.total || 0)
                : 'background:#fee2e2;color:#991b1b';

            // Deadline badge
            var dlDays = a.deadline_days_remaining;
            var dlBadge = '';
            if (dlDays !== null && dlDays !== undefined) {
                var dlColor = dlDays <= 7 ? '#dc2626' : dlDays <= 14 ? '#ca8a04' : dlDays <= 30 ? '#ca8a04' : '#16a34a';
                var dlBg = dlDays <= 7 ? '#fef2f2' : dlDays <= 14 ? '#fefce8' : dlDays <= 30 ? '#fefce8' : '#f0fdf4';
                dlBadge = '<span class="flex-shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full" style="color:' + dlColor + ';background:' + dlBg + '">'
                    + dlDays + ' ' + t('common.days') + '</span>';
            } else if (!a.appeal_deadline) {
                dlBadge = '<span class="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" style="color:#6b7280;background:var(--color-bg-card)">'
                    + t('deadline.pre_publication') + '</span>';
            }

            // Header row (clickable to expand)
            var conflictThumb = '<div class="flex-shrink-0" onclick="event.stopPropagation()" title="' + t('watchlist.view_logo') + '">'
                + window.AppComponents.renderThumbnail(c.image_path, c.name, c.application_no, 'w-8 h-8')
                + '</div>';
            html += '<div class="rounded-lg overflow-hidden" style="background:var(--color-bg-muted)">'
                + '<div class="flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:opacity-80" onclick="event.stopPropagation(); toggleInlineAlertDetail(\'' + a.id + '\')">'
                + '<div class="flex-shrink-0 w-8 h-8 rounded flex items-center justify-center font-bold text-xs border" style="' + scoreStyle + '">'
                + risk + '%</div>'
                + conflictThumb
                + '<div class="flex-1 min-w-0">'
                + '<div class="text-xs font-medium truncate" style="color:var(--color-text-primary)">' + escapeHtml(c.name || t('common.na')) + '</div>'
                + '<div class="text-xs" style="color:var(--color-text-faint)">' + escapeHtml(c.application_no || '') + (a.conflict_bulletin_no ? ' · ' + t('common.bulletin_label') + ' ' + escapeHtml(a.conflict_bulletin_no) : '') + '</div>'
                + '</div>'
                + dlBadge
                + '<span id="inline-alert-chevron-' + a.id + '" class="text-xs transition-transform inline-block" style="color:var(--color-text-faint)">&#9660;</span>'
                + '</div>';

            // Expandable detail section
            html += '<div id="inline-alert-detail-' + a.id + '" class="hidden px-2 pb-2">'
                + '<div class="h-px mb-2" style="background:var(--color-border)"></div>'
                // Score breakdown: 3 columns with sub-scores
                + '<div class="grid grid-cols-3 gap-1.5 mb-2">'
                // TEXT column — text, semantic, phonetic
                + '<div class="p-1.5 rounded" style="background:var(--color-bg-card)">'
                + '<div class="text-[9px] uppercase tracking-wide text-center mb-1" style="color:var(--color-text-muted)">' + t('landing.detail_text') + '</div>'
                + '<div class="text-xs font-bold text-center" style="color:' + _inlineRiskColor(textSim) + '">' + Math.round(textSim * 100) + '%</div>'
                + '<div class="mt-1 space-y-0.5">'
                + '<div class="flex justify-between text-[10px]"><span style="color:var(--color-text-faint)">' + t('watchlist.score_text') + '</span><span style="color:' + _inlineRiskColor(textSim) + '">' + Math.round(textSim * 100) + '%</span></div>'
                + '<div class="flex justify-between text-[10px]"><span style="color:var(--color-text-faint)">' + t('watchlist.score_semantic') + '</span><span style="color:' + _inlineRiskColor(semanticSim) + '">' + Math.round(semanticSim * 100) + '%</span></div>'
                + '<div class="flex justify-between text-[10px]"><span style="color:var(--color-text-faint)">' + t('watchlist.score_phonetic') + '</span><span style="color:' + (phoneticMatch ? 'var(--color-risk-critical-text)' : 'var(--color-risk-low-text)') + '">' + (phoneticMatch ? t('common.yes') : t('common.no')) + '</span></div>'
                + '</div></div>'
                // VISUAL column
                + '<div class="p-1.5 rounded" style="background:var(--color-bg-card)">'
                + '<div class="text-[9px] uppercase tracking-wide text-center mb-1" style="color:var(--color-text-muted)">' + t('landing.detail_visual') + '</div>'
                + '<div class="text-xs font-bold text-center" style="color:' + _inlineRiskColor(visualSim) + '">' + Math.round(visualSim * 100) + '%</div>'
                + '<div class="mt-1">'
                + '<div class="flex justify-between text-[10px]"><span style="color:var(--color-text-faint)">' + t('watchlist.score_logo') + '</span><span style="color:' + _inlineRiskColor(visualSim) + '">' + Math.round(visualSim * 100) + '%</span></div>'
                + '</div></div>'
                // TRANSLATION column
                + '<div class="p-1.5 rounded" style="background:var(--color-bg-card)">'
                + '<div class="text-[9px] uppercase tracking-wide text-center mb-1" style="color:var(--color-text-muted)">' + t('landing.detail_translation') + '</div>'
                + '<div class="text-xs font-bold text-center" style="color:' + _inlineRiskColor(transSim) + '">' + Math.round(transSim * 100) + '%</div>'
                + '<div class="mt-1">'
                + '<div class="flex justify-between text-[10px]"><span style="color:var(--color-text-faint)">' + t('watchlist.score_translate') + '</span><span style="color:' + _inlineRiskColor(transSim) + '">' + Math.round(transSim * 100) + '%</span></div>'
                + '</div></div>'
                + '</div>';

            // Deadline bar
            if (dlDays !== null && dlDays !== undefined) {
                var dlBarColor = dlDays <= 7 ? '#dc2626' : dlDays <= 14 ? '#ca8a04' : dlDays <= 30 ? '#ca8a04' : '#16a34a';
                var dlBarBg = dlDays <= 7 ? '#fef2f2' : dlDays <= 14 ? '#fefce8' : dlDays <= 30 ? '#fefce8' : '#f0fdf4';
                var dlBarBorder = dlDays <= 7 ? '#fecaca' : dlDays <= 14 ? '#fde68a' : dlDays <= 30 ? '#fde68a' : '#bbf7d0';
                var dlIcon = dlDays <= 7 ? '&#9888;' : dlDays <= 14 ? '&#9200;' : '&#128197;';
                html += '<div class="flex items-center gap-2 px-2 py-1.5 mb-2 rounded-md border text-xs" style="background:' + dlBarBg + ';border-color:' + dlBarBorder + ';color:' + dlBarColor + '">'
                    + '<span>' + dlIcon + '</span>'
                    + '<span class="font-semibold">' + t('deadline.days_remaining', { count: dlDays }) + '</span>'
                    + (a.appeal_deadline ? '<span style="opacity:0.7">(' + t('deadline.appeal_deadline') + ': ' + a.appeal_deadline + ')</span>' : '')
                    + '</div>';
            } else if (!a.appeal_deadline) {
                html += '<div class="flex items-center gap-2 px-2 py-1.5 mb-2 rounded-md border text-xs" style="background:var(--color-bg-card);border-color:var(--color-border);color:var(--color-text-muted)">'
                    + '<span>&#128203;</span>'
                    + '<span>' + t('deadline.pre_publication') + '</span>'
                    + '</div>';
            }

            // Detail fields
            html += '<div class="text-xs space-y-0.5">';
            if (c.status) {
                html += '<div class="flex gap-1"><span style="color:var(--color-text-muted)">' + t('landing.detail_status') + ':</span><span class="font-medium px-1.5 py-0.5 rounded-full text-xs" style="color:' + getStatusColor(c.status) + ';background:' + getStatusBg(c.status) + '">' + escapeHtml(translateStatus(c.status)) + '</span></div>';
            }
            if (c.classes && c.classes.length) {
                html += '<div class="flex gap-1"><span style="color:var(--color-text-muted)">' + t('landing.detail_classes') + ':</span><span class="font-medium" style="color:var(--color-text-primary)">' + c.classes.join(', ') + '</span></div>';
            }
            if (a.conflict_bulletin_no) {
                html += '<div class="flex gap-1"><span style="color:var(--color-text-muted)">' + t('common.bulletin_label') + '</span><span class="font-medium" style="color:var(--color-text-primary)">' + escapeHtml(a.conflict_bulletin_no) + '</span></div>';
            }
            if (a.conflict_bulletin_date) {
                html += '<div class="flex gap-1"><span style="color:var(--color-text-muted)">' + t('deadline.bulletin_date') + '</span><span class="font-medium" style="color:var(--color-text-primary)">' + formatDateTRShort(a.conflict_bulletin_date) + '</span></div>';
            }
            if (c.holder) {
                html += '<div class="flex gap-1"><span class="shrink-0" style="color:var(--color-text-muted)">' + t('landing.detail_holder') + ':</span><span class="font-medium truncate" style="color:var(--color-text-primary)">' + escapeHtml(c.holder) + '</span></div>';
            }
            if (c.application_date) {
                html += '<div class="flex gap-1"><span style="color:var(--color-text-muted)">' + t('landing.detail_date') + ':</span><span class="font-medium" style="color:var(--color-text-primary)">' + escapeHtml(c.application_date) + '</span></div>';
            }
            html += '</div>';

            // Action buttons: itiraz + resolve + dismiss
            var _wlBrand = '';
            if (_watchlistItemsCache) {
                var _wlMatch = _watchlistItemsCache.find(function (w) { return String(w.id) === String(watchlistItemId); });
                if (_wlMatch) _wlBrand = _wlMatch.brand_name || '';
            }
            _itirazContextMap[a.id] = {
                name: c.name || '',
                appNo: c.application_no || '',
                holder: c.holder || '',
                bulletinNo: a.conflict_bulletin_no || '',
                bulletinDate: a.conflict_bulletin_date || '',
                classes: c.classes || [],
                riskScore: Math.round((sc.total || 0) * 100),
                deadline: a.appeal_deadline || null,
                watchedBrand: _wlBrand
            };
            html += '<div class="flex items-center gap-1.5 mt-2 pt-2" style="border-top:1px solid var(--color-border)">'
                + '<button onclick="event.stopPropagation(); openItirazForm(\'' + a.id + '\')" '
                + 'class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg transition-colors bg-orange-50 text-orange-700 hover:bg-orange-100 border border-orange-200 flex items-center justify-center gap-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>'
                + t('alerts.file_opposition') + '</button>'
                + '<button onclick="event.stopPropagation(); inlineResolveAlert(\'' + a.id + '\', \'' + watchlistItemId + '\')" '
                + 'class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg transition-colors bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 flex items-center justify-center gap-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
                + t('alerts.resolved') + '</button>'
                + '<button onclick="event.stopPropagation(); inlineDismissAlert(\'' + a.id + '\', \'' + watchlistItemId + '\')" '
                + 'class="flex-1 px-2 py-1.5 text-xs font-medium rounded-lg transition-colors bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200 flex items-center justify-center gap-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
                + t('alerts.dismiss') + '</button>'
                + '</div>';

            html += '</div></div>';
        });
        html += '</div>';
        panel.innerHTML = html;
    } catch (e) {
        panel.innerHTML = '<div class="text-xs text-center py-2 text-red-500">' + t('dashboard.threats_load_failed') + '</div>';
    }
}

function filterAlertsByWatchlistItem(watchlistItemId, brandName) {
    // Update header in watchlist tab panel
    var wlHeader = document.getElementById('watchlist-alert-header');
    if (wlHeader) {
        wlHeader.innerHTML = '<div class="flex items-center justify-between">'
            + '<span class="font-semibold" style="color:var(--color-text-primary)">' + t('dashboard.threats_label', { brand: escapeHtml(brandName) }) + '</span>'
            + '<button onclick="clearAlertFilter()" class="text-sm text-blue-600 hover:text-blue-800">' + t('dashboard.remove_filter') + '</button>'
            + '</div>';
    }
    // Also update the threats section header in watchlist tab
    var alertHeader = document.getElementById('alert-list-header');
    if (alertHeader) {
        alertHeader.innerHTML = '<div class="flex items-center justify-between">'
            + '<span class="font-semibold" style="color:var(--color-text-primary)">' + t('dashboard.threats_label', { brand: escapeHtml(brandName) }) + '</span>'
            + '<button onclick="clearAlertFilter()" class="text-sm text-blue-600 hover:text-blue-800">' + t('dashboard.remove_filter') + '</button>'
            + '</div>';
    }
    window._alertFilterWatchlistId = watchlistItemId;
    loadFilteredAlerts(watchlistItemId);
}

async function loadFilteredAlerts(watchlistItemId) {
    try {
        var token = getAuthToken();
        var res = await fetch('/api/v1/alerts?watchlist_id=' + watchlistItemId + '&page=1&page_size=50', {
            headers: token ? { 'Authorization': 'Bearer ' + token } : {}
        });
        if (!res.ok) throw new Error('Failed to load alerts');
        var data = await res.json();
        var items = data.items || [];

        var mapped = items.map(function (a) {
            var c = a.conflicting || {};
            var sc = a.scores || {};
            return {
                alert_id: a.id,
                conflicting_brand: c.name || 'N/A',
                conflicting_app_no: c.application_no || '',
                conflict_bulletin_no: a.conflict_bulletin_no || '',
                brand_watched: a.watched_brand_name || '',
                risk_score: Math.round((sc.total || 0) * 100),
                scores: sc,
                date: a.detected_at || '',
                appeal_deadline: a.appeal_deadline || null,
                deadline_status: a.deadline_status || null,
                deadline_days_remaining: a.deadline_days_remaining,
                deadline_label: a.deadline_label || '',
                deadline_urgency: a.deadline_urgency || '',
                severity: a.severity || null
            };
        });

        // Update the Alpine component's alerts (watchlist tab threats section)
        var data = _alpineRoot();
        if (data) {
            data.alerts = mapped;
        }

        // Filter out expired appeals (deadline already passed) — only show appealable conflicts
        var today = new Date().toISOString().slice(0, 10);
        mapped = mapped.filter(function (a) {
            if (!a.appeal_deadline) return false; // no deadline — not appealable, hide
            return a.appeal_deadline >= today;     // only show if deadline not yet passed
        });

        // Cache alerts and render with filters
        _watchlistAlertsCache = mapped;
        var filtersEl = document.getElementById('alert-filters');
        if (filtersEl) filtersEl.classList.toggle('hidden', mapped.length === 0);
        renderWatchlistAlerts(mapped);
    } catch (err) {
        showToast(t('dashboard.threats_load_failed'), 'error');
    }
}

function clearAlertFilter() {
    window._alertFilterWatchlistId = null;
    _watchlistAlertsCache = [];
    // Collapse any expanded inline card
    if (_expandedWatchlistId) {
        var oldPanel = document.getElementById('wl-alerts-' + _expandedWatchlistId);
        var oldChevron = document.getElementById('wl-chevron-' + _expandedWatchlistId);
        if (oldPanel) oldPanel.classList.add('hidden');
        if (oldChevron) oldChevron.style.transform = '';
        _expandedWatchlistId = null;
    }
    var alertHeader = document.getElementById('alert-list-header');
    if (alertHeader) {
        alertHeader.innerHTML = '<span class="font-semibold" style="color:var(--color-text-primary)">' + t('dashboard.recent_threats') + '</span>';
    }
    // Reset watchlist tab alert panel
    var wlHeader = document.getElementById('watchlist-alert-header');
    if (wlHeader) {
        wlHeader.innerHTML = '<h3 class="font-semibold" style="color:var(--color-text-primary)">' + t('dashboard.recent_threats') + '</h3>'
            + '<p class="text-xs mt-0.5" style="color:var(--color-text-faint)">' + t('empty.watchlist_desc') + '</p>';
    }
    var wlList = document.getElementById('watchlist-alert-list');
    if (wlList) {
        wlList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('empty.alerts_desc') + '</div>';
    }
    var filtersEl = document.getElementById('alert-filters');
    if (filtersEl) filtersEl.classList.add('hidden');
    // Reload all alerts by refreshing the Alpine component
    var data = _alpineRoot();
    if (data && typeof data.loadData === 'function') {
        data.loadData();
    }
}

// ============================================
// ALERT FILTERS (Tehditler panel)
// ============================================
var _watchlistAlertsCache = [];

function renderWatchlistAlerts(alerts) {
    var wlList = document.getElementById('watchlist-alert-list');
    if (!wlList) return;
    if (!alerts || alerts.length === 0) {
        wlList.innerHTML = '<div class="p-6 text-center text-sm" style="color:var(--color-text-faint)">' + t('empty.alerts_desc') + '</div>';
        return;
    }
    wlList.innerHTML = alerts.map(function (a) {
        var scoreStyle = window.AppComponents && window.AppComponents.getScoreColorStyle
            ? window.AppComponents.getScoreColorStyle(a.risk_score / 100)
            : 'background:#fee2e2;color:#991b1b;border-color:#fca5a5';

        // Similarity breakdown badges (same as genel bakis)
        var badgesHtml = (window.AppComponents && window.AppComponents.renderSimilarityBadges && a.scores)
            ? window.AppComponents.renderSimilarityBadges(a.scores)
            : '';

        // Deadline info
        var deadlineHtml = '';
        if (a.deadline_days_remaining !== null && a.deadline_days_remaining !== undefined && a.appeal_deadline) {
            var dColor = a.deadline_days_remaining <= 7 ? 'var(--color-risk-critical-text)' : a.deadline_days_remaining <= 30 ? '#ea580c' : 'var(--color-text-faint)';
            deadlineHtml = '<div class="text-xs mt-1" style="color:' + dColor + '">'
                + t('deadline.days_remaining', { count: a.deadline_days_remaining }) + '</div>';
        }

        // Action buttons: itiraz + resolve + dismiss
        _itirazContextMap[a.alert_id] = {
            name: a.conflicting_brand || '',
            appNo: a.conflicting_app_no || '',
            holder: '',
            bulletinNo: a.conflict_bulletin_no || '',
            bulletinDate: a.conflict_bulletin_date || '',
            classes: a.overlapping_classes || [],
            riskScore: a.risk_score || null,
            deadline: a.appeal_deadline || null,
            watchedBrand: a.brand_watched || ''
        };
        var actionsHtml = '<div class="flex items-center gap-1 mt-2">'
            + '<button onclick="event.stopPropagation(); openItirazForm(\'' + a.alert_id + '\')" '
            + 'class="px-2 py-1 text-xs font-medium rounded transition-colors bg-orange-50 text-orange-700 hover:bg-orange-100 border border-orange-200" '
            + 'title="' + t('alerts.file_opposition') + '">'
            + '<svg class="w-3 h-3 inline mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>'
            + t('alerts.file_opposition') + '</button>'
            + '<button onclick="event.stopPropagation(); quickResolveAlert(\'' + a.alert_id + '\')" '
            + 'class="px-2 py-1 text-xs font-medium rounded transition-colors bg-green-50 text-green-700 hover:bg-green-100 border border-green-200" '
            + 'title="' + t('alerts.resolved') + '">'
            + '<svg class="w-3 h-3 inline mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
            + t('alerts.resolved') + '</button>'
            + '<button onclick="event.stopPropagation(); quickDismissAlert(\'' + a.alert_id + '\')" '
            + 'class="px-2 py-1 text-xs font-medium rounded transition-colors bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200" '
            + 'title="' + t('alerts.dismiss') + '">'
            + '<svg class="w-3 h-3 inline mr-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
            + t('alerts.dismiss') + '</button>'
            + '</div>';

        return '<div class="p-4 transition-colors border-b" style="border-color:var(--color-border)">'
            + '<div class="flex items-center gap-3 cursor-pointer" onclick="showAlertDetail(\'' + a.alert_id + '\')">'
            + '<div class="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center font-bold text-sm border" style="' + scoreStyle + '">'
            + a.risk_score + '%</div>'
            + '<div class="flex-1 min-w-0">'
            + '<div class="font-semibold text-sm truncate" style="color:var(--color-text-primary)">' + escapeHtml(getTrademarkDisplayName(a.conflicting_brand)) + '</div>'
            + '<div class="text-xs" style="color:var(--color-text-faint)">' + escapeHtml(a.conflicting_app_no) + '</div>'
            + deadlineHtml
            + '</div>'
            + '</div>'
            + badgesHtml
            + actionsHtml
            + '</div>';
    }).join('');
}

function applyAlertFilters() {
    var items = _watchlistAlertsCache;
    if (!items || items.length === 0) return;

    var dateEl = document.getElementById('alert-filter-date');
    var bulletinEl = document.getElementById('alert-filter-bulletin');
    var sortEl = document.getElementById('alert-filter-sort');

    var dateDays = dateEl ? dateEl.value : 'all';
    var bulletinSort = bulletinEl ? bulletinEl.value : 'none';
    var sortBy = sortEl ? sortEl.value : 'date_desc';

    var filtered = items.filter(function (a) {
        if (dateDays !== 'all') {
            var days = parseInt(dateDays, 10);
            var cutoff = new Date();
            cutoff.setDate(cutoff.getDate() - days);
            var aDate = a.date ? new Date(a.date) : null;
            if (aDate && aDate < cutoff) return false;
        }
        return true;
    });

    if (bulletinSort !== 'none') {
        filtered.sort(function (a, b) {
            var ba = parseInt(a.conflict_bulletin_no || '0', 10) || 0;
            var bb = parseInt(b.conflict_bulletin_no || '0', 10) || 0;
            return bulletinSort === 'bulletin_desc' ? bb - ba : ba - bb;
        });
    } else {
        filtered.sort(function (a, b) {
            if (sortBy === 'risk_desc') return b.risk_score - a.risk_score;
            if (sortBy === 'risk_asc') return a.risk_score - b.risk_score;
            if (sortBy === 'name_asc') return (a.conflicting_brand || '').localeCompare(b.conflicting_brand || '');
            if (sortBy === 'date_asc') return new Date(a.date || 0) - new Date(b.date || 0);
            return new Date(b.date || 0) - new Date(a.date || 0);
        });
    }

    renderWatchlistAlerts(filtered);
}

function handleWatchlistLogoUpload(itemId, input) {
    var file = input.files && input.files[0];
    if (!file) return;

    if (!file.type.startsWith('image/')) {
        showToast(t('watchlist.select_image_file'), 'error');
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        showToast(t('watchlist.file_too_large'), 'error');
        return;
    }

    showToast(t('watchlist.logo_uploading'), 'info');
    AppAPI.uploadWatchlistLogo(itemId, file).then(function (data) {
        showToast(data.message || t('watchlist.logo_uploaded'), 'success');
        refreshWatchlistAndStats();
    }).catch(function (e) {
        if (e && e.status === 403) {
            showUpgradeModal(e, 'watchlist_logo');
            return;
        }
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

function deleteWatchlistLogo(itemId) {
    AppAPI.deleteWatchlistLogo(itemId).then(function (data) {
        showToast(data.message || t('watchlist.logo_deleted'), 'success');
        refreshWatchlistAndStats();
    }).catch(function (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

// ============================================
// WATCHLIST LOGO PREVIEW (uses global lightbox)
// ============================================
function openLogoPreview(url, title) {
    window.dispatchEvent(new CustomEvent('open-lightbox', {
        detail: { src: url, title: title || '', subtitle: '' }
    }));
}

// ============================================
// WATCHLIST REFRESH HELPER
// ============================================
// Note: refreshWatchlistAndStats defined above in WATCHLIST TAB INIT section

// ============================================
// WATCHLIST ACTIONS (DELETE, EDIT, SCAN)
// ============================================
function deleteWatchlistItem(itemId, brandName) {
    if (!confirm(t('watchlist.delete_confirm', { name: brandName }))) return;
    AppAPI.deleteWatchlistItem(itemId).then(function () {
        showToast(t('watchlist.deleted_success'), 'success');
        refreshWatchlistAndStats();
        clearAlertFilter();
    }).catch(function (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

function scanWatchlistItem(itemId) {
    showToast(t('watchlist.scan_started'), 'info');
    AppAPI.scanWatchlistItem(itemId).then(function () {
        showToast(t('watchlist.scan_queued'), 'success');
        _setScanInProgress(_getScanDurationMs(1), 'item');
    }).catch(function (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

// ============================================
// SCAN-ALL: estimate, modal, button state
// ============================================
var _SCAN_LS_START = 'wl_scan_start_ms';
var _SCAN_LS_DUR   = 'wl_scan_dur_ms';
var _SCAN_LS_MODE  = 'wl_scan_mode';
var _scanPollTimer = null;

function _getScanDurationMs(count) {
    count = parseInt(count, 10);
    if (!count || count < 1) count = 1;
    return Math.max(count * 15000, 60000); // background scan duration varies; keep the visible session generous
}

function _coerceQueuedScanCount(data, fallbackCount) {
    var hasQueuedScans = data && Object.prototype.hasOwnProperty.call(data, 'queued_scans');
    var hasScanCount = data && Object.prototype.hasOwnProperty.call(data, 'scan_count');
    var count = 0;
    if (hasQueuedScans) count = Number(data.queued_scans);
    else if (hasScanCount) count = Number(data.scan_count);
    else if (fallbackCount !== undefined) count = Number(fallbackCount);
    if (!isFinite(count) || count < 0) return 0;
    return Math.floor(count);
}

function _extractScanCountFromResponse(data, fallbackCount) {
    var hasExplicitCount = data && (
        Object.prototype.hasOwnProperty.call(data, 'queued_scans') ||
        Object.prototype.hasOwnProperty.call(data, 'scan_count')
    );
    var count = _coerceQueuedScanCount(data, undefined);
    if (hasExplicitCount) return count > 0 ? Math.floor(count) : 0;
    if (!count && data && data.message) {
        var match = String(data.message).match(/^\s*(\d+)\s+/);
        if (match) count = parseInt(match[1], 10) || 0;
    }
    if (!count && fallbackCount !== undefined) count = Number(fallbackCount) || 0;
    return count > 0 ? Math.floor(count) : 0;
}

function _getScanEstimate(count) {
    if (!count || count <= 0) return '';
    var secs = Math.ceil(_getScanDurationMs(count) / 1000);
    if (secs < 60) return t('watchlist.scan_est_under_minute') || '< 1 dakika';
    var mins = Math.ceil(secs / 60);
    if (mins < 60) return '~' + mins + ' ' + (t('watchlist.scan_est_min') || 'dakika');
    var hrs = Math.ceil(mins / 60);
    return '~' + hrs + ' ' + (t('watchlist.scan_est_hour') || 'saat');
}

function _formatScanRemaining(ms) {
    if (!ms || ms <= 0) return '';
    if (ms < 60000) return t('watchlist.scan_est_under_minute') || '< 1 dakika';
    var mins = Math.ceil(ms / 60000);
    if (mins < 60) return '~' + mins + ' ' + (t('watchlist.scan_est_min') || 'dakika');
    var hrs = Math.ceil(mins / 60);
    return '~' + hrs + ' ' + (t('watchlist.scan_est_hour') || 'saat');
}

var _SCAN_ICON = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>';
var _SPIN_ICON  = '<svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>';

function _updateScanButtonState(scanning) {
    var btn = document.getElementById('btn-scan-all');
    if (!btn) return;
    if (scanning) {
        btn.disabled = true;
        btn.style.opacity = '0.55';
        btn.style.cursor = 'not-allowed';
        btn.innerHTML = _SPIN_ICON + '<span class="hidden sm:inline">' + (t('watchlist.scanning') || 'Taranıyor...') + '</span>';
    } else {
        btn.disabled = false;
        btn.style.opacity = '';
        btn.style.cursor = '';
        btn.innerHTML = _SCAN_ICON + '<span class="hidden sm:inline">' + (t('watchlist.scan_all') || 'Tara') + '</span>';
    }
}

function _getScanState() {
    try {
        var start = parseInt(localStorage.getItem(_SCAN_LS_START) || '0', 10);
        var dur = parseInt(localStorage.getItem(_SCAN_LS_DUR) || '0', 10);
        var mode = localStorage.getItem(_SCAN_LS_MODE) || 'all';
        var elapsed = start ? Date.now() - start : 0;
        return {
            hasState: !!(start && dur),
            active: !!(start && dur && elapsed < dur),
            start: start,
            duration: dur,
            mode: mode,
            elapsed: elapsed,
            remaining: Math.max(0, dur - elapsed)
        };
    } catch (e) {
        return { hasState: false, active: false, start: 0, duration: 0, mode: 'all', elapsed: 0, remaining: 0 };
    }
}

function _ensureScanBanner() {
    var main = document.getElementById('tab-content-watchlist');
    if (!main) return null;
    var banner = document.getElementById('watchlist-scan-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'watchlist-scan-banner';
        banner.className = 'mb-4 rounded-xl border px-4 py-3';
        banner.style.cssText = 'display:none;background:rgba(59,130,246,0.08);border-color:rgba(59,130,246,0.25)';
        main.insertBefore(banner, main.children[1] || null);
    }
    return banner;
}

function _hideScanBanner() {
    var banner = document.getElementById('watchlist-scan-banner');
    if (banner) banner.style.display = 'none';
}

function _updateScanBanner() {
    var state = _getScanState();
    if (!state.active) {
        _hideScanBanner();
        return;
    }
    var banner = _ensureScanBanner();
    if (!banner) return;
    var isSingle = state.mode === 'item';
    var title = isSingle ? t('watchlist.scan_live_single_title') : t('watchlist.scan_live_title');
    var remaining = _formatScanRemaining(state.remaining);
    var status = t('watchlist.scan_live_refreshing');
    if (remaining) {
        status += ' · ' + t('watchlist.scan_live_remaining', { time: remaining });
    }
    banner.innerHTML =
        '<div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">'
        + '<div class="flex items-start gap-3">'
        + '<div class="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style="background:rgba(59,130,246,0.14);color:var(--color-primary)">' + _SPIN_ICON + '</div>'
        + '<div>'
        + '<div class="text-sm font-semibold" style="color:var(--color-text-primary)">' + title + '</div>'
        + '<div class="text-xs mt-0.5" style="color:var(--color-text-muted)">' + t('watchlist.scan_live_desc') + '</div>'
        + '</div>'
        + '</div>'
        + '<div class="text-xs font-medium px-2.5 py-1 rounded-full self-start sm:self-center" style="background:var(--color-bg-card);color:var(--color-text-secondary);border:1px solid var(--color-border)">' + status + '</div>'
        + '</div>';
    banner.style.display = '';
}

function _restoreExpandedWatchlistPanel() {
    if (!_expandedWatchlistId) return;
    var panel = document.getElementById('wl-alerts-' + _expandedWatchlistId);
    if (!panel) return;
    var chevron = document.getElementById('wl-chevron-' + _expandedWatchlistId);
    panel.classList.remove('hidden');
    if (chevron) chevron.style.transform = 'rotate(180deg)';
    loadInlineAlerts(_expandedWatchlistId, panel);
}

function _refreshWatchlistDuringScan() {
    loadWatchlistStats();
    _wlSilentRefresh = true;
    loadPortfolio();
    _wlSilentRefresh = false;
    if (window._alertFilterWatchlistId && typeof loadFilteredAlerts === 'function') {
        loadFilteredAlerts(window._alertFilterWatchlistId);
    }
}

function _scheduleScanPolling(refreshNow) {
    if (_scanPollTimer) {
        clearTimeout(_scanPollTimer);
        _scanPollTimer = null;
    }
    var state = _getScanState();
    if (!state.hasState) {
        _updateScanButtonState(false);
        _hideScanBanner();
        return;
    }
    if (!state.active) {
        _clearScanInProgress(true);
        return;
    }
    _updateScanButtonState(true);
    _updateScanBanner();
    if (refreshNow) {
        _refreshWatchlistDuringScan();
    }
    var delay = state.elapsed < 120000 ? 5000 : 10000;
    _scanPollTimer = setTimeout(function () {
        _scheduleScanPolling(true);
    }, Math.min(delay, state.remaining || delay));
}

function _setScanInProgress(estimatedMs, mode) {
    estimatedMs = Math.max(parseInt(estimatedMs, 10) || 60000, 30000);
    try {
        localStorage.setItem(_SCAN_LS_START, Date.now().toString());
        localStorage.setItem(_SCAN_LS_DUR, estimatedMs.toString());
        localStorage.setItem(_SCAN_LS_MODE, mode || 'all');
    } catch (e) { /* ignore storage failures */ }
    _updateScanButtonState(true);
    _updateScanBanner();
    _scheduleScanPolling(true);
}

function _clearScanInProgress(showDoneToast) {
    if (_scanPollTimer) {
        clearTimeout(_scanPollTimer);
        _scanPollTimer = null;
    }
    try {
        localStorage.removeItem(_SCAN_LS_START);
        localStorage.removeItem(_SCAN_LS_DUR);
        localStorage.removeItem(_SCAN_LS_MODE);
    } catch (e) { /* ignore storage failures */ }
    _updateScanButtonState(false);
    _hideScanBanner();
    if (showDoneToast) {
        _refreshWatchlistDuringScan();
        showToast(t('watchlist.scan_live_done') || t('watchlist.scan_completed_check'), 'success');
    }
}

function _initScanButtonState() {
    var state = _getScanState();
    if (!state.hasState) return;
    if (state.active) {
        _scheduleScanPolling(true);
    } else {
        _clearScanInProgress(false);
        _refreshWatchlistDuringScan();
    }
}

function scanAllWatchlist() {
    var count = _wlTotalCount || 0;
    var estStr = _getScanEstimate(count);

    // Build inline confirmation modal
    var overlay = document.createElement('div');
    overlay.id = 'scan-confirm-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML =
        '<div style="background:var(--color-bg-card);border-radius:12px;padding:24px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.35)">'
        + '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">'
        +   '<div style="width:40px;height:40px;border-radius:50%;background:#fef3c7;display:flex;align-items:center;justify-content:center;flex-shrink:0">'
        +     '<svg style="width:20px;height:20px" fill="none" stroke="#d97706" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>'
        +   '</div>'
        +   '<h3 style="font-size:1rem;font-weight:600;color:var(--color-text-primary)">' + (t('watchlist.scan_all_confirm_title') || 'Tüm Markalar Taranacak') + '</h3>'
        + '</div>'
        + '<div style="margin-bottom:20px">'
        + (count > 0
            ? '<p style="font-size:0.9rem;color:var(--color-text-secondary);margin-bottom:10px">'
              + (t('watchlist.scan_all_items_count', { count: count }) || count + ' marka taranacak.')
              + '</p>'
            : '')
        + (estStr
            ? '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:8px;background:var(--color-bg-muted)">'
              + '<svg style="width:16px;height:16px;flex-shrink:0" fill="none" stroke="var(--color-text-muted)" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
              + '<span style="font-size:0.875rem;color:var(--color-text-secondary)">'
              + (t('watchlist.scan_est_label') || 'Tahmini süre:') + ' <strong style="color:var(--color-text-primary)">' + estStr + '</strong>'
              + '</span>'
              + '</div>'
            : '')
        + '</div>'
        + '<div style="display:flex;gap:10px;justify-content:flex-end">'
        + '<button id="scan-confirm-cancel" style="padding:8px 18px;border-radius:8px;border:1px solid var(--color-border);color:var(--color-text-secondary);background:transparent;cursor:pointer;font-size:0.875rem">' + (t('common.cancel') || 'İptal') + '</button>'
        + '<button id="scan-confirm-ok" style="padding:8px 18px;border-radius:8px;border:none;background:var(--color-primary);color:white;cursor:pointer;font-size:0.875rem;font-weight:600">' + (t('watchlist.scan_all_start') || 'Taramayı Başlat') + '</button>'
        + '</div>'
        + '</div>';

    document.body.appendChild(overlay);

    function removeOverlay() { overlay.remove(); }
    document.getElementById('scan-confirm-cancel').addEventListener('click', removeOverlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) removeOverlay(); });

    document.getElementById('scan-confirm-ok').addEventListener('click', function () {
        removeOverlay();
        showToast(t('watchlist.scan_all_started') || 'Toplu tarama başlatılıyor...', 'info');

        AppAPI.scanAllWatchlist().then(function (res) {
            var msg = (res && res.message) ? res.message : t('watchlist.scan_all_queued');
            var queuedScans = _extractScanCountFromResponse(res);
            if (queuedScans > 0) {
                _setScanInProgress(_getScanDurationMs(queuedScans), 'all');
                _scheduleScanPolling(true);
            } else {
                _clearScanInProgress(false);
            }
            showToast(msg, 'success');
        }).catch(function (e) {
            _clearScanInProgress(false);
            showToast((t('common.error') || 'Hata') + ': ' + e.message, 'error');
        });
    });
}

function deleteAllWatchlist() {
    if (!confirm(t('watchlist.delete_all_confirm'))) return;
    if (!confirm(t('watchlist.delete_all_confirm_2'))) return;
    AppAPI.deleteAllWatchlist().then(function () {
        showToast(t('watchlist.delete_all_success'), 'success');
        refreshWatchlistAndStats();
        clearAlertFilter();
    }).catch(function (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

// ============================================
// WATCHLIST EDIT MODAL
// ============================================
var _editingWatchlistItem = null;
var _watchlistItemsCache = [];

function openEditWatchlistModal(idx) {
    var item = _watchlistItemsCache[idx];
    if (!item) return;
    _editingWatchlistItem = item;
    var modal = document.getElementById('watchlist-edit-modal');
    if (!modal) return;
    document.getElementById('edit-wl-brand').value = item.brand_name || '';
    document.getElementById('edit-wl-description').value = item.description || '';
    document.getElementById('edit-wl-threshold').value = String(item.similarity_threshold || 0.5);
    document.getElementById('edit-wl-classes').value = (item.nice_class_numbers || []).join(', ');
    document.getElementById('edit-wl-monitor-text').checked = item.monitor_text !== false;
    document.getElementById('edit-wl-monitor-visual').checked = item.monitor_visual === true;
    document.getElementById('edit-wl-monitor-phonetic').checked = item.monitor_phonetic === true;
    document.getElementById('edit-wl-frequency').value = item.alert_frequency || 'daily';
    modal.classList.remove('hidden');
    lockBodyScroll();
}

function closeEditWatchlistModal() {
    var modal = document.getElementById('watchlist-edit-modal');
    if (modal) modal.classList.add('hidden');
    unlockBodyScroll();
    _editingWatchlistItem = null;
}

function submitEditWatchlist() {
    if (!_editingWatchlistItem) return;
    var btn = document.getElementById('edit-wl-submit-btn');
    btn.disabled = true;

    var classesStr = document.getElementById('edit-wl-classes').value;
    var classes = classesStr.split(',').map(function (c) { return parseInt(c.trim()); }).filter(function (n) { return n >= 1 && n <= 45; });

    var data = {
        brand_name: document.getElementById('edit-wl-brand').value.trim(),
        description: document.getElementById('edit-wl-description').value.trim(),
        similarity_threshold: parseFloat(document.getElementById('edit-wl-threshold').value),
        nice_class_numbers: classes.length > 0 ? classes : undefined,
        monitor_text: document.getElementById('edit-wl-monitor-text').checked,
        monitor_visual: document.getElementById('edit-wl-monitor-visual').checked,
        monitor_phonetic: document.getElementById('edit-wl-monitor-phonetic').checked,
        alert_frequency: document.getElementById('edit-wl-frequency').value
    };

    AppAPI.updateWatchlistItem(_editingWatchlistItem.id, data).then(function () {
        showToast(t('watchlist.updated_success'), 'success');
        closeEditWatchlistModal();
        refreshWatchlistAndStats();
    }).catch(function (e) {
        if (e && e.status === 403) {
            showUpgradeModal(e, 'watchlist_logo');
            return;
        }
        showToast(t('common.error') + ': ' + e.message, 'error');
    }).finally(function () {
        btn.disabled = false;
    });
}

// ============================================
// WATCHLIST BULK FILE UPLOAD
// ============================================
function openBulkUploadModal() {
    var modal = document.getElementById('watchlist-upload-modal');
    if (!modal) return;
    document.getElementById('upload-wl-file').value = '';
    showBulkUploadStepOne();
    document.getElementById('upload-wl-result').classList.add('hidden');
    document.getElementById('upload-wl-filename').textContent = '';
    document.getElementById('upload-wl-filename').classList.add('hidden');
    document.getElementById('upload-wl-usage').innerHTML = '';
    document.getElementById('upload-wl-mapping').innerHTML = '';
    _uploadDetectedColumns = null;
    _uploadSelectedFile = null;
    _uploadUsage = { used: 0, limit: 0 };
    modal.classList.remove('hidden');
    lockBodyScroll();
}

function closeBulkUploadModal() {
    var modal = document.getElementById('watchlist-upload-modal');
    if (modal) modal.classList.add('hidden');
    clearUploadUpgradeOffer();
    unlockBodyScroll();
}

function downloadWatchlistTemplate() {
    var token = window.AppAuth ? window.AppAuth.getToken() : '';
    var a = document.createElement('a');
    a.href = '/api/v1/watchlist/upload/template';
    a.download = 'watchlist_template.xlsx';
    // Use fetch with auth header
    fetch('/api/v1/watchlist/upload/template', {
        headers: { 'Authorization': 'Bearer ' + token }
    }).then(function (res) {
        if (!res.ok) throw new Error('Download failed');
        return res.blob();
    }).then(function (blob) {
        var url = URL.createObjectURL(blob);
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }).catch(function (e) {
        showToast(t('common.error') + ': ' + e.message, 'error');
    });
}

var _uploadDetectedColumns = null;
var _uploadSelectedFile = null;
var _uploadUsage = { used: 0, limit: 0 };
var _uploadUpgradeOffer = null;

function clearUploadUpgradeOffer() {
    _uploadUpgradeOffer = null;

    var modalCard = document.getElementById('watchlist-upload-modal-card');
    if (modalCard) {
        modalCard.classList.remove('max-w-5xl');
        modalCard.classList.add('max-w-lg');
    }

    var layout = document.getElementById('upload-wl-step-2-layout');
    if (layout) layout.classList.remove('lg:grid-cols-[minmax(0,1fr)_340px]');

    var offerEl = document.getElementById('upload-wl-upgrade-offer');
    if (offerEl) {
        offerEl.classList.add('hidden');
        delete offerEl.dataset.recommendedPlan;
        delete offerEl.dataset.upgradeContext;
    }
}

function showBulkUploadStepOne() {
    document.getElementById('upload-wl-step-1').classList.remove('hidden');
    document.getElementById('upload-wl-step-2').classList.add('hidden');
    clearUploadUpgradeOffer();
}

function resolveUploadUpgradeOffer(totalRows, detail) {
    if (!(window.AppUpgradeModal && typeof window.AppUpgradeModal.resolveOffer === 'function')) return null;

    var used = Number(_uploadUsage.used || 0);
    var limit = Number(_uploadUsage.limit || 0);
    var remaining = limit >= 999999 ? totalRows : Math.max(0, limit - used);
    var cannotAdd = Math.max(0, totalRows - Math.min(totalRows, remaining));
    if (cannotAdd <= 0) return null;

    var normalized = detail && typeof detail === 'object' ? Object.assign({}, detail) : {};
    if (!normalized.upgrade_context) normalized.upgrade_context = 'watchlist_items';
    normalized.required_feature = 'max_watchlist_items';
    normalized.required_feature_value = used + totalRows;

    return window.AppUpgradeModal.resolveOffer(normalized, 'watchlist_items');
}

function renderUploadUpgradeOffer(totalRows, detail) {
    var offerEl = document.getElementById('upload-wl-upgrade-offer');
    if (!offerEl) return;

    var used = Number(_uploadUsage.used || 0);
    var limit = Number(_uploadUsage.limit || 0);
    var remaining = limit >= 999999 ? totalRows : Math.max(0, limit - used);
    var cannotAdd = Math.max(0, totalRows - Math.min(totalRows, remaining));
    var offer = resolveUploadUpgradeOffer(totalRows, detail);
    if (!offer) {
        clearUploadUpgradeOffer();
        return;
    }

    _uploadUpgradeOffer = offer;

    var modalCard = document.getElementById('watchlist-upload-modal-card');
    if (modalCard) {
        modalCard.classList.remove('max-w-lg');
        modalCard.classList.add('max-w-5xl');
    }

    var layout = document.getElementById('upload-wl-step-2-layout');
    if (layout) layout.classList.add('lg:grid-cols-[minmax(0,1fr)_340px]');

    offerEl.classList.remove('hidden');
    offerEl.dataset.recommendedPlan = offer.recommendedPlan || '';
    offerEl.dataset.upgradeContext = offer.context || '';

    var titleEl = document.getElementById('upload-wl-upgrade-title');
    if (titleEl) titleEl.textContent = t('watchlist.upload_upgrade_title');

    var descEl = document.getElementById('upload-wl-upgrade-desc');
    if (descEl) descEl.textContent = t('watchlist.upload_upgrade_desc', { count: cannotAdd, total: totalRows });

    var planNameEl = document.getElementById('upload-wl-upgrade-plan-name');
    if (planNameEl) planNameEl.textContent = offer.planName || '';

    var badgeEl = document.getElementById('upload-wl-upgrade-badge');
    if (badgeEl) badgeEl.textContent = offer.recommendedBadge || t('upgrade.recommended_badge');

    var priceEl = document.getElementById('upload-wl-upgrade-price');
    if (priceEl) priceEl.textContent = offer.priceLabel || '';

    var periodEl = document.getElementById('upload-wl-upgrade-period');
    if (periodEl) periodEl.textContent = offer.perMonthLabel || '';

    var includesLabelEl = document.getElementById('upload-wl-upgrade-includes-label');
    if (includesLabelEl) includesLabelEl.textContent = offer.includesLabel || t('checkout.includes');

    var featureListEl = document.getElementById('upload-wl-upgrade-features');
    if (featureListEl) {
        featureListEl.innerHTML = '';
        (offer.features || []).forEach(function (feature) {
            var li = document.createElement('li');
            li.className = 'flex items-start gap-2';
            li.innerHTML = '<span class="mt-0.5" style="color:#4ade80">&#10003;</span><span></span>';
            li.lastChild.textContent = feature;
            featureListEl.appendChild(li);
        });
    }

    var ctaEl = document.getElementById('upload-wl-upgrade-cta');
    if (ctaEl) ctaEl.setAttribute('href', offer.checkoutUrl || '/pricing');
}

function detectUploadColumns() {
    var fileInput = document.getElementById('upload-wl-file');
    var file = fileInput.files && fileInput.files[0];
    if (!file) {
        showToast(t('watchlist.select_file_first'), 'error');
        return;
    }
    _uploadSelectedFile = file;
    var btn = document.getElementById('upload-wl-detect-btn');
    btn.disabled = true;
    btn.textContent = t('dashboard.loading');

    // Fetch column detection and usage in parallel
    var token = getAuthToken();
    Promise.all([
        AppAPI.detectWatchlistColumns(file),
        fetch('/api/v1/usage/summary', { headers: { 'Authorization': 'Bearer ' + token } }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (results) {
        var data = results[0];
        var usage = results[1];
        _uploadDetectedColumns = data;

        // Parse usage (response is { plan, display_name, usage: { watchlist_items: {...} } })
        var wlUsage = usage && usage.usage && usage.usage.watchlist_items;
        if (wlUsage) {
            _uploadUsage = { used: wlUsage.used || 0, limit: wlUsage.limit || 0 };
        } else {
            _uploadUsage = { used: 0, limit: 999999 };
        }

        renderColumnMapping(data);
        renderUploadUsageInfo(data.total_rows || 0);
        document.getElementById('upload-wl-step-1').classList.add('hidden');
        document.getElementById('upload-wl-step-2').classList.remove('hidden');
    }).catch(function (e) {
        if (e && e.status === 403) {
            showUpgradeModal(e, 'watchlist_items');
            return;
        }
        showToast(t('common.error') + ': ' + e.message, 'error');
    }).finally(function () {
        btn.disabled = false;
        btn.textContent = t('watchlist.detect_columns');
    });
}

function renderUploadUsageInfo(totalRows) {
    var container = document.getElementById('upload-wl-usage');
    if (!container) return;

    var used = _uploadUsage.used;
    var limit = _uploadUsage.limit;
    var remaining = Math.max(0, limit - used);
    var canAdd = Math.min(totalRows, remaining);
    var cannotAdd = totalRows - canAdd;
    var pct = limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0;
    var barColor = pct > 90 ? 'var(--color-risk-critical-text)' : pct > 70 ? '#f59e0b' : '#3b82f6';

    var limitDisplay = limit >= 999999 ? t('common.unlimited') : limit;
    var pctDisplay = limit >= 999999 ? '∞' : pct + '%';

    var html = '<div class="mb-4 p-3 rounded-lg" style="background:var(--color-bg-muted)">'
        + '<div class="flex items-center justify-between text-sm mb-1.5">'
        + '<span style="color:var(--color-text-secondary)">' + t('watchlist.bulk_usage_info', { used: used, limit: limitDisplay }) + '</span>'
        + '<span class="font-mono text-xs" style="color:var(--color-text-faint)">' + totalRows + ' ' + t('watchlist.rows_in_file') + '</span>'
        + '</div>'
        + '<div class="h-2 rounded-full overflow-hidden" style="background:var(--color-bg-card)">'
        + '<div class="h-full rounded-full transition-all" style="width:' + (limit >= 999999 ? '0' : pct) + '%;background:' + barColor + '"></div>'
        + '</div>'
        + '<div class="flex gap-3 mt-2">'
        + '<div class="flex-1 p-2 rounded text-center" style="background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.2)">'
        + '<span class="text-sm font-bold" style="color:#16a34a">' + canAdd + '</span> '
        + '<span class="text-xs" style="color:#16a34a">' + t('watchlist.bulk_can_add', { count: '' }).replace('{count}', '').trim() + '</span>'
        + '</div>';

    if (cannotAdd > 0) {
        html += '<div class="flex-1 p-2 rounded text-center" style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.2)">'
            + '<span class="text-sm font-bold" style="color:#d97706">' + cannotAdd + '</span> '
            + '<span class="text-xs" style="color:#d97706">' + t('watchlist.bulk_cannot_add', { count: '' }).replace('{count}', '').trim() + '</span>'
            + '</div>';
    }

    html += '</div>';

    if (cannotAdd > 0) {
        html += '<p class="text-xs mt-2" style="color:#d97706">'
            + '<svg class="w-3.5 h-3.5 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
            + t('watchlist.upload_limit_warning', { max: remaining })
            + '</p>';
    }

    html += '</div>';
    container.innerHTML = html;
    renderUploadUpgradeOffer(totalRows);
}

function renderColumnMapping(data) {
    var container = document.getElementById('upload-wl-mapping');
    var cols = data.columns || [];
    var auto = data.auto_mappings || {};
    var fields = ['brand_name', 'application_no', 'nice_classes', 'bulletin_no'];
    var fieldLabels = {
        brand_name: t('watchlist.brand_name') + ' *',
        application_no: t('watchlist.application_no'),
        nice_classes: t('watchlist.nice_classes'),
        bulletin_no: t('common.bulletin_label')
    };

    var html = '';
    fields.forEach(function (field) {
        html += '<div class="flex items-center gap-3 mb-2">'
            + '<label class="text-sm w-32 flex-shrink-0" style="color:var(--color-text-secondary)">' + fieldLabels[field] + '</label>'
            + '<select id="upload-map-' + field + '" class="flex-1 px-2 py-1.5 rounded text-sm" style="border:1px solid var(--color-border-input);color:var(--color-text-primary);background:var(--color-bg-input)">'
            + '<option value="">-- ' + t('watchlist.skip_column') + ' --</option>';
        cols.forEach(function (col) {
            var sel = auto[field] === col ? ' selected' : '';
            html += '<option value="' + escapeHtml(col) + '"' + sel + '>' + escapeHtml(col) + '</option>';
        });
        html += '</select></div>';
    });

    if (data.sample_data && data.sample_data.length > 0) {
        html += '<div class="mt-3 text-xs" style="color:var(--color-text-faint)">' + t('watchlist.preview_rows', { count: data.sample_data.length }) + '</div>';
    }

    container.innerHTML = html;
}

function isUploadLimitOnlyResult(data) {
    var summary = data && data.summary ? data.summary : {};
    var errors = Number(summary.errors || 0);
    if (errors <= 0) return false;

    var errorItems = Array.isArray(data && data.error_items) ? data.error_items : [];
    if (!errorItems.length) return false;

    return errorItems.every(function (item) {
        var message = item && item.error ? String(item.error).toLowerCase() : '';
        return message.indexOf('izleme listesi limiti asildi') >= 0
            || message.indexOf('watchlist limit') >= 0;
    });
}

function submitBulkUpload() {
    if (!_uploadSelectedFile) return;

    var mapping = {};
    ['brand_name', 'application_no', 'nice_classes', 'bulletin_no'].forEach(function (field) {
        var sel = document.getElementById('upload-map-' + field);
        if (sel && sel.value) mapping[field] = sel.value;
    });

    // Validate required fields (at minimum brand_name)
    if (!mapping.brand_name) {
        showToast(t('watchlist.mapping_required_brand'), 'error');
        return;
    }

    var btn = document.getElementById('upload-wl-submit-btn');
    btn.disabled = true;

    AppAPI.uploadWatchlistFile(_uploadSelectedFile, mapping).then(function (data) {
        var s = data.summary || {};
        var limitOnlyResult = isUploadLimitOnlyResult(data);
        var resultEl = document.getElementById('upload-wl-result');
        resultEl.innerHTML = '<div class="text-center py-3">'
            + '<svg class="w-8 h-8 mx-auto mb-2 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
            + '<p class="font-medium" style="color:var(--color-text-primary)">' + t('watchlist.upload_success', { created: s.added || 0, total: s.total_rows || 0 }) + '</p>'
            + (s.skipped > 0 ? '<p class="text-xs mt-1" style="color:var(--color-text-faint)">' + s.skipped + ' ' + t('watchlist.upload_skipped') + '</p>' : '')
            + (s.errors > 0 ? '<p class="text-xs mt-1" style="color:var(--color-risk-high-text)">' + s.errors + ' ' + t(limitOnlyResult ? 'watchlist.upload_limit_result' : 'watchlist.upload_errors') + '</p>' : '')
            + '</div>';
        document.getElementById('upload-wl-step-2').classList.add('hidden');
        resultEl.classList.remove('hidden');
        var queuedScans = _coerceQueuedScanCount(data, s.added || 0);
        if (queuedScans > 0 && typeof _setScanInProgress === 'function' && typeof _getScanDurationMs === 'function') {
            _setScanInProgress(_getScanDurationMs(queuedScans), 'bulk');
        }
        refreshWatchlistAndStats();
        // Land the user on the Watchlist tab so they see the new rows when
        // they close the upload modal. Don't auto-close the modal — they
        // may want to read the skipped / error counts.
        if ((s.added || 0) > 0 && typeof showDashboardTab === 'function') {
            showDashboardTab('watchlist');
        }
    }).catch(function (e) {
        if (e && e.status === 403) {
            var stepTwoVisible = !document.getElementById('upload-wl-step-2').classList.contains('hidden');
            var totalRows = _uploadDetectedColumns && _uploadDetectedColumns.total_rows ? _uploadDetectedColumns.total_rows : 0;
            if (stepTwoVisible && totalRows > 0) {
                renderUploadUpgradeOffer(totalRows, e);
                showToast((e && e.message) || t('watchlist.plan_limit'), 'error');
                return;
            }
            showUpgradeModal(e, 'watchlist_items');
            return;
        }
        showToast(t('common.error') + ': ' + e.message, 'error');
    }).finally(function () {
        btn.disabled = false;
    });
}

// ============================================
// REPORTS TAB
// ============================================
window._reportsInitialized = false;
var _reportsCurrentPage = 1;
window._pendingRiskReportClaims = window._pendingRiskReportClaims || {};

function claimPendingRiskReport(claimToken) {
    if (!claimToken || window._pendingRiskReportClaims[claimToken]) return;
    window._pendingRiskReportClaims[claimToken] = true;
    if (typeof showDashboardTab === 'function') {
        showDashboardTab('reports');
    }

    var claimFn = typeof claimRiskReportAPI === 'function'
        ? claimRiskReportAPI
        : (window.AppAPI && window.AppAPI.claimRiskReport);
    if (typeof claimFn !== 'function') {
        showToast(t('search.risk_report_failed'), 'error');
        delete window._pendingRiskReportClaims[claimToken];
        return;
    }

    claimFn(claimToken).then(function () {
        showToast(t('search.risk_report_ready_view'), 'success');
        window._reportsInitialized = false;
        _reportsCurrentPage = 1;
        loadReportsTab();
    }).catch(function (err) {
        var detail = err && err.data && (err.data.detail || err.data);
        if (err && (err.status === 402 || err.status === 403)) {
            showUpgradeModal(detail || err, 'reports');
            return;
        }
        if (err && err.status === 409) {
            showToast((detail && detail.message) || t('search.risk_report_ready'), 'success');
            window._reportsInitialized = false;
            loadReportsTab();
            return;
        }
        showToast(t('search.risk_report_failed') + ': ' + ((detail && detail.message) || (err && err.message) || ''), 'error');
    }).finally(function () {
        delete window._pendingRiskReportClaims[claimToken];
    });
}

function loadReportsTab() {
    var loading = document.getElementById('reports-loading');
    var list = document.getElementById('reports-list');
    var empty = document.getElementById('reports-empty');
    var pagination = document.getElementById('reports-pagination');
    var upgradePrompt = document.getElementById('reports-upgrade-prompt');

    loading.classList.remove('hidden');
    list.innerHTML = '';
    empty.classList.add('hidden');
    pagination.classList.add('hidden');
    upgradePrompt.classList.add('hidden');

    loadReportsAPI(1).then(function (data) {
        loading.classList.add('hidden');
        _reportsCurrentPage = data.page || 1;

        renderReportsList(data);
    }).catch(function (err) {
        loading.classList.add('hidden');
        if (err.status === 403) {
            upgradePrompt.classList.remove('hidden');
        } else {
            showToast(t('reports.load_failed'), 'error');
        }
    });
}

function renderReportsList(data) {
    var list = document.getElementById('reports-list');
    var empty = document.getElementById('reports-empty');
    var reports = data.reports || [];

    if (reports.length === 0) {
        empty.classList.remove('hidden');
        list.innerHTML = '';
        return;
    }

    empty.classList.add('hidden');

    var typeLabels = {
        'weekly_digest': t('reports.type_weekly'),
        'monthly_summary': t('reports.type_monthly'),
        'watchlist_status': t('reports.type_portfolio'),
        'watchlist_summary': t('reports.type_weekly'),
        'alert_digest': t('reports.type_monthly'),
        'risk_assessment': t('reports.type_risk_assessment'),
        'competitor_analysis': t('reports.type_competitor_analysis'),
        'portfolio_status': t('reports.type_portfolio'),
        'single_trademark': t('reports.type_single'),
        'full_portfolio': t('reports.type_full'),
        'custom': t('reports.type_custom')
    };

    var html = '';
    reports.forEach(function (report) {
        var typeLabel = typeLabels[report.report_type] || report.report_type;
        var title = escapeHtml(report.title || typeLabel);
        var encodedId = encodeURIComponent(report.id || '');
        var encodedTitle = encodeURIComponent(report.title || typeLabel || '');
        var dateStr = report.created_at ? formatReportDate(report.created_at) : '-';

        var statusBadge = '';
        var downloadBtn = '';
        var deleteBtn = '<button onclick="deleteReport(decodeURIComponent(\'' + encodedId + '\'), decodeURIComponent(\'' + encodedTitle + '\'))" '
            + 'title="' + escapeHtml(t('reports.delete_report')) + '" '
            + 'class="p-2 bg-red-50 hover:bg-red-100 text-red-600 rounded-lg transition-colors flex items-center justify-center" '
            + 'aria-label="' + escapeHtml(t('reports.delete_report')) + '">'
            + '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3m-9 0h10"/>'
            + '</svg></button>';
        if (report.status === 'completed') {
            statusBadge = '<span class="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">' + t('reports.status_completed') + '</span>';
            downloadBtn = '<button onclick="handleReportDownload(\'' + report.id + '\')" '
                + 'class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded-lg transition-colors flex items-center gap-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
                + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>'
                + '</svg> ' + t('reports.download_btn') + '</button>';
        } else if (report.status === 'generating' || report.status === 'pending') {
            statusBadge = '<span class="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full font-medium">' + t('reports.status_generating') + '</span>';
        } else if (report.status === 'failed') {
            statusBadge = '<span class="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-medium">' + t('reports.status_failed_label') + '</span>';
        }

        var sizeStr = '';
        if (report.file_size_bytes) {
            sizeStr = '<span class="text-xs text-gray-400 ml-2">' + formatFileSize(report.file_size_bytes) + '</span>';
        }

        html += '<div class="bg-white rounded-xl p-4 border border-gray-100 shadow-sm flex items-center gap-4">'
            + '<div class="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">'
            + '<svg class="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>'
            + '</svg></div>'
            + '<div class="flex-1 min-w-0">'
            + '<div class="font-medium text-gray-900 truncate">' + title + '</div>'
            + '<div class="text-sm text-gray-500">' + dateStr + ' &bull; ' + escapeHtml(report.file_format || 'pdf').toUpperCase() + sizeStr + '</div>'
            + '</div>'
            + '<div class="flex items-center gap-3 flex-shrink-0">'
            + statusBadge
            + downloadBtn
            + deleteBtn
            + '</div></div>';
    });

    list.innerHTML = html;
    renderReportsPagination(data);
}

function renderReportsPagination(data) {
    var container = document.getElementById('reports-pagination');
    var totalPages = data.total_pages || 1;
    var page = data.page || 1;

    if (totalPages <= 1) {
        container.classList.add('hidden');
        container.innerHTML = '';
        return;
    }

    container.classList.remove('hidden');
    var html = '<button onclick="navigateReportsPage(' + (page - 1) + ')" '
        + 'class="px-4 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg disabled:opacity-50 text-sm" '
        + (page === 1 ? 'disabled' : '') + '>' + t('pagination.prev') + '</button>'
        + '<span class="text-gray-500 text-sm">' + t('pagination.page_of', { current: page, total: totalPages }) + '</span>'
        + '<button onclick="navigateReportsPage(' + (page + 1) + ')" '
        + 'class="px-4 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg disabled:opacity-50 text-sm" '
        + (page === totalPages ? 'disabled' : '') + '>' + t('pagination.next') + '</button>';
    container.innerHTML = html;
}

function navigateReportsPage(page) {
    if (page < 1) return;
    var loading = document.getElementById('reports-loading');
    loading.classList.remove('hidden');

    loadReportsAPI(page).then(function (data) {
        loading.classList.add('hidden');
        _reportsCurrentPage = data.page || page;
        renderReportsList(data);
    }).catch(function () {
        loading.classList.add('hidden');
        showToast(t('reports.load_failed'), 'error');
    });
}

function formatReportDate(isoStr) {
    if (!isoStr) return '-';
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    var day = String(d.getDate()).padStart(2, '0');
    var month = String(d.getMonth() + 1).padStart(2, '0');
    var year = d.getFullYear();
    return day + '.' + month + '.' + year;
}

function formatFileSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function deleteReport(reportId, reportTitle) {
    if (!reportId) return;
    var title = reportTitle || t('reports.untitled');
    if (!window.confirm(t('reports.delete_confirm', { title: title }))) return;

    var deleteFn = typeof deleteReportAPI === 'function'
        ? deleteReportAPI
        : (window.AppAPI && window.AppAPI.deleteReport);
    if (typeof deleteFn !== 'function') {
        showToast(t('reports.delete_failed'), 'error');
        return;
    }

    deleteFn(reportId).then(function () {
        showToast(t('reports.delete_success'), 'success');
        loadReportsTab();
    }).catch(function (err) {
        showToast(t('reports.delete_failed') + ': ' + ((err && err.message) || ''), 'error');
    });
}

function deleteAllReports() {
    if (!window.confirm(t('reports.delete_all_confirm'))) return;

    var deleteFn = typeof deleteAllReportsAPI === 'function'
        ? deleteAllReportsAPI
        : (window.AppAPI && window.AppAPI.deleteAllReports);
    if (typeof deleteFn !== 'function') {
        showToast(t('reports.delete_all_failed'), 'error');
        return;
    }

    deleteFn().then(function (data) {
        showToast(t('reports.delete_all_success', { count: data.deleted_count || 0 }), 'success');
        _reportsCurrentPage = 1;
        loadReportsTab();
    }).catch(function (err) {
        showToast(t('reports.delete_all_failed') + ': ' + ((err && err.message) || ''), 'error');
    });
}

// ============================================
// REPORT GENERATION MODAL
// ============================================
function showReportGenerateModal() {
    document.getElementById('report-generate-modal').classList.remove('hidden');
    lockBodyScroll();
}

function hideReportGenerateModal() {
    document.getElementById('report-generate-modal').classList.add('hidden');
    unlockBodyScroll();
    document.getElementById('reportTypeSelect').selectedIndex = 0;
    document.getElementById('reportTitleInput').value = '';
    document.getElementById('reportFormatSelect').selectedIndex = 0;
    document.getElementById('reportStartDate').value = '';
    document.getElementById('reportEndDate').value = '';
}

function submitReportGeneration() {
    var reportType = document.getElementById('reportTypeSelect').value;
    var title = (document.getElementById('reportTitleInput').value || '').trim();
    var fileFormat = document.getElementById('reportFormatSelect').value;
    var periodStart = document.getElementById('reportStartDate').value || null;
    var periodEnd = document.getElementById('reportEndDate').value || null;

    if (!title) {
        var typeNames = {
            'watchlist_summary': t('reports.type_weekly'),
            'alert_digest': t('reports.type_monthly'),
            'portfolio_status': t('reports.type_portfolio'),
            'risk_assessment': t('reports.type_risk_assessment'),
            'competitor_analysis': t('reports.type_full')
        };
        title = (typeNames[reportType] || t('reports.title')) + ' - ' + formatReportDate(new Date().toISOString());
    }

    var btn = document.getElementById('reportSubmitBtn');
    btn.disabled = true;
    btn.textContent = t('reports.creating');

    var payload = {
        report_type: reportType,
        title: title,
        file_format: fileFormat
    };
    if (periodStart) payload.period_start = periodStart;
    if (periodEnd) payload.period_end = periodEnd;

    generateReport(payload).then(function () {
        showToast(t('reports.created_toast'), 'success');
        hideReportGenerateModal();
        window._reportsInitialized = false;
        loadReportsTab();
    }).catch(function (err) {
        if (err.status === 402 || err.status === 403) {
            showUpgradeModal(err, 'reports');
        } else {
            showToast(t('reports.generate_failed') + ': ' + err.message, 'error');
        }
    }).finally(function () {
        btn.disabled = false;
        btn.textContent = t('reports.create');
    });
}

// ============================================
// REPORT DOWNLOAD
// ============================================
function handleReportDownload(reportId) {
    downloadReportAPI(reportId).then(function (blob) {
        var filename = blob._filename || 'rapor.pdf';
        var url = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    }).catch(function (err) {
        showToast(t('reports.download_failed') + ': ' + err.message, 'error');
    });
}

// ============================================
// EXTRACTED GOODS (Cikarilmis Urunler)
// ============================================

async function showExtractedGoods(applicationNo, buttonElement) {
    // Toggle existing panel
    var existingPanel = document.getElementById('extracted-goods-' + applicationNo.replace(/\//g, '_'));
    if (existingPanel) {
        existingPanel.classList.toggle('hidden');
        return;
    }

    // Loading state
    var originalText = buttonElement.innerHTML;
    buttonElement.innerHTML = '<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg> ' + t('common.loading');
    buttonElement.disabled = true;

    try {
        var data = await loadExtractedGoods(applicationNo);

        if (!data.has_extracted_goods || !data.extracted_goods || data.extracted_goods.length === 0) {
            showToast(t('extracted_goods.not_found'), 'info');
            return;
        }

        var panelHtml = renderExtractedGoodsPanel(applicationNo, data);
        var panelDiv = document.createElement('div');
        panelDiv.id = 'extracted-goods-' + applicationNo.replace(/\//g, '_');
        panelDiv.innerHTML = panelHtml;

        // Insert after the button's closest card-level container
        var cardContainer = buttonElement.closest('.bg-white') || buttonElement.closest('[class*="rounded"]') || buttonElement.parentElement.parentElement;
        cardContainer.appendChild(panelDiv);

    } catch (err) {
        showToast(t('extracted_goods.load_failed'), 'error');
        console.error('Extracted goods load error:', err);
    } finally {
        buttonElement.innerHTML = originalText;
        buttonElement.disabled = false;
    }
}

function renderExtractedGoodsPanel(applicationNo, data) {
    var items = data.extracted_goods;
    var safeId = applicationNo.replace(/\//g, '_');
    var itemsHtml = '';

    // Real structure: [{CLASSID: "98", SUBCLASSID: "98", TEXT: "...", SEQ: n}]
    if (items.length > 0 && typeof items[0] === 'object') {
        items.forEach(function (item, idx) {
            var text = item.TEXT || item.text || '';
            if (!text) return;

            // Split TEXT by sub-class patterns (e.g. "06.01 ...; 06.02 ...")
            // Each TEXT may contain multiple sub-class entries separated by NN.MM patterns
            var subEntries = text.split(/(?=\d{2}\.\d{2}\s)/);

            subEntries.forEach(function (entry, subIdx) {
                entry = entry.trim();
                if (!entry) return;

                // Extract class number prefix if present (e.g. "06.01")
                var classMatch = entry.match(/^(\d{2}\.\d{2})\s+(.*)$/s);
                var classLabel = classMatch ? classMatch[1] : '';
                var description = classMatch ? classMatch[2] : entry;

                // Truncate very long descriptions for display
                var displayText = description.length > 500
                    ? description.substring(0, 500) + '...'
                    : description;

                itemsHtml += '<div class="flex items-start gap-2 py-2'
                    + ((idx > 0 || subIdx > 0) ? ' border-t border-amber-200' : '') + '">'
                    + (classLabel
                        ? '<span class="flex-shrink-0 px-1.5 py-0.5 rounded bg-amber-500 text-white text-xs font-mono font-bold mt-0.5">' + escapeHtml(classLabel) + '</span>'
                        : '<span class="flex-shrink-0 w-5 h-5 rounded-full bg-amber-500 text-white text-xs flex items-center justify-center mt-0.5">' + (subIdx + 1) + '</span>')
                    + '<div class="text-sm text-gray-800 leading-relaxed">' + escapeHtml(displayText) + '</div>'
                    + '</div>';
            });
        });
    } else if (items.length > 0 && typeof items[0] === 'string') {
        items.forEach(function (text, idx) {
            itemsHtml += '<div class="flex items-start gap-2 py-2'
                + (idx > 0 ? ' border-t border-amber-200' : '') + '">'
                + '<span class="flex-shrink-0 w-5 h-5 rounded-full bg-amber-500 text-white text-xs flex items-center justify-center mt-0.5">' + (idx + 1) + '</span>'
                + '<div class="text-sm text-gray-800">' + escapeHtml(text) + '</div>'
                + '</div>';
        });
    } else {
        itemsHtml = '<pre class="text-xs text-gray-600 whitespace-pre-wrap">' + escapeHtml(JSON.stringify(items, null, 2)) + '</pre>';
    }

    return '<div class="mt-2 rounded-lg border border-amber-300 bg-amber-50 overflow-hidden">'
        + '<div class="px-3 py-2 bg-amber-100 border-b border-amber-300 flex items-center justify-between">'
        + '<div class="flex items-center gap-2">'
        + '<svg class="w-4 h-4 text-amber-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>'
        + '</svg>'
        + '<span class="text-sm font-semibold text-amber-800">' + t('extracted_goods.title') + '</span>'
        + '<span class="text-xs text-amber-600">' + t('extracted_goods.records_count', { count: items.length }) + '</span>'
        + '</div>'
        + '<button onclick="document.getElementById(\'extracted-goods-' + safeId + '\').classList.add(\'hidden\')" '
        + 'class="text-amber-600 hover:text-amber-800 text-sm font-bold px-1">&times;</button>'
        + '</div>'
        + '<div class="px-3 py-2 text-xs text-amber-700 bg-amber-50 border-b border-amber-200">'
        + t('extracted_goods.description')
        + '</div>'
        + '<div class="px-3 py-2 max-h-60 overflow-y-auto">'
        + itemsHtml
        + '</div></div>';
}


// ============================================
// EVENTS TIMELINE
// ============================================

async function showEventsTimeline(applicationNo) {
    var modal = document.getElementById('events-timeline-modal');
    var body = document.getElementById('events-modal-body');
    var titleEl = document.getElementById('events-modal-title');
    var appNoEl = document.getElementById('events-modal-app-no');

    modal.classList.remove('hidden');
    titleEl.textContent = t('events.title');
    appNoEl.textContent = applicationNo;
    body.innerHTML = '<div class="text-center py-8" style="color:var(--color-text-muted)">'
        + '<div class="animate-spin w-8 h-8 border-2 border-indigo-300 border-t-indigo-600 rounded-full mx-auto mb-3"></div>'
        + t('events.loading') + '</div>';

    try {
        var res = await fetch('/api/v1/trademark/' + encodeURIComponent(applicationNo) + '/events', {
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') }
        });
        if (!res.ok) throw new Error(res.statusText);
        var data = await res.json();

        if (!data.events || data.events.length === 0) {
            body.innerHTML = '<div class="text-center py-12" style="color:var(--color-text-muted)">'
                + '<svg class="w-12 h-12 mx-auto mb-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
                + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>'
                + '</svg>' + t('events.no_events') + '</div>';
            return;
        }

        titleEl.textContent = (data.name || '') + ' — ' + t('events.title');
        body.innerHTML = renderHealthCard(data.health_card) + renderEventsTimeline(data.events, data.total, data.page, data.pages, applicationNo);

    } catch (err) {
        console.error('Events load error:', err);
        body.innerHTML = '<div class="text-center py-8 text-red-500">' + t('events.load_failed') + '</div>';
    }
}

function hideEventsTimeline() {
    document.getElementById('events-timeline-modal').classList.add('hidden');
}

function renderHealthCard(hc) {
    if (!hc) return '';

    var severityColors = {
        healthy: 'background:var(--color-risk-low-bg);border-color:var(--color-risk-low-border);color:var(--color-risk-low-text)',
        warning: 'background:var(--color-risk-high-bg);border-color:var(--color-risk-high-border);color:var(--color-risk-high-text)',
        critical: 'background:var(--color-risk-critical-bg);border-color:var(--color-risk-critical-border);color:var(--color-risk-critical-text)'
    };
    var severityStyle = severityColors[hc.severity] || severityColors.healthy;
    var severityLabel = t('events.severity_' + hc.severity) || hc.severity;

    var html = '<div class="rounded-xl border p-4 mb-4" style="' + severityStyle + '">'
        + '<div class="flex items-center justify-between mb-3">'
        + '<div class="flex items-center gap-2">'
        + '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>'
        + '</svg>'
        + '<span class="font-bold text-sm">' + t('events.health_card') + '</span>'
        + '</div>'
        + '<span class="text-xs font-semibold px-2.5 py-1 rounded-full" style="' + severityStyle + '">' + severityLabel + '</span>'
        + '</div>';

    // Status row
    if (hc.effective_status) {
        html += '<div class="flex justify-between text-sm mb-1">'
            + '<span class="opacity-80">' + t('events.effective_status') + '</span>'
            + '<span class="font-semibold">' + escapeHtml(hc.effective_status) + '</span></div>';
    }

    // Holder change
    if (hc.holder_changed && hc.current_holder_name) {
        html += '<div class="flex justify-between text-sm mb-1">'
            + '<span class="opacity-80">' + t('events.current_holder') + '</span>'
            + '<span class="font-semibold">' + escapeHtml(hc.current_holder_name) + '</span></div>';
        if (hc.holder_changed_at) {
            html += '<div class="flex justify-between text-xs mb-1 opacity-70">'
                + '<span>' + t('events.holder_changed_at') + '</span>'
                + '<span>' + hc.holder_changed_at + '</span></div>';
        }
    }

    // Restrictions
    if (hc.active_restriction_count > 0) {
        html += '<div class="flex justify-between text-sm mb-1">'
            + '<span class="opacity-80">' + t('events.restrictions') + '</span>'
            + '<span class="font-bold">' + hc.active_restriction_count + ' ' + t('events.active_restrictions') + '</span></div>';
    }

    // Renewal/expiry
    if (hc.renewal_expiry) {
        html += '<div class="flex justify-between text-sm mb-1">'
            + '<span class="opacity-80">' + t('events.renewal_expiry') + '</span>'
            + '<span class="font-semibold">' + hc.renewal_expiry + '</span></div>';
    }

    // Last event + total
    if (hc.last_event_type_label) {
        html += '<div class="flex justify-between text-sm mb-1">'
            + '<span class="opacity-80">' + t('events.last_event') + '</span>'
            + '<span class="font-semibold">' + escapeHtml(hc.last_event_type_label) + (hc.last_event_date ? ' (' + hc.last_event_date + ')' : '') + '</span></div>';
    }

    html += '<div class="flex justify-between text-sm">'
        + '<span class="opacity-80">' + t('events.total_events') + '</span>'
        + '<span class="font-semibold">' + (hc.total_event_count || 0) + '</span></div>';

    html += '</div>';
    return html;
}

function renderEventsTimeline(events, total, page, pages, applicationNo) {
    var html = '<div class="space-y-3">';

    for (var i = 0; i < events.length; i++) {
        var ev = events[i];
        var severityDot = {
            critical: 'bg-red-500',
            warning: 'bg-amber-500',
            info: 'bg-blue-400'
        };
        var dotClass = severityDot[ev.severity] || 'bg-gray-400';
        var typeLabel = t('events.type_' + ev.event_type) || ev.event_type_label || ev.event_type;
        var sourceLabel = ev.source_type === 'GZ' ? t('events.source_gz') : t('events.source_blt');

        html += '<div class="flex gap-3">'
            + '<div class="flex flex-col items-center">'
            + '<div class="w-3 h-3 rounded-full mt-1.5 ' + dotClass + '"></div>'
            + (i < events.length - 1 ? '<div class="w-0.5 flex-1 bg-gray-200 mt-1"></div>' : '')
            + '</div>'
            + '<div class="flex-1 pb-3">'
            + '<div class="flex items-center gap-2 flex-wrap">'
            + '<span class="text-sm font-semibold" style="color:var(--color-text-primary)">' + escapeHtml(typeLabel) + '</span>'
            + '<span class="text-xs px-1.5 py-0.5 rounded" style="background:var(--color-bg-muted);color:var(--color-text-muted)">' + sourceLabel + ' #' + escapeHtml(ev.bulletin_no || '') + '</span>'
            + (ev.bulletin_date ? '<span class="text-xs" style="color:var(--color-text-faint)">' + ev.bulletin_date + '</span>' : '')
            + '</div>';

        // Old/new values
        if (ev.old_value || ev.new_value) {
            html += '<div class="mt-1 text-xs" style="color:var(--color-text-secondary)">';
            if (ev.old_value) {
                html += '<div><span class="opacity-60">' + t('events.old_value') + ':</span> ' + escapeHtml(ev.old_value.substring(0, 200)) + '</div>';
            }
            if (ev.new_value) {
                html += '<div><span class="opacity-60">' + t('events.new_value') + ':</span> ' + escapeHtml(ev.new_value.substring(0, 200)) + '</div>';
            }
            html += '</div>';
        }

        html += '</div></div>';
    }

    html += '</div>';

    // Pagination
    if (pages > 1) {
        html += '<div class="text-center mt-4">'
            + '<span class="text-xs" style="color:var(--color-text-muted)">' + page + ' / ' + pages + ' (' + total + ' ' + t('events.total_events').toLowerCase() + ')</span>';
        if (page < pages) {
            html += ' <button onclick="loadMoreEvents(\'' + applicationNo.replace(/'/g, "\\'") + '\', ' + (page + 1) + ')" '
                + 'class="ml-2 text-xs text-indigo-600 hover:text-indigo-800 font-semibold">' + t('events.show_more') + '</button>';
        }
        html += '</div>';
    }

    return html;
}

async function loadMoreEvents(applicationNo, page) {
    try {
        var res = await fetch('/api/v1/trademark/' + encodeURIComponent(applicationNo) + '/events?page=' + page, {
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('token') || '') }
        });
        if (!res.ok) throw new Error(res.statusText);
        var data = await res.json();

        var body = document.getElementById('events-modal-body');
        // Append new events to existing timeline
        var timelineContainer = body.querySelector('.space-y-3');
        if (timelineContainer && data.events) {
            var tempDiv = document.createElement('div');
            tempDiv.innerHTML = renderEventsTimeline(data.events, data.total, data.page, data.pages, applicationNo);
            var newItems = tempDiv.querySelector('.space-y-3');
            if (newItems) {
                timelineContainer.innerHTML += newItems.innerHTML;
            }
            // Update pagination at bottom
            var oldPagination = body.querySelector('.text-center.mt-4');
            if (oldPagination) oldPagination.remove();
            var newPagination = tempDiv.querySelector('.text-center.mt-4');
            if (newPagination) body.appendChild(newPagination);
        }
    } catch (err) {
        console.error('Load more events error:', err);
    }
}

// ============================================
// APPLICATIONS TAB
// ============================================

function _appFetch(url, opts) {
    var token = getAuthToken();
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (token) opts.headers['Authorization'] = 'Bearer ' + token;
    return fetch(url, opts);
}

function _appErrorFromResponse(response, fallbackMessageKey) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
        payload = payload || {};
        payload.status = response.status;
        if (!payload.detail && fallbackMessageKey) payload.detail = t(fallbackMessageKey);
        throw payload;
    });
}

function _applicationErrorMessage(err, fallbackMessageKey) {
    var msg = (err && err.detail) || t(fallbackMessageKey);
    if (typeof msg === 'object' && msg.message) {
        var fields = (msg.fields || []).join(', ');
        msg = msg.message + (fields ? ': ' + fields : '');
    }
    return typeof msg === 'string' ? msg : JSON.stringify(msg);
}

var _applicationsState = {
    initialized: false,
    filter: null,
    typeFilter: null,
    page: 1,
    pageSize: 20,
    selectedClasses: [],
    logoFile: null,
    editingId: null,
    appealContext: null  // stores conflict details when type=appeal
};

function initApplicationsTab() {
    if (!_applicationsState.initialized) {
        _applicationsState.initialized = true;
        // Populate Nice class dropdown
        var select = document.getElementById('app-nice-class-select');
        if (select && select.options.length <= 1) {
            for (var i = 1; i <= 45; i++) {
                var opt = document.createElement('option');
                opt.value = i;
                opt.textContent = t('nice_class_names.' + i) || ('Class ' + i);
                select.appendChild(opt);
            }
        }
    }
    loadApplicationsList();
}

function filterApplications(status) {
    _applicationsState.filter = status;
    _applicationsState.page = 1;
    // Update filter button styles
    document.querySelectorAll('.app-filter-btn').forEach(function (btn) {
        btn.classList.remove('bg-indigo-600', 'text-white');
        btn.style.color = 'var(--color-text-muted)';
        btn.style.background = 'var(--color-bg-muted)';
    });
    var activeId = 'app-filter-' + (status || 'all');
    var activeBtn = document.getElementById(activeId);
    if (activeBtn) {
        activeBtn.classList.add('bg-indigo-600', 'text-white');
        activeBtn.style.color = '';
        activeBtn.style.background = '';
    }
    loadApplicationsList();
}

function filterApplicationsByType(appType) {
    _applicationsState.typeFilter = appType;
    _applicationsState.page = 1;
    // Update type filter button styles
    document.querySelectorAll('.app-type-btn').forEach(function (btn) {
        btn.classList.remove('bg-indigo-600', 'text-white');
        btn.style.color = 'var(--color-text-muted)';
        btn.style.background = 'var(--color-bg-muted)';
    });
    var activeId = 'app-type-' + (appType || 'all');
    var activeBtn = document.getElementById(activeId);
    if (activeBtn) {
        activeBtn.classList.add('bg-indigo-600', 'text-white');
        activeBtn.style.color = '';
        activeBtn.style.background = '';
    }
    loadApplicationsList();
}

function loadApplicationsList() {
    var listEl = document.getElementById('applications-list');
    var loadingEl = document.getElementById('applications-loading');
    var emptyEl = document.getElementById('applications-empty');
    var paginationEl = document.getElementById('applications-pagination');

    listEl.innerHTML = '';
    loadingEl.classList.remove('hidden');
    emptyEl.classList.add('hidden');
    paginationEl.classList.add('hidden');

    var url = '/api/v1/applications/?page=' + _applicationsState.page + '&page_size=' + _applicationsState.pageSize;
    if (_applicationsState.filter) url += '&status=' + _applicationsState.filter;
    if (_applicationsState.typeFilter) url += '&application_type=' + _applicationsState.typeFilter;

    _appFetch(url)
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (data) {
            loadingEl.classList.add('hidden');
            if (!data.items || data.items.length === 0) {
                emptyEl.classList.remove('hidden');
                return;
            }
            data.items.forEach(function (app) {
                listEl.innerHTML += renderApplicationCard(app);
            });
            // Pagination
            if (data.total_pages > 1) {
                paginationEl.classList.remove('hidden');
                paginationEl.innerHTML = '';
                if (_applicationsState.page > 1) {
                    paginationEl.innerHTML += '<button onclick="loadApplicationsPage(' + (_applicationsState.page - 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">&laquo; ' + t('applications.prev') + '</button>';
                }
                paginationEl.innerHTML += '<span class="text-sm" style="color:var(--color-text-muted)">' + _applicationsState.page + ' / ' + data.total_pages + '</span>';
                if (_applicationsState.page < data.total_pages) {
                    paginationEl.innerHTML += '<button onclick="loadApplicationsPage(' + (_applicationsState.page + 1) + ')" class="px-3 py-1.5 rounded-lg text-sm btn-press" style="background:var(--color-bg-muted);color:var(--color-text-secondary)">' + t('applications.next') + ' &raquo;</button>';
                }
            }
        })
        .catch(function (err) {
            loadingEl.classList.add('hidden');
            console.error('Failed to load applications:', err);
            listEl.innerHTML = '<p class="text-center py-8" style="color:var(--color-text-faint)">' + t('applications.load_error') + '</p>';
        });
}

function loadApplicationsPage(page) {
    _applicationsState.page = page;
    loadApplicationsList();
}

function renderApplicationCard(app) {
    var statusColors = {
        'draft': 'bg-gray-100 text-gray-700',
        'submitted': 'bg-blue-100 text-blue-700',
        'under_review': 'bg-yellow-100 text-yellow-700',
        'approved': 'bg-green-100 text-green-700',
        'rejected': 'bg-red-100 text-red-700',
        'completed': 'bg-emerald-100 text-emerald-700'
    };
    var statusLabel = t('applications.status_' + app.status) || app.status;
    var statusClass = statusColors[app.status] || 'bg-gray-100 text-gray-700';
    var classesStr = (app.nice_class_numbers || []).map(function (c) { return c; }).join(', ');
    var dateStr = app.created_at ? new Date(app.created_at).toLocaleDateString() : '';
    var isDraft = app.status === 'draft';

    var actions = '';
    if (isDraft) {
        actions = '<div class="flex items-center gap-2 mt-3">'
            + '<button onclick="editApplication(\'' + app.id + '\')" class="text-xs text-indigo-600 hover:text-indigo-800 font-medium flex items-center gap-1 btn-press">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>'
            + t('applications.edit') + '</button>'
            + '<button onclick="deleteApplication(\'' + app.id + '\')" class="text-xs text-red-500 hover:text-red-700 font-medium flex items-center gap-1 btn-press">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>'
            + t('applications.delete') + '</button>'
            + '</div>';
    }

    var typeColors = {
        'registration': 'bg-blue-50 text-blue-600',
        'appeal': 'bg-orange-50 text-orange-600',
        'renewal': 'bg-purple-50 text-purple-600'
    };
    var appTypeLabel = t('applications.type_' + (app.application_type || 'registration')) || app.application_type;
    var appTypeClass = typeColors[app.application_type] || 'bg-blue-50 text-blue-600';

    return '<div class="rounded-xl p-4 transition-all hover:shadow-md" style="background:var(--color-bg-card);border:1px solid var(--color-border)">'
        + '<div class="flex items-start justify-between gap-3">'
        + '<div class="flex-1 min-w-0">'
        + '<div class="flex items-center gap-2 mb-1">'
        + '<h4 class="font-semibold truncate" style="color:var(--color-text-primary)">' + (app.brand_name || '-') + '</h4>'
        + '<span class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ' + statusClass + '">' + statusLabel + '</span>'
        + '<span class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ' + appTypeClass + '">' + appTypeLabel + '</span>'
        + '</div>'
        + '<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs" style="color:var(--color-text-muted)">'
        + (classesStr ? '<span>' + t('applications.classes') + ': ' + classesStr + '</span>' : '')
        + '<span>' + dateStr + '</span>'
        + (app.mark_type ? '<span>' + t('applications.type_' + app.mark_type) + '</span>' : '')
        + '</div>'
        + actions
        + '</div>'
        + (app.has_logo ? '<img src="' + app.logo_url + '" class="w-12 h-12 rounded-lg object-cover flex-shrink-0" alt="Logo">' : '')
        + '</div>'
        + '</div>';
}

function showApplicationForm(existingData) {
    document.getElementById('applications-list-view').classList.add('hidden');
    document.getElementById('applications-form-view').classList.remove('hidden');

    // Reset form (preserve appealContext if it was set by openItirazForm before this call)
    var _preservedAppealContext = _applicationsState.appealContext;
    _applicationsState.selectedClasses = [];
    _applicationsState.logoFile = null;
    _applicationsState.editingId = null;
    _applicationsState.appealContext = _preservedAppealContext;
    document.getElementById('app-form-id').value = '';
    document.getElementById('app-brand-name').value = '';
    document.getElementById('app-application-type').value = 'registration';
    document.getElementById('app-mark-type').value = 'word';
    document.getElementById('app-goods-services').value = '';
    document.getElementById('app-notes').value = '';
    document.getElementById('app-appeal-grounds').value = '';
    document.getElementById('app-applicant-name').value = '';
    document.getElementById('app-applicant-id-type').value = 'tc_kimlik';
    document.getElementById('app-applicant-id-no').value = '';
    document.getElementById('app-applicant-phone').value = '';
    document.getElementById('app-applicant-email').value = '';
    document.getElementById('app-applicant-address').value = '';
    document.getElementById('app-logo-preview').classList.add('hidden');
    document.getElementById('app-logo-placeholder').classList.remove('hidden');

    // Pre-fill from user profile
    if (window.AppAuth && window.AppAuth.getProfile) {
        var profile = window.AppAuth.getProfile();
        if (profile) {
            document.getElementById('app-applicant-name').value = ((profile.first_name || '') + ' ' + (profile.last_name || '')).trim();
            document.getElementById('app-applicant-email').value = profile.email || '';
            document.getElementById('app-applicant-phone').value = profile.phone || '';
        }
    }

    // Pre-fill from context (search results)
    if (existingData) {
        if (existingData.brand_name) document.getElementById('app-brand-name').value = existingData.brand_name;
        if (existingData.nice_class_numbers && existingData.nice_class_numbers.length > 0) {
            _applicationsState.selectedClasses = existingData.nice_class_numbers.slice();
        }
        if (existingData.source_search_query) {
            document.getElementById('app-form-id').dataset.sourceQuery = existingData.source_search_query;
            document.getElementById('app-form-id').dataset.sourceRisk = existingData.source_risk_score || '';
        }
        // Appeal type coming from İtiraz Et button
        if (existingData.applicationType) {
            document.getElementById('app-application-type').value = existingData.applicationType;
        }
        if (existingData.brandName) document.getElementById('app-brand-name').value = existingData.brandName;
    }

    // Pre-fill from application context (from search CTA)
    if (window._applicationContext) {
        var ctx = window._applicationContext;
        if (ctx.brandName) document.getElementById('app-brand-name').value = ctx.brandName;
        if (ctx.classes && ctx.classes.length > 0) {
            _applicationsState.selectedClasses = ctx.classes.slice();
        }
        window._applicationContext = null;
    }

    renderAppNiceClassChips();
    // Trigger type-specific UI update (shows appeal section if type=appeal)
    onAppTypeChange();
}

function showApplicationsList(skipAutoSave) {
    var formView = document.getElementById('applications-form-view');
    if (!skipAutoSave && formView && !formView.classList.contains('hidden')) {
        var brandName = document.getElementById('app-brand-name').value.trim();
        if (brandName) {
            var formIdEl = document.getElementById('app-form-id');
            var appId = formIdEl.value;
            var body = _collectApplicationBody();
            var url = appId ? '/api/v1/applications/' + appId : '/api/v1/applications/';
            var method = appId ? 'PUT' : 'POST';
            _appFetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (saved) {
                    if (saved && !appId && document.getElementById('app-form-id')) {
                        document.getElementById('app-form-id').value = saved.id;
                    }
                    if (typeof AppToast !== 'undefined') AppToast.success(t('applications.auto_saved_draft'));
                })
                .catch(function () {});
        }
    }
    document.getElementById('applications-form-view').classList.add('hidden');
    document.getElementById('applications-list-view').classList.remove('hidden');
    _applicationsState.appealContext = null;
    loadApplicationsList();
}

function renderAppNiceClassChips() {
    var container = document.getElementById('app-nice-classes-container');
    if (!container) return;
    container.innerHTML = '';
    _applicationsState.selectedClasses.forEach(function (cls) {
        var chip = document.createElement('span');
        chip.className = 'inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700';
        chip.innerHTML = (t('nice_class_names.' + cls) || ('Class ' + cls))
            + ' <button onclick="removeAppNiceClass(' + cls + ')" class="ml-1 hover:text-red-600 font-bold">&times;</button>';
        container.appendChild(chip);
    });
    if (_applicationsState.selectedClasses.length === 0) {
        container.innerHTML = '<span class="text-xs" style="color:var(--color-text-faint)">' + t('applications.no_classes') + '</span>';
    }
}

function addAppNiceClass() {
    var select = document.getElementById('app-nice-class-select');
    var val = parseInt(select.value);
    if (!val || _applicationsState.selectedClasses.indexOf(val) !== -1) return;
    _applicationsState.selectedClasses.push(val);
    _applicationsState.selectedClasses.sort(function (a, b) { return a - b; });
    select.value = '';
    renderAppNiceClassChips();
}

function removeAppNiceClass(cls) {
    _applicationsState.selectedClasses = _applicationsState.selectedClasses.filter(function (c) { return c !== cls; });
    renderAppNiceClassChips();
}

function handleAppLogoSelect(event) {
    var file = event.target.files[0];
    if (!file) return;
    var allowed = ['image/png', 'image/jpeg', 'image/webp'];
    if (allowed.indexOf(file.type) === -1) {
        if (typeof AppToast !== 'undefined') AppToast.error(t('applications.invalid_logo_type'));
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        if (typeof AppToast !== 'undefined') AppToast.error(t('applications.logo_too_large'));
        return;
    }
    _applicationsState.logoFile = file;
    var reader = new FileReader();
    reader.onload = function (e) {
        document.getElementById('app-logo-preview-img').src = e.target.result;
        document.getElementById('app-logo-preview').classList.remove('hidden');
        document.getElementById('app-logo-placeholder').classList.add('hidden');
    };
    reader.readAsDataURL(file);
}

function clearAppLogo() {
    _applicationsState.logoFile = null;
    document.getElementById('app-logo-input').value = '';
    document.getElementById('app-logo-preview').classList.add('hidden');
    document.getElementById('app-logo-placeholder').classList.remove('hidden');
}

function saveApplication(mode) {
    var brandName = document.getElementById('app-brand-name').value.trim();
    if (!brandName) {
        if (typeof AppToast !== 'undefined') AppToast.error(t('applications.brand_name_required'));
        return;
    }

    var formIdEl = document.getElementById('app-form-id');
    var appId = formIdEl.value;
    var body = _collectApplicationBody();

    var url = appId ? '/api/v1/applications/' + appId : '/api/v1/applications/';
    var method = appId ? 'PUT' : 'POST';

    _appFetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function (r) {
            if (!r.ok) return _appErrorFromResponse(r, 'applications.save_error');
            return r.json();
        })
        .then(function (saved) {
            // Upload logo if selected
            if (_applicationsState.logoFile && saved.id) {
                var fd = new FormData();
                fd.append('file', _applicationsState.logoFile);
                return _appFetch('/api/v1/applications/' + saved.id + '/logo', { method: 'POST', body: fd })
                    .then(function (logoResponse) {
                        if (!logoResponse.ok) return _appErrorFromResponse(logoResponse, 'applications.save_error');
                        return saved;
                    });
            }
            return saved;
        })
        .then(function (saved) {
            if (typeof AppToast !== 'undefined') AppToast.success(t('applications.saved_success'));
            if (!appId && saved && saved.id) {
                document.getElementById('app-form-id').value = saved.id;
            }
            showApplicationsList(true);
        })
        .catch(function (err) {
            if (err && err.status === 403) {
                showUpgradeModal(err, 'applications');
                return;
            }
            if (typeof AppToast !== 'undefined') AppToast.error(_applicationErrorMessage(err, 'applications.save_error'));
        });
}

function submitApplication() {
    var brandName = document.getElementById('app-brand-name').value.trim();
    if (!brandName) {
        if (typeof AppToast !== 'undefined') AppToast.error(t('applications.brand_name_required'));
        return;
    }

    var formIdEl = document.getElementById('app-form-id');
    var appId = formIdEl.value;

    // If it's a new form (no ID), save first as draft then submit
    if (!appId) {
        var body = _collectApplicationBody();

        _appFetch('/api/v1/applications/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(function (r) {
                if (!r.ok) return _appErrorFromResponse(r, 'applications.submit_error');
                return r.json();
            })
            .then(function (saved) {
                // Upload logo if present
                var logoPromise = Promise.resolve(saved);
                if (_applicationsState.logoFile && saved.id) {
                    var fd = new FormData();
                    fd.append('file', _applicationsState.logoFile);
                    logoPromise = _appFetch('/api/v1/applications/' + saved.id + '/logo', { method: 'POST', body: fd })
                        .then(function (logoResponse) {
                            if (!logoResponse.ok) return _appErrorFromResponse(logoResponse, 'applications.submit_error');
                            return saved;
                        });
                }
                return logoPromise;
            })
            .then(function (saved) {
                // Now submit
                return _appFetch('/api/v1/applications/' + saved.id + '/submit', { method: 'POST' });
            })
            .then(function (r) {
                if (!r.ok) return _appErrorFromResponse(r, 'applications.submit_error');
                return r.json();
            })
            .then(function () {
                if (typeof AppToast !== 'undefined') AppToast.success(t('applications.submitted_success'));
                showApplicationsList(true);
            })
            .catch(function (err) {
                if (err && err.status === 403) {
                    showUpgradeModal(err, 'applications');
                    return;
                }
                if (typeof AppToast !== 'undefined') AppToast.error(_applicationErrorMessage(err, 'applications.submit_error'));
            });
        return;
    }

    // Already saved draft — just submit
    _appFetch('/api/v1/applications/' + appId + '/submit', { method: 'POST' })
        .then(function (r) {
            if (!r.ok) return _appErrorFromResponse(r, 'applications.submit_error');
            return r.json();
        })
        .then(function () {
            if (typeof AppToast !== 'undefined') AppToast.success(t('applications.submitted_success'));
            showApplicationsList(true);
        })
        .catch(function (err) {
            if (err && err.status === 403) {
                showUpgradeModal(err, 'applications');
                return;
            }
            if (typeof AppToast !== 'undefined') AppToast.error(_applicationErrorMessage(err, 'applications.submit_error'));
        });
}

function editApplication(appId) {
    _appFetch('/api/v1/applications/' + appId)
        .then(function (r) { return r.json(); })
        .then(function (app) {
            // Populate appeal context before calling showApplicationForm so onAppTypeChange can render the card
            if (app.application_type === 'appeal') {
                _applicationsState.appealContext = {
                    targetAppNo: app.opposition_target_app_no || null,
                    targetBrand: app.opposition_target_brand || null,
                    targetHolder: app.opposition_target_holder || null,
                    targetBulletinNo: app.opposition_target_bulletin_no || null,
                    targetBulletinDate: app.opposition_target_bulletin_date || null,
                    targetClasses: app.opposition_target_classes || [],
                    targetRiskScore: null,
                    deadline: null,
                    watchedBrand: app.brand_name || null
                };
            } else {
                _applicationsState.appealContext = null;
            }

            showApplicationForm();
            _applicationsState.editingId = appId;
            document.getElementById('app-form-id').value = appId;
            document.getElementById('app-brand-name').value = app.brand_name || '';
            document.getElementById('app-application-type').value = app.application_type || 'registration';
            document.getElementById('app-mark-type').value = app.mark_type || 'word';
            document.getElementById('app-goods-services').value = app.goods_services_description || '';
            document.getElementById('app-notes').value = app.notes || '';
            document.getElementById('app-appeal-grounds').value = app.opposition_grounds || '';
            document.getElementById('app-applicant-name').value = app.applicant_full_name || '';
            document.getElementById('app-applicant-id-type').value = app.applicant_id_type || 'tc_kimlik';
            document.getElementById('app-applicant-id-no').value = app.applicant_id_no || '';
            document.getElementById('app-applicant-phone').value = app.applicant_phone || '';
            document.getElementById('app-applicant-email').value = app.applicant_email || '';
            document.getElementById('app-applicant-address').value = app.applicant_address || '';
            _applicationsState.selectedClasses = (app.nice_class_numbers || []).slice();
            renderAppNiceClassChips();
            // Re-run type change to show/hide appeal section with loaded data
            onAppTypeChange();

            if (app.has_logo && app.logo_url) {
                document.getElementById('app-logo-preview-img').src = app.logo_url;
                document.getElementById('app-logo-preview').classList.remove('hidden');
                document.getElementById('app-logo-placeholder').classList.add('hidden');
            }
        })
        .catch(function (err) {
            console.error('Failed to load application:', err);
            if (typeof AppToast !== 'undefined') AppToast.error(t('applications.load_error'));
        });
}

function deleteApplication(appId) {
    if (!confirm(t('applications.delete_confirm'))) return;
    _appFetch('/api/v1/applications/' + appId, { method: 'DELETE' })
        .then(function (r) {
            if (!r.ok) return r.json().then(function (e) { throw e; });
            return r.json();
        })
        .then(function () {
            if (typeof AppToast !== 'undefined') AppToast.success(t('applications.deleted_success'));
            loadApplicationsList();
        })
        .catch(function (err) {
            var msg = (err && err.detail) || t('applications.delete_error');
            if (typeof AppToast !== 'undefined') AppToast.error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        });
}

function openApplicationWithContext(brandName, classes) {
    window._applicationContext = { brandName: brandName, classes: classes || [] };
    showDashboardTab('applications');
    setTimeout(function () { showApplicationForm(); }, 100);
}

// Window-level shim that the design_search.js card invokes when the
// user clicks "Sahip" on a Tasarım result. Resolves the Alpine root
// and delegates to loadPortfolio('design-holder', ...). The button
// element is forwarded so the shim can pull the holder display name
// out of its visible text — keeps design_search.js (vanilla JS, no
// Alpine access) cleanly decoupled from the modal layer.
window.openHolderPortfolio = function (holderTpe, button) {
    if (!holderTpe) return;
    var holderName = '';
    if (button && button.textContent) {
        holderName = String(button.textContent || '').trim();
    }
    try {
        var root = document.querySelector('[x-data="dashboard()"]') || document.body;
        if (root && window.Alpine && typeof window.Alpine.$data === 'function') {
            var dash = window.Alpine.$data(root);
            if (dash && typeof dash.loadPortfolio === 'function') {
                dash.loadPortfolio('design-holder', String(holderTpe), holderName);
                return;
            }
        }
    } catch (_) { /* swallow — modal is best-effort */ }
};

// Many design rows ship a name with the address concatenated:
// e.g. "DİDEM GÖKGÖZ Merkez Mah. Perihan Sok. No:116 ... TÜRKİYE".
// This trims everything from the first Turkish address keyword so
// the card + modal show just the personal/company name. The full
// string is still passed to the backend for portfolio matching —
// the backend uses normalize_designer_name on the stored column,
// not on this display string, so search behaviour is unaffected.
window._stripTurkishAddress = function (name) {
    if (!name) return '';
    var s = String(name).trim();
    var words = s.split(/\s+/);
    var ADDR = /^(Mah\.|Mahallesi|Cad\.|Caddesi|Sok\.|Sokak|Bulv\.|Bulvarı|Apt\.|Apartmanı|Blok|Kat:|Daire:)$/i;
    for (var i = 1; i < words.length; i++) {
        if (ADDR.test(words[i]) || /^No:/i.test(words[i]) || /^D:\d/i.test(words[i])) {
            // Address starts at the word BEFORE the keyword (e.g.
            // "Merkez Mah." — drop "Merkez" too).
            return words.slice(0, Math.max(1, i - 1)).join(' ').trim();
        }
    }
    return s;
};

// Sister shim for designer-chip clicks on the Tasarım card.
// Designers have no canonical id in our schema, so the name itself
// is the round-tripped identifier; the backend resolves via the
// conservative-normalization GIN index on designs.designers.
// portfolioType is 'design-designer' so the modal renders the right
// label + the bulk-add modal posts {designer_name: ...} to
// /api/v1/design-watchlist/bulk-from-portfolio.
window.openDesignerPortfolio = function (designerName, _button) {
    if (!designerName) return;
    var name = String(designerName || '').trim();
    if (!name) return;
    try {
        var root = document.querySelector('[x-data="dashboard()"]') || document.body;
        if (root && window.Alpine && typeof window.Alpine.$data === 'function') {
            var dash = window.Alpine.$data(root);
            if (dash && typeof dash.loadPortfolio === 'function') {
                dash.loadPortfolio('design-designer', name, name);
                return;
            }
        }
    } catch (_) { /* swallow — modal is best-effort */ }
};

// Attorney click: the lookup needs (name, firm) together to match
// the backend's stricter name+firm rule. The portfolio modal's
// _portfolioEntityId state holds a single string, so we JSON-encode
// both fields and the URL-builder + bulk-confirm modal decode it.
// Firm may be empty — backend matches NULL-firm rows then.
window.openAttorneyPortfolio = function (attorneyName, attorneyFirm, _button) {
    if (!attorneyName) return;
    var nm = String(attorneyName || '').trim();
    if (!nm) return;
    var fm = String(attorneyFirm || '').trim();
    var encoded = JSON.stringify({ name: nm, firm: fm });
    // Many design rows embed the firm in the attorney_name itself
    // (e.g. "ALPER AKSU (SEMBOL PATENT ...)") and still carry it on
    // attorney_firm — skip the dash-join when it would duplicate.
    var firmInName = fm && nm.toLowerCase().indexOf(fm.toLowerCase()) !== -1;
    var display = (fm && !firmInName) ? (nm + ' — ' + fm) : nm;
    try {
        var root = document.querySelector('[x-data="dashboard()"]') || document.body;
        if (root && window.Alpine && typeof window.Alpine.$data === 'function') {
            var dash = window.Alpine.$data(root);
            if (dash && typeof dash.loadPortfolio === 'function') {
                dash.loadPortfolio('design-attorney', encoded, display);
                return;
            }
        }
    } catch (_) { /* swallow — modal is best-effort */ }
};
