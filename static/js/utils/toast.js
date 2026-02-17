/**
 * toast.js - Toast notification system
 * Uses CSS custom properties for z-index and modern styling.
 */
window.AppToast = window.AppToast || {};

window.AppToast.showToast = function(message, type) {
    type = type || 'info';
    var icons = {
        success: '<svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        error: '<svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        info: '<svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        warning: '<svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>'
    };
    var colors = {
        success: 'bg-green-600',
        error: 'bg-red-600',
        info: 'bg-blue-600',
        warning: 'bg-amber-600'
    };

    var toast = document.createElement('div');
    var isMobile = window.innerWidth < 640;
    toast.className = 'fixed px-5 py-3 rounded-xl text-white text-sm shadow-lg ' + (colors[type] || colors.info) + ' flex items-center gap-2.5'
        + (isMobile ? ' left-4 right-4 bottom-20' : ' top-4 right-4 max-w-sm');
    toast.style.cssText = 'z-index:var(--z-toast,80);transform:translateY(' + (isMobile ? '8px' : '-8px') + ');opacity:0;transition:transform 0.3s ease, opacity 0.3s ease';
    toast.innerHTML = (icons[type] || icons.info) + '<span>' + message + '</span>';
    document.body.appendChild(toast);

    // Animate in
    requestAnimationFrame(function() {
        toast.style.transform = 'translateY(0)';
        toast.style.opacity = '1';
    });

    // Animate out after delay
    setTimeout(function() {
        toast.style.transform = 'translateY(-8px)';
        toast.style.opacity = '0';
        setTimeout(function() { toast.remove(); }, 300);
    }, 4000);
};

// Expose as global
var showToast = window.AppToast.showToast;
