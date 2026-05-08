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

        // Tab state (home, about, mission, education)
        activeTab: 'home',

        // Auth state
        isLoggedIn: !!(window.AppAuth && window.AppAuth.hasValidAccessToken && window.AppAuth.hasValidAccessToken()),

        // Search state
        searchQuery: '',
        searchResults: [],
        searchLoading: false,
        searchError: '',
        resultsAtBottom: false,
        expandedResult: null,
        lightboxImage: '',
        riskReportLoading: false,
        riskReportError: '',
        riskReport: null,

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

        // Education state
        educationLoading: false,
        educationError: '',
        educationCatalog: null,
        educationSelectedCategoryId: '',
        educationMobileSection: '',
        educationMobileSectionByCategory: {},
        educationDeckLoading: false,
        educationSelectedDeckId: '',
        educationSelectedDeck: null,
        educationFlashcardIndex: 0,
        educationFlashcardFlipped: false,
        educationQuizLoading: false,
        educationSelectedQuizId: '',
        educationSelectedQuiz: null,
        educationQuizIndex: 0,
        educationQuizAnswers: {},
        educationQuizExplanationOpen: false,
        educationQuizExplanationLoading: false,
        _educationQuizExplanationTimer: null,
        educationProgressLoading: false,
        educationProgressMap: {},
        educationProgressNotice: '',
        educationCanModerate: false,
        educationModerationBusyMap: {},
        educationQuizExplanationEditorOpen: false,
        educationQuizExplanationEditorQuestionId: '',
        educationQuizExplanationDraft: '',
        educationQuizSummaryDraft: '',

        // Reactive t() wrapper
        t: function (key, params) {
            void this.lang_code;
            return window.AppI18n ? window.AppI18n.t(key, params) : key;
        },

        init: function () {
            var self = this;
            var urlParams = new URLSearchParams(window.location.search);
            var requestedTab = urlParams.get('tab');
            var allowedTabs = { home: true, about: true, mission: true, education: true };

            // Listen for locale changes
            window.addEventListener('locale-changed', function (e) {
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

            if (requestedTab && allowedTabs[requestedTab]) {
                this.activeTab = requestedTab;
            }

            // If user is already logged in, redirect to dashboard unless an explicit landing tab is requested
            if (this.isLoggedIn && !requestedTab) {
                window.location.href = '/dashboard';
                return;
            }

            // Load search history
            this.loadSearchHistory();
            this.loadLocalEducationProgress();
            this.loadEducationMobileSectionPreferences();

            // Load stats
            this.loadStats();
            this.loadEducationCatalog();

            if (this.isLoggedIn) {
                this.loadEducationTesterContext();
                this.loadEducationProgress();
            }

            // Check URL params for ?login or ?register
            if (urlParams.get('login') !== null) this.showLogin = true;
            if (urlParams.get('register') !== null) this.showRegister = true;
        },

        // ==================== LANGUAGE ====================
        setLang: function (locale) {
            if (window.AppI18n && window.AppI18n.setLocale) {
                window.AppI18n.setLocale(locale);
            }
        },

        // ==================== DARK MODE ====================
        toggleDark: function () {
            var html = document.documentElement;
            this.isDark = html.classList.toggle('dark');
            localStorage.setItem('theme', this.isDark ? 'dark' : 'light');
        },

        // ==================== STATS ====================
        loadStats: function () {
            var self = this;
            fetch('/api/v1/status')
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (data) {
                    if (data && data.statistics) {
                        self.dbCount = data.statistics.total_trademarks || 0;
                    }
                })
                .catch(function () { /* silent */ });
        },

        // ==================== EDUCATION ====================
        getAuthToken: function () {
            if (window.AppAuth && window.AppAuth.getAuthToken) return window.AppAuth.getAuthToken();
            return localStorage.getItem('auth_token')
                || localStorage.getItem('access_token')
                || sessionStorage.getItem('auth_token')
                || sessionStorage.getItem('access_token')
                || '';
        },

        getAuthHeaders: function () {
            var token = this.getAuthToken();
            return token ? { Authorization: 'Bearer ' + token } : {};
        },

        loadEducationTesterContext: function () {
            if (!this.isLoggedIn) {
                this.educationCanModerate = false;
                return Promise.resolve(false);
            }

            var self = this;
            return fetch('/api/v1/auth/me', {
                headers: this.getAuthHeaders()
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('education_tester_context_failed');
                    return res.json();
                })
                .then(function (profile) {
                    var role = String((profile && profile.role) || '').toLowerCase();
                    self.educationCanModerate = !!(profile && (profile.is_superadmin || role === 'admin'));
                    return self.educationCanModerate;
                })
                .catch(function () {
                    self.educationCanModerate = false;
                    return false;
                });
        },

        queuePostAuthRedirect: function (url) {
            try {
                localStorage.setItem('pending_post_auth_redirect', url);
            } catch (e) { /* ignore */ }
        },

        consumePostAuthRedirect: function () {
            var pendingRedirect = '';
            try {
                pendingRedirect = localStorage.getItem('pending_post_auth_redirect')
                    || localStorage.getItem('pending_studio_redirect')
                    || '';
                localStorage.removeItem('pending_post_auth_redirect');
                localStorage.removeItem('pending_studio_redirect');
            } catch (e) {
                pendingRedirect = '';
            }
            return pendingRedirect;
        },

        beginEducationSync: function () {
            this.queuePostAuthRedirect('/?tab=education');
            this.showLogin = true;
        },

        getEducationProgressStorageKey: function () {
            return 'landing_education_progress_v1';
        },

        getEducationMobileSectionStorageKey: function () {
            return 'landing_education_mobile_section_v1';
        },

        getEducationProgressCompositeKey: function (itemType, itemKey) {
            return String(itemType || '') + '::' + String(itemKey || '');
        },

        loadLocalEducationProgress: function () {
            try {
                var raw = localStorage.getItem(this.getEducationProgressStorageKey());
                var parsed = raw ? JSON.parse(raw) : {};
                this.educationProgressMap = parsed && typeof parsed === 'object' ? parsed : {};
            } catch (e) {
                this.educationProgressMap = {};
            }
        },

        saveLocalEducationProgressStore: function () {
            try {
                localStorage.setItem(this.getEducationProgressStorageKey(), JSON.stringify(this.educationProgressMap || {}));
            } catch (e) { /* ignore */ }
        },

        loadEducationMobileSectionPreferences: function () {
            try {
                var raw = localStorage.getItem(this.getEducationMobileSectionStorageKey());
                var parsed = raw ? JSON.parse(raw) : {};
                this.educationMobileSectionByCategory = parsed && typeof parsed === 'object' ? parsed : {};
            } catch (e) {
                this.educationMobileSectionByCategory = {};
            }
        },

        saveEducationMobileSectionPreferences: function () {
            try {
                localStorage.setItem(this.getEducationMobileSectionStorageKey(), JSON.stringify(this.educationMobileSectionByCategory || {}));
            } catch (e) { /* ignore */ }
        },

        replaceEducationProgressItems: function (items) {
            var self = this;
            var map = {};
            (items || []).forEach(function (item) {
                if (!item || !item.item_type || !item.item_key) return;
                map[self.getEducationProgressCompositeKey(item.item_type, item.item_key)] = item;
            });
            this.educationProgressMap = map;
            this.saveLocalEducationProgressStore();
        },

        getEducationProgressItem: function (itemType, itemKey) {
            return this.educationProgressMap[this.getEducationProgressCompositeKey(itemType, itemKey)] || null;
        },

        getEducationCategories: function () {
            if (!this.educationCatalog || !Array.isArray(this.educationCatalog.categories)) return [];
            var normalizeCategoryId = function (value) {
                var raw = String(value || '');
                if (raw === 'tasarm') return 'tasarim';
                return raw;
            };
            var order = {
                genel: 0,
                patent: 1,
                marka: 2,
                'cografi-isaret': 3,
                tasarim: 4
            };
            return this.educationCatalog.categories.slice().sort(function (left, right) {
                var leftId = normalizeCategoryId(left && left.id);
                var rightId = normalizeCategoryId(right && right.id);
                var leftRank = Object.prototype.hasOwnProperty.call(order, leftId) ? order[leftId] : 999;
                var rightRank = Object.prototype.hasOwnProperty.call(order, rightId) ? order[rightId] : 999;
                if (leftRank !== rightRank) return leftRank - rightRank;
                return String((left && left.title) || '').localeCompare(String((right && right.title) || ''));
            });
        },

        getEducationCategoryById: function (categoryId) {
            var categories = this.getEducationCategories();
            for (var i = 0; i < categories.length; i += 1) {
                if (categories[i] && categories[i].id === categoryId) {
                    return categories[i];
                }
            }
            return null;
        },

        getEducationActiveCategory: function () {
            var categories = this.getEducationCategories();
            if (!categories.length) return null;
            return this.getEducationCategoryById(this.educationSelectedCategoryId) || categories[0];
        },

        getEducationCategoryTheme: function (categoryOrId) {
            var category = typeof categoryOrId === 'string'
                ? this.getEducationCategoryById(categoryOrId)
                : categoryOrId;
            var categoryId = category && category.id ? String(category.id) : String(categoryOrId || 'genel');
            if (categoryId === 'tasarm') categoryId = 'tasarim';
            var themes = {
                patent: {
                    accent: '#06b6d4',
                    deep: '#0891b2',
                    strong: '#67e8f9',
                    rgb: '6,182,212',
                    shadowRgb: '14,116,144'
                },
                marka: {
                    accent: '#f43f5e',
                    deep: '#e11d48',
                    strong: '#fda4af',
                    rgb: '244,63,94',
                    shadowRgb: '136,19,55'
                },
                'cografi-isaret': {
                    accent: '#22c55e',
                    deep: '#16a34a',
                    strong: '#86efac',
                    rgb: '34,197,94',
                    shadowRgb: '21,128,61'
                },
                tasarim: {
                    accent: '#ec4899',
                    deep: '#db2777',
                    strong: '#f9a8d4',
                    rgb: '236,72,153',
                    shadowRgb: '157,23,77'
                },
                genel: {
                    accent: '#6366f1',
                    deep: '#4f46e5',
                    strong: '#a5b4fc',
                    rgb: '99,102,241',
                    shadowRgb: '55,48,163'
                }
            };

            return themes[categoryId] || themes.genel;
        },

        getEducationCategoryThemeVars: function (categoryOrId) {
            var theme = this.getEducationCategoryTheme(categoryOrId);
            return [
                '--education-accent:' + theme.accent,
                '--education-accent-deep:' + theme.deep,
                '--education-accent-strong:' + theme.strong,
                '--education-accent-rgb:' + theme.rgb,
                '--education-shadow-rgb:' + theme.shadowRgb
            ].join(';');
        },

        getEducationCategoryDeck: function (categoryOrId) {
            var category = typeof categoryOrId === 'string'
                ? this.getEducationCategoryById(categoryOrId)
                : categoryOrId;
            if (!category || !category.flashcard_deck_id || !this.educationCatalog) return null;
            return (this.educationCatalog.flashcard_decks || []).find(function (deck) {
                return deck.id === category.flashcard_deck_id;
            }) || null;
        },

        getEducationCategoryQuiz: function (categoryOrId) {
            var category = typeof categoryOrId === 'string'
                ? this.getEducationCategoryById(categoryOrId)
                : categoryOrId;
            if (!category || !category.quiz_section_id || !this.educationCatalog) return null;
            return (this.educationCatalog.quiz_sections || []).find(function (section) {
                return section.id === category.quiz_section_id;
            }) || null;
        },

        getEducationCategoryProgress: function (categoryId) {
            var category = this.getEducationCategoryById(categoryId);
            if (!category) {
                return {
                    percent_complete: 0,
                    status: 'not_started',
                    completed_items: 0,
                    in_progress_items: 0,
                    total_items: 0
                };
            }

            var items = [];
            if (category.flashcard_deck_id) {
                items.push(this.getEducationProgressItem('flashcard', category.flashcard_deck_id) || {
                    status: 'not_started',
                    percent_complete: 0
                });
            }
            if (category.quiz_section_id) {
                items.push(this.getEducationProgressItem('quiz', category.quiz_section_id) || {
                    status: 'not_started',
                    percent_complete: 0
                });
            }

            if (!items.length) {
                return {
                    percent_complete: 0,
                    status: 'not_started',
                    completed_items: 0,
                    in_progress_items: 0,
                    total_items: 0
                };
            }

            var percentSum = 0;
            var completedItems = 0;
            var inProgressItems = 0;
            items.forEach(function (item) {
                var percent = Math.max(0, Math.min(100, parseInt(item.percent_complete || 0, 10) || 0));
                percentSum += percent;
                if (item.status === 'completed' || percent >= 100) {
                    completedItems += 1;
                } else if (item.status === 'in_progress' || percent > 0) {
                    inProgressItems += 1;
                }
            });

            var status = 'not_started';
            if (completedItems === items.length) {
                status = 'completed';
            } else if (completedItems > 0 || inProgressItems > 0 || percentSum > 0) {
                status = 'in_progress';
            }

            return {
                percent_complete: Math.round(percentSum / items.length),
                status: status,
                completed_items: completedItems,
                in_progress_items: inProgressItems,
                total_items: items.length
            };
        },

        getEducationCategoryPercentLabel: function (categoryId) {
            return String(this.getEducationCategoryProgress(categoryId).percent_complete || 0) + '%';
        },

        getEducationCategoryStatusLabel: function (categoryId) {
            var progress = this.getEducationCategoryProgress(categoryId);
            if (progress.status === 'completed') return this.t('landing.education_status_completed');
            if (progress.status === 'in_progress') return this.t('landing.education_status_in_progress');
            return this.t('landing.education_status_not_started');
        },

        educationStartedCategoryCount: function () {
            var self = this;
            return this.getEducationCategories().filter(function (category) {
                return self.getEducationCategoryProgress(category.id).status !== 'not_started';
            }).length;
        },

        hasEducationQuickAction: function (sectionType) {
            var category = this.getEducationActiveCategory();
            if (!category) return false;
            if (sectionType === 'quiz') return !!category.quiz_section_id;
            if (sectionType === 'flashcards') return !!category.flashcard_deck_id;
            if (sectionType === 'pdfs') return !!(this.educationCatalog && (this.educationCatalog.pdfs || []).length);
            if (sectionType === 'progress') return true;
            return false;
        },

        getEducationQuickActionLabel: function (sectionType) {
            var category = this.getEducationActiveCategory();
            if (!category) return '';

            if (sectionType === 'quiz') {
                if (!category.quiz_section_id) return '';
                var quizProgress = this.getEducationProgressItem('quiz', category.quiz_section_id);
                return quizProgress && quizProgress.status !== 'not_started'
                    ? this.t('landing.education_continue_quiz')
                    : this.t('landing.education_start_quiz');
            }

            if (sectionType === 'flashcards') {
                if (!category.flashcard_deck_id) return '';
                var flashcardProgress = this.getEducationProgressItem('flashcard', category.flashcard_deck_id);
                return flashcardProgress && flashcardProgress.status !== 'not_started'
                    ? this.t('landing.education_continue_deck')
                    : this.t('landing.education_view_deck');
            }

            if (sectionType === 'pdfs') {
                return this.t('landing.education_pdf_library');
            }

            if (sectionType === 'progress') {
                return this.t('landing.education_progress_title');
            }

            return '';
        },

        resolveEducationMobileSection: function (sectionType) {
            var category = this.getEducationActiveCategory();
            var hasQuiz = !!(category && category.quiz_section_id);
            var hasFlashcards = !!(category && category.flashcard_deck_id);
            var hasPdfs = !!(this.educationCatalog && (this.educationCatalog.pdfs || []).length);

            if (sectionType === 'quiz' && hasQuiz) return 'quiz';
            if (sectionType === 'flashcards' && hasFlashcards) return 'flashcards';
            if (sectionType === 'pdfs' && hasPdfs) return 'pdfs';
            if (hasQuiz) return 'quiz';
            if (hasFlashcards) return 'flashcards';
            if (hasPdfs) return 'pdfs';
            return 'quiz';
        },

        setEducationMobileSection: function (sectionType, options) {
            var nextSection = this.resolveEducationMobileSection(sectionType);
            var force = !!(options && options.force);
            var activeCategory = this.getEducationActiveCategory();

            if (!force && this.educationMobileSection === nextSection) {
                if (
                    activeCategory &&
                    activeCategory.id &&
                    this.educationMobileSectionByCategory[activeCategory.id] !== nextSection
                ) {
                    this.educationMobileSectionByCategory[activeCategory.id] = nextSection;
                    this.saveEducationMobileSectionPreferences();
                }
                return;
            }

            this.educationMobileSection = nextSection;
            if (activeCategory && activeCategory.id) {
                this.educationMobileSectionByCategory[activeCategory.id] = nextSection;
                this.saveEducationMobileSectionPreferences();
            }
        },

        isEducationMobileSectionActive: function (sectionType) {
            return this.educationMobileSection === this.resolveEducationMobileSection(sectionType);
        },

        getEducationSectionAnchorId: function (sectionType) {
            if (sectionType === 'quiz') return 'education-quiz-panel';
            if (sectionType === 'flashcards') return 'education-flashcards-panel';
            if (sectionType === 'pdfs') return 'education-pdf-library-panel';
            return 'education-progress-overview';
        },

        openEducationMobileSection: function (sectionType) {
            var nextSection = this.resolveEducationMobileSection(sectionType);
            this.setEducationMobileSection(nextSection, { force: true });

            var self = this;
            window.setTimeout(function () {
                self.scrollEducationSection(self.getEducationSectionAnchorId(nextSection));
            }, 0);
        },

        scrollEducationSection: function (sectionId) {
            if (!sectionId) return;
            var section = document.getElementById(sectionId);
            if (!section) return;
            section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        },

        setEducationCategory: function (categoryId, options) {
            var categories = this.getEducationCategories();
            if (!categories.length) return;

            var category = this.getEducationCategoryById(categoryId) || categories[0];
            if (!category) return;

            var force = !!(options && options.force);
            var syncPanels = !options || options.syncPanels !== false;
            if (!force && this.educationSelectedCategoryId === category.id && !syncPanels) {
                return;
            }

            this.educationSelectedCategoryId = category.id;
            this.setEducationMobileSection(
                this.educationMobileSectionByCategory[category.id] || this.educationMobileSection || '',
                { force: force }
            );
            if (!syncPanels) return;

            if (category.flashcard_deck_id) {
                if (force || this.educationSelectedDeckId !== category.flashcard_deck_id) {
                    this.selectEducationDeck(category.flashcard_deck_id);
                }
            } else {
                this.educationSelectedDeckId = '';
                this.educationSelectedDeck = null;
                this.educationFlashcardIndex = 0;
                this.educationFlashcardFlipped = false;
            }

            if (category.quiz_section_id) {
                if (force || this.educationSelectedQuizId !== category.quiz_section_id) {
                    this.selectEducationQuiz(category.quiz_section_id);
                }
            } else {
                this.educationSelectedQuizId = '';
                this.educationSelectedQuiz = null;
                this.educationQuizIndex = 0;
                this.educationQuizAnswers = {};
                this.resetEducationQuizExplanationState();
                this.resetEducationQuizExplanationEditorState();
            }
        },

        educationCompletedCount: function () {
            return Object.values(this.educationProgressMap || {}).filter(function (item) {
                return item && item.status === 'completed';
            }).length;
        },

        educationInProgressCount: function () {
            return Object.values(this.educationProgressMap || {}).filter(function (item) {
                return item && item.status === 'in_progress';
            }).length;
        },

        getEducationStatusLabel: function (itemType, itemKey) {
            var progress = this.getEducationProgressItem(itemType, itemKey);
            if (!progress || progress.status === 'not_started') return this.t('landing.education_status_not_started');
            if (progress.status === 'completed') return this.t('landing.education_status_completed');
            return this.t('landing.education_status_in_progress');
        },

        getEducationPercentLabel: function (itemType, itemKey) {
            var progress = this.getEducationProgressItem(itemType, itemKey);
            return progress ? String(progress.percent_complete || 0) + '%' : '0%';
        },

        getEducationModeratorCategories: function () {
            var preferredOrder = ['Genel', 'Patent', 'Marka', 'Co\u011frafi \u0130\u015faret', 'Tasar\u0131m'];
            var seen = {};
            var titles = [];

            preferredOrder.forEach(function (title) {
                if (seen[title]) return;
                seen[title] = true;
                titles.push(title);
            });

            (this.educationCatalog && this.educationCatalog.categories || []).forEach(function (category) {
                var title = String((category && category.title) || '').trim();
                if (!title || seen[title]) return;
                seen[title] = true;
                titles.push(title);
            });

            return titles;
        },

        getEducationModerationCompositeKey: function (itemType, itemId) {
            return String(itemType || '') + '::' + String(itemId || '');
        },

        isEducationModerationBusy: function (itemType, itemId) {
            return !!this.educationModerationBusyMap[this.getEducationModerationCompositeKey(itemType, itemId)];
        },

        normalizeEducationQuizAnswers: function (section, storedAnswers) {
            if (!section || !Array.isArray(section.questions)) return {};
            var incomingAnswers = storedAnswers && typeof storedAnswers === 'object' ? storedAnswers : {};
            var normalizedAnswers = {};

            section.questions.forEach(function (question) {
                if (!question || !question.id) return;
                if (incomingAnswers[question.id]) {
                    normalizedAnswers[question.id] = incomingAnswers[question.id];
                    return;
                }
                if (question.legacy_id && incomingAnswers[question.legacy_id]) {
                    normalizedAnswers[question.id] = incomingAnswers[question.legacy_id];
                }
            });

            return normalizedAnswers;
        },

        refreshEducationAfterModeration: function () {
            var activeCategory = this.getEducationActiveCategory();
            var activeCategoryId = activeCategory && activeCategory.id ? activeCategory.id : this.educationSelectedCategoryId;
            this.resetEducationQuizExplanationEditorState();
            var self = this;
            return this.loadEducationCatalog({ force: true })
                .then(function () {
                    self.setEducationCategory(activeCategoryId, { force: true });
                });
        },

        applyEducationModeration: function (payload, options) {
            if (!this.educationCanModerate || !payload || !payload.item_type || !payload.item_id) {
                return Promise.resolve(null);
            }

            var self = this;
            var moderationKey = this.getEducationModerationCompositeKey(payload.item_type, payload.item_id);
            this.educationModerationBusyMap[moderationKey] = true;
            this.educationError = '';

            return fetch('/api/v1/education/moderation', {
                method: 'PUT',
                headers: Object.assign({ 'Content-Type': 'application/json' }, this.getAuthHeaders()),
                body: JSON.stringify(payload)
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('education_moderation_failed');
                    return res.json();
                })
                .then(function (item) {
                    var shouldRefresh = !!(options && options.refresh);
                    if (!shouldRefresh && item && item.item_type === 'flashcard') {
                        var currentCard = self.currentEducationFlashcard();
                        if (currentCard && currentCard.id === item.item_id) {
                            currentCard.category_title = item.category_title || currentCard.category_title;
                        }
                    }
                    if (!shouldRefresh && item && item.item_type === 'quiz_question') {
                        var currentQuestion = self.currentEducationQuizQuestion();
                        if (currentQuestion && currentQuestion.id === item.item_id) {
                            currentQuestion.category_title = item.category_title || currentQuestion.category_title;
                            if (Object.prototype.hasOwnProperty.call(item, 'explanation')) {
                                currentQuestion.explanation = item.explanation || '';
                            }
                            if (Object.prototype.hasOwnProperty.call(item, 'summary')) {
                                currentQuestion.summary = item.summary || '';
                            }
                            if (!self.shouldShowEducationQuizExplainButton(currentQuestion)) {
                                self.resetEducationQuizExplanationState();
                            }
                        }
                    }
                    if (shouldRefresh) {
                        return self.refreshEducationAfterModeration();
                    }
                    return item;
                })
                .catch(function () {
                    self.educationError = self.t('landing.education_tester_save_failed');
                    return null;
                })
                .finally(function () {
                    delete self.educationModerationBusyMap[moderationKey];
                });
        },

        setEducationFlashcardTesterCategory: function (categoryTitle) {
            var card = this.currentEducationFlashcard();
            if (!card || !categoryTitle || card.category_title === categoryTitle) return;
            return this.applyEducationModeration(
                {
                    item_type: 'flashcard',
                    item_id: card.id,
                    category_title: categoryTitle
                },
                { refresh: true }
            );
        },

        deleteEducationFlashcard: function () {
            var card = this.currentEducationFlashcard();
            if (!card) return;
            if (!window.confirm(this.t('landing.education_tester_delete_confirm'))) return;
            return this.applyEducationModeration(
                {
                    item_type: 'flashcard',
                    item_id: card.id,
                    deleted: true
                },
                { refresh: true }
            );
        },

        setEducationQuizTesterCategory: function (categoryTitle) {
            var question = this.currentEducationQuizQuestion();
            if (!question || !categoryTitle || question.category_title === categoryTitle) return;
            return this.applyEducationModeration(
                {
                    item_type: 'quiz_question',
                    item_id: question.id,
                    category_title: categoryTitle
                },
                { refresh: true }
            );
        },

        openEducationQuizExplanationEditor: function () {
            var question = this.currentEducationQuizQuestion();
            if (!question) return;
            this.educationQuizExplanationEditorQuestionId = question.id;
            this.educationQuizExplanationDraft = question.explanation || '';
            this.educationQuizSummaryDraft = question.summary || '';
            this.educationQuizExplanationEditorOpen = true;
        },

        resetEducationQuizExplanationEditorState: function () {
            this.educationQuizExplanationEditorOpen = false;
            this.educationQuizExplanationEditorQuestionId = '';
            this.educationQuizExplanationDraft = '';
            this.educationQuizSummaryDraft = '';
        },

        cancelEducationQuizExplanationEdit: function () {
            this.resetEducationQuizExplanationEditorState();
        },

        isEducationQuizExplanationEditorOpen: function (question) {
            return !!(
                question &&
                this.educationQuizExplanationEditorOpen &&
                this.educationQuizExplanationEditorQuestionId === question.id
            );
        },

        saveEducationQuizExplanationEdit: function () {
            var question = this.currentEducationQuizQuestion();
            if (!question) return Promise.resolve(null);

            var self = this;
            return this.applyEducationModeration(
                {
                    item_type: 'quiz_question',
                    item_id: question.id,
                    explanation: this.educationQuizExplanationDraft,
                    summary: this.educationQuizSummaryDraft
                },
                { refresh: false }
            ).then(function (item) {
                if (item) {
                    self.resetEducationQuizExplanationEditorState();
                }
                return item;
            });
        },

        deleteEducationQuizQuestion: function () {
            var question = this.currentEducationQuizQuestion();
            if (!question) return;
            if (!window.confirm(this.t('landing.education_tester_delete_confirm'))) return;
            return this.applyEducationModeration(
                {
                    item_type: 'quiz_question',
                    item_id: question.id,
                    deleted: true
                },
                { refresh: true }
            );
        },

        loadEducationCatalog: function (options) {
            var forceReload = !!(options && options.force);
            if (this.educationLoading) {
                return Promise.resolve(this.educationCatalog);
            }
            if (this.educationCatalog && !forceReload) {
                return Promise.resolve(this.educationCatalog);
            }
            this.educationLoading = true;
            this.educationError = '';

            var self = this;
            if (forceReload) {
                this.educationCatalog = null;
            }

            return fetch('/api/v1/education/catalog')
                .then(function (res) {
                    if (!res.ok) throw new Error('education_catalog_failed');
                    return res.json();
                })
                .then(function (data) {
                    self.educationCatalog = data;
                    self.setEducationCategory(self.educationSelectedCategoryId, { force: true });
                    return data;
                })
                .catch(function () {
                    self.educationError = self.t('landing.education_load_failed');
                    return null;
                })
                .finally(function () {
                    self.educationLoading = false;
                });
        },

        loadEducationProgress: function () {
            if (!this.isLoggedIn) return;
            this.educationProgressLoading = true;

            var self = this;
            fetch('/api/v1/education/progress', {
                headers: this.getAuthHeaders()
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('education_progress_failed');
                    return res.json();
                })
                .then(function (data) {
                    var localItems = Object.values(self.educationProgressMap || {});
                    if (localItems.length > 0) {
                        self.syncEducationProgress(false);
                        return;
                    }
                    self.replaceEducationProgressItems((data && data.items) || []);
                })
                .catch(function () {
                    self.educationProgressNotice = self.t('landing.education_progress_local_only');
                })
                .finally(function () {
                    self.educationProgressLoading = false;
                });
        },

        syncEducationProgress: function (showNotice) {
            if (!this.isLoggedIn) return;

            var payloadItems = Object.values(this.educationProgressMap || {}).map(function (item) {
                return {
                    item_type: item.item_type,
                    item_key: item.item_key,
                    status: item.status,
                    percent_complete: item.percent_complete || 0,
                    progress_data: item.progress_data || {}
                };
            });
            if (!payloadItems.length) return;

            var self = this;
            fetch('/api/v1/education/progress/sync', {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, this.getAuthHeaders()),
                body: JSON.stringify({ items: payloadItems })
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('education_progress_sync_failed');
                    return res.json();
                })
                .then(function (data) {
                    self.replaceEducationProgressItems((data && data.items) || []);
                    if (showNotice !== false) {
                        self.educationProgressNotice = self.t('landing.education_progress_synced');
                    }
                })
                .catch(function () {
                    self.educationProgressNotice = self.t('landing.education_progress_local_only');
                });
        },

        persistEducationProgress: function (payload) {
            if (!payload || !payload.item_type || !payload.item_key) return;

            var percentComplete = Math.max(0, Math.min(100, parseInt(payload.percent_complete || 0, 10) || 0));
            var status = payload.status || 'not_started';
            if (percentComplete >= 100) status = 'completed';
            else if (percentComplete > 0 && status === 'not_started') status = 'in_progress';

            var item = {
                item_type: payload.item_type,
                item_key: payload.item_key,
                status: status,
                percent_complete: percentComplete,
                progress_data: payload.progress_data || {}
            };
            this.educationProgressMap[this.getEducationProgressCompositeKey(item.item_type, item.item_key)] = item;
            this.saveLocalEducationProgressStore();

            if (!this.isLoggedIn) {
                this.educationProgressNotice = this.t('landing.education_progress_local_only');
                return;
            }

            var self = this;
            fetch('/api/v1/education/progress', {
                method: 'PUT',
                headers: Object.assign({ 'Content-Type': 'application/json' }, this.getAuthHeaders()),
                body: JSON.stringify(item)
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('education_progress_save_failed');
                    return res.json();
                })
                .then(function (savedItem) {
                    if (savedItem && savedItem.item_type && savedItem.item_key) {
                        self.educationProgressMap[self.getEducationProgressCompositeKey(savedItem.item_type, savedItem.item_key)] = savedItem;
                        self.saveLocalEducationProgressStore();
                    }
                    self.educationProgressNotice = self.t('landing.education_progress_synced');
                })
                .catch(function () {
                    self.educationProgressNotice = self.t('landing.education_progress_local_only');
                });
        },

        selectEducationDeck: function (deckId) {
            if (!deckId) return;
            this.educationDeckLoading = true;
            this.educationSelectedDeckId = deckId;
            this.educationSelectedDeck = null;
            this.educationFlashcardIndex = 0;
            this.educationFlashcardFlipped = false;

            var self = this;
            fetch('/api/v1/education/flashcards/' + encodeURIComponent(deckId))
                .then(function (res) {
                    if (!res.ok) throw new Error('education_deck_failed');
                    return res.json();
                })
                .then(function (deck) {
                    self.educationSelectedDeck = deck;
                    var progress = self.getEducationProgressItem('flashcard', deck.id);
                    var lastIndex = progress && progress.progress_data ? parseInt(progress.progress_data.last_index || 0, 10) || 0 : 0;
                    self.educationFlashcardIndex = Math.max(0, Math.min(lastIndex, Math.max((deck.cards || []).length - 1, 0)));
                    self.educationFlashcardFlipped = false;
                })
                .catch(function () {
                    self.educationError = self.t('landing.education_load_failed');
                })
                .finally(function () {
                    self.educationDeckLoading = false;
                });
        },

        currentEducationFlashcard: function () {
            if (!this.educationSelectedDeck || !this.educationSelectedDeck.cards || !this.educationSelectedDeck.cards.length) return null;
            return this.educationSelectedDeck.cards[this.educationFlashcardIndex] || this.educationSelectedDeck.cards[0];
        },

        toggleEducationFlashcard: function () {
            this.educationFlashcardFlipped = !this.educationFlashcardFlipped;
            if (this.educationFlashcardFlipped) {
                this.recordEducationFlashcardProgress(false);
            }
        },

        recordEducationFlashcardProgress: function (markCompleted) {
            var deck = this.educationSelectedDeck;
            if (!deck || !deck.cards || !deck.cards.length) return;

            var progress = this.getEducationProgressItem('flashcard', deck.id);
            var seenIds = [];
            if (progress && progress.progress_data && Array.isArray(progress.progress_data.seen_card_ids)) {
                seenIds = progress.progress_data.seen_card_ids.slice();
            }

            var currentCard = this.currentEducationFlashcard();
            if (currentCard && seenIds.indexOf(currentCard.id) === -1) {
                seenIds.push(currentCard.id);
            }

            var total = deck.cards.length || 1;
            var percentComplete = Math.round((seenIds.length / total) * 100);
            if (markCompleted) percentComplete = 100;

            this.persistEducationProgress({
                item_type: 'flashcard',
                item_key: deck.id,
                status: markCompleted ? 'completed' : (percentComplete > 0 ? 'in_progress' : 'not_started'),
                percent_complete: percentComplete,
                progress_data: {
                    last_index: this.educationFlashcardIndex,
                    seen_card_ids: seenIds
                }
            });
        },

        previousEducationFlashcard: function () {
            if (!this.educationSelectedDeck || this.educationFlashcardIndex <= 0) return;
            this.recordEducationFlashcardProgress(false);
            this.educationFlashcardIndex -= 1;
            this.educationFlashcardFlipped = false;
        },

        nextEducationFlashcard: function () {
            if (!this.educationSelectedDeck || !this.educationSelectedDeck.cards || !this.educationSelectedDeck.cards.length) return;
            if (this.educationFlashcardIndex >= this.educationSelectedDeck.cards.length - 1) {
                this.recordEducationFlashcardProgress(true);
                return;
            }
            this.recordEducationFlashcardProgress(false);
            this.educationFlashcardIndex += 1;
            this.educationFlashcardFlipped = false;
        },

        selectEducationQuiz: function (sectionId) {
            if (!sectionId) return;
            this.educationQuizLoading = true;
            this.educationSelectedQuizId = sectionId;
            this.educationSelectedQuiz = null;
            this.educationQuizIndex = 0;
            this.educationQuizAnswers = {};
            this.resetEducationQuizExplanationState();
            this.resetEducationQuizExplanationEditorState();

            var self = this;
            fetch('/api/v1/education/quizzes/' + encodeURIComponent(sectionId))
                .then(function (res) {
                    if (!res.ok) throw new Error('education_quiz_failed');
                    return res.json();
                })
                .then(function (section) {
                    self.educationSelectedQuiz = section;
                    var progress = self.getEducationProgressItem('quiz', section.id);
                    var answers = progress && progress.progress_data && progress.progress_data.answers
                        ? progress.progress_data.answers
                        : {};
                    var lastIndex = progress && progress.progress_data ? parseInt(progress.progress_data.last_index || 0, 10) || 0 : 0;
                    self.educationQuizAnswers = self.normalizeEducationQuizAnswers(section, answers);
                    self.educationQuizIndex = Math.max(0, Math.min(lastIndex, Math.max((section.questions || []).length - 1, 0)));
                })
                .catch(function () {
                    self.educationError = self.t('landing.education_load_failed');
                })
                .finally(function () {
                    self.educationQuizLoading = false;
                });
        },

        currentEducationQuizQuestion: function () {
            if (!this.educationSelectedQuiz || !this.educationSelectedQuiz.questions || !this.educationSelectedQuiz.questions.length) return null;
            return this.educationSelectedQuiz.questions[this.educationQuizIndex] || this.educationSelectedQuiz.questions[0];
        },

        getEducationQuizAnswer: function (questionId) {
            return this.educationQuizAnswers[questionId] || '';
        },

        isEducationQuizQuestionAnswered: function (question) {
            return !!(question && this.getEducationQuizAnswer(question.id));
        },

        isEducationQuizAnswerCorrect: function (question) {
            var selectedAnswer = question ? this.getEducationQuizAnswer(question.id) : '';
            return !!selectedAnswer && selectedAnswer === question.correct_option_id;
        },

        isEducationQuizAnswerIncorrect: function (question) {
            var selectedAnswer = question ? this.getEducationQuizAnswer(question.id) : '';
            return !!selectedAnswer && selectedAnswer !== question.correct_option_id;
        },

        shouldShowEducationQuizExplainButton: function (question) {
            return this.isEducationQuizAnswerIncorrect(question) && !!((question && question.summary) || (question && question.explanation));
        },

        getEducationQuizExplanationText: function (question) {
            if (!question) return '';
            return question.explanation || '';
        },

        getEducationQuizSummaryText: function (question) {
            if (!question) return '';
            return question.summary || '';
        },

        clearEducationQuizExplanationTimer: function () {
            if (this._educationQuizExplanationTimer) {
                window.clearTimeout(this._educationQuizExplanationTimer);
                this._educationQuizExplanationTimer = null;
            }
        },

        resetEducationQuizExplanationState: function () {
            this.clearEducationQuizExplanationTimer();
            this.educationQuizExplanationLoading = false;
            this.educationQuizExplanationOpen = false;
        },

        toggleEducationQuizExplanation: function () {
            var question = this.currentEducationQuizQuestion();
            if (!this.shouldShowEducationQuizExplainButton(question)) return;
            if (this.educationQuizExplanationOpen) {
                this.resetEducationQuizExplanationState();
                return;
            }
            if (this.educationQuizExplanationLoading) return;

            var self = this;
            var questionId = question.id;
            this.clearEducationQuizExplanationTimer();
            this.educationQuizExplanationLoading = true;
            this.educationQuizExplanationOpen = false;
            this._educationQuizExplanationTimer = window.setTimeout(function () {
                self._educationQuizExplanationTimer = null;
                var currentQuestion = self.currentEducationQuizQuestion();
                if (!currentQuestion || currentQuestion.id !== questionId || !self.shouldShowEducationQuizExplainButton(currentQuestion)) {
                    self.educationQuizExplanationLoading = false;
                    self.educationQuizExplanationOpen = false;
                    return;
                }
                self.educationQuizExplanationLoading = false;
                self.educationQuizExplanationOpen = true;
            }, 1400);
        },

        educationQuizCorrectCount: function () {
            var section = this.educationSelectedQuiz;
            if (!section || !section.questions) return 0;
            var self = this;
            return section.questions.filter(function (question) {
                return self.educationQuizAnswers[question.id] && self.educationQuizAnswers[question.id] === question.correct_option_id;
            }).length;
        },

        educationQuizAnsweredCount: function () {
            return Object.keys(this.educationQuizAnswers || {}).length;
        },

        isEducationQuizComplete: function () {
            if (!this.educationSelectedQuiz || !this.educationSelectedQuiz.questions) return false;
            return this.educationQuizAnsweredCount() >= this.educationSelectedQuiz.questions.length;
        },

        getEducationQuizOptionClasses: function (question, option) {
            var base = 'w-full rounded-[22px] border px-4 py-4 text-left transition-all';
            var selectedAnswer = this.getEducationQuizAnswer(question.id);

            if (!selectedAnswer) {
                return base + ' hover:-translate-y-[1px]';
            }
            if (option.id === question.correct_option_id) {
                return base + ' shadow-[0_0_0_1px_rgba(16,185,129,0.08)]';
            }
            if (option.id === selectedAnswer) {
                return base + ' shadow-[0_0_0_1px_rgba(239,68,68,0.08)]';
            }
            return base;
        },

        getEducationQuizOptionStyle: function (question, option) {
            var selectedAnswer = this.getEducationQuizAnswer(question.id);

            if (!selectedAnswer) {
                return 'background:rgba(15,23,42,0.28);border-color:var(--color-border);color:var(--color-text-primary)';
            }
            if (option.id === question.correct_option_id) {
                return 'background:rgba(16,185,129,0.08);border-color:rgba(16,185,129,0.48);color:var(--color-text-primary)';
            }
            if (option.id === selectedAnswer) {
                return 'background:rgba(239,68,68,0.08);border-color:rgba(239,68,68,0.48);color:var(--color-text-primary)';
            }
            return 'background:rgba(15,23,42,0.18);border-color:var(--color-border);color:var(--color-text-muted)';
        },

        shouldShowEducationQuizOptionFeedback: function (question, option) {
            if (!question || !option || !this.isEducationQuizQuestionAnswered(question)) return false;
            return !!option.short_feedback || !!this.getEducationQuizOptionFeedbackKey(question, option);
        },

        getEducationQuizOptionFeedbackKey: function (question, option) {
            if (!this.shouldShowEducationQuizOptionFeedback(question, option)) return '';
            var selectedAnswer = this.getEducationQuizAnswer(question.id);
            if (option.id === question.correct_option_id) {
                return selectedAnswer === option.id ? 'landing.education_thats_right' : 'landing.education_right_answer';
            }
            if (option.id === selectedAnswer) {
                return 'landing.education_not_quite';
            }
            return '';
        },

        getEducationQuizOptionFeedbackStyle: function (question, option) {
            var feedbackKey = this.getEducationQuizOptionFeedbackKey(question, option);
            if (!feedbackKey) return 'color:var(--color-text-muted)';
            if (feedbackKey === 'landing.education_not_quite') {
                return 'color:rgb(248 113 113)';
            }
            return 'color:rgb(52 211 153)';
        },

        persistEducationQuizProgress: function () {
            var section = this.educationSelectedQuiz;
            if (!section || !section.questions || !section.questions.length) return;

            var answeredIds = Object.keys(this.educationQuizAnswers || {});
            var total = section.questions.length || 1;
            var percentComplete = Math.round((answeredIds.length / total) * 100);
            var correctCount = this.educationQuizCorrectCount();

            this.persistEducationProgress({
                item_type: 'quiz',
                item_key: section.id,
                status: answeredIds.length >= total ? 'completed' : (answeredIds.length > 0 ? 'in_progress' : 'not_started'),
                percent_complete: answeredIds.length >= total ? 100 : percentComplete,
                progress_data: {
                    answers: this.educationQuizAnswers,
                    answered_question_ids: answeredIds,
                    correct_count: correctCount,
                    last_index: this.educationQuizIndex
                }
            });
        },

        answerEducationQuestion: function (optionId) {
            var question = this.currentEducationQuizQuestion();
            if (!question) return;

            var answers = Object.assign({}, this.educationQuizAnswers || {});
            answers[question.id] = optionId;
            this.educationQuizAnswers = answers;
            this.resetEducationQuizExplanationState();
            this.resetEducationQuizExplanationEditorState();
            this.persistEducationQuizProgress();
        },

        previousEducationQuizQuestion: function () {
            if (!this.educationSelectedQuiz || this.educationQuizIndex <= 0) return;
            this.educationQuizIndex -= 1;
            this.resetEducationQuizExplanationState();
            this.resetEducationQuizExplanationEditorState();
            this.persistEducationQuizProgress();
        },

        nextEducationQuizQuestion: function () {
            if (!this.educationSelectedQuiz || !this.educationSelectedQuiz.questions || !this.educationSelectedQuiz.questions.length) return;
            if (this.educationQuizIndex >= this.educationSelectedQuiz.questions.length - 1) {
                this.resetEducationQuizExplanationState();
                this.resetEducationQuizExplanationEditorState();
                this.persistEducationQuizProgress();
                return;
            }
            this.educationQuizIndex += 1;
            this.resetEducationQuizExplanationState();
            this.resetEducationQuizExplanationEditorState();
            this.persistEducationQuizProgress();
        },

        openEducationPdf: function (pdf) {
            if (!pdf || !pdf.download_url) return;
            this.persistEducationProgress({
                item_type: 'pdf',
                item_key: pdf.id,
                status: 'in_progress',
                percent_complete: 25,
                progress_data: {
                    opened: true,
                    last_opened_at: new Date().toISOString()
                }
            });
            window.open(pdf.download_url, '_blank', 'noopener');
        },

        markEducationPdfReviewed: function (pdf) {
            if (!pdf) return;
            this.persistEducationProgress({
                item_type: 'pdf',
                item_key: pdf.id,
                status: 'completed',
                percent_complete: 100,
                progress_data: {
                    opened: true,
                    reviewed: true,
                    reviewed_at: new Date().toISOString()
                }
            });
        },

        // ==================== IMAGE UPLOAD ====================
        onImageSelected: function (event) {
            var file = event.target.files && event.target.files[0];
            if (!file) return;
            this._setImage(file);
        },

        handleDrop: function (event) {
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

        _setImage: function (file) {
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

        clearImage: function () {
            this.selectedImage = null;
            this.imagePreview = '';
            this.imageName = '';
            this.clearRiskReportState();
            if (this.$refs.landingImageInput) {
                this.$refs.landingImageInput.value = '';
            }
            if (!this.searchQuery.trim()) {
                this.searchResults = [];
                this.searchError = '';
                this.expandedResult = null;
                this.clearRiskReportState();
            }
        },

        // ==================== CLASS FINDER ====================
        _syncClassInput: function () {
            // Keep textarea in sync with selected classes (backend-compatible format)
            this.classInput = this.selectedClasses.join(', ');
        },

        submitClassInput: function () {
            var input = this.classInput.trim();
            if (!input) return;
            this.classError = '';

            // Detect: if all comma/space-separated parts are numbers 1-45, treat as manual
            var parts = input.split(/[,\s]+/).filter(function (p) { return p.length > 0; });
            var allNumbers = parts.every(function (p) {
                var n = parseInt(p, 10);
                return !isNaN(n) && n >= 1 && n <= 45 && String(n) === p.trim();
            });

            if (allNumbers) {
                // Manual class addition
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
                // AI suggestion
                this.suggestClasses();
            }
        },

        suggestClasses: function () {
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
                .then(function (res) {
                    if (!res.ok) throw new Error('Suggestion failed');
                    return res.json();
                })
                .then(function (data) {
                    self.suggestedClasses = data.suggestions || [];
                    if (self.suggestedClasses.length === 0) {
                        self.classError = self.t('search.no_class_suggestions');
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

        selectClass: function (cls) {
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
        toggleBrowseAll: function () {
            this.showBrowse = !this.showBrowse;
            if (this.showBrowse && this.allClasses.length === 0) {
                this.loadAllClasses();
            }
        },

        loadAllClasses: function () {
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

        toggleBrowseClass: function (num) {
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

        // --- Shared ---
        removeClass: function (num) {
            var idx = this.selectedClasses.indexOf(num);
            if (idx !== -1) {
                this.selectedClasses.splice(idx, 1);
            }
            this._syncClassInput();
        },

        clearAllClasses: function () {
            this.selectedClasses = [];
            this._syncClassInput();
        },

        // ==================== PUBLIC SEARCH ====================
        // ==================== SEARCH HISTORY ====================
        loadSearchHistory: function () {
            try {
                var raw = localStorage.getItem('search_history');
                this.searchHistory = raw ? JSON.parse(raw) : [];
            } catch (e) { this.searchHistory = []; }
        },

        saveSearchQuery: function (query) {
            if (!query || !query.trim()) return;
            var q = query.trim();
            this.searchHistory = this.searchHistory.filter(function (h) { return h.toLowerCase() !== q.toLowerCase(); });
            this.searchHistory.unshift(q);
            if (this.searchHistory.length > 20) this.searchHistory = this.searchHistory.slice(0, 20);
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch (e) { }
        },

        removeSearchHistoryItem: function (query) {
            this.searchHistory = this.searchHistory.filter(function (h) { return h !== query; });
            try { localStorage.setItem('search_history', JSON.stringify(this.searchHistory)); } catch (e) { }
        },

        clearSearchHistory: function () {
            this.searchHistory = [];
            try { localStorage.removeItem('search_history'); } catch (e) { }
            this.showSearchHistory = false;
        },

        filteredSearchHistory: function () {
            var q = (this.searchQuery || '').trim().toLowerCase();
            if (!q) return this.searchHistory.slice(0, 10);
            return this.searchHistory.filter(function (h) { return h.toLowerCase().indexOf(q) !== -1; }).slice(0, 10);
        },

        selectSearchHistoryItem: function (item) {
            this.searchQuery = item;
            this.showSearchHistory = false;
        },

        publicSearch: function () {
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
            this.clearRiskReportState();

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
                    .then(function (res) {
                        if (res.status === 429) {
                            return res.json().catch(function () { return {}; }).then(function (errData) {
                                var detail = errData.detail || errData;
                                if (window.AppUpgradeModal && typeof window.AppUpgradeModal.maybeHandle === 'function'
                                    && window.AppUpgradeModal.maybeHandle(detail, 'public_search')) {
                                    return null;
                                }
                                self.searchError = self.t('search.rate_limited');
                                return null;
                            });
                        }
                        if (!res.ok) throw new Error('Search failed');
                        return res.json();
                    })
                    .then(function (data) {
                        if (data) {
                            self.searchResults = (data.results || []).map(function (r) { r._showGoods = false; return r; });
                            self.resultsAtBottom = false;
                            self.expandedResult = null;
                            self.saveSearchQuery(query);
                            self.showSearchHistory = false;
                            if (self.searchResults.length === 0) {
                                self.searchError = self.t('search.no_results');
                            }
                        }
                    })
                    .catch(function () {
                        self.searchError = self.t('search.search_failed');
                    })
                    .finally(function () {
                        self.searchLoading = false;
                    });
            } else {
                // GET text-only search
                fetch('/api/v1/search/public?query=' + encodeURIComponent(query))
                    .then(function (res) {
                        if (res.status === 429) {
                            return res.json().catch(function () { return {}; }).then(function (errData) {
                                var detail = errData.detail || errData;
                                if (window.AppUpgradeModal && typeof window.AppUpgradeModal.maybeHandle === 'function'
                                    && window.AppUpgradeModal.maybeHandle(detail, 'public_search')) {
                                    return null;
                                }
                                self.searchError = self.t('search.rate_limited');
                                return null;
                            });
                        }
                        if (!res.ok) throw new Error('Search failed');
                        return res.json();
                    })
                    .then(function (data) {
                        if (data) {
                            self.searchResults = (data.results || []).map(function (r) { r._showGoods = false; return r; });
                            self.saveSearchQuery(query);
                            self.showSearchHistory = false;
                            if (self.searchResults.length === 0) {
                                self.searchError = self.t('search.no_results');
                            }
                        }
                    })
                    .catch(function () {
                        self.searchError = self.t('search.search_failed');
                    })
                    .finally(function () {
                        self.searchLoading = false;
                    });
            }
        },

        buildRiskReportCandidate: function (r) {
            var name = '';
            if (typeof getTrademarkDisplayName === 'function') {
                name = getTrademarkDisplayName(r);
            } else if (window.AppUtils && window.AppUtils.getTrademarkDisplayName) {
                name = window.AppUtils.getTrademarkDisplayName(r);
            }
            name = name || (r && (r.trademark_name || r.name || r.name_tr)) || (r && r.application_no ? '#' + r.application_no : this.t('search.risk_report_candidate'));

            return {
                name: name,
                application_no: (r && r.application_no) || null,
                status: (r && r.status) || null,
                status_code: (r && r.status_code) || null,
                nice_classes: (r && Array.isArray(r.nice_classes)) ? r.nice_classes : ((r && Array.isArray(r.classes)) ? r.classes : []),
                owner: (r && (r.owner || r.holder_name)) || null,
                attorney: (r && (r.attorney || r.attorney_name)) || null,
                image_url: (r && (r.image_url || r.image_path)) || null
            };
        },

        getRiskReportLanguage: function () {
            var locale = window.AppI18n && window.AppI18n.getLocale
                ? window.AppI18n.getLocale()
                : (this.currentLang || this.lang_code || 'tr');
            return ['tr', 'en', 'ar'].indexOf(locale) !== -1 ? locale : 'tr';
        },

        clearRiskReportOrdering: function () {
            if (this.searchResults && this.searchResults.length) {
                this.searchResults.forEach(function (result) {
                    delete result._riskReportScore;
                    delete result._riskReportRank;
                    delete result._riskReportApplicationNo;
                });
                this.searchResults = this.searchResults.slice();
            }
        },

        clearRiskReportState: function () {
            this.riskReport = null;
            this.riskReportError = '';
            this.clearRiskReportOrdering();
        },

        applyRiskReportOrdering: function (report, visibleResults) {
            if (!report || !Array.isArray(report.results)) return;
            this.clearRiskReportOrdering();
            var used = [];
            var ordered = [];
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
                used.push(source);
                ordered.push(source);
            }, this);
            var remainder = this.searchResults.filter(function (result) {
                return used.indexOf(result) === -1;
            });
            this.searchResults = ordered.concat(remainder);
            this.expandedResult = null;
        },

        openRiskReportPdf: function (report) {
            var reportId = report && report.report_id;
            var claimToken = report && report.claim_token;
            if (!reportId && claimToken) {
                var redirectUrl = '/dashboard?tab=reports&claim_risk_report=' + encodeURIComponent(claimToken);
                if (!this.getAuthToken()) {
                    this.queuePostAuthRedirect(redirectUrl);
                    this.searchError = this.t('search.risk_report_login_to_view');
                    this.showLogin = true;
                    return;
                }
                window.location.href = redirectUrl;
                return;
            }
            if (!reportId) {
                if (window.AppToast) window.AppToast.showToast(this.t('search.risk_report_failed'), 'error');
                return;
            }
            var viewer = window.open('', '_blank');
            if (viewer) viewer.opener = null;
            fetch('/api/v1/reports/' + encodeURIComponent(reportId) + '/download', {
                headers: this.getAuthHeaders()
            }).then(function (res) {
                if (!res.ok) {
                    return res.json().catch(function () { return {}; }).then(function (data) {
                        var err = new Error((data.detail && data.detail.message) || data.detail || 'download_failed');
                        err.status = res.status;
                        err.data = data;
                        throw err;
                    });
                }
                return res.blob();
            }).then(function (blob) {
                var url = window.URL.createObjectURL(blob);
                if (viewer) {
                    viewer.location.href = url;
                } else {
                    window.open(url, '_blank', 'noopener');
                }
                setTimeout(function () { window.URL.revokeObjectURL(url); }, 60000);
            }).catch(function (err) {
                if (viewer) viewer.close();
                if (window.AppToast) {
                    window.AppToast.showToast(this.t('reports.download_failed') + ': ' + ((err && err.message) || ''), 'error');
                }
            }.bind(this));
        },

        showRiskReportReadyNotification: function (report) {
            var self = this;
            if (!window.AppToast) return;
            window.AppToast.showToast(this.t('search.risk_report_ready_view'), 'success', {
                actionLabel: this.t('search.risk_report_open'),
                duration: 9000,
                onAction: function () {
                    self.openRiskReportPdf(report);
                }
            });
        },

        generateRiskReport: function () {
            var query = (this.searchQuery || '').trim();
            if (!query && !this.selectedImage) {
                this.searchError = this.t('search.live_search_name_required');
                return;
            }
            if (!this.selectedClasses || this.selectedClasses.length === 0) {
                this.searchError = this.t('search.risk_report_classes_required');
                return;
            }

            var token = this.getAuthToken();
            var endpoint = token
                ? '/api/v1/search/intelligent-risk-report'
                : '/api/v1/search/intelligent-risk-report/public';
            var language = this.getRiskReportLanguage();
            var classes = this.selectedClasses || [];

            var body = new FormData();
            if (query) body.append('query', query);
            if (classes.length) body.append('classes', classes.join(','));
            body.append('language', language);
            if (this.selectedImage) {
                body.append('image', this.selectedImage, this.selectedImage.name || 'query-logo');
            }

            var headers = token ? this.getAuthHeaders() : {};
            var self = this;
            var messageFromDetail = function (detail) {
                if (!detail) return '';
                if (typeof detail === 'string') return detail;
                return detail['message_' + language] || detail.message || detail.error || '';
            };

            this.riskReportLoading = true;
            this.riskReportError = '';
            this.riskReport = null;
            this.searchError = '';
            this.searchResults = [];

            fetch(endpoint, {
                method: 'POST',
                headers: headers,
                body: body
            })
                .then(function (res) {
                    return res.json().catch(function () { return {}; }).then(function (data) {
                        if (res.status === 401) {
                            self.showLogin = true;
                            throw new Error(self.t('auth.session_expired'));
                        }
                        if (res.status === 402 || res.status === 403) {
                            var detail = data.detail || data;
                            if (token && window.AppUpgradeModal && typeof window.AppUpgradeModal.maybeHandle === 'function'
                                && window.AppUpgradeModal.maybeHandle(detail, 'reports')) {
                                return null;
                            }
                            throw new Error(messageFromDetail(detail) || self.t('search.risk_report_failed'));
                        }
                        if (res.status === 429) {
                            throw new Error(messageFromDetail(data.detail || data) || self.t('search.search_failed'));
                        }
                        if (!res.ok) {
                            throw new Error(messageFromDetail(data.detail || data) || self.t('search.risk_report_failed'));
                        }
                        return data;
                    });
                })
                .then(function (data) {
                    if (!data) return;
                    if (data.cancelled) return;

                    var searchPayload = data.search || {};
                    var resultsArray = (searchPayload.results || []).slice(0, 20).map(function (r) {
                        r._showGoods = false;
                        return r;
                    });
                    self.searchResults = resultsArray;

                    self.applyRiskReportOrdering(data, resultsArray);
                    self.riskReport = data;
                    self.showRiskReportReadyNotification(data);
                })
                .catch(function (err) {
                    self.riskReportError = err.message || self.t('search.risk_report_failed');
                })
                .finally(function () {
                    self.riskReportLoading = false;
                });
        },

        // ==================== PORTFOLIO ====================
        portfolioTotalCount: 0,
        _portfolioAllResults: [],

        loadPortfolio: function (type, id, name) {
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
                .then(function (res) {
                    if (res.status === 429) {
                        self.searchError = self.t('search.rate_limited');
                        self.showPortfolio = false;
                        return null;
                    }
                    if (!res.ok) throw new Error('Portfolio failed');
                    return res.json();
                })
                .then(function (data) {
                    if (data) {
                        var all = data.results || [];
                        self._portfolioAllResults = all;
                        self.portfolioTotalCount = (data.total_count != null) ? data.total_count : all.length;
                        self.portfolioResults = all.slice(0, 5);
                        self.portfolioName = data.entity_name || name || id;
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

        closePortfolio: function () {
            this.showPortfolio = false;
            this.portfolioResults = [];
            this._portfolioAllResults = [];
            this.portfolioTotalCount = 0;
            this.portfolioName = '';
            this.portfolioType = '';
            this._portfolioEntityId = '';
        },

        downloadPortfolioCsv: function () {
            var id = this._portfolioEntityId;
            var type = this.portfolioType;
            if (!id || !type) return;
            var self = this;
            var token = this.getAuthToken();
            if (!token) {
                self.showLogin = false;
                if (typeof showUpgradeModal === 'function') {
                    showUpgradeModal({
                        error: 'upgrade_required',
                        current_plan: 'free',
                        upgrade_context: 'portfolio_download',
                        message: self.t('upgrade.portfolio_download_description')
                    }, 'portfolio_download');
                } else if (window.AppToast) {
                    AppToast.showToast('CSV indirmek için planınızı yükseltin', 'warning');
                }
                return;
            }
            var param = type === 'holder' ? 'holder_id' : 'attorney_no';
            var csvUrl = '/api/v1/portfolio/public/csv?' + param + '=' + encodeURIComponent(id);
            fetch(csvUrl, { headers: { 'Authorization': 'Bearer ' + token } })
                .then(function (res) {
                    if (res.status === 401) {
                        self.showLogin = true;
                        throw { handled: true };
                    }
                    if (res.status === 403) {
                        return res.json().catch(function () { return {}; }).then(function (data) {
                            if (typeof showUpgradeModal === 'function') {
                                showUpgradeModal((data && data.detail) || data || {}, 'portfolio_download');
                            } else if (window.AppToast) {
                                AppToast.showToast('CSV indirmek için planınızı yükseltin', 'warning');
                            }
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
                    a.download = (type === 'holder' ? 'sahip' : 'vekil') + '_' + id + '_portfolio.csv';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                })
                .catch(function (err) {
                    if (err && err.handled) return;
                    if (window.AppToast) AppToast.showToast('CSV indirilemedi', 'error');
                });
        },

        addPortfolioToWatchlist: function () {
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

            var token = this.getAuthToken();
            if (!token) {
                // Save redirect so after login user lands on dashboard with bulk modal
                localStorage.setItem('pending_studio_redirect', redirectUrl);
                this.showLogin = true;
                return;
            }
            window.location.href = redirectUrl;
        },

        // ==================== LOGIN ====================
        submitLogin: function () {
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
                .then(function (res) {
                    if (!res.ok) {
                        if (res.status === 401) throw new Error('invalid_credentials');
                        throw new Error('login_failed');
                    }
                    return res.json();
                })
                .then(function (data) {
                    if (data.access_token) {
                        self.isLoggedIn = true;
                        if (window.AppAuth && window.AppAuth.storeTokenPair) {
                            window.AppAuth.storeTokenPair(data);
                        } else {
                            localStorage.setItem('auth_token', data.access_token);
                            if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
                        }
                        var pendingRedirect = self.consumePostAuthRedirect();
                        if (pendingRedirect) {
                            window.location.href = pendingRedirect;
                        } else {
                            window.location.href = '/dashboard';
                        }
                    } else {
                        throw new Error('no_token');
                    }
                })
                .catch(function (err) {
                    if (err.message === 'invalid_credentials') {
                        self.loginError = self.t('auth.error_invalid_credentials') + ' ' + self.t('auth.no_account_prompt');
                    } else {
                        self.loginError = self.t('auth.error_generic');
                    }
                    self.loginLoading = false;
                });
        },

        // ==================== FORGOT PASSWORD ====================
        forgotRequestCode: function () {
            if (!this.forgotEmail) return;
            this.forgotLoading = true;
            this.forgotError = '';
            var self = this;
            fetch('/api/v1/auth/forgot-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: this.forgotEmail, lang: this.lang_code || 'tr' })
            })
                .then(function (res) {
                    if (!res.ok) throw new Error('request_failed');
                    return res.json();
                })
                .then(function (data) {
                    self.forgotStep = 'code';
                    self.forgotCode = '';
                    self.forgotNewPassword = '';
                    self.forgotConfirmPassword = '';
                    self.forgotError = '';
                    self.forgotSuccess = self.t('auth.check_email_for_code');
                })
                .catch(function () {
                    self.forgotError = self.t('auth.error_generic');
                })
                .finally(function () {
                    self.forgotLoading = false;
                });
        },

        forgotResetPassword: function () {
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
                .then(function (res) {
                    if (!res.ok) return res.json().then(function (d) { throw new Error(d.detail || 'reset_failed'); });
                    return res.json();
                })
                .then(function () {
                    self.forgotSuccess = self.t('auth.password_reset_success');
                    setTimeout(function () {
                        self.showForgotPassword = false;
                        self.showLogin = true;
                        self.loginEmail = self.forgotEmail;
                        self.loginPassword = '';
                    }, 2000);
                })
                .catch(function (err) {
                    self.forgotError = err.message === 'reset_failed'
                        ? self.t('auth.error_generic')
                        : (err.message || self.t('auth.error_generic'));
                })
                .finally(function () {
                    self.forgotLoading = false;
                });
        },

        // ==================== REGISTER ====================
        submitRegister: function () {
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
                .then(function (res) {
                    if (!res.ok) {
                        if (res.status === 409 || res.status === 400) throw new Error('email_taken');
                        throw new Error('register_failed');
                    }
                    return res.json();
                })
                .then(function (data) {
                    if (data.access_token) {
                        self.isLoggedIn = true;
                        if (window.AppAuth && window.AppAuth.storeTokenPair) {
                            window.AppAuth.storeTokenPair(data);
                        } else {
                            localStorage.setItem('auth_token', data.access_token);
                            if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
                        }
                        var pendingRedirect = self.consumePostAuthRedirect();
                        if (pendingRedirect) {
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
                .catch(function (err) {
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

        hasHighRisk: function () {
            if (!this.searchResults || this.searchResults.length === 0) return false;
            for (var i = 0; i < this.searchResults.length; i++) {
                if (this.searchResults[i].risk_score >= 0.65) return true;
            }
            return false;
        },

        getStudioCtaTitle: function () {
            if (this._lastSearchType === 'image') return this.t('landing.studio_cta_title_image');
            if (this._lastSearchType === 'both') return this.t('landing.studio_cta_title_both');
            return this.t('landing.studio_cta_title_text');
        },

        getStudioCtaDesc: function () {
            if (this._lastSearchType === 'image') return this.t('landing.studio_cta_desc_image');
            if (this._lastSearchType === 'both') return this.t('landing.studio_cta_desc_both');
            return this.t('landing.studio_cta_desc_text');
        },

        addToWatchlist: function (r) {
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

        goToStudio: function (mode) {
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
        formatNumber: function (n) {
            if (!n) return '0';
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n / 1000).toFixed(0) + 'K';
            return String(n);
        },

        getRiskBg: function (score) {
            if (score >= 0.9) return 'var(--color-risk-critical-bg)';
            if (score >= 0.7) return 'var(--color-risk-high-bg)';
            if (score >= 0.5) return 'var(--color-risk-medium-bg)';
            return 'var(--color-risk-low-bg)';
        },

        getRiskColor: function (score) {
            if (score >= 0.9) return 'var(--color-risk-critical-text)';
            if (score >= 0.7) return 'var(--color-risk-high-text)';
            if (score >= 0.5) return 'var(--color-risk-medium-text)';
            return 'var(--color-risk-low-text)';
        },

        getTextScore: function (result) {
            if (window.AppComponents && window.AppComponents.getOriginalTextScore) {
                return window.AppComponents.getOriginalTextScore(result);
            }
            if (result && result.path_a_score !== undefined && result.path_a_score !== null) return result.path_a_score;
            return (result && result.text_similarity) || 0;
        },

        getRiskReportScoreFraction: function (score) {
            var numeric = parseFloat(score);
            if (isNaN(numeric)) return 0;
            return Math.max(0, Math.min(100, numeric)) / 100;
        },

        getStatusColor: function (status) { return window.AppUtils.getStatusColor(status); },
        getStatusBg: function (status) { return window.AppUtils.getStatusBg(status); },
        translateStatus: function (status) {
            if (!status) return '';
            var map = {
                'başvuruldu': 'pending', 'basvuruldu': 'pending',
                'yayında': 'published', 'yayinda': 'published', 'yayınlandı': 'published',
                'tescil edildi': 'registered',
                'reddedildi': 'rejected',
                'geri çekildi': 'withdrawn', 'geri cekildi': 'withdrawn',
                'i̇tiraz edildi': 'opposed', 'itiraz edildi': 'opposed',
                'süresi doldu': 'expired', 'suresi doldu': 'expired',
                'kısmi red': 'partial_refusal', 'kismi red': 'partial_refusal',
                'yenilendi': 'renewed',
                'devredildi': 'transferred',
                'i̇ptal edildi': 'cancelled', 'iptal edildi': 'cancelled',
                'bilinmiyor': 'unknown'
            };
            var key = map[(status || '').toLowerCase().replace(/\u0307/g, '')];
            if (key) {
                var translated = t('landing.status_' + key);
                if (translated && !translated.startsWith('landing.')) return translated;
            }
            return status;
        }
    };
}
