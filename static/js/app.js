/**
 * app.js - Main Alpine.js app initialization + remaining UI functions
 * Loaded last - depends on all other JS files
 */

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
var LEADS_PER_PAGE = 20;
var SEARCH_PER_PAGE = 20;
var currentHolderTpeId = null;
var _storedSearchResults = [];
var _lastSearchBannerHtml = '';
var currentSearchPage = 1;
var currentSearchTotalPages = 1;
var currentSearchTotal = 0;
var currentSearchType = 'quick';

// ============================================
// ALPINE.JS DASHBOARD COMPONENT
// ============================================
function dashboard() {
    return {
        userId: '...',
        stats: {},
        alerts: [],
        watchlist: [],
        deadlines: [],
        chartInstance: null,

        init() {
            this.loadData();
            // Poll for real username from auth profile
            var self = this;
            var attempts = 0;
            var pollName = setInterval(function() {
                attempts++;
                if (window.AppAuth && window.AppAuth.currentUserName) {
                    self.userId = window.AppAuth.currentUserName;
                    clearInterval(pollName);
                } else if (attempts >= 50) {
                    clearInterval(pollName);
                }
            }, 200);
        },

        getScoreColor(score) {
            // Delegate to shared function (score-badge.js)
            return window.AppComponents.getScoreColor(score);
        },

        async loadData() {
            try {
                var token = getAuthToken();
                var headers = token ? { 'Authorization': 'Bearer ' + token } : {};

                var results = await Promise.allSettled([
                    fetch('/api/v1/dashboard/stats', { headers: headers }),
                    fetch('/api/v1/alerts?page=1&page_size=10', { headers: headers }),
                    fetch('/api/v1/alerts/summary', { headers: headers })
                ]);

                // Dashboard stats
                if (results[0].status === 'fulfilled' && results[0].value.ok) {
                    var statsData = await results[0].value.json();
                    this.stats = {
                        total_watched: statsData.active_watchlist || 0,
                        high_risk_count: statsData.critical_alerts || 0,
                        pending_deadlines: statsData.new_alerts || 0,
                        recent_activity_count: statsData.alerts_this_week || 0
                    };
                }

                // Recent alerts for list + chart
                if (results[1].status === 'fulfilled' && results[1].value.ok) {
                    var alertsData = await results[1].value.json();
                    var items = alertsData.items || [];
                    this.alerts = items.map(function(a) {
                        var c = a.conflicting || {};
                        var sc = a.scores || {};
                        return {
                            alert_id: a.id,
                            conflicting_brand: c.name || 'N/A',
                            conflicting_app_no: c.application_no || '',
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
                            has_extracted_goods: c.has_extracted_goods || false
                        };
                    });
                }

                // Deadlines: use backend-computed deadline_status fields (no client-side date math)
                var derivedDeadlines = this.alerts
                    .filter(function(a) { return a.deadline_status && a.deadline_status.indexOf('active') === 0; })
                    .sort(function(a, b) { return (a.deadline_days_remaining || 999) - (b.deadline_days_remaining || 999); })
                    .slice(0, 10)
                    .map(function(a) {
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

                // KPI: count of alerts with active deadlines
                var activeDeadlineCount = this.alerts.filter(function(a) {
                    return a.deadline_status && a.deadline_status.indexOf('active') === 0;
                }).length;
                this.stats.pending_deadlines = activeDeadlineCount;

                // Pre-publication count for info banner
                var prePubCount = this.alerts.filter(function(a) {
                    return a.deadline_status === 'pre_publication';
                }).length;
                this.stats.pre_publication_count = prePubCount;

                this.renderChart();

            } catch (error) {
                console.error("API Error:", error);
            }
        },

        showOppositionModal(deadline) {
            var modal = document.getElementById('opposition-modal');
            var content = document.getElementById('opposition-content');
            if (!modal || !content) return;

            var urgencyClass = deadline.days_left < 10 ? 'text-red-600' : 'text-orange-600';
            var urgencyBg = deadline.days_left < 10 ? 'bg-red-50 border-red-200' : 'bg-orange-50 border-orange-200';

            var subject = encodeURIComponent('Marka Itiraz Basvurusu - ' + (deadline.conflicting_brand || ''));
            var body = encodeURIComponent(
                'Sayin Yetkili,\n\n'
                + 'Asagidaki marka basvurusuna itiraz etmek istiyorum:\n\n'
                + 'Cakisan Marka: ' + (deadline.conflicting_brand || 'N/A') + '\n'
                + 'Basvuru No: ' + (deadline.app_no || 'N/A') + '\n'
                + 'Izlenen Marka: ' + (deadline.brand_watched || 'N/A') + '\n'
                + 'Itiraz Son Tarih: ' + (deadline.appeal_deadline || 'N/A') + '\n'
                + 'Risk Skoru: %' + (deadline.risk_score || 0) + '\n\n'
                + 'Bilgilendirmenizi rica ederim.\n'
            );

            content.innerHTML = '<div class="space-y-4">'
                + '<div class="text-center ' + urgencyBg + ' border rounded-xl p-4">'
                + '<div class="' + urgencyClass + ' text-3xl font-bold">' + deadline.days_left + ' Gun</div>'
                + '<div class="text-sm text-gray-600">Itiraz suresi dolmak uzere</div>'
                + '<div class="text-xs text-gray-400 mt-1">Son tarih: ' + escapeHtml(deadline.appeal_deadline || '') + '</div></div>'
                + '<div class="grid grid-cols-2 gap-3">'
                + '<div class="bg-gray-50 rounded-lg p-3 border"><div class="text-xs text-gray-500 mb-1">Cakisan Marka</div>'
                + '<div class="font-semibold text-gray-900">' + escapeHtml(deadline.conflicting_brand || 'N/A') + '</div>'
                + '<div class="text-xs text-gray-400">' + escapeHtml(deadline.app_no || '') + '</div></div>'
                + '<div class="bg-gray-50 rounded-lg p-3 border"><div class="text-xs text-gray-500 mb-1">Izlenen Marka</div>'
                + '<div class="font-semibold text-gray-900">' + escapeHtml(deadline.brand_watched || 'N/A') + '</div>'
                + '<div class="text-xs text-gray-400">Risk: %' + (deadline.risk_score || 0) + '</div></div></div>'
                + '<div class="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm">'
                + '<div class="font-semibold text-amber-800 mb-2">Itiraz Sureci Hakkinda</div>'
                + '<ul class="space-y-1 text-gray-700">'
                + '<li>&bull; 556 sayili KHK m.42 uyarinca, ilan tarihinden itibaren <strong>2 ay</strong> icinde itiraz edilmelidir.</li>'
                + '<li>&bull; Itiraz, TURKPATENT\'e yazili olarak veya <strong>e-Devlet</strong> uzerinden yapilabilir.</li>'
                + '<li>&bull; Itiraz harcini odemeyi unutmayiniz.</li></ul></div>'
                + '<div class="flex gap-3">'
                + '<a href="https://epats.turkpatent.gov.tr/" target="_blank" rel="noopener" '
                + 'class="flex-1 px-4 py-2.5 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-center text-sm font-medium">'
                + 'TURKPATENT Portal</a>'
                + '<a href="mailto:?subject=' + subject + '&body=' + body + '" '
                + 'class="flex-1 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-center text-sm font-medium">'
                + 'Avukata Gonder</a></div></div>';

            modal.classList.remove('hidden');
        },

        async showAlertDetail(alertId) {
            var modal = document.getElementById('alert-detail-modal');
            var content = document.getElementById('alert-detail-content');
            var actionsContainer = document.getElementById('alert-detail-actions');
            if (!modal || !content) return;

            modal.classList.remove('hidden');
            content.innerHTML = '<div class="text-center py-8"><div class="animate-spin inline-block w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full"></div></div>';
            window._currentAlertId = alertId;

            try {
                var token = getAuthToken();
                var res = await fetch('/api/v1/alerts/' + alertId, {
                    headers: token ? { 'Authorization': 'Bearer ' + token } : {}
                });
                if (!res.ok) throw new Error('Alert yuklenemedi');
                var alert = await res.json();

                var scorePercent = Math.round((alert.scores.total || 0) * 100);
                var scoreColor = window.AppComponents.getScoreColor(scorePercent);
                var c = alert.conflicting || {};
                var s = alert.scores || {};

                var badgesHtml = window.AppComponents.renderSimilarityBadges(s);

                var imageHtml = c.image_path
                    ? '<img src="/api/trademark-image/' + encodeURIComponent(c.image_path) + '" class="w-20 h-20 object-contain rounded border" onerror="this.style.display=\'none\'">'
                    : '<div class="w-20 h-20 bg-gray-100 rounded border flex items-center justify-center text-gray-400 text-2xl">&#x1f4cb;</div>';

                var overlappingHtml = '';
                if (alert.overlapping_classes && alert.overlapping_classes.length > 0) {
                    overlappingHtml = '<div class="mt-3"><span class="text-xs font-medium text-gray-500">Ortak Siniflar:</span> '
                        + alert.overlapping_classes.map(function(cl) { return '<span class="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">' + cl + '</span>'; }).join(' ')
                        + '</div>';
                }

                // Deadline section at the top
                var deadlineSection = renderAlertDetailDeadlineSection(alert);

                content.innerHTML = '<div class="space-y-4">'
                    + deadlineSection
                    + '<div class="text-center">'
                    + '<div class="inline-flex flex-col items-center justify-center h-20 w-20 rounded-xl font-bold text-2xl border-2 mx-auto ' + scoreColor + '">'
                    + scorePercent + '<span class="text-xs font-normal opacity-75">%</span></div>'
                    + '<div class="text-gray-500 text-sm mt-1">Genel Risk Skoru</div>'
                    + '<div class="flex justify-center mt-2">' + badgesHtml + '</div></div>'
                    + '<div class="grid grid-cols-2 gap-4">'
                    + '<div class="bg-indigo-50 rounded-xl p-4 border border-indigo-100">'
                    + '<div class="text-indigo-600 font-semibold text-sm mb-2">Izlenen Marka</div>'
                    + '<div class="font-medium text-gray-900">' + escapeHtml(alert.watched_brand_name || 'N/A') + '</div>'
                    + (alert.watchlist_application_no ? window.AppComponents.renderTurkpatentButton(alert.watchlist_application_no) : '')
                    + '</div>'
                    + '<div class="bg-red-50 rounded-xl p-4 border border-red-100">'
                    + '<div class="text-red-600 font-semibold text-sm mb-2">Cakisan Marka</div>'
                    + '<div class="flex items-center gap-3">' + imageHtml
                    + '<div><div class="font-medium text-gray-900">' + escapeHtml(c.name || 'N/A') + '</div>'
                    + window.AppComponents.renderTurkpatentButton(c.application_no)
                    + (c.holder ? '<div class="text-xs text-gray-400 mt-1">' + escapeHtml(c.holder) + '</div>' : '')
                    + '</div></div></div></div>'
                    + overlappingHtml
                    + (c.has_extracted_goods
                        ? '<div class="mt-3 text-center"><button onclick="showExtractedGoods(\'' + (c.application_no || '').replace(/'/g, "\\'") + '\', this)" '
                          + 'class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer transition-colors">'
                          + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
                          + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>'
                          + '</svg>CIKARILMIS URUN: <span class="underline">EVET</span></button></div>'
                        : '')
                    + '<div class="text-xs text-gray-400 text-center mt-2">'
                    + 'Durum: ' + escapeHtml(alert.status || '') + ' &bull; Kaynak: ' + escapeHtml(alert.source_type || '') + ' &bull; ' + (alert.detected_at || '')
                    + '</div></div>';

                // Update action buttons based on deadline status
                if (actionsContainer) {
                    var actionsHtml = '';

                    // Opposition button — only if deadline is active
                    if (alert.deadline_status && alert.deadline_status.indexOf('active') === 0) {
                        var urgentBtnClass = alert.deadline_urgency === 'critical'
                            ? 'bg-red-600 hover:bg-red-700 animate-pulse'
                            : 'bg-orange-600 hover:bg-orange-700';
                        var daysText = alert.deadline_days_remaining !== null ? ' (' + alert.deadline_days_remaining + ' gun)' : '';
                        actionsHtml += '<button onclick="document.getElementById(\'alert-detail-modal\').classList.add(\'hidden\'); '
                            + 'dashboard().showOppositionModal({conflicting_brand: \'' + escapeHtml(c.name || '').replace(/'/g, "\\'") + '\', '
                            + 'app_no: \'' + escapeHtml(c.application_no || '').replace(/'/g, "\\'") + '\', '
                            + 'appeal_deadline: \'' + (alert.appeal_deadline || '') + '\', '
                            + 'days_left: ' + (alert.deadline_days_remaining || 0) + ', '
                            + 'risk_score: ' + scorePercent + ', '
                            + 'brand_watched: \'' + escapeHtml(alert.watched_brand_name || '').replace(/'/g, "\\'") + '\'})" '
                            + 'class="px-3 py-2.5 ' + urgentBtnClass + ' text-white text-sm rounded-lg font-medium">'
                            + 'Itiraz Basvurusu' + daysText + '</button>';
                    }

                    // Pre-publication note
                    if (alert.deadline_status === 'pre_publication') {
                        actionsHtml += '<div class="px-3 py-2.5 bg-blue-100 text-blue-800 text-sm rounded-lg">'
                            + 'Bulten yayinlandiginda bilgilendirileceksiniz</div>';
                    }

                    // Standard actions
                    actionsHtml += '<button onclick="acknowledgeAlert()" class="flex-1 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium">Onayla</button>';
                    actionsHtml += '<button onclick="resolveAlert()" class="flex-1 px-4 py-2.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium">Cozuldu</button>';
                    actionsHtml += '<button onclick="dismissAlert()" class="px-4 py-2.5 bg-gray-100 hover:bg-gray-200 text-gray-600 rounded-lg text-sm font-medium border border-gray-200">Reddet</button>';

                    actionsContainer.innerHTML = actionsHtml;
                }

            } catch (e) {
                content.innerHTML = '<div class="text-center py-8 text-red-500">' + escapeHtml(e.message) + '</div>';
            }
        },

        renderChart() {
            var ctx = document.getElementById('riskChart');
            if (!ctx) return;

            // 5 categories matching backend RISK_THRESHOLDS
            var critical = this.alerts.filter(function(a) { return a.risk_score >= 90; }).length;
            var veryHigh = this.alerts.filter(function(a) { return a.risk_score >= 80 && a.risk_score < 90; }).length;
            var high = this.alerts.filter(function(a) { return a.risk_score >= 70 && a.risk_score < 80; }).length;
            var medium = this.alerts.filter(function(a) { return a.risk_score >= 50 && a.risk_score < 70; }).length;
            var low = this.alerts.filter(function(a) { return a.risk_score < 50; }).length;

            if (critical + veryHigh + high + medium + low === 0) { critical = 1; high = 2; low = 5; }

            if (this.chartInstance) this.chartInstance.destroy();

            this.chartInstance = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['Kritik (%90+)', 'Cok Yuksek (%80+)', 'Yuksek (%70+)', 'Orta (%50+)', 'Dusuk'],
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
                        legend: { position: 'right' }
                    },
                    cutout: '70%'
                }
            });
        }
    };
}

// ============================================
// DEADLINE STATUS BADGE RENDERING
// ============================================
function renderDeadlineStatusBadge(alert) {
    if (!alert.deadline_status) return '';

    var statusConfig = {
        'pre_publication': { bg: 'bg-blue-100', text: 'text-blue-800', border: 'border-blue-300', label: 'Erken Uyari' },
        'active_critical': { bg: 'bg-red-100', text: 'text-red-800', border: 'border-red-300', label: alert.deadline_label || 'Kritik', pulse: true },
        'active_urgent': { bg: 'bg-orange-100', text: 'text-orange-800', border: 'border-orange-300', label: alert.deadline_label || 'Acil' },
        'active': { bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-300', label: alert.deadline_label || 'Aktif' },
        'expired': { bg: 'bg-gray-100', text: 'text-gray-500', border: 'border-gray-200', label: 'Itiraz suresi doldu' },
        'registered': { bg: 'bg-gray-100', text: 'text-gray-500', border: 'border-gray-200', label: 'Tescil edildi' },
        'opposed': { bg: 'bg-purple-100', text: 'text-purple-800', border: 'border-purple-300', label: 'Itiraz edilmis' },
        'resolved': { bg: 'bg-green-100', text: 'text-green-800', border: 'border-green-300', label: 'Tehdit kalkti' }
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
        + '<span class="font-semibold">Erken Tespit:</span> Bu basvuru henuz Resmi Bulten\'de yayinlanmadi. '
        + 'Itiraz suresi bulten yayin tarihinden itibaren baslayacak.</div></div></div>';
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
        + '<span class="text-sm font-semibold text-gray-700">Itiraz Durumu</span>'
        + badge + '</div>';

    if (alert.conflict_bulletin_date) {
        html += '<div class="text-xs text-gray-600">Bulten tarihi: ' + formatDateTRShort(alert.conflict_bulletin_date) + '</div>';
    }
    if (alert.appeal_deadline && alert.deadline_days_remaining !== null && alert.deadline_days_remaining >= 0) {
        html += '<div class="text-xs text-gray-600">Son itiraz tarihi: ' + formatDateTRShort(alert.appeal_deadline) + '</div>';
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
    } catch(e) { return dateStr; }
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
        var classes = [
            [1,'Kimyasal Urunler'],[2,'Boyalar'],[3,'Kozmetik'],[4,'Yaglar'],[5,'Eczacilik'],
            [6,'Metal Urunler'],[7,'Makineler'],[8,'El Aletleri'],[9,'Elektronik'],[10,'Tibbi Cihaz'],
            [11,'Aydinlatma'],[12,'Tasitlar'],[13,'Atesli Silahlar'],[14,'Kuyumculuk'],[15,'Muzik Aletleri'],
            [16,'Kagit Urunler'],[17,'Kaucuk'],[18,'Deri Urunler'],[19,'Yapi Malzemeleri'],[20,'Mobilya'],
            [21,'Ev Gerecleri'],[22,'Halatlar'],[23,'Iplikler'],[24,'Kumaslar'],[25,'Giyim'],
            [26,'Dantel'],[27,'Halilar'],[28,'Oyuncaklar'],[29,'Et Urunleri'],[30,'Gida'],
            [31,'Tarim Urunleri'],[32,'Bira/Alkol.Icecek'],[33,'Alkollu Icecek'],[34,'Tutun'],
            [35,'Reklamcilik'],[36,'Sigortacilik'],[37,'Insaat'],[38,'Telekomun.'],[39,'Tasimacilik'],
            [40,'Malzeme Isleme'],[41,'Egitim'],[42,'Yazilim/BT'],[43,'Yiyecek/Icecek'],[44,'Saglik'],
            [45,'Hukuk']
        ];
        // Remove the disabled placeholder
        niceSelect.innerHTML = '';
        classes.forEach(function(c) {
            var opt = document.createElement('option');
            opt.value = c[0];
            opt.textContent = c[0] + ' - ' + c[1];
            niceSelect.appendChild(opt);
        });
    }

    input.addEventListener('input', function() {
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

    input.addEventListener('search', function() {
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
    currentSearchPage = 1;
    currentSearchTotalPages = 1;
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
// SEARCH IMAGE UPLOAD
// ============================================
function onSearchImageSelected(input) {
    var file = input.files && input.files[0];
    var wrapper = document.getElementById('search-image-preview-wrapper');
    var preview = document.getElementById('search-image-preview');
    var nameEl = document.getElementById('search-image-name');
    var hint = document.getElementById('search-image-hint');

    if (file) {
        var reader = new FileReader();
        reader.onload = function(e) {
            preview.src = e.target.result;
            nameEl.textContent = file.name;
            wrapper.classList.remove('hidden');
            if (hint) hint.classList.add('hidden');
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
    var hint = document.getElementById('search-image-hint');
    if (hint) hint.classList.remove('hidden');
}

// ============================================
// AGENTIC SEARCH CANCEL
// ============================================
function cancelAgenticSearch() {
    agenticSearchAborted = true;
    hideAgenticLoadingModal();
    showToast('Arama iptal edildi', 'info');
}

// ============================================
// LOADING MODAL
// ============================================
function showAgenticLoadingModal() {
    var modal = document.getElementById('agentic-loading-modal');
    var log = document.getElementById('agentic-log');
    var progress = document.getElementById('agentic-progress');

    modal.classList.remove('hidden');
    log.innerHTML = '<div class="log-line opacity-70">&gt; Ajan baslatiliyor...</div>';
    progress.style.width = '0%';

    var steps = [
        { text: '> TurkPatent portalina baglaniliyor...',    delay: 800,   progress: 10 },
        { text: '> Oturum dogrulaniyor...',                  delay: 1500,  progress: 20 },
        { text: '> Arama sorgusu gonderiliyor...',           delay: 2500,  progress: 35 },
        { text: '> Sonuclar taraniyor...',                   delay: 4000,  progress: 50 },
        { text: '> Veriler analiz ediliyor...',              delay: 6000,  progress: 65 },
        { text: '> AI embeddings olusturuluyor...',          delay: 8000,  progress: 80 },
        { text: '> Risk skoru hesaplaniyor...',              delay: 10000, progress: 90 },
    ];

    steps.forEach(function(step) {
        setTimeout(function() {
            if (!agenticSearchAborted && !modal.classList.contains('hidden')) {
                addLogLine(step.text);
                progress.style.width = step.progress + '%';
            }
        }, step.delay);
    });
}

function hideAgenticLoadingModal() {
    document.getElementById('agentic-loading-modal').classList.add('hidden');
}

function addLogLine(text) {
    var log = document.getElementById('agentic-log');
    var line = document.createElement('div');
    line.className = 'log-line';
    line.textContent = text;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

// ============================================
// UPGRADE MODAL
// ============================================
function showUpgradeModal(detail) {
    document.getElementById('upgrade-modal').classList.remove('hidden');
}
function hideUpgradeModal() {
    document.getElementById('upgrade-modal').classList.add('hidden');
}
function redirectToUpgrade() {
    hideUpgradeModal();
    window.location.href = '/pricing';
}

// ============================================
// CREDITS MODAL
// ============================================
function showCreditsModal(detail) {
    var msg = document.getElementById('credits-message');
    if (detail && detail.message) msg.textContent = detail.message;
    document.getElementById('credits-modal').classList.remove('hidden');
}
function hideCreditsModal() {
    document.getElementById('credits-modal').classList.add('hidden');
}
function buyCredits(amount) {
    hideCreditsModal();
    window.location.href = 'mailto:sales@ipwatchdog.com.tr?subject='
        + encodeURIComponent('Kredi Satin Alma Talebi - ' + amount + ' kredi')
        + '&body=' + encodeURIComponent('Merhaba,\n\n' + amount + ' adet arama kredisi satin almak istiyorum.\n\nBilgi rica ederim.');
}

// ============================================
// TAB SWITCHING
// ============================================
function showDashboardTab(tabId) {
    document.getElementById('tab-content-overview').classList.add('hidden');
    document.getElementById('tab-content-opposition-radar').classList.add('hidden');
    document.getElementById('tab-content-ai-studio').classList.add('hidden');
    document.getElementById('tab-content-reports').classList.add('hidden');

    document.querySelectorAll('.dashboard-tab-btn').forEach(function(btn) {
        btn.classList.remove('bg-indigo-600', 'text-white');
        btn.classList.add('text-gray-500', 'hover:text-gray-700', 'hover:bg-gray-50');
    });

    var content = document.getElementById('tab-content-' + tabId);
    if (content) content.classList.remove('hidden');

    var btn = document.getElementById('tab-btn-' + tabId);
    if (btn) {
        btn.classList.add('bg-indigo-600', 'text-white');
        btn.classList.remove('text-gray-500', 'hover:text-gray-700', 'hover:bg-gray-50');
    }

    clearSearchResults();

    if (tabId === 'opposition-radar') {
        initOppositionRadar();
    }
    if (tabId === 'ai-studio') {
        initAIStudio();
    }
    if (tabId === 'reports') {
        if (!window._reportsInitialized) {
            window._reportsInitialized = true;
            loadReportsTab();
        }
    }
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
// LEAD DETAIL MODAL HANDLERS
// ============================================
function hideLeadDetailModal() {
    document.getElementById('lead-detail-modal').classList.add('hidden');
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
        if (!res.ok) throw new Error('Islem basarisiz');
        showToast('Uyari onaylandi', 'success');
        hideAlertDetailModal();
    } catch (e) {
        showToast('Hata: ' + e.message, 'error');
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
        if (!res.ok) throw new Error('Islem basarisiz');
        showToast('Uyari cozuldu olarak isaretlendi', 'success');
        hideAlertDetailModal();
    } catch (e) {
        showToast('Hata: ' + e.message, 'error');
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
        if (!res.ok) throw new Error('Islem basarisiz');
        showToast('Uyari reddedildi', 'success');
        hideAlertDetailModal();
    } catch (e) {
        showToast('Hata: ' + e.message, 'error');
    }
}

// ============================================
// OPPOSITION MODAL HANDLER
// ============================================
function hideOppositionModal() {
    document.getElementById('opposition-modal').classList.add('hidden');
}

function showLeadUpgradePrompt() {
    document.getElementById('lead-feed-loading').classList.add('hidden');
    document.getElementById('lead-feed-container').classList.add('hidden');
    document.getElementById('lead-stats-cards').classList.add('hidden');
    document.getElementById('lead-upgrade-prompt').classList.remove('hidden');
}

// ============================================
// HOLDER PORTFOLIO MODAL
// ============================================
function showHolderPortfolio(tpeClientId, holderName) {
    if (!tpeClientId) return;
    currentHolderTpeId = tpeClientId;

    var modal = document.getElementById('holderPortfolioModal');
    modal.classList.remove('hidden');

    document.getElementById('holderModalTitle').textContent = holderName || 'Marka Sahibi';
    document.getElementById('holderModalSubtitle').textContent = 'TPE No: ' + tpeClientId + ' \u2022 Yukleniyor...';

    document.getElementById('holderPortfolioLoading').classList.remove('hidden');
    document.getElementById('holderPortfolioResults').classList.add('hidden');
    document.getElementById('holderPortfolioError').classList.add('hidden');

    loadHolderTrademarks(tpeClientId, 1);
}

function renderHolderTrademarks(trademarks) {
    var container = document.getElementById('holderTrademarksList');
    if (!trademarks || trademarks.length === 0) {
        container.innerHTML = '<div class="text-center py-8 text-gray-500">Bu sahibe ait marka bulunamadi.</div>';
        return;
    }
    var html = '';
    trademarks.forEach(function(tm) {
        var egIndicator = '';
    if (tm.has_extracted_goods) {
        var safeAppNo = (tm.application_no || '').replace(/'/g, "\\'");
        egIndicator = '<button onclick="event.stopPropagation(); showExtractedGoods(\'' + safeAppNo + '\', this)" '
            + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer mt-1">'
            + 'CIKARILMIS URUN: <span class="underline">EVET</span></button>';
    }
    html += '<div class="flex items-center gap-4 p-4 bg-gray-50 hover:bg-gray-100 rounded-xl transition-colors">'
            + '<div class="w-12 h-12 bg-white rounded-lg border flex items-center justify-center overflow-hidden flex-shrink-0">'
            + (tm.image_path
                ? '<img src="/api/trademark-image/' + encodeURIComponent(escapeHtml(tm.image_path)) + '" alt="" class="w-full h-full object-contain" onerror="this.style.display=\'none\'; this.parentElement.innerHTML=\'&#x1f4cb;\';\">'
                : '<span class="text-gray-400 text-xl">&#x1f4cb;</span>')
            + '</div>'
            + '<div class="flex-1 min-w-0">'
            + '<div class="font-semibold text-gray-900 truncate">' + (escapeHtml(tm.name) || 'Isimsiz') + '</div>'
            + (tm.application_date ? '<div class="text-xs text-gray-400">' + formatHolderDate(tm.application_date) + '</div>' : '')
            + window.AppComponents.renderTurkpatentButton(tm.application_no)
            + egIndicator + '</div>'
            + '<div class="flex-shrink-0"><span class="' + getStatusBadgeClass(tm.status) + ' px-2 py-1 rounded text-xs font-medium">'
            + getStatusText(tm.status) + '</span></div>'
            + '<div class="text-sm text-gray-500 flex-shrink-0 hidden sm:block">'
            + (tm.classes && tm.classes.length > 0 ? 'Sinif: ' + tm.classes.slice(0, 3).join(', ') + (tm.classes.length > 3 ? '...' : '') : '')
            + '</div></div>';
    });
    container.innerHTML = html;
}

function renderHolderPagination(currentPage, totalPages, tpeClientId) {
    var container = document.getElementById('holderPagination');
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    var html = '<button onclick="loadHolderTrademarks(\'' + escapeHtml(tpeClientId) + '\', ' + (currentPage - 1) + ')" '
        + 'class="px-3 py-2 rounded-lg ' + (currentPage === 1 ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (currentPage === 1 ? 'disabled' : '') + '>&larr; Onceki</button>'
        + '<span class="px-4 py-2 text-gray-600">Sayfa ' + currentPage + ' / ' + totalPages + '</span>'
        + '<button onclick="loadHolderTrademarks(\'' + escapeHtml(tpeClientId) + '\', ' + (currentPage + 1) + ')" '
        + 'class="px-3 py-2 rounded-lg ' + (currentPage === totalPages ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (currentPage === totalPages ? 'disabled' : '') + '>Sonraki &rarr;</button>';
    container.innerHTML = html;
}

function closeHolderPortfolio() {
    document.getElementById('holderPortfolioModal').classList.add('hidden');
    currentHolderTpeId = null;
    document.getElementById('holderSearchInput').value = '';
    document.getElementById('holderSearchResults').innerHTML = '';
    document.getElementById('holderSearchResults').classList.add('hidden');
    document.getElementById('holderSearchClearBtn').classList.add('hidden');
    document.getElementById('holderSearchBtn').classList.remove('hidden');
    window._holderSearchPreviousState = null;
}

// ============================================
// HOLDER SEARCH FUNCTIONS
// ============================================
window._holderSearchPreviousState = null;
window._currentHolderTpeId = null;
window._currentHolderName = null;

function handleHolderSearchKeydown(event) {
    if (event.key === 'Enter') performHolderSearch();
}

function performHolderSearch() {
    var input = document.getElementById('holderSearchInput');
    var query = (input.value || '').trim();
    if (query.length < 2) { showToast('En az 2 karakter girin', 'warning'); return; }

    var searchResults = document.getElementById('holderSearchResults');
    searchResults.innerHTML = '<div class="flex flex-col items-center justify-center py-12">'
        + '<div class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"></div>'
        + '<p class="text-gray-500 mt-4">Aranıyor...</p></div>';
    document.getElementById('holderPortfolioBody').classList.add('hidden');
    searchResults.classList.remove('hidden');
    document.getElementById('holderSearchClearBtn').classList.remove('hidden');
    document.getElementById('holderSearchBtn').classList.add('hidden');

    window._holderSearchPreviousState = {
        tpeId: window._currentHolderTpeId || currentHolderTpeId,
        name: window._currentHolderName
    };

    searchHolders(query).then(function(data) {
        renderHolderSearchResults(data.results || []);
    }).catch(function(err) {
        if (err.status === 403) {
            searchResults.classList.add('hidden');
            document.getElementById('holderPortfolioBody').classList.remove('hidden');
            showUpgradeModal('Marka sahibi arama Professional plan gerektirir');
        } else {
            showToast('Arama sirasinda hata olustu', 'error');
            searchResults.innerHTML = '<div class="text-center py-8 text-red-500">Arama basarisiz oldu.</div>';
        }
    });
}

function renderHolderSearchResults(results) {
    var container = document.getElementById('holderSearchResults');
    if (!results || results.length === 0) {
        container.innerHTML = '<div class="text-center py-12">'
            + '<svg class="mx-auto h-12 w-12 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>'
            + '<p class="text-gray-400">Sonuc bulunamadi</p></div>';
        return;
    }

    var html = '<div class="mb-3 text-sm text-gray-500">' + results.length + ' sonuc bulundu</div>';
    results.forEach(function(result) {
        var escapedName = escapeHtml(result.holder_name || '');
        var escapedId = escapeHtml(result.holder_tpe_client_id || '');
        html += '<div onclick="selectHolderFromSearch(\'' + escapedId + '\', \'' + escapedName.replace(/'/g, "\\'") + '\')" '
            + 'class="flex items-center justify-between p-3 border border-gray-200 rounded-lg hover:bg-blue-50 cursor-pointer transition-colors mb-2">'
            + '<div>'
            + '<div class="font-medium text-gray-900">' + escapedName + '</div>'
            + '<div class="text-sm text-gray-500">' + result.trademark_count + ' marka</div>'
            + '</div>'
            + '<svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
            + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>'
            + '</svg></div>';
    });
    container.innerHTML = html;
}

function selectHolderFromSearch(tpeClientId, holderName) {
    document.getElementById('holderSearchResults').classList.add('hidden');
    document.getElementById('holderPortfolioBody').classList.remove('hidden');
    window._currentHolderTpeId = tpeClientId;
    window._currentHolderName = holderName;
    showHolderPortfolio(tpeClientId, holderName);
}

function clearHolderSearch() {
    document.getElementById('holderSearchInput').value = '';
    document.getElementById('holderSearchResults').classList.add('hidden');
    document.getElementById('holderSearchResults').innerHTML = '';
    document.getElementById('holderPortfolioBody').classList.remove('hidden');
    document.getElementById('holderSearchClearBtn').classList.add('hidden');
    document.getElementById('holderSearchBtn').classList.remove('hidden');

    if (window._holderSearchPreviousState && window._holderSearchPreviousState.tpeId) {
        document.getElementById('holderPortfolioResults').classList.remove('hidden');
    }
    window._holderSearchPreviousState = null;
}

// Escape key to close modals
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var reportModal = document.getElementById('report-generate-modal');
        if (reportModal && !reportModal.classList.contains('hidden')) { hideReportGenerateModal(); return; }
        var alertModal = document.getElementById('alert-detail-modal');
        if (alertModal && !alertModal.classList.contains('hidden')) { hideAlertDetailModal(); return; }
        var oppositionModal = document.getElementById('opposition-modal');
        if (oppositionModal && !oppositionModal.classList.contains('hidden')) { hideOppositionModal(); return; }
        var holderModal = document.getElementById('holderPortfolioModal');
        if (holderModal && !holderModal.classList.contains('hidden')) closeHolderPortfolio();
    }
});

// ============================================
// SEARCH RESULTS SORTING
// ============================================
function buildSortBarHtml(count) {
    return '<div class="flex flex-wrap items-center justify-between gap-3 mb-3">'
        + '<div class="text-sm text-gray-600">' + count + ' sonuc</div>'
        + '<div class="flex items-center gap-2">'
        + '<span class="text-xs text-gray-500">Sirala:</span>'
        + '<select id="sortSelect" onchange="sortSearchResults()" '
        + 'class="text-sm border border-gray-200 rounded-lg px-2 py-1.5 pr-7 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer">'
        + '<option value="risk_desc">Risk &#x2193;</option>'
        + '<option value="risk_asc">Risk &#x2191;</option>'
        + '<option value="date_desc">Tarih (Yeni)</option>'
        + '<option value="date_asc">Tarih (Eski)</option>'
        + '</select>'
        + '<button onclick="resetSort()" class="text-xs text-gray-400 hover:text-blue-600" title="Sifirla">&#x21ba;</button>'
        + '</div></div>';
}

function sortSearchResults() {
    var sel = document.getElementById('sortSelect');
    var mode = sel ? sel.value : 'risk_desc';
    var sorted = _storedSearchResults.slice();

    if (mode === 'risk_desc') {
        sorted.sort(function(a, b) { return getResultScore(b) - getResultScore(a); });
    } else if (mode === 'risk_asc') {
        sorted.sort(function(a, b) { return getResultScore(a) - getResultScore(b); });
    } else if (mode === 'date_desc') {
        sorted.sort(function(a, b) { return parseResultDate(b.application_date) - parseResultDate(a.application_date); });
    } else if (mode === 'date_asc') {
        sorted.sort(function(a, b) { return parseResultDate(a.application_date) - parseResultDate(b.application_date); });
    }

    var cardsContainer = document.getElementById('search-results-cards');
    if (cardsContainer) {
        cardsContainer.innerHTML = sorted.map(renderResultCard).join('');
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

    // Store pagination state from server
    currentSearchPage = data.page || 1;
    currentSearchTotalPages = data.total_pages || 1;
    currentSearchTotal = data.total || 0;

    var html = '';

    var imageBadge = data.image_used
        ? '<span class="inline-flex items-center gap-1 text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full ml-2">'
        + '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>'
        + 'Gorsel Analiz</span>'
        : '';

    if (data.scrape_triggered) {
        html += '<div class="mb-4 p-4 bg-amber-50 border border-amber-200 rounded-xl">'
            + '<div class="flex items-center gap-2 text-amber-700 font-semibold">'
            + '<span class="text-xl">&#x1f575;&#xfe0f;</span><span>Canli Arama Sonuclari</span>' + imageBadge + '</div>'
            + '<p class="text-sm text-gray-500 mt-1">'
            + (data.total || 0) + ' sonuc bulundu '
            + (data.scraped_count ? '(' + data.scraped_count + ' yeni kayit) ' : '')
            + '&bull; ' + (data.elapsed_seconds || 0) + 's</p></div>';
    } else {
        html += '<div class="mb-4 p-4 bg-indigo-50 border border-indigo-200 rounded-xl">'
            + '<div class="flex items-center gap-2 text-indigo-700 font-semibold">'
            + '<span class="text-xl">&#x1f50d;</span><span>Veritabani Sonuclari</span>' + imageBadge + '</div>'
            + '<p class="text-sm text-gray-500 mt-1">'
            + (data.total || 0) + ' sonuc &bull; ' + (data.elapsed_seconds || 0) + 's</p></div>';
    }

    var results = data.results || [];
    // Attach query context to each result for AI Studio CTA
    var searchInput = document.getElementById('search-input');
    var queryName = searchInput ? searchInput.value.trim() : '';
    var queryClasses = getSelectedNiceClasses();
    results.forEach(function(r) {
        r._query_name = queryName;
        r._query_classes = queryClasses;
    });
    _storedSearchResults = results.slice();

    if (results.length === 0 && currentSearchPage === 1) {
        html += '<div class="text-center py-8 text-gray-400"><div class="text-4xl mb-2">&#x1f50d;</div><p>Sonuc bulunamadi</p></div>';
    } else {
        html += buildSortBarHtml(data.total || results.length);
        html += '<div id="search-results-cards">';
        results.forEach(function(r) { html += renderResultCard(r); });
        html += '</div>';

        // Pagination controls
        if (currentSearchTotalPages > 1) {
            html += buildSearchPaginationHtml(currentSearchPage, currentSearchTotalPages);
        }
    }

    container.innerHTML = html;
}

function buildSearchPaginationHtml(page, totalPages) {
    var prevDisabled = page <= 1;
    var nextDisabled = page >= totalPages;

    return '<div class="flex items-center justify-center gap-3 mt-4 pt-4 border-t border-gray-100">'
        + '<button onclick="navigateSearchPage(' + (page - 1) + ')" '
        + 'class="px-4 py-2 rounded-lg text-sm font-medium transition-colors '
        + (prevDisabled ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (prevDisabled ? 'disabled' : '') + '>&larr; Onceki</button>'
        + '<span class="text-sm text-gray-600">Sayfa ' + page + ' / ' + totalPages + '</span>'
        + '<button onclick="navigateSearchPage(' + (page + 1) + ')" '
        + 'class="px-4 py-2 rounded-lg text-sm font-medium transition-colors '
        + (nextDisabled ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-gray-200 hover:bg-gray-300 text-gray-700') + '" '
        + (nextDisabled ? 'disabled' : '') + '>Sonraki &rarr;</button>'
        + '</div>';
}

function navigateSearchPage(page) {
    if (page < 1 || page > currentSearchTotalPages) return;
    if (currentSearchType === 'intelligent') {
        handleAgenticSearch(page);
    } else {
        handleQuickSearch(page);
    }
}

// ============================================
// AI STUDIO
// ============================================

function initAIStudio() {
    if (studioInitialized) return;
    studioInitialized = true;
    // Populate nice class selects for studio
    populateStudioNiceClasses('studio-name-classes');
    populateStudioNiceClasses('studio-logo-classes');
    updateStudioCredits();
    // Check service availability and disable features if unavailable
    checkCreativeSuiteStatus();
}

function checkCreativeSuiteStatus() {
    fetch('/api/v1/tools/status')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var nameBtn = document.getElementById('studio-name-btn');
            var logoBtn = document.getElementById('studio-logo-btn');

            if (data.name_generator && !data.name_generator.available) {
                if (nameBtn) {
                    nameBtn.disabled = true;
                    nameBtn.classList.add('opacity-50', 'cursor-not-allowed');
                    nameBtn.title = data.name_generator.reason || 'Servis kullanilamiyor';
                }
                var namePanel = document.getElementById('studio-name-panel');
                if (namePanel && !document.getElementById('name-unavailable-banner')) {
                    var banner = document.createElement('div');
                    banner.id = 'name-unavailable-banner';
                    banner.className = 'bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3 text-sm text-amber-700';
                    banner.innerHTML = '<strong>Servis su anda kullanilamiyor.</strong> ' + (data.name_generator.reason || '');
                    namePanel.insertBefore(banner, namePanel.firstChild);
                }
            }

            if (data.logo_studio && !data.logo_studio.available) {
                if (logoBtn) {
                    logoBtn.disabled = true;
                    logoBtn.classList.add('opacity-50', 'cursor-not-allowed');
                    logoBtn.title = data.logo_studio.reason || 'Servis kullanilamiyor';
                }
                var logoPanel = document.getElementById('studio-logo-panel');
                if (logoPanel && !document.getElementById('logo-unavailable-banner')) {
                    var banner = document.createElement('div');
                    banner.id = 'logo-unavailable-banner';
                    banner.className = 'bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3 text-sm text-amber-700';
                    banner.innerHTML = '<strong>Logo Studio su anda kullanilamiyor.</strong> ' + (data.logo_studio.reason || '');
                    logoPanel.insertBefore(banner, logoPanel.firstChild);
                }
            }
        })
        .catch(function() {
            // Status endpoint unreachable — don't block usage, just log
            console.warn('Creative Suite status check failed');
        });
}

function populateStudioNiceClasses(selectId) {
    var select = document.getElementById(selectId);
    if (!select || select.options.length > 5) return;
    var classes = [
        [1,'Kimyasal Urunler'],[2,'Boyalar'],[3,'Kozmetik'],[4,'Yaglar'],[5,'Eczacilik'],
        [6,'Metal Urunler'],[7,'Makineler'],[8,'El Aletleri'],[9,'Elektronik'],[10,'Tibbi Cihaz'],
        [11,'Aydinlatma'],[12,'Tasitlar'],[13,'Atesli Silahlar'],[14,'Kuyumculuk'],[15,'Muzik Aletleri'],
        [16,'Kagit Urunler'],[17,'Kaucuk'],[18,'Deri Urunler'],[19,'Yapi Malzemeleri'],[20,'Mobilya'],
        [21,'Ev Gerecleri'],[22,'Halatlar'],[23,'Iplikler'],[24,'Kumaslar'],[25,'Giyim'],
        [26,'Dantel'],[27,'Halilar'],[28,'Oyuncaklar'],[29,'Et Urunleri'],[30,'Gida'],
        [31,'Tarim Urunleri'],[32,'Bira/Alkol.Icecek'],[33,'Alkollu Icecek'],[34,'Tutun'],
        [35,'Reklamcilik'],[36,'Sigortacilik'],[37,'Insaat'],[38,'Telekomun.'],[39,'Tasimacilik'],
        [40,'Malzeme Isleme'],[41,'Egitim'],[42,'Yazilim/BT'],[43,'Yiyecek/Icecek'],[44,'Saglik'],
        [45,'Hukuk']
    ];
    classes.forEach(function(c) {
        var opt = document.createElement('option');
        opt.value = c[0];
        opt.textContent = c[0] + ' - ' + c[1];
        select.appendChild(opt);
    });
}

function getStudioNiceClasses(selectId) {
    var select = document.getElementById(selectId);
    if (!select) return [];
    return Array.from(select.selectedOptions).map(function(o) { return parseInt(o.value); }).filter(function(v) { return !isNaN(v); });
}

function updateStudioCredits() {
    // Simple display update from latest generation response
    var el = document.getElementById('studio-credits-display');
    if (!el) return;
    el.textContent = '-';
}

function switchStudioMode(mode) {
    studioActiveMode = mode;

    document.getElementById('studio-name-panel').classList.toggle('hidden', mode !== 'name');
    document.getElementById('studio-logo-panel').classList.toggle('hidden', mode !== 'logo');

    document.querySelectorAll('.studio-mode-btn').forEach(function(btn) {
        btn.classList.remove('bg-white', 'text-gray-900', 'shadow-sm');
        btn.classList.add('text-gray-500', 'hover:text-gray-700');
    });

    var activeBtn = document.getElementById('studio-mode-' + mode);
    if (activeBtn) {
        activeBtn.classList.add('bg-white', 'text-gray-900', 'shadow-sm');
        activeBtn.classList.remove('text-gray-500', 'hover:text-gray-700');
    }

    // Update credits display for the active mode
    if (mode === 'logo') {
        updateLogoCreditsDisplay();
    }
}

// ============================================
// NAME LAB: GENERATE
// ============================================
async function generateNames() {
    var query = (document.getElementById('studio-name-query').value || '').trim();
    if (!query) { showToast('Lutfen bir marka adi veya konsept girin', 'error'); return; }

    if (studioNameLoading) return;
    studioNameLoading = true;

    var classes = getStudioNiceClasses('studio-name-classes');
    var industry = (document.getElementById('studio-name-industry').value || '').trim();
    var style = document.getElementById('studio-name-style').value || 'modern';

    // Show loading, hide others
    document.getElementById('studio-name-loading').classList.remove('hidden');
    document.getElementById('studio-name-results').classList.add('hidden');
    document.getElementById('studio-name-empty').classList.add('hidden');
    document.getElementById('studio-name-error').classList.add('hidden');

    // Disable button
    var btn = document.getElementById('studio-name-btn');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }

    try {
        var data = await generateNamesAPI({
            query: query,
            nice_classes: classes,
            industry: industry,
            style: style,
            language: 'tr',
            avoid_names: []
        });

        document.getElementById('studio-name-loading').classList.add('hidden');

        var safeNames = data.safe_names || [];
        if (safeNames.length === 0) {
            document.getElementById('studio-name-empty').classList.remove('hidden');
        } else {
            document.getElementById('studio-name-results').classList.remove('hidden');
            document.getElementById('studio-name-meta').textContent =
                safeNames.length + ' guvenli isim / ' + data.total_generated + ' uretildi, '
                + data.filtered_count + ' filtrelendi'
                + (data.cached ? ' (onbellekten)' : '');

            var cardsHtml = '';
            safeNames.forEach(function(name, i) {
                cardsHtml += renderNameCard(name, i);
            });
            document.getElementById('studio-name-cards').innerHTML = cardsHtml;
        }

        // Update credits display
        if (data.credits_remaining) {
            var credEl = document.getElementById('studio-credits-display');
            if (credEl) {
                var cr = data.credits_remaining;
                var remaining = cr.session_limit === -1 ? 'Sinirsiz' : (cr.session_limit - cr.used);
                credEl.textContent = remaining + (cr.purchased > 0 ? ' + ' + cr.purchased : '');
            }
        }

    } catch (e) {
        document.getElementById('studio-name-loading').classList.add('hidden');
        if (e.message !== 'upgrade_required' && e.message !== 'credits_exhausted' && e.message !== 'unauthorized') {
            document.getElementById('studio-name-error').classList.remove('hidden');
            document.getElementById('studio-name-error-msg').textContent = e.message || 'Isim olusturma basarisiz oldu.';
        }
    } finally {
        studioNameLoading = false;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
    }
}

// ============================================
// NAME LAB: USE FOR LOGO
// ============================================
function useNameForLogo(name) {
    switchStudioMode('logo');
    document.getElementById('studio-logo-name').value = name;
    document.getElementById('studio-logo-name').focus();
    showToast('Marka adi Logo Studio\'ya aktarildi', 'info');
}

// ============================================
// LOGO STUDIO: GENERATE
// ============================================
async function generateLogos() {
    var brandName = (document.getElementById('studio-logo-name').value || '').trim();
    if (!brandName) { showToast('Lutfen bir marka adi girin', 'error'); return; }

    if (studioLogoLoading) return;
    studioLogoLoading = true;

    var description = (document.getElementById('studio-logo-desc').value || '').trim();
    var style = document.getElementById('studio-logo-style').value || 'modern';
    var colors = (document.getElementById('studio-logo-colors').value || '').trim();
    var classes = getStudioNiceClasses('studio-logo-classes');

    // Show loading, hide others
    document.getElementById('studio-logo-loading').classList.remove('hidden');
    document.getElementById('studio-logo-results').classList.add('hidden');
    document.getElementById('studio-logo-error').classList.add('hidden');

    var btn = document.getElementById('studio-logo-btn');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }

    try {
        var data = await generateLogosAPI({
            brand_name: brandName,
            description: description,
            style: style,
            color_preferences: colors,
            nice_classes: classes
        });

        document.getElementById('studio-logo-loading').classList.add('hidden');

        var logos = data.logos || [];
        if (logos.length === 0) {
            document.getElementById('studio-logo-error').classList.remove('hidden');
            document.getElementById('studio-logo-error-msg').textContent = 'Logo olusturulamadi.';
        } else {
            document.getElementById('studio-logo-results').classList.remove('hidden');
            var cardsHtml = '';
            logos.forEach(function(logo) {
                cardsHtml += renderLogoCard(logo);
            });
            document.getElementById('studio-logo-cards').innerHTML = cardsHtml;
            // Store logo data for detail toggle and load images with auth headers
            storeLogoData(logos);
            loadLogoImages(logos);
        }

        // Update credits
        if (data.credits_remaining) {
            updateLogoCreditsFromData(data.credits_remaining);
        }

    } catch (e) {
        document.getElementById('studio-logo-loading').classList.add('hidden');
        if (e.message !== 'upgrade_required' && e.message !== 'credits_exhausted' && e.message !== 'unauthorized') {
            document.getElementById('studio-logo-error').classList.remove('hidden');
            document.getElementById('studio-logo-error-msg').textContent = e.message || 'Logo olusturma basarisiz oldu.';
        }
    } finally {
        studioLogoLoading = false;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
    }
}

function updateLogoCreditsDisplay() {
    var el = document.getElementById('studio-logo-credit-info');
    if (!el) return;
    el.textContent = '';
}

function updateLogoCreditsFromData(credits) {
    var total = (credits.monthly || 0) + (credits.purchased || 0);
    var infoEl = document.getElementById('studio-logo-credit-info');
    if (infoEl) {
        infoEl.textContent = 'Bu ay ' + total + ' logo hakkiniz kaldi (aylik: ' + (credits.monthly || 0) + ', ek: ' + (credits.purchased || 0) + ')';
    }
    var badgeEl = document.getElementById('studio-credits-display');
    if (badgeEl) {
        badgeEl.textContent = total;
    }
}

var _studioLogos = {};

function storeLogoData(logos) {
    if (!logos) return;
    logos.forEach(function(logo) {
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
        showToast('Logo verisi bulunamadi', 'error');
        return;
    }

    var vb = logo.visual_breakdown || {};
    var simPct = Math.round(logo.similarity_score || 0);

    function makeBar(label, value) {
        var pct = Math.round((value || 0) * 100);
        var color = pct >= 70 ? 'bg-red-500' : pct >= 50 ? 'bg-amber-500' : 'bg-green-500';
        return '<div class="flex items-center gap-2 text-xs">'
            + '<span class="w-14 text-gray-500">' + label + '</span>'
            + '<div class="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">'
            + '<div class="h-full rounded-full ' + color + '" style="width:' + pct + '%"></div></div>'
            + '<span class="w-8 text-right font-medium text-gray-700">' + pct + '%</span></div>';
    }

    var barsHtml = '';
    if (vb.clip != null) barsHtml += makeBar('CLIP', vb.clip);
    if (vb.dino != null) barsHtml += makeBar('DINOv2', vb.dino);
    if (vb.ocr != null) barsHtml += makeBar('OCR', vb.ocr);
    if (vb.color != null) barsHtml += makeBar('Renk', vb.color);

    if (!barsHtml) {
        barsHtml = '<div class="text-xs text-gray-400 text-center py-2">Gorsel analiz verisi yok</div>';
    }

    var closestHtml = logo.closest_match_name
        ? '<div class="text-xs text-gray-500 mt-2">En yakin: <span class="font-medium">' + escapeHtml(logo.closest_match_name) + '</span> (' + simPct + '%)</div>'
        : '';

    var panelHtml = '<div id="logo-detail-' + imageId + '" class="px-4 pb-4 border-t border-gray-100 mt-0 pt-3 bg-gray-50 rounded-b-xl">'
        + '<div class="text-xs font-semibold text-gray-600 mb-2">Gorsel Analiz</div>'
        + '<div class="space-y-1.5">' + barsHtml + '</div>'
        + closestHtml
        + '</div>';

    // Find the logo card by its image placeholder
    var imgContainer = document.getElementById('logo-img-' + imageId);
    if (imgContainer) {
        var card = imgContainer.closest('.bg-white.rounded-xl');
        if (card) {
            card.insertAdjacentHTML('beforeend', panelHtml);
            return;
        }
    }
    showToast('Kart bulunamadi', 'error');
}

// ============================================
// LOGO CREDITS EXHAUSTED MODAL
// ============================================
function showLogoCreditsExhausted(detail) {
    var msg = (detail && detail.message) || 'Logo olusturma kredileriniz tukendi.';
    showCreditsModal({ message: msg });
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
    Array.from(select.options).forEach(function(opt) {
        opt.selected = values.indexOf(parseInt(opt.value)) !== -1;
    });
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

    var stepNames = ['download', 'extract', 'metadata', 'embeddings', 'ingest'];

    // Update step cards from last run
    stepNames.forEach(function(name) {
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
        lastInfo.textContent = 'Son: ' + AppUtils.formatDateTR(pipelineLastRun.completed_at)
            + ' (' + AppUtils.formatDuration(pipelineLastRun.duration_seconds) + ')';
    } else if (pipelineLastRun && pipelineLastRun.status === 'running') {
        lastInfo.textContent = 'Suanda calisiyor...';
    } else {
        lastInfo.textContent = 'Henuz calistirilmadi';
    }

    // Footer: next scheduled
    var nextInfo = document.getElementById('pipeline-next-run-info');
    if (pipelineNextScheduled) {
        nextInfo.textContent = 'Sonraki: ' + AppUtils.formatDateTR(pipelineNextScheduled);
    } else {
        nextInfo.textContent = '';
    }
}

function stepDisplayName(step) {
    var names = {
        'starting': 'Baslatiliyor',
        'download': 'Indirme',
        'extract': 'Cikarma',
        'metadata': 'Metadata',
        'embeddings': 'Yapay Zeka',
        'ingest': 'Yukleme'
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
        showToast('Pipeline baslatildi', 'success');
        pipelineRunning = true;
        pollPipelineStatus();
    } catch (e) {
        showToast('Pipeline baslatilamadi: ' + e.message, 'error');
        if (btnFull) btnFull.disabled = false;
        if (btnSkip) btnSkip.disabled = false;
    }
}

function pollPipelineStatus() {
    var poll = function() {
        AppAPI.getPipelineStatus().then(function(data) {
            if (!data) return;
            updatePipelineUI(data);
            if (data.is_running) {
                setTimeout(poll, 5000);
            } else {
                showToast('Pipeline tamamlandi', 'success');
            }
        }).catch(function() {
            // Retry on error
            setTimeout(poll, 10000);
        });
    };
    setTimeout(poll, 2000); // First poll after 2s
}

// ============================================
// PORTFOLIO / WATCHLIST WITH LOGO UPLOAD
// ============================================
function loadPortfolio() {
    AppAPI.getWatchlistItems(1, 100).then(function(data) {
        var items = data.items || [];
        var total = data.total || items.length;
        var countEl = document.getElementById('portfolio-count');
        if (countEl) countEl.textContent = total + ' marka';
        renderPortfolioGrid(items);
    }).catch(function(e) {
        var grid = document.getElementById('portfolio-grid');
        if (grid) grid.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">Yuklenemedi</div>';
    });
}

function renderConflictStatusBadges(summary) {
    if (!summary || summary.total === 0) return '';

    var badges = '';

    if (summary.pre_publication > 0) {
        badges += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-blue-100 text-blue-800" title="Henuz yayinlanmamis tehditler">'
            + summary.pre_publication + ' Erken Uyari</span>';
    }
    if (summary.active_critical > 0) {
        badges += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800 animate-pulse" title="7 gun veya daha az">'
            + summary.active_critical + ' Kritik</span>';
    }
    if (summary.active_urgent > 0) {
        badges += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-orange-100 text-orange-800" title="30 gun veya daha az">'
            + summary.active_urgent + ' Acil</span>';
    }
    if (summary.active > 0) {
        badges += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-semibold bg-yellow-100 text-yellow-800">'
            + summary.active + ' Aktif</span>';
    }
    if (summary.expired > 0) {
        badges += '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-500">'
            + summary.expired + ' Suresi dolmus</span>';
    }

    if (summary.nearest_deadline && summary.nearest_deadline_days !== null) {
        var days = summary.nearest_deadline_days;
        var urgencyClass = days <= 7 ? 'text-red-700 font-bold' : days <= 30 ? 'text-orange-600 font-semibold' : 'text-yellow-600';
        badges += '<div class="mt-0.5 text-xs ' + urgencyClass + '">En yakin: ' + days + ' gun kaldi</div>';
    }

    return '<div class="flex flex-wrap gap-1 mt-1">' + badges + '</div>';
}

function renderPortfolioGrid(items) {
    var grid = document.getElementById('portfolio-grid');
    if (!grid) return;

    if (!items || items.length === 0) {
        grid.innerHTML = '<div class="text-sm text-gray-400 text-center py-4">Izleme listesi bos</div>';
        return;
    }

    grid.innerHTML = items.map(function(item) {
        var esc = window.AppUtils.escapeHtml;
        var classes = (item.nice_class_numbers || []).map(function(c) {
            return '<span class="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">' + c + '</span>';
        }).join(' ');

        var logoHtml;
        if (item.has_logo) {
            logoHtml = '<div class="w-10 h-10 rounded border border-gray-200 overflow-hidden flex-shrink-0 relative group">'
                + '<img src="' + item.logo_url + '" class="w-full h-full object-contain" onerror="this.style.display=\'none\'">'
                + '<button onclick="event.stopPropagation(); deleteWatchlistLogo(\'' + item.id + '\')" '
                + 'class="absolute inset-0 bg-red-500 bg-opacity-0 group-hover:bg-opacity-70 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all" '
                + 'title="Logoyu sil">'
                + '<svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
                + '</button>'
                + '</div>';
        } else {
            logoHtml = '<label onclick="event.stopPropagation();" class="w-10 h-10 rounded border-2 border-dashed border-gray-300 flex items-center justify-center flex-shrink-0 cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors" '
                + 'title="Logo yukle">'
                + '<svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>'
                + '<input type="file" accept="image/*" class="hidden" onchange="handleWatchlistLogoUpload(\'' + item.id + '\', this)">'
                + '</label>';
        }

        var alertBadge = '';
        if (item.new_alerts_count > 0) {
            alertBadge = '<span class="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full font-medium">' + item.new_alerts_count + '</span>';
        }

        var conflictBadges = renderConflictStatusBadges(item.conflict_summary);

        var escapedName = esc(item.brand_name).replace(/'/g, "\\'");

        return '<div class="flex items-center gap-3 p-2.5 rounded-lg hover:bg-gray-50 transition-colors border border-transparent hover:border-blue-200 cursor-pointer hover:ring-1 hover:ring-blue-200" '
            + 'onclick="filterAlertsByWatchlistItem(\'' + item.id + '\', \'' + escapedName + '\')">'
            + logoHtml
            + '<div class="flex-1 min-w-0">'
            + '<div class="flex items-center gap-2">'
            + '<span class="font-medium text-gray-900 text-sm truncate">' + esc(item.brand_name) + '</span>'
            + alertBadge
            + '</div>'
            + '<div class="flex gap-1 mt-1 flex-wrap">' + classes + '</div>'
            + conflictBadges
            + '</div>'
            + '<div class="flex-shrink-0 text-xs text-gray-400">'
            + (item.last_scan_at ? AppUtils.formatDateTR(item.last_scan_at) : 'Taranmadi')
            + '</div>'
            + '</div>';
    }).join('');
}

// ============================================
// WATCHLIST CARD → FILTERED ALERTS
// ============================================
window._alertFilterWatchlistId = null;

function filterAlertsByWatchlistItem(watchlistItemId, brandName) {
    var alertHeader = document.getElementById('alert-list-header');
    if (alertHeader) {
        alertHeader.innerHTML = '<div class="flex items-center justify-between">'
            + '<span class="font-semibold text-gray-900">' + escapeHtml(brandName) + ' \u2014 Tehditler</span>'
            + '<button onclick="clearAlertFilter()" class="text-sm text-blue-600 hover:text-blue-800">\u2715 Filtreyi Kaldir</button>'
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

        // Update the Alpine component's alerts
        var alpineEl = document.querySelector('[x-data]');
        if (alpineEl && alpineEl.__x) {
            var alpineData = alpineEl.__x.$data;
            alpineData.alerts = items.map(function(a) {
                var c = a.conflicting || {};
                var sc = a.scores || {};
                return {
                    alert_id: a.id,
                    conflicting_brand: c.name || 'N/A',
                    conflicting_app_no: c.application_no || '',
                    brand_watched: a.watched_brand_name || '',
                    risk_score: Math.round((sc.total || 0) * 100),
                    scores: sc,
                    date: a.detected_at || '',
                    appeal_deadline: a.appeal_deadline || null,
                    deadline_status: a.deadline_status || null,
                    deadline_days_remaining: a.deadline_days_remaining,
                    deadline_label: a.deadline_label || '',
                    deadline_urgency: a.deadline_urgency || ''
                };
            });
        }
    } catch (err) {
        showToast('Tehditler yuklenemedi', 'error');
    }
}

function clearAlertFilter() {
    window._alertFilterWatchlistId = null;
    var alertHeader = document.getElementById('alert-list-header');
    if (alertHeader) {
        alertHeader.innerHTML = '<span class="font-semibold text-gray-900">Son Tehditler</span>';
    }
    // Reload all alerts by refreshing the Alpine component
    var alpineEl = document.querySelector('[x-data]');
    if (alpineEl && alpineEl.__x) {
        alpineEl.__x.$data.loadData();
    }
}

function handleWatchlistLogoUpload(itemId, input) {
    var file = input.files && input.files[0];
    if (!file) return;

    if (!file.type.startsWith('image/')) {
        showToast('Lutfen bir gorsel dosyasi secin', 'error');
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        showToast('Dosya boyutu 5MB\'yi asamaz', 'error');
        return;
    }

    showToast('Logo yukleniyor...', 'info');
    AppAPI.uploadWatchlistLogo(itemId, file).then(function(data) {
        showToast(data.message || 'Logo yuklendi', 'success');
        loadPortfolio();
    }).catch(function(e) {
        showToast('Hata: ' + e.message, 'error');
    });
}

function deleteWatchlistLogo(itemId) {
    AppAPI.deleteWatchlistLogo(itemId).then(function(data) {
        showToast(data.message || 'Logo silindi', 'success');
        loadPortfolio();
    }).catch(function(e) {
        showToast('Hata: ' + e.message, 'error');
    });
}

// ============================================
// REPORTS TAB
// ============================================
window._reportsInitialized = false;
var _reportsCurrentPage = 1;

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

    loadReportsAPI(1).then(function(data) {
        loading.classList.add('hidden');
        _reportsCurrentPage = data.page || 1;

        // Update usage counter
        if (data.usage) {
            var usageEl = document.getElementById('reports-usage-count');
            if (usageEl) {
                usageEl.textContent = (data.usage.reports_limit - data.usage.reports_used) + '/' + data.usage.reports_limit + ' rapor (bu ay)';
            }
        }

        renderReportsList(data);
    }).catch(function(err) {
        loading.classList.add('hidden');
        if (err.status === 403) {
            upgradePrompt.classList.remove('hidden');
        } else {
            showToast('Raporlar yuklenemedi', 'error');
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
        'weekly_digest': 'Haftalik Ozet',
        'monthly_summary': 'Aylik Ozet',
        'watchlist_status': 'Portfoy Durumu',
        'watchlist_summary': 'Haftalik Ozet',
        'alert_digest': 'Aylik Ozet',
        'risk_assessment': 'Risk Analizi',
        'competitor_analysis': 'Rakip Analizi',
        'portfolio_status': 'Portfoy Durumu',
        'single_trademark': 'Tekli Marka',
        'full_portfolio': 'Tam Portfoy',
        'custom': 'Ozel Rapor'
    };

    var html = '';
    reports.forEach(function(report) {
        var typeLabel = typeLabels[report.report_type] || report.report_type;
        var title = escapeHtml(report.title || typeLabel);
        var dateStr = report.created_at ? formatReportDate(report.created_at) : '-';

        var statusBadge = '';
        var downloadBtn = '';
        if (report.status === 'completed') {
            statusBadge = '<span class="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">Tamamlandi</span>';
            downloadBtn = '<button onclick="handleReportDownload(\'' + report.id + '\')" '
                + 'class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded-lg transition-colors flex items-center gap-1">'
                + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">'
                + '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>'
                + '</svg> Indir</button>';
        } else if (report.status === 'generating' || report.status === 'pending') {
            statusBadge = '<span class="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full font-medium">Olusturuluyor</span>';
        } else if (report.status === 'failed') {
            statusBadge = '<span class="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-medium">Basarisiz</span>';
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
        + (page === 1 ? 'disabled' : '') + '>&larr; Onceki</button>'
        + '<span class="text-gray-500 text-sm">Sayfa ' + page + ' / ' + totalPages + '</span>'
        + '<button onclick="navigateReportsPage(' + (page + 1) + ')" '
        + 'class="px-4 py-2 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 rounded-lg disabled:opacity-50 text-sm" '
        + (page === totalPages ? 'disabled' : '') + '>Sonraki &rarr;</button>';
    container.innerHTML = html;
}

function navigateReportsPage(page) {
    if (page < 1) return;
    var loading = document.getElementById('reports-loading');
    loading.classList.remove('hidden');

    loadReportsAPI(page).then(function(data) {
        loading.classList.add('hidden');
        _reportsCurrentPage = data.page || page;
        renderReportsList(data);
    }).catch(function() {
        loading.classList.add('hidden');
        showToast('Raporlar yuklenemedi', 'error');
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

// ============================================
// REPORT GENERATION MODAL
// ============================================
function showReportGenerateModal() {
    document.getElementById('report-generate-modal').classList.remove('hidden');
}

function hideReportGenerateModal() {
    document.getElementById('report-generate-modal').classList.add('hidden');
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
            'watchlist_summary': 'Haftalik Ozet',
            'alert_digest': 'Aylik Ozet',
            'portfolio_status': 'Portfoy Durumu',
            'risk_assessment': 'Risk Analizi',
            'competitor_analysis': 'Tam Portfoy'
        };
        title = (typeNames[reportType] || 'Rapor') + ' - ' + formatReportDate(new Date().toISOString());
    }

    var btn = document.getElementById('reportSubmitBtn');
    btn.disabled = true;
    btn.textContent = 'Olusturuluyor...';

    var payload = {
        report_type: reportType,
        title: title,
        file_format: fileFormat
    };
    if (periodStart) payload.period_start = periodStart;
    if (periodEnd) payload.period_end = periodEnd;

    generateReport(payload).then(function() {
        showToast('Rapor olusturuldu', 'success');
        hideReportGenerateModal();
        window._reportsInitialized = false;
        loadReportsTab();
    }).catch(function(err) {
        if (err.status === 402) {
            showCreditsModal();
        } else if (err.status === 403) {
            showUpgradeModal('Rapor limiti doldu');
        } else {
            showToast('Rapor olusturulamadi: ' + err.message, 'error');
        }
    }).finally(function() {
        btn.disabled = false;
        btn.textContent = 'Olustur';
    });
}

// ============================================
// REPORT DOWNLOAD
// ============================================
function handleReportDownload(reportId) {
    downloadReportAPI(reportId).then(function(blob) {
        var filename = blob._filename || 'rapor.pdf';
        var url = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    }).catch(function(err) {
        if (err.status === 403) {
            showUpgradeModal('Rapor indirme icin planinizi yukseltin');
        } else {
            showToast('Rapor indirilemedi: ' + err.message, 'error');
        }
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
    buttonElement.innerHTML = '<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg> Yukleniyor...';
    buttonElement.disabled = true;

    try {
        var data = await loadExtractedGoods(applicationNo);

        if (!data.has_extracted_goods || !data.extracted_goods || data.extracted_goods.length === 0) {
            showToast('Cikarilmis urun verisi bulunamadi', 'info');
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
        showToast('Cikarilmis urun verileri yuklenemedi', 'error');
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
        items.forEach(function(item, idx) {
            var text = item.TEXT || item.text || '';
            if (!text) return;

            // Split TEXT by sub-class patterns (e.g. "06.01 ...; 06.02 ...")
            // Each TEXT may contain multiple sub-class entries separated by NN.MM patterns
            var subEntries = text.split(/(?=\d{2}\.\d{2}\s)/);

            subEntries.forEach(function(entry, subIdx) {
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
        items.forEach(function(text, idx) {
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
        + '<span class="text-sm font-semibold text-amber-800">Cikarilmis Urun/Hizmetler</span>'
        + '<span class="text-xs text-amber-600">(' + items.length + ' kayit)</span>'
        + '</div>'
        + '<button onclick="document.getElementById(\'extracted-goods-' + safeId + '\').classList.add(\'hidden\')" '
        + 'class="text-amber-600 hover:text-amber-800 text-sm font-bold px-1">&times;</button>'
        + '</div>'
        + '<div class="px-3 py-2 text-xs text-amber-700 bg-amber-50 border-b border-amber-200">'
        + 'Bu urun/hizmetler, basvuru kapsaminda tescil edilmis olan mal ve hizmetleri gostermektedir.'
        + '</div>'
        + '<div class="px-3 py-2 max-h-60 overflow-y-auto">'
        + itemsHtml
        + '</div></div>';
}
