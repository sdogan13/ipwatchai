/**
 * opposition-timeline.js - Reusable opposition timeline rendering
 * Shows: "Published: date -> Deadline: date (X days left)" with urgency colors
 * Used by lead cards, lead detail modal, and alert views.
 * Uses CSS custom properties for dark mode compatibility.
 */
window.AppComponents = window.AppComponents || {};

/**
 * Format a date string (YYYY-MM-DD or ISO) to localized short format.
 * Returns "15 Jan 2025" style or raw string if unparseable.
 */
window.AppComponents.formatTimelineDate = function(dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return d.getDate() + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
};

/**
 * Compute days remaining from a deadline date string.
 * Returns integer (negative if past).
 */
window.AppComponents.daysUntilDeadline = function(deadlineStr) {
    if (!deadlineStr) return null;
    var deadline = new Date(deadlineStr);
    if (isNaN(deadline.getTime())) return null;
    var now = new Date();
    now.setHours(0, 0, 0, 0);
    deadline.setHours(0, 0, 0, 0);
    return Math.ceil((deadline - now) / (1000 * 60 * 60 * 24));
};

/**
 * Get urgency classification for a deadline.
 * Returns { style, textStyle, label, icon, pulse }
 * Uses CSS variables for dark mode compatibility.
 */
window.AppComponents.getDeadlineUrgency = function(daysLeft) {
    if (daysLeft === null || daysLeft === undefined) {
        return {
            style: 'background:var(--color-bg-muted);border-color:var(--color-border)',
            textStyle: 'color:var(--color-text-faint)',
            label: '',
            icon: '',
            pulse: false
        };
    }
    if (daysLeft < 0) {
        return {
            style: 'background:var(--color-bg-muted);border-color:var(--color-border)',
            textStyle: 'color:var(--color-text-faint);text-decoration:line-through',
            label: t('opposition.expired'),
            icon: '<svg class="w-3.5 h-3.5" style="color:var(--color-deadline-expired)" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
            pulse: false
        };
    }
    if (daysLeft <= 7) {
        return {
            style: 'background:var(--color-risk-critical-bg);border-color:var(--color-risk-critical-border)',
            textStyle: 'color:var(--color-risk-critical-text);font-weight:700',
            label: t('opposition.days_left', { days: daysLeft }),
            icon: '<svg class="w-3.5 h-3.5" style="color:var(--color-deadline-critical)" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>',
            pulse: true
        };
    }
    if (daysLeft <= 30) {
        return {
            style: 'background:var(--color-risk-medium-bg);border-color:var(--color-risk-medium-border)',
            textStyle: 'color:var(--color-risk-medium-text);font-weight:600',
            label: t('opposition.days_left', { days: daysLeft }),
            icon: '<svg class="w-3.5 h-3.5" style="color:var(--color-deadline-warning)" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
            pulse: false
        };
    }
    return {
        style: 'background:var(--color-risk-low-bg);border-color:var(--color-risk-low-border)',
        textStyle: 'color:var(--color-risk-low-text)',
        label: t('opposition.days_left', { days: daysLeft }),
        icon: '<svg class="w-3.5 h-3.5" style="color:var(--color-deadline-safe)" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        pulse: false
    };
};

/**
 * Render a compact opposition timeline bar for lead cards.
 * Shows: Published date -> arrow -> Deadline date (days left)
 *
 * @param {string} bulletinDate - YYYY-MM-DD bulletin publication date
 * @param {string} appealDeadline - YYYY-MM-DD opposition deadline (or opposition_deadline)
 * @param {object} opts - Optional: { compact: true } for minimal display
 * @returns {string} HTML string
 */
window.AppComponents.renderOppositionTimeline = function(bulletinDate, appealDeadline, opts) {
    if (!appealDeadline && !bulletinDate) return '';
    opts = opts || {};

    var daysLeft = window.AppComponents.daysUntilDeadline(appealDeadline);
    var urgency = window.AppComponents.getDeadlineUrgency(daysLeft);
    var fmtBulletin = window.AppComponents.formatTimelineDate(bulletinDate);
    var fmtDeadline = window.AppComponents.formatTimelineDate(appealDeadline);
    var pulseClass = urgency.pulse ? ' deadline-critical-pulse' : '';

    // Compact mode: single line for card footers
    if (opts.compact) {
        var parts = [];
        if (fmtBulletin) {
            parts.push('<span style="color:var(--color-text-muted)">' + t('opposition.published') + '</span> '
                + '<span style="color:var(--color-text-secondary)">' + fmtBulletin + '</span>');
        }
        if (fmtDeadline) {
            parts.push('<span style="color:var(--color-text-muted)">' + t('opposition.deadline') + '</span> '
                + '<span style="' + urgency.textStyle + '">' + fmtDeadline + '</span>');
        }
        if (urgency.label) {
            parts.push('<span class="' + pulseClass + '" style="' + urgency.textStyle + '">' + urgency.label + '</span>');
        }
        return '<div class="flex items-center gap-2 text-xs flex-wrap">'
            + urgency.icon
            + parts.join(' <span style="color:var(--color-text-faint)">&rarr;</span> ')
            + '</div>';
    }

    // Full mode: bordered box for lead detail modal
    var html = '<div class="rounded-lg border p-3" style="' + urgency.style + '">'
        + '<div class="flex items-center gap-4 text-sm">';

    // Published date
    if (fmtBulletin) {
        html += '<div class="flex-1">'
            + '<div class="text-xs mb-0.5" style="color:var(--color-text-muted)">' + t('opposition.published') + '</div>'
            + '<div class="font-medium" style="color:var(--color-text-primary)">' + fmtBulletin + '</div>'
            + '</div>';
    }

    // Arrow
    html += '<div style="color:var(--color-text-faint)">'
        + '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>'
        + '</div>';

    // Deadline date + urgency
    if (fmtDeadline) {
        html += '<div class="flex-1 text-right">'
            + '<div class="text-xs mb-0.5" style="color:var(--color-text-muted)">' + t('opposition.deadline') + '</div>'
            + '<div class="font-medium" style="' + urgency.textStyle + '">' + fmtDeadline + '</div>'
            + '</div>';
    }

    html += '</div>';

    // Urgency label bar
    if (urgency.label) {
        html += '<div class="mt-2 pt-2 flex items-center justify-center gap-1.5' + pulseClass + '" style="border-top:1px solid var(--color-border)">'
            + urgency.icon
            + '<span class="text-sm" style="' + urgency.textStyle + '">' + urgency.label + '</span>'
            + '</div>';
    }

    html += '</div>';
    return html;
};

