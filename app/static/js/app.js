// Main Application JS

// Tab Switching Logic
document.addEventListener('DOMContentLoaded', () => {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanels = document.querySelectorAll('.tab-panel');

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.tab;

            tabBtns.forEach(b => b.classList.remove('active'));
            tabPanels.forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            document.querySelector(`.tab-panel[data-panel="${target}"]`).classList.add('active');
            
            // Update URL hash without jumping
            history.replaceState(null, null, `#${target}`);
        });
    });

    // Check hash on load
    if (window.location.hash) {
        const hash = window.location.hash.substring(1);
        const targetBtn = document.querySelector(`.tab-btn[data-tab="${hash}"]`);
        if (targetBtn) targetBtn.click();
    }
});

// Flash Message System
window.showFlash = function(message, category) {
    const stack = document.getElementById('flash-stack');
    if (!stack) return;

    const div = document.createElement('div');
    div.className = `flash ${category}`;
    div.textContent = message;
    stack.appendChild(div);

    setTimeout(() => {
        div.style.transition = 'all 0.4s';
        div.style.opacity = '0';
        div.style.transform = 'translateX(20px)';
        setTimeout(() => div.remove(), 400);
    }, 4000);
};

// Dropdown Toggles (Topbar)
document.addEventListener('click', (e) => {
    // Project Switcher
    const projBtn = document.getElementById('proj-switcher-btn');
    const projPanel = document.getElementById('proj-switcher-panel');
    if (projBtn && projBtn.contains(e.target)) {
        projPanel.classList.toggle('open');
    } else if (projPanel && !projPanel.contains(e.target)) {
        projPanel.classList.remove('open');
    }

    // Notifications
    const notifBtn = document.getElementById('notif-btn');
    const notifPanel = document.getElementById('notif-panel');
    if (notifBtn && notifBtn.contains(e.target)) {
        notifPanel.classList.toggle('open');
    } else if (notifPanel && !notifPanel.contains(e.target)) {
        notifPanel.classList.remove('open');
    }
});

// Modal Logic
window.closeModal = function(id) {
    document.getElementById(id).classList.remove('open');
};
// SSE Notifications
if (!!window.EventSource) {
    const source = new EventSource('/notifications/stream');
    source.onmessage = function(e) {
        const data = JSON.parse(e.data);
        if (window.showFlash) {
            window.showFlash(`${data.title}: ${data.body}`, 'info');
        }
        // Increment badge if exists
        const badge = document.querySelector('.notif-badge');
        if (badge) {
            badge.textContent = parseInt(badge.textContent || 0) + 1;
            badge.style.display = 'flex';
        }
    };
    source.onerror = function(e) {
        console.log("SSE error, state:", source.readyState);
    };
}

// Image Board — lightbox modal
window.openImageModal = function(imageSrc) {
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.95); display:flex; align-items:center; justify-content:center; z-index:9999; cursor:pointer;';
    modal.innerHTML = '<img src="' + imageSrc + '" style="max-width:90vw; max-height:90vh; object-fit:contain;" onclick="event.stopPropagation();"><span style="position:absolute; top:20px; right:20px; color:#fff; font-size:28px; cursor:pointer; user-select:none;">×</span>';
    modal.onclick = () => modal.remove();
    document.body.appendChild(modal);
};

// Image Board — delete image
window.deleteImage = function(imgId, projectId, csrf) {
    if (confirm('Delete this image?')) {
        fetch('/projects/' + projectId + '/images/' + imgId + '/delete', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf }
        }).then(() => window.location.reload());
    }
};
