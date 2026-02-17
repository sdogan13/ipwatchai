/**
 * Landing page Alpine.js component
 * Handles: public search, image upload, class suggestions (AI/manual/browse),
 * login/register modals, stats, dark mode, i18n
 */
function landing() {
    return {
        // i18n
        lang_code: window.AppI18n ? window.AppI18n._locale : 'tr',
        currentLang: window.AppI18n ? window.AppI18n._locale : 'tr',

        // Dark mode
        isDark: document.documentElement.classList.contains('dark'),

        // Mobile menu
        mobileMenu: false,

        // Tab state (home, about, mission)
        activeTab: 'home',

        // Auth state
        isLoggedIn: !!localStorage.getItem('auth_token'),

        // Search state
        searchQuery: '',
        searchResults: [],
        searchLoading: false,
        searchError: '',
        resultsAtBottom: false,
        expandedResult: null,
        lightboxImage: '',

        // Search history state
        searchHistory: [],
        showSearchHistory: false,

        // Portfolio state
        portfolioResults: [],
        portfolioName: '',
        portfolioType: '',
        _portfolioEntityId: '',
        portfolioLoading: false,
        portfolioBulkAdding: false,
        showPortfolio: false,

        // Image upload state
        selectedImage: null,
        imagePreview: '',
        imageName: '',
        dragOver: false,

        // Class selection state
        selectedClasses: [],
        classError: '',
        classInput: '',
        suggestedClasses: [],
        suggesting: false,

        // Browse all sub-state
        showBrowse: false,
        allClasses: [],
        browseLoading: false,
        browseFilter: '',

        // Login modal
        showLogin: false,
        loginEmail: '',
        loginPassword: '',
        loginLoading: false,
        loginError: '',

        // Forgot password modal
        showForgotPassword: false,
        forgotStep: 'email',
        forgotEmail: '',
        forgotCode: '',
        forgotNewPassword: '',
        forgotConfirmPassword: '',
        forgotLoading: false,
        forgotError: '',
        forgotSuccess: '',
        _forgotResetCode: '',  // unused, kept for compat

        // Password visibility toggles
        showRegPw: false,
        showRegConfirmPw: false,
        showForgotNewPw: false,
        showForgotConfirmPw: false,

        // Register modal
        showRegister: false,
        regFirstName: '',
        regLastName: '',
        regEmail: '',
        regPassword: '',
        regConfirmPassword: '',
        regOrgName: '',
        regLoading: false,
        regError: '',

        // Stats
        dbCount: 0,

        // Reactive t() wrapper
        t: function(key, params) {
            void this.lang_code;
            return window.AppI18n ? window.AppI18n.t(key, params) : key;
        },

        init: function() {
            var self = this;

            // Listen for locale changes
            window.addEventListener('locale-changed', function(e) {
                self.lang_code = e.detail.locale + '_' + Date.now();
                self.currentLang = e.detail.locale;
                var dir = e.detail.dir || 'ltr';
                document.documentElement.setAttribute('dir', dir);
                document.documentElement.setAttribute('lang', e.detail.locale);
                // Reload Nice classes in new language
                if (self.allClasses.length > 0) {
                    self.allClasses = [];
                    self.loadAllClasses();
                }
            });

            // If user is already logged in, redirect to dashboard
            if (this.isLoggedIn) {
                window.location.href = '/dashboard';
                return;
            }

            // Load search history
            this.loadSearchHistory();

            // Load stats
            this.loadStats();

            // Check URL params for ?login or ?register
            var urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get('login') !== null) this.showLogin = true;
            if (urlParams.get('register') !== null) this.showRegister = true;
        },

        // ==================== LANGUAGE ====================
        setLang: function(locale) {
            if (window.AppI18n && window.AppI18n.setLocale) {
                window.AppI18n.setLocale(locale);
            }
        },

        // ==================== DARK MODE ====================
        toggleDark: function() {
            var html = document.documentElement;
            this.isDark = html.classList.toggle('dark');
            localStorage.setItem('theme', this.isDark ? 'dark' : 'light');
        },

        // ==================== STATS ====================
        loadStats: function() {
            var self = this;
            fetch('/api/v1/status')
                .then(function(res) { return res.ok ? res.json() : null; })
                .then(function(data) {
                    if (data && data.statistics) {
                        self.dbCount = data.statistics.total_trademarks || 0;
                    }
                })
                .catch(function() { /* silent */ });
        },

        // ==================== IMAGE UPLOAD ====================
        onImageSelected: function(event) {
            var file = event.target.files && event.target.files[0];
            if (!file) return;
            this._setImage(file);
        },

        handleDrop: function(event) {
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

        _setImage: function(file) {
            this.selectedImage = file;
            this.imageName = file.name;
            this.searchError = '';
            var self = this;
            var reader = new FileReader();
            reader.onload = function(e) {
                self.imagePreview = e.target.result;
            };
            reader.readAsDataURL(file);
        },

        clearImage: function() {
            this.selectedImage = null;
            this.imagePreview = '';
            this.imageName = '';
            if (this.$refs.landingImageInput) {
                this.$refs.landingImageInput.value = '';
            }
            if (!this.searchQuery.trim()) {
                this.searchResults = [];
                this.searchError = '';
                this.expandedResult = null;
            }
        },

        // ==================== CLASS FINDER ====================
        _syncClassInput: function() {
            // Keep textarea in sync with selected classes (backend-compatible format)
            this.classInput = this.selectedClasses.join(', ');
        },

        submitClassInput: function() {
            var input = this.classInput.trim();
            if (!input) return;
            this.classError = '';

            // Detect: if all comma/space-separated parts are numbers 1-45, treat as manual
            var parts = input.split(/[,\s]+/).filter(function(p) { return p.length > 0; });
            var allNumbers = parts.every(function(p) {
                var n = parseInt(p, 10);
                return !isNaN(n) && n >= 1 && n <= 45 && String(n) === p.trim();
            });

            if (allNumbers) {
                // Manual class addition
                var self = this;
                var added = 0;
                parts.forEach(function(part) {
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
                // AI suggestion
                this.suggestClasses();
            }
        },

        suggestClasses: function() {
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

            fetch('/api/suggest-classes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ description: desc, top_k: 5 })
            })
            .then(function(res) {
                if (!res.ok) throw new Error('Suggestion failed');
                return res.json();
            })
            .then(function(data) {
                self.suggestedClasses = data.suggestions || [];
                if (self.suggestedClasses.length === 0) {
                    self.classError = self.t('search.no_class_suggestions');
                }
            })
            .catch(function() {
                self.suggestedClasses = [];
                self.classError = self.t('search.class_suggestion_failed');
            })
            .finally(function() {
                self.suggesting = false;
            });
        },

        selectClass: function(cls) {
            var num = cls.class_number;
            var idx = this.selectedClasses.indexOf(num);
            if (idx === -1) {
                this.selectedClasses.push(num);
            } else {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        // --- Browse All Classes ---
        toggleBrowseAll: function() {
            this.showBrowse = !this.showBrowse;
            if (this.showBrowse && this.allClasses.length === 0) {
                this.loadAllClasses();
            }
        },

        loadAllClasses: function() {
            if (this.browseLoading) return;
            this.browseLoading = true;
            var self = this;

            fetch('/api/nice-classes?lang=' + (this.currentLang || 'tr'))
                .then(function(res) { return res.ok ? res.json() : null; })
                .then(function(data) {
                    if (data && data.classes) {
                        self.allClasses = data.classes;
                    }
                })
                .catch(function() { /* silent */ })
                .finally(function() {
                    self.browseLoading = false;
                });
        },

        toggleBrowseClass: function(num) {
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
            return this.allClasses.filter(function(cls) {
                return String(cls.number).indexOf(q) !== -1 ||
                       cls.name.toLowerCase().indexOf(q) !== -1;
            });
        },

        // --- Shared ---
        removeClass: function(num) {
            var idx = this.selectedClasses.indexOf(num);
            if (idx !== -1) {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        clearAllClasses: function() {
            this.selectedClasses = [];
            this._syncClassInput();
        },

        // ==================== PUBLIC SEARCH ====================
        // ==================== SEARCH HISTORY ====================
        loadSearchHistory: function() {
            try {
                var raw = localStorage.getItem('search_history');
                this.searchHistory = raw ? JSON.parse(raw) : [];
            } catch(e) { this.searchHistory = []; }
        },

        saveSearchQuery: function(query) {
            if (!query || !query.trim()) return;
            var q = query.trim();
            this.searchHistory = this.searchHistory.filter(function(h) { return h.toLowerCase() !== q.toLowerCase(); });
            this.searchHistory.unshift(q);
            if (this.searchHistory.length > 20) this.searchHistory = this.searchHistory.slice(0, 20);
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch(e) {}
        },

        removeSearchHistoryItem: function(query) {
            this.searchHistory = this.searchHistory.filter(function(h) { return h !== query; });
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch(e) {}
        },

        clearSearchHistory: function() {
            this.searchHistory = [];
            try { localStorage.removeItem('search_history'); } catch(e) {}
            this.showSearchHistory = false;
        },

        filteredSearchHistory: function() {
            var q = (this.searchQuery || '').trim().toLowerCase();
            if (!q) return this.searchHistory.slice(0, 10);
            return this.searchHistory.filter(function(h) { return h.toLowerCase().indexOf(q) !== -1; }).slice(0, 10);
        },

        selectSearchHistoryItem: function(item) {
            this.searchQuery = item;
            this.showSearchHistory = false;
        },

        publicSearch: function() {
            var query = this.searchQuery.trim();
            var hasImage = !!this.selectedImage;
            var hasClasses = this.selectedClasses.length > 0;

            // Need at least text or image
            if (!hasImage && (!query || query.length < 2)) {
                this.searchError = this.t('search.enter_brand_name');
                return;
            }

            this.searchLoading = true;
            this.searchError = '';
            this.searchResults = [];

            // Track search type for CTA messaging
            if (hasImage && query) this._lastSearchType = 'both';
            else if (hasImage) this._lastSearchType = 'image';
            else this._lastSearchType = 'text';

            var self = this;

            if (hasImage || hasClasses) {
                // POST with FormData (image and/or classes)
                var formData = new FormData();
                if (query) {
                    formData.append('query', query);
                }
                if (hasImage) {
                    formData.append('image', this.selectedImage);
                }
                if (hasClasses) {
                    formData.append('classes', this.selectedClasses.join(','));
                }

                fetch('/api/v1/search/public', {
                    method: 'POST',
                    body: formData
                })
                .then(function(res) {
                    if (res.status === 429) {
                        self.searchError = self.t('landing.search_limit_reached');
                        return null;
                    }
                    if (!res.ok) throw new Error('Search failed');
                    return res.json();
                })
                .then(function(data) {
                    if (data) {
                        self.searchResults = (data.results || []).map(function(r) { r._showGoods = false; return r; });
                        self.resultsAtBottom = false;
                        self.expandedResult = null;
                        self.saveSearchQuery(query);
                        self.showSearchHistory = false;
                        if (self.searchResults.length === 0) {
                            self.searchError = self.t('search.no_results');
                        }
                    }
                })
                .catch(function() {
                    self.searchError = self.t('search.search_failed');
                })
                .finally(function() {
                    self.searchLoading = false;
                });
            } else {
                // GET text-only search
                fetch('/api/v1/search/public?query=' + encodeURIComponent(query))
                    .then(function(res) {
                        if (res.status === 429) {
                            self.searchError = self.t('landing.search_limit_reached');
                            return null;
                        }
                        if (!res.ok) throw new Error('Search failed');
                        return res.json();
                    })
                    .then(function(data) {
                        if (data) {
                            self.searchResults = (data.results || []).map(function(r) { r._showGoods = false; return r; });
                            self.saveSearchQuery(query);
                            self.showSearchHistory = false;
                            if (self.searchResults.length === 0) {
                                self.searchError = self.t('search.no_results');
                            }
                        }
                    })
                    .catch(function() {
                        self.searchError = self.t('search.search_failed');
                    })
                    .finally(function() {
                        self.searchLoading = false;
                    });
            }
        },

        // ==================== PORTFOLIO ====================
        portfolioTotalCount: 0,
        _portfolioAllResults: [],

        loadPortfolio: function(type, id, name) {
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
            var param = type === 'holder' ? 'holder_id' : 'attorney_no';
            fetch('/api/v1/portfolio/public?' + param + '=' + encodeURIComponent(id))
                .then(function(res) {
                    if (res.status === 429) {
                        self.searchError = self.t('landing.search_limit_reached');
                        self.showPortfolio = false;
                        return null;
                    }
                    if (!res.ok) throw new Error('Portfolio failed');
                    return res.json();
                })
                .then(function(data) {
                    if (data) {
                        var all = data.results || [];
                        self._portfolioAllResults = all;
                        self.portfolioTotalCount = (data.total_count != null) ? data.total_count : all.length;
                        self.portfolioResults = all.slice(0, 5);
                        self.portfolioName = data.entity_name || name || id;
                    }
                })
                .catch(function() {
                    self.searchError = self.t('search.search_failed');
                    self.showPortfolio = false;
                })
                .finally(function() {
                    self.portfolioLoading = false;
                });
        },

        closePortfolio: function() {
            this.showPortfolio = false;
            this.portfolioResults = [];
            this._portfolioAllResults = [];
            this.portfolioTotalCount = 0;
            this.portfolioName = '';
            this.portfolioType = '';
            this._portfolioEntityId = '';
        },

        downloadPortfolioCsv: function() {
            var id = this._portfolioEntityId;
            var type = this.portfolioType;
            if (!id || !type) return;
            var param = type === 'holder' ? 'holder_id' : 'attorney_no';
            var csvUrl = '/api/v1/portfolio/public/csv?' + param + '=' + encodeURIComponent(id);
            fetch(csvUrl)
                .then(function(res) {
                    if (!res.ok) throw new Error('CSV export failed');
                    return res.blob();
                })
                .then(function(blob) {
                    var url = URL.createObjectURL(blob);
                    var a = document.createElement('a');
                    a.href = url;
                    a.download = (type === 'holder' ? 'sahip' : 'vekil') + '_' + id + '_portfolio.csv';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                })
                .catch(function() {
                    if (window.AppToast) AppToast.showToast('CSV indirilemedi', 'error');
                });
        },

        addPortfolioToWatchlist: function() {
            var id = this._portfolioEntityId;
            var type = this.portfolioType;
            if (!id || !type) {
                if (window.AppToast) AppToast.showToast('Portfolio bilgisi eksik', 'error');
                return;
            }
            // Build redirect URL with bulk watchlist params
            var params = new URLSearchParams();
            params.set('tab', 'search');
            params.set('bulk_watchlist', '1');
            params.set('bw_type', type);
            params.set('bw_id', id);
            params.set('bw_name', this.portfolioName || '');
            params.set('bw_count', String(this.portfolioTotalCount || 0));
            var redirectUrl = '/dashboard?' + params.toString();

            var token = localStorage.getItem('auth_token') || localStorage.getItem('access_token');
            if (!token) {
                // Save redirect so after login user lands on dashboard with bulk modal
                localStorage.setItem('pending_studio_redirect', redirectUrl);
                this.showLogin = true;
                return;
            }
            window.location.href = redirectUrl;
        },

        // ==================== LOGIN ====================
        submitLogin: function() {
            if (!this.loginEmail || !this.loginPassword) return;

            this.loginLoading = true;
            this.loginError = '';

            var self = this;
            var formData = new URLSearchParams();
            formData.append('username', this.loginEmail);
            formData.append('password', this.loginPassword);

            fetch('/api/v1/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString()
            })
            .then(function(res) {
                if (!res.ok) {
                    if (res.status === 401) throw new Error('invalid_credentials');
                    throw new Error('login_failed');
                }
                return res.json();
            })
            .then(function(data) {
                if (data.access_token) {
                    localStorage.setItem('auth_token', data.access_token);
                    if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
                    var pendingRedirect = localStorage.getItem('pending_studio_redirect');
                    if (pendingRedirect) {
                        localStorage.removeItem('pending_studio_redirect');
                        window.location.href = pendingRedirect;
                    } else {
                        window.location.href = '/dashboard';
                    }
                } else {
                    throw new Error('no_token');
                }
            })
            .catch(function(err) {
                if (err.message === 'invalid_credentials') {
                    self.loginError = self.t('auth.error_invalid_credentials') + ' ' + self.t('auth.no_account_prompt');
                } else {
                    self.loginError = self.t('auth.error_generic');
                }
                self.loginLoading = false;
            });
        },

        // ==================== FORGOT PASSWORD ====================
        forgotRequestCode: function() {
            if (!this.forgotEmail) return;
            this.forgotLoading = true;
            this.forgotError = '';
            var self = this;
            fetch('/api/v1/auth/forgot-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: this.forgotEmail, lang: this.lang_code || 'tr' })
            })
            .then(function(res) {
                if (!res.ok) throw new Error('request_failed');
                return res.json();
            })
            .then(function(data) {
                self.forgotStep = 'code';
                self.forgotCode = '';
                self.forgotNewPassword = '';
                self.forgotConfirmPassword = '';
                self.forgotError = '';
                self.forgotSuccess = self.t('auth.check_email_for_code');
            })
            .catch(function() {
                self.forgotError = self.t('auth.error_generic');
            })
            .finally(function() {
                self.forgotLoading = false;
            });
        },

        forgotResetPassword: function() {
            if (!this.forgotCode || !this.forgotNewPassword) return;
            if (this.forgotNewPassword !== this.forgotConfirmPassword) {
                this.forgotError = this.t('auth.passwords_not_match');
                return;
            }
            if (this.forgotNewPassword.length < 8) {
                this.forgotError = this.t('auth.password_min_length');
                return;
            }
            this.forgotLoading = true;
            this.forgotError = '';
            this.forgotSuccess = '';
            var self = this;
            fetch('/api/v1/auth/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: this.forgotCode, new_password: this.forgotNewPassword })
            })
            .then(function(res) {
                if (!res.ok) return res.json().then(function(d) { throw new Error(d.detail || 'reset_failed'); });
                return res.json();
            })
            .then(function() {
                self.forgotSuccess = self.t('auth.password_reset_success');
                setTimeout(function() {
                    self.showForgotPassword = false;
                    self.showLogin = true;
                    self.loginEmail = self.forgotEmail;
                    self.loginPassword = '';
                }, 2000);
            })
            .catch(function(err) {
                self.forgotError = err.message === 'reset_failed'
                    ? self.t('auth.error_generic')
                    : (err.message || self.t('auth.error_generic'));
            })
            .finally(function() {
                self.forgotLoading = false;
            });
        },

        // ==================== REGISTER ====================
        submitRegister: function() {
            if (!this.regFirstName || !this.regLastName || !this.regEmail || !this.regPassword) return;
            if (this.regPassword !== this.regConfirmPassword) {
                this.regError = this.t('auth.passwords_not_match');
                return;
            }
            if (this.regPassword.length < 8) {
                this.regError = this.t('auth.password_min_length');
                return;
            }

            this.regLoading = true;
            this.regError = '';

            var self = this;
            fetch('/api/v1/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    first_name: this.regFirstName,
                    last_name: this.regLastName,
                    email: this.regEmail,
                    password: this.regPassword,
                    organization_name: this.regOrgName || (this.regFirstName + ' ' + this.regLastName),
                    lang: this.lang_code || 'tr'
                })
            })
            .then(function(res) {
                if (!res.ok) {
                    if (res.status === 409 || res.status === 400) throw new Error('email_taken');
                    throw new Error('register_failed');
                }
                return res.json();
            })
            .then(function(data) {
                if (data.access_token) {
                    localStorage.setItem('auth_token', data.access_token);
                    if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
                    var pendingRedirect = localStorage.getItem('pending_studio_redirect');
                    if (pendingRedirect) {
                        localStorage.removeItem('pending_studio_redirect');
                        window.location.href = pendingRedirect;
                    } else {
                        window.location.href = '/dashboard';
                    }
                } else {
                    self.showRegister = false;
                    self.showLogin = true;
                    self.loginEmail = self.regEmail;
                    if (window.AppToast) window.AppToast.showToast(self.t('auth.register_success'), 'success');
                }
            })
            .catch(function(err) {
                if (err.message === 'email_taken') {
                    self.regError = self.t('auth.error_email_taken');
                } else {
                    self.regError = self.t('auth.error_generic');
                }
                self.regLoading = false;
            });
        },

        // ==================== PASSWORD STRENGTH ====================
        get pwStrength() {
            var pw = this.regPassword || '';
            var s = 0;
            if (pw.length >= 8) s++;
            if (/[A-Z]/.test(pw)) s++;
            if (/[a-z]/.test(pw)) s++;
            if (/[0-9]/.test(pw)) s++;
            return s;
        },

        get pwStrengthColor() {
            var colors = ['#ef4444', '#f97316', '#eab308', '#22c55e'];
            return colors[Math.max(0, this.pwStrength - 1)] || '#ef4444';
        },

        get pwStrengthLabel() {
            if (!this.regPassword) return '';
            var labels = [
                this.t('auth.password_strength_weak'),
                this.t('auth.password_strength_fair'),
                this.t('auth.password_strength_good'),
                this.t('auth.password_strength_strong')
            ];
            return labels[Math.max(0, this.pwStrength - 1)] || '';
        },

        // ==================== AI STUDIO CTA ====================
        _pendingStudioMode: '',
        _lastSearchType: 'text', // 'text', 'image', 'both'

        hasHighRisk: function() {
            if (!this.searchResults || this.searchResults.length === 0) return false;
            for (var i = 0; i < this.searchResults.length; i++) {
                if (this.searchResults[i].risk_score >= 0.65) return true;
            }
            return false;
        },

        getStudioCtaTitle: function() {
            if (this._lastSearchType === 'image') return this.t('landing.studio_cta_title_image');
            if (this._lastSearchType === 'both') return this.t('landing.studio_cta_title_both');
            return this.t('landing.studio_cta_title_text');
        },

        getStudioCtaDesc: function() {
            if (this._lastSearchType === 'image') return this.t('landing.studio_cta_desc_image');
            if (this._lastSearchType === 'both') return this.t('landing.studio_cta_desc_both');
            return this.t('landing.studio_cta_desc_text');
        },

        addToWatchlist: function(r) {
            var params = new URLSearchParams();
            params.set('tab', 'search');
            params.set('watchlist_add', '1');
            if (r.trademark_name) params.set('wl_name', r.trademark_name);
            if (r.application_no) params.set('wl_appno', r.application_no);
            if (r.nice_classes && r.nice_classes.length) params.set('wl_classes', r.nice_classes.join(','));
            var url = '/dashboard?' + params.toString();

            if (this.isLoggedIn) {
                window.location.href = url;
            } else {
                localStorage.setItem('pending_studio_redirect', url);
                this.showRegister = true;
            }
        },

        goToStudio: function(mode) {
            var query = (this.searchQuery || '').trim();
            var studioUrl = '/dashboard?tab=ai-studio&studio_mode=' + mode;
            if (query) studioUrl += '&studio_query=' + encodeURIComponent(query);

            if (this.isLoggedIn) {
                window.location.href = studioUrl;
            } else {
                // Save pending redirect, then open register
                this._pendingStudioMode = mode;
                localStorage.setItem('pending_studio_redirect', studioUrl);
                this.showRegister = true;
            }
        },

        // ==================== HELPERS ====================
        formatNumber: function(n) {
            if (!n) return '0';
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
            return String(n);
        },

        getRiskBg: function(score) {
            if (score >= 0.9) return 'var(--color-risk-critical-bg)';
            if (score >= 0.7) return 'var(--color-risk-high-bg)';
            if (score >= 0.5) return 'var(--color-risk-medium-bg)';
            return 'var(--color-risk-low-bg)';
        },

        getRiskColor: function(score) {
            if (score >= 0.9) return 'var(--color-risk-critical-text)';
            if (score >= 0.7) return 'var(--color-risk-high-text)';
            if (score >= 0.5) return 'var(--color-risk-medium-text)';
            return 'var(--color-risk-low-text)';
        },

        getStatusColor: function(status) {
            var s = (status || '').toLowerCase();
            if (s === 'registered' || s === 'renewed') return '#16a34a';
            if (s === 'published') return '#ca8a04';
            if (s === 'applied') return 'var(--color-text-secondary)';
            if (s === 'refused' || s === 'withdrawn') return '#dc2626';
            return 'var(--color-text-primary)';
        }
    };
}