/**
 * Render a visual horizontal progress bar showing where TODAY falls
 * between bulletin publication and appeal deadline.
 *
 * @param {string} bulletinDate - YYYY-MM-DD publication date (start)
 * @param {string} appealDeadline - YYYY-MM-DD opposition deadline (end)
 * @param {object} opts - Optional: { height: 'sm'|'md' }
 * @returns {string} HTML string
 */
window.AppComponents.renderTimelineBar = function(bulletinDate, appealDeadline, opts) {
    if (!bulletinDate || !appealDeadline) return '';
    opts = opts || {};

    var start = new Date(bulletinDate);
    var end = new Date(appealDeadline);
    if (isNaN(start.getTime()) || isNaN(end.getTime())) return '';

    start.setHours(0, 0, 0, 0);
    end.setHours(0, 0, 0, 0);

    var now = new Date();
    now.setHours(0, 0, 0, 0);

    var totalMs = end - start;
    if (totalMs <= 0) return '';

    var elapsedMs = now - start;
    var pct = Math.max(0, Math.min(100, Math.round((elapsedMs / totalMs) * 100)));

    var daysLeft = Math.ceil((end - now) / (1000 * 60 * 60 * 24));
    var totalDays = Math.round(totalMs / (1000 * 60 * 60 * 24));
    var elapsed = totalDays - Math.max(0, daysLeft);

    // Determine bar color based on urgency
    var barColor, markerColor;
    if (daysLeft < 0) {
        barColor = 'var(--color-deadline-expired)';
        markerColor = 'var(--color-deadline-expired)';
    } else if (daysLeft <= 7) {
        barColor = 'var(--color-deadline-critical)';
        markerColor = 'var(--color-deadline-critical)';
    } else if (daysLeft <= 30) {
        barColor = 'var(--color-deadline-warning)';
        markerColor = 'var(--color-deadline-warning)';
    } else {
        barColor = 'var(--color-deadline-safe)';
        markerColor = 'var(--color-deadline-safe)';
    }

    var fmtStart = window.AppComponents.formatTimelineDate(bulletinDate);
    var fmtEnd = window.AppComponents.formatTimelineDate(appealDeadline);
    var heightClass = opts.height === 'sm' ? 'h-1.5' : 'h-2.5';
    var pulseClass = daysLeft >= 0 && daysLeft <= 7 ? ' deadline-critical-pulse' : '';

    var urgencyLabel = '';
    if (daysLeft < 0) {
        urgencyLabel = t('opposition.expired');
    } else {
        urgencyLabel = t('opposition.days_left', { days: daysLeft });
    }

    var html = '<div class="timeline-bar-wrapper" role="progressbar" aria-valuenow="' + pct + '" aria-valuemin="0" aria-valuemax="100">';

    // Top labels: dates + urgency badge
    html += '<div class="flex items-center justify-between mb-1">'
        + '<span class="text-xs" style="color:var(--color-text-muted)">' + fmtStart + '</span>'
        + '<span class="text-xs font-semibold' + pulseClass + '" style="color:' + markerColor + '">' + urgencyLabel + '</span>'
        + '<span class="text-xs" style="color:var(--color-text-muted)">' + fmtEnd + '</span>'
        + '</div>';

    // Progress track
    html += '<div class="w-full rounded-full ' + heightClass + ' relative" style="background:var(--color-border)">';

    // Filled portion
    html += '<div class="rounded-full ' + heightClass + ' timeline-bar-fill" style="width:' + pct + '%;background:' + barColor + '"></div>';

    // TODAY marker (only if within range)
    if (pct > 0 && pct < 100) {
        html += '<div class="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full border-2 shadow-sm timeline-bar-marker" '
            + 'style="left:' + pct + '%;transform:translate(-50%,-50%);background:var(--color-bg-card);border-color:' + markerColor + '"></div>';
    }

    html += '</div>';

    // Bottom labels: elapsed/total
    html += '<div class="flex items-center justify-between mt-1">'
        + '<span class="text-xs" style="color:var(--color-text-faint)">'
        + t('timeline.elapsed', { days: elapsed, total: totalDays }) + '</span>'
        + '<span class="text-xs" style="color:var(--color-text-faint)">'
        + t('timeline.progress', { pct: pct }) + '</span>'
        + '</div>';

    html += '</div>';
    return html;
};

// Expose as global
var renderOppositionTimeline = window.AppComponents.renderOppositionTimeline;
var renderTimelineBar = window.AppComponents.renderTimelineBar;
