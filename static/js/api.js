/**
 * api.js - All fetch/API calls
 */
window.AppAPI = window.AppAPI || {};

// ============================================
// QUICK (DB-ONLY) SEARCH
// ============================================
window.AppAPI.handleQuickSearch = async function(page) {
    if (page === undefined) page = 1;
    var input = document.getElementById('search-input');
    var query = (input && input.value || '').trim();
    if (!query) { showToast('Lutfen bir marka adi girin', 'error'); return; }

    var classes = getSelectedNiceClasses();
    var url = '/api/v1/search/quick?query=' + encodeURIComponent(query)
        + '&page=' + page + '&per_page=' + SEARCH_PER_PAGE;
    if (classes.length) url += '&classes=' + classes.join(',');

    try {
        var res = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (res.status === 401) { showToast('Oturum suresiz doldu. Lutfen tekrar giris yapin.', 'error'); return; }
        if (res.status === 429) {
            var errData = await res.json().catch(function() { return {}; });
            var msg = (errData.detail && errData.detail.message) || 'Gunluk arama limitinize ulastiniz.';
            showToast(msg, 'warning');
            return;
        }
        if (!res.ok) throw new Error('Arama basarisiz');
        var data = await res.json();
        currentSearchPage = data.page || 1;
        currentSearchType = 'quick';
        displayAgenticResults(data);
        if (page === 1) showToast((data.total || 0) + ' sonuc bulundu (veritabani)', 'success');
    } catch (e) {
        console.error('Quick search error:', e);
        showToast('Hata: ' + e.message, 'error');
    }
};

// ============================================
// AGENTIC (LIVE) SEARCH
// ============================================
window.AppAPI.handleAgenticSearch = async function(page) {
    if (page === undefined) page = 1;
    var input = document.getElementById('search-input');
    var query = (input && input.value || '').trim();
    if (!query) { showToast('Lutfen bir marka adi girin', 'error'); return; }

    var classes = getSelectedNiceClasses();
    var imageInput = document.getElementById('search-image');
    var imageFile = imageInput && imageInput.files && imageInput.files[0];

    if (page === 1) {
        agenticSearchAborted = false;
        showAgenticLoadingModal();
    }

    try {
        var res;
        if (imageFile) {
            // POST with FormData (multipart) when image is provided
            var formData = new FormData();
            formData.append('query', query);
            formData.append('image', imageFile);
            formData.append('page', page);
            formData.append('per_page', SEARCH_PER_PAGE);
            if (classes.length) formData.append('classes', classes.join(','));

            res = await fetch('/api/v1/search/intelligent', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + getAuthToken() },
                body: formData
            });
        } else {
            // GET without image (backward compatible)
            var url = '/api/v1/search/intelligent?query=' + encodeURIComponent(query)
                + '&page=' + page + '&per_page=' + SEARCH_PER_PAGE;
            if (classes.length) url += '&classes=' + classes.join(',');

            res = await fetch(url, {
                headers: { 'Authorization': 'Bearer ' + getAuthToken() }
            });
        }

        if (agenticSearchAborted) return;
        var data = await res.json();

        if (res.status === 403) { hideAgenticLoadingModal(); showUpgradeModal(data.detail); return; }
        if (res.status === 402) { hideAgenticLoadingModal(); showCreditsModal(data.detail); return; }
        if (res.status === 401) { hideAgenticLoadingModal(); showToast('Oturum suresiz doldu. Lutfen tekrar giris yapin.', 'error'); return; }
        if (!res.ok) throw new Error(data.detail?.message || data.detail || 'Arama basarisiz');

        hideAgenticLoadingModal();
        currentSearchPage = data.page || 1;
        currentSearchType = 'intelligent';
        displayAgenticResults(data);

        if (page === 1) {
            var creditsMsg = data.scrape_triggered
                ? 'Kalan hak: ' + data.credits_remaining
                : 'Veritabanindan (kredi kullanilmadi)';
            var imageMsg = data.image_used ? ' (gorsel analiz dahil)' : '';
            showToast((data.total || 0) + ' sonuc bulundu' + imageMsg + '. ' + creditsMsg, 'success');
        }

    } catch (e) {
        if (!agenticSearchAborted) {
            hideAgenticLoadingModal();
            console.error('Agentic search error:', e);
            showToast('Hata: ' + e.message, 'error');
        }
    }
};

// ============================================
// OPPOSITION RADAR - LEADS
// ============================================
window.AppAPI.loadLeadStats = async function() {
    try {
        var response = await fetch('/api/v1/leads/stats', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        if (response.status === 403) {
            showLeadUpgradePrompt();
            return;
        }
        if (!response.ok) return;

        var stats = await response.json();
        document.getElementById('stat-critical').textContent = stats.critical_leads || 0;
        document.getElementById('stat-urgent').textContent = stats.urgent_leads || 0;
        document.getElementById('stat-total').textContent = stats.total_leads || 0;
        document.getElementById('stat-converted').textContent = stats.converted_leads || 0;
    } catch (error) {
        console.error('Failed to load lead stats:', error);
    }
};

window.AppAPI.loadLeadCredits = async function() {
    try {
        var response = await fetch('/api/v1/leads/credits', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) return;

        var credits = await response.json();
        var remaining = credits.remaining === 'unlimited' ? '\u221E' : credits.remaining;
        document.getElementById('lead-credits-remaining').textContent = remaining;

        if (credits.plan === 'enterprise') {
            document.getElementById('export-leads-btn').classList.remove('hidden');
        }
    } catch (error) {
        console.error('Failed to load lead credits:', error);
    }
};

window.AppAPI.loadLeadFeed = async function(page) {
    if (page === undefined) page = 1;
    currentLeadPage = page;

    var container = document.getElementById('lead-feed-list');
    var loading = document.getElementById('lead-feed-loading');
    var empty = document.getElementById('lead-feed-empty');
    var pagination = document.getElementById('lead-pagination');

    loading.classList.remove('hidden');
    container.innerHTML = '';
    empty.classList.add('hidden');

    var urgency = document.getElementById('filter-urgency').value;
    var risk = document.getElementById('filter-risk').value;
    var niceClass = document.getElementById('filter-nice-class').value;
    var status = document.getElementById('filter-status').value;

    var url = '/api/v1/leads/feed?page=' + page + '&limit=' + LEADS_PER_PAGE;
    if (urgency) url += '&urgency=' + urgency;
    if (risk) url += '&risk_level=' + risk;
    if (niceClass) url += '&nice_class=' + niceClass;
    if (status) url += '&status=' + status;

    try {
        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        loading.classList.add('hidden');

        if (response.status === 403) { showLeadUpgradePrompt(); return; }
        if (response.status === 429) { showToast('Gunluk lead limitinize ulastiniz.', 'warning'); return; }
        if (!response.ok) throw new Error('Failed to load leads');

        var leads = await response.json();

        if (leads.length === 0) {
            empty.classList.remove('hidden');
            pagination.classList.add('hidden');
            return;
        }

        container.innerHTML = leads.map(renderLeadCard).join('');

        pagination.classList.remove('hidden');
        document.getElementById('lead-page-info').textContent = 'Sayfa ' + page;
        document.getElementById('lead-prev-btn').disabled = page === 1;
        document.getElementById('lead-next-btn').disabled = leads.length < LEADS_PER_PAGE;

    } catch (error) {
        loading.classList.add('hidden');
        console.error('Failed to load leads:', error);
        showToast('Leadler yuklenirken hata olustu.', 'error');
    }
};

window.AppAPI.showLeadDetail = async function(leadId) {
    currentLeadId = leadId;
    var modal = document.getElementById('lead-detail-modal');
    var content = document.getElementById('lead-detail-content');

    modal.classList.remove('hidden');
    content.innerHTML = '<div class="text-center py-8"><div class="animate-spin inline-block w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full"></div></div>';

    try {
        var response = await fetch('/api/v1/leads/' + leadId, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) throw new Error('Failed to load lead');

        var lead = await response.json();
        var scorePercent = Math.round(lead.similarity_score * 100);

        var riskColorClass = window.AppComponents.getRiskColorClass(lead.risk_level);

        var reasonsHtml = '';
        if (lead.conflict_reasons && lead.conflict_reasons.length) {
            reasonsHtml = '<div class="bg-gray-50 rounded-xl p-4 border border-gray-200">'
                + '<div class="text-gray-600 font-semibold mb-2 text-sm">Cakisma Nedenleri</div>'
                + '<ul class="space-y-1">'
                + lead.conflict_reasons.map(function(r) { return '<li class="text-sm text-gray-700 flex items-center gap-2"><span class="text-amber-500">&bull;</span> ' + r + '</li>'; }).join('')
                + '</ul></div>';
        }

        content.innerHTML = '<div class="space-y-5">'
            + '<div class="text-center">'
            + '<div class="text-4xl font-bold ' + riskColorClass + '">' + scorePercent + '%</div>'
            + '<div class="text-gray-500 text-sm">Benzerlik Skoru</div>'
            + '<span class="inline-block mt-1 text-xs font-medium px-2.5 py-0.5 rounded-full '
            + window.AppComponents.getRiskBadgeSmall(lead.risk_level)
            + '">' + lead.risk_level + ' Risk</span>'
            + '<div class="flex justify-center mt-2">' + window.AppComponents.renderSimilarityBadges(lead) + '</div>'
            + '</div>'
            + '<div class="bg-amber-50 border border-amber-200 rounded-xl p-4 text-center">'
            + '<div class="text-amber-700 font-semibold text-sm">Itiraz Suresi</div>'
            + '<div class="text-2xl font-bold text-gray-900">' + lead.days_until_deadline + ' gun kaldi</div>'
            + '<div class="text-sm text-gray-500">Son tarih: ' + lead.opposition_deadline + '</div></div>'
            + '<div class="grid grid-cols-2 gap-3">'
            + '<div class="bg-red-50 rounded-xl p-4 border border-red-100">'
            + '<div class="text-red-600 font-semibold text-sm mb-2">Yeni Basvuru</div>'
            + '<div class="flex items-start gap-3">'
            + window.AppComponents.renderThumbnail(lead.new_mark_image, lead.new_mark_name, lead.new_mark_app_no, 'w-14 h-14')
            + '<div class="flex-1 min-w-0 space-y-1 text-sm">'
            + '<div><span class="text-gray-500">Marka:</span> <span class="text-gray-900 font-medium">' + (lead.new_mark_name || 'N/A') + '</span></div>'
            + '<div><span class="text-gray-500">No:</span> <span class="text-gray-900">' + (lead.new_mark_app_no || 'N/A') + '</span></div>'
            + '<div><span class="text-gray-500">Sahip:</span> <span class="text-gray-900">' + (lead.new_mark_holder_name || 'Bilinmiyor') + '</span></div>'
            + '<div><span class="text-gray-500">Siniflar:</span> ' + window.AppComponents.renderNiceClassBadges(lead.new_mark_nice_classes, 4) + '</div>'
            + (lead.new_mark_has_extracted_goods
                ? '<div class="mt-2"><button onclick="showExtractedGoods(\'' + (lead.new_mark_app_no || '').replace(/\'/g, "\\'") + '\', this)" '
                  + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer">'
                  + 'CIKARILMIS URUN: <span class="underline">EVET</span></button></div>' : '')
            + '</div></div></div>'
            + '<div class="bg-green-50 rounded-xl p-4 border border-green-100">'
            + '<div class="text-green-600 font-semibold text-sm mb-2">Potansiyel Musteri</div>'
            + '<div class="flex items-start gap-3">'
            + window.AppComponents.renderThumbnail(lead.existing_mark_image, lead.existing_mark_name, lead.existing_mark_app_no, 'w-14 h-14')
            + '<div class="flex-1 min-w-0 space-y-1 text-sm">'
            + '<div><span class="text-gray-500">Marka:</span> <span class="text-gray-900 font-medium">' + (lead.existing_mark_name || 'N/A') + '</span></div>'
            + '<div><span class="text-gray-500">No:</span> <span class="text-gray-900">' + (lead.existing_mark_app_no || 'N/A') + '</span></div>'
            + '<div><span class="text-gray-500">Sahip:</span> <span class="text-gray-900 font-semibold">' + (lead.existing_mark_holder_name || 'Bilinmiyor') + '</span></div>'
            + '<div><span class="text-gray-500">Siniflar:</span> ' + window.AppComponents.renderNiceClassBadges(lead.existing_mark_nice_classes, 4) + '</div>'
            + (lead.existing_mark_has_extracted_goods
                ? '<div class="mt-2"><button onclick="showExtractedGoods(\'' + (lead.existing_mark_app_no || '').replace(/\'/g, "\\'") + '\', this)" '
                  + 'class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300 hover:bg-amber-200 cursor-pointer">'
                  + 'CIKARILMIS URUN: <span class="underline">EVET</span></button></div>' : '')
            + '</div></div></div></div>'
            + reasonsHtml
            + '<div class="text-sm text-gray-400 text-center">'
            + 'Bulten: ' + (lead.bulletin_no || 'N/A') + ' &bull; Tarih: ' + (lead.bulletin_date || 'N/A') + ' &bull; Tip: ' + lead.conflict_type
            + '</div></div>';

        loadLeadFeed(currentLeadPage);

    } catch (error) {
        console.error('Failed to load lead detail:', error);
        content.innerHTML = '<div class="text-center py-8 text-red-500">Lead yuklenirken hata olustu.</div>';
    }
};

window.AppAPI.updateLeadStatus = async function(leadId, action) {
    try {
        var response = await fetch('/api/v1/leads/' + leadId + '/' + action, {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (!response.ok) throw new Error('Failed');

        var result = await response.json();
        showToast(result.message, 'success');
        hideLeadDetailModal();
        loadLeadFeed(currentLeadPage);
        loadLeadStats();
    } catch (error) {
        console.error('Failed to update lead:', error);
        showToast('Islem basarisiz.', 'error');
    }
};

window.AppAPI.exportLeadsCSV = async function() {
    try {
        var urgency = document.getElementById('filter-urgency').value;
        var niceClass = document.getElementById('filter-nice-class').value;

        var url = '/api/v1/leads/export/csv?';
        if (urgency) url += 'urgency=' + urgency + '&';
        if (niceClass) url += 'nice_class=' + niceClass;

        var response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        if (response.status === 403) { showToast('CSV export sadece Enterprise plan icin kullanilabilir.', 'warning'); return; }
        if (!response.ok) throw new Error('Export failed');

        var blob = await response.blob();
        var downloadUrl = window.URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = downloadUrl;
        a.download = 'leads_' + new Date().toISOString().split('T')[0] + '.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(downloadUrl);
        showToast('CSV basariyla indirildi.', 'success');
    } catch (error) {
        console.error('Export failed:', error);
        showToast('Export basarisiz.', 'error');
    }
};

// ============================================
// HOLDER PORTFOLIO
// ============================================
window.AppAPI.loadHolderTrademarks = async function(tpeClientId, page) {
    try {
        var res = await fetch('/api/v1/holders/' + encodeURIComponent(tpeClientId) + '/trademarks?page=' + page + '&page_size=20', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });

        if (res.status === 403) {
            closeHolderPortfolio();
            showUpgradeModal();
            return;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);

        var data = await res.json();

        document.getElementById('holderModalTitle').textContent = data.holder_name;
        document.getElementById('holderModalSubtitle').textContent = 'TPE No: ' + data.holder_tpe_client_id + ' \u2022 ' + data.total_count + ' marka basvurusu';

        document.getElementById('holderTotalCount').textContent = data.total_count;
        var registered = 0, pending = 0;
        data.trademarks.forEach(function(t) {
            if (t.status === 'Registered' || t.status === 'Renewed') registered++;
            if (t.status === 'Applied' || t.status === 'Published' || t.status === 'Opposed') pending++;
        });
        document.getElementById('holderRegisteredCount').textContent = registered;
        document.getElementById('holderPendingCount').textContent = pending;

        renderHolderTrademarks(data.trademarks);
        renderHolderPagination(data.page, data.total_pages, tpeClientId);

        document.getElementById('holderPortfolioLoading').classList.add('hidden');
        document.getElementById('holderPortfolioResults').classList.remove('hidden');

    } catch(e) {
        document.getElementById('holderPortfolioLoading').classList.add('hidden');
        document.getElementById('holderPortfolioError').classList.remove('hidden');
        document.getElementById('holderErrorMessage').textContent = 'Portfolio yuklenirken bir hata olustu. Lutfen tekrar deneyin.';
    }
};

// ============================================
// CREATIVE SUITE - NAME GENERATOR
// ============================================
window.AppAPI.generateNames = async function(params) {
    var res = await fetch('/api/v1/tools/suggest-names', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(params)
    });

    var data = await res.json();

    if (res.status === 403) {
        showUpgradeModal(data.detail);
        throw new Error('upgrade_required');
    }
    if (res.status === 402) {
        showCreditsModal(data.detail);
        throw new Error('credits_exhausted');
    }
    if (res.status === 401) {
        showToast('Oturum suresi doldu. Lutfen tekrar giris yapin.', 'error');
        throw new Error('unauthorized');
    }
    if (!res.ok) {
        var msg = (data.detail && data.detail.message) || data.detail || 'Isim olusturma basarisiz';
        throw new Error(msg);
    }

    return data;
};

// ============================================
// CREATIVE SUITE - LOGO GENERATOR
// ============================================
window.AppAPI.generateLogos = async function(params) {
    var res = await fetch('/api/v1/tools/generate-logo', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(params)
    });

    var data = await res.json();

    if (res.status === 403) {
        showUpgradeModal(data.detail);
        throw new Error('upgrade_required');
    }
    if (res.status === 402) {
        showLogoCreditsExhausted(data.detail);
        throw new Error('credits_exhausted');
    }
    if (res.status === 401) {
        showToast('Oturum suresi doldu. Lutfen tekrar giris yapin.', 'error');
        throw new Error('unauthorized');
    }
    if (!res.ok) {
        var msg = (data.detail && data.detail.message) || data.detail || 'Logo olusturma basarisiz';
        throw new Error(msg);
    }

    return data;
};

// ============================================
// CREATIVE SUITE - GENERATION HISTORY
// ============================================
window.AppAPI.getGenerationHistory = async function(page, featureType) {
    if (page === undefined) page = 1;
    var url = '/api/v1/tools/generation-history?page=' + page + '&per_page=20';
    if (featureType) url += '&feature_type=' + featureType;

    var res = await fetch(url, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error('Gecmis yuklenemedi');
    return await res.json();
};

// ============================================
// PIPELINE MANAGEMENT (admin only)
// ============================================
window.AppAPI.getPipelineStatus = async function() {
    var res = await fetch('/api/v1/pipeline/status', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (res.status === 403) return null;
    if (!res.ok) throw new Error('Pipeline durumu alinamadi');
    return await res.json();
};

window.AppAPI.triggerPipeline = async function(skipDownload) {
    var res = await fetch('/api/v1/pipeline/trigger?skip_download=' + (skipDownload ? 'true' : 'false'), {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json();
    if (res.status === 409) throw new Error(data.detail.message || 'Pipeline zaten calisiyor');
    if (res.status === 403) throw new Error('Yetkiniz yok');
    if (!res.ok) throw new Error(data.detail || 'Pipeline baslatilamadi');
    return data;
};

window.AppAPI.triggerPipelineStep = async function(step) {
    var res = await fetch('/api/v1/pipeline/trigger-step?step=' + encodeURIComponent(step), {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    var data = await res.json();
    if (res.status === 409) throw new Error(data.detail.message || 'Pipeline zaten calisiyor');
    if (res.status === 403) throw new Error('Yetkiniz yok');
    if (!res.ok) throw new Error(data.detail || 'Adim baslatilamadi');
    return data;
};

// ============================================
// WATCHLIST LOGO MANAGEMENT
// ============================================
window.AppAPI.uploadWatchlistLogo = async function(itemId, file) {
    var formData = new FormData();
    formData.append('logo', file);

    var res = await fetch('/api/v1/watchlist/' + itemId + '/logo', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() },
        body: formData
    });
    if (!res.ok) {
        var data = await res.json();
        throw new Error(data.detail || 'Logo yukleme basarisiz');
    }
    return await res.json();
};

window.AppAPI.deleteWatchlistLogo = async function(itemId) {
    var res = await fetch('/api/v1/watchlist/' + itemId + '/logo', {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json();
        throw new Error(data.detail || 'Logo silme basarisiz');
    }
    return await res.json();
};

window.AppAPI.getWatchlistItems = async function(page, pageSize) {
    if (page === undefined) page = 1;
    if (pageSize === undefined) pageSize = 50;
    var res = await fetch('/api/v1/watchlist?page=' + page + '&page_size=' + pageSize, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) throw new Error('Izleme listesi yuklenemedi');
    return await res.json();
};

// ============================================
// HOLDER SEARCH
// ============================================
window.AppAPI.searchHolders = async function(query, limit) {
    if (limit === undefined) limit = 10;
    var res = await fetch('/api/v1/holders/search?query=' + encodeURIComponent(query) + '&limit=' + limit, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function() { return {}; });
        var err = new Error(data.detail || 'Arama basarisiz');
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return await res.json();
};

// ============================================
// REPORTS
// ============================================
window.AppAPI.generateReport = async function(reportData) {
    var res = await fetch('/api/v1/reports/generate', {
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + getAuthToken(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(reportData)
    });
    var data = await res.json();
    if (!res.ok) {
        var err = new Error((data.detail && data.detail.message) || data.detail || 'Rapor olusturulamadi');
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
};

window.AppAPI.loadReports = async function(page, pageSize) {
    if (page === undefined) page = 1;
    if (pageSize === undefined) pageSize = 10;
    var res = await fetch('/api/v1/reports?page=' + page + '&page_size=' + pageSize, {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var err = new Error('Raporlar yuklenemedi');
        err.status = res.status;
        throw err;
    }
    return await res.json();
};

window.AppAPI.downloadReport = async function(reportId) {
    var res = await fetch('/api/v1/reports/' + reportId + '/download', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!res.ok) {
        var data = await res.json().catch(function() { return {}; });
        var err = new Error((data.detail && data.detail.message) || data.detail || 'Rapor indirilemedi');
        err.status = res.status;
        err.data = data;
        throw err;
    }
    var disposition = res.headers.get('Content-Disposition');
    var filename = 'rapor.pdf';
    if (disposition) {
        var match = disposition.match(/filename[^;=\n]*=["']?([^"';\n]*)["']?/);
        if (match && match[1]) filename = match[1];
    }
    var blob = await res.blob();
    blob._filename = filename;
    return blob;
};

// ============================================
// EXTRACTED GOODS LAZY LOAD
// ============================================
window.AppAPI.loadExtractedGoods = async function(applicationNo) {
    var resp = await fetch('/api/v1/trademark/' + encodeURIComponent(applicationNo) + '/extracted-goods', {
        headers: { 'Authorization': 'Bearer ' + getAuthToken() }
    });
    if (!resp.ok) {
        throw new Error('Failed to load extracted goods: ' + resp.status);
    }
    return await resp.json();
};

// Expose as globals for inline onclick handlers
var handleQuickSearch = window.AppAPI.handleQuickSearch;
var handleAgenticSearch = window.AppAPI.handleAgenticSearch;
var loadLeadStats = window.AppAPI.loadLeadStats;
var loadLeadCredits = window.AppAPI.loadLeadCredits;
var loadLeadFeed = window.AppAPI.loadLeadFeed;
var showLeadDetail = window.AppAPI.showLeadDetail;
var updateLeadStatus = window.AppAPI.updateLeadStatus;
var exportLeadsCSV = window.AppAPI.exportLeadsCSV;
var loadHolderTrademarks = window.AppAPI.loadHolderTrademarks;
var searchHolders = window.AppAPI.searchHolders;
var generateNamesAPI = window.AppAPI.generateNames;
var generateLogosAPI = window.AppAPI.generateLogos;
var getGenerationHistory = window.AppAPI.getGenerationHistory;
var generateReport = window.AppAPI.generateReport;
var loadReportsAPI = window.AppAPI.loadReports;
var downloadReportAPI = window.AppAPI.downloadReport;
var loadExtractedGoods = window.AppAPI.loadExtractedGoods;
