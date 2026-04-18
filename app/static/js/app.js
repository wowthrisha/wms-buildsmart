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

// ── RAG Chat Widget ──────────────────────────────────────────────
window.sendRagQuery = function() {
  const input    = document.getElementById('rag-input');
  const messages = document.getElementById('rag-messages');
  const btn      = document.getElementById('rag-send-btn');
  if (!input || !messages) return;
  const query = input.value.trim();
  if (!query) return;

  // User message bubble (right-aligned, gold tint)
  const userMsg = document.createElement('div');
  userMsg.style.cssText = [
    'align-self:flex-end',
    'background:rgba(232,201,122,0.15)',
    'border:1px solid rgba(232,201,122,0.4)',
    'border-radius:12px',
    'padding:10px 14px',
    'font-size:13px',
    'color:var(--chalk)',
    'max-width:80%',
    'word-break:break-word'
  ].join(';');
  userMsg.textContent = query;
  messages.appendChild(userMsg);

  // Loading bubble
  const loadingMsg = document.createElement('div');
  loadingMsg.id = 'rag-loading';
  loadingMsg.style.cssText = [
    'align-self:flex-start',
    'background:var(--ink-2)',
    'border:1px solid var(--line)',
    'border-radius:12px',
    'padding:10px 14px',
    'font-size:13px',
    'color:var(--chalk-3)',
    'max-width:80%'
  ].join(';');
  loadingMsg.textContent = 'Thinking…';
  messages.appendChild(loadingMsg);
  messages.scrollTop = messages.scrollHeight;

  input.value = '';
  if (btn) btn.disabled = true;

  const csrf = window._RAG_CSRF
    || document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value
    || '';
  const endpoint = window._RAG_ENDPOINT || '/api/rag/query';

  fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf
    },
    body: JSON.stringify({ question: query })
  })
  .then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  })
  .then(data => {
    const loading = document.getElementById('rag-loading');
    if (loading) loading.remove();
    const replyMsg = document.createElement('div');
    replyMsg.style.cssText = [
      'align-self:flex-start',
      'background:var(--ink-2)',
      'border:1px solid var(--line)',
      'border-radius:12px',
      'padding:10px 14px',
      'font-size:13px',
      'color:var(--chalk)',
      'max-width:80%',
      'line-height:1.6',
      'white-space:pre-wrap',
      'word-break:break-word'
    ].join(';');
    replyMsg.textContent = data.answer || data.response || data.result || JSON.stringify(data);
    messages.appendChild(replyMsg);
    messages.scrollTop = messages.scrollHeight;
  })
  .catch(err => {
    const loading = document.getElementById('rag-loading');
    if (loading) loading.remove();
    const errMsg = document.createElement('div');
    errMsg.style.cssText = [
      'align-self:flex-start',
      'background:rgba(226,75,74,0.1)',
      'border:1px solid rgba(226,75,74,0.4)',
      'border-radius:12px',
      'padding:10px 14px',
      'font-size:13px',
      'color:#fb7185',
      'max-width:80%'
    ].join(';');
    errMsg.textContent = 'Could not get a response. Please try again.';
    messages.appendChild(errMsg);
    messages.scrollTop = messages.scrollHeight;
  })
  .finally(() => { if (btn) btn.disabled = false; });
};

// ── AI Suggestion Cards (Kanban) ─────────────────────────────────
window.loadSuggestedRequirements = function(projectId) {
  fetch('/projects/' + projectId + '/requirements/suggested')
  .then(r => r.json())
  .then(cards => {
    const section   = document.getElementById('suggested-section');
    const container = document.getElementById('suggested-cards');
    if (!section || !container) return;
    if (!cards || cards.length === 0) return;

    section.style.display = 'block';
    container.innerHTML = '';

    cards.forEach(card => {
      const div = document.createElement('div');
      div.className = 'kanban-card';
      div.style.cssText = 'width:220px; border:1px dashed rgba(255,255,255,0.1); opacity:0.9;';

      let tags = [];
      try {
        tags = typeof card.vision_tags === 'string'
          ? JSON.parse(card.vision_tags)
          : (card.vision_tags || []);
      } catch(e) { tags = []; }

      div.innerHTML =
        '<p class="kanban-card-title">' +
          (card.fused_label || 'Untitled') +
        '</p>' +
        (card.extracted_intent
          ? '<p class="kanban-card-meta" style="font-size:11px;">' +
              card.extracted_intent.substring(0, 80) + '...' +
            '</p>'
          : '') +
        '<div style="margin:8px 0; display:flex; flex-wrap:wrap; gap:4px;">' +
          tags.slice(0, 3).map(t =>
            '<span class="badge badge-muted" style="font-size:10px;">' + t + '</span>'
          ).join('') +
        '</div>' +
        '<div style="display:flex; gap:8px; margin-top:10px;">' +
          '<button class="btn btn-primary" style="flex:1; padding:6px 12px; font-size:11px;"' +
            ' onclick="acceptSuggestion(' + card.id + ', this)">Accept</button>' +
          '<button class="btn btn-ghost" style="padding:6px 12px; font-size:11px;"' +
            ' onclick="this.closest(\'.kanban-card\').remove(); checkSuggestionsEmpty()">Dismiss</button>' +
        '</div>';

      container.appendChild(div);
    });
  })
  .catch(e => console.log('No suggestions:', e));
};

window.acceptSuggestion = function(cardId, btn) {
  const projectId = window.BUILDSMART_PROJECT_ID;
  if (!projectId) return;
  btn.disabled = true;
  btn.textContent = 'Adding…';
  const csrf = document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value || '';
  fetch('/projects/' + projectId + '/requirements/accept-suggestion/' + cardId, {
    method: 'POST',
    headers: { 'X-CSRFToken': csrf }
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      const card = btn.closest('.kanban-card');
      card.style.opacity = '0.4';
      card.style.pointerEvents = 'none';
      btn.textContent = '✓ Added';
      setTimeout(() => location.reload(), 800);
    }
  });
};

window.checkSuggestionsEmpty = function() {
  const container = document.getElementById('suggested-cards');
  const section   = document.getElementById('suggested-section');
  if (container && section && container.children.length === 0) {
    section.style.display = 'none';
  }
};

// ── Requirement Edit / Delete ─────────────────────────────────────
window.editRequirement = function(id, title, description) {
  document.getElementById('edit-req-id').value    = id;
  document.getElementById('edit-req-title').value = title;
  document.getElementById('edit-req-desc').value  = description;
  const modal = document.getElementById('edit-req-modal');
  if (modal) modal.style.display = 'flex';
};

window.closeEditModal = function() {
  const modal = document.getElementById('edit-req-modal');
  if (modal) modal.style.display = 'none';
};

window.saveRequirement = function() {
  const id    = document.getElementById('edit-req-id').value;
  const title = document.getElementById('edit-req-title').value.trim();
  const desc  = document.getElementById('edit-req-desc').value;
  if (!title) { alert('Title is required'); return; }

  const csrf = document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value || '';

  fetch('/requirements/' + id, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify({ title: title, description: desc })
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      const titleEl = document.getElementById('req-title-' + id);
      if (titleEl) titleEl.textContent = data.title;
      window.closeEditModal();
    }
  });
};

window.deleteRequirement = function(id, title) {
  if (!confirm('Delete requirement "' + title + '"?\n\nThis cannot be undone.')) return;
  const csrf = document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value || '';
  fetch('/requirements/' + id, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': csrf }
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      const card = document.querySelector('[data-req-id="' + id + '"]');
      if (card) {
        card.style.transition = 'all 0.2s';
        card.style.opacity    = '0';
        card.style.transform  = 'scale(0.95)';
        setTimeout(() => card.remove(), 200);
      }
    }
  });
};

// ── Image Board — delete image
    if (confirm('Delete this image?')) {
        fetch('/projects/' + projectId + '/images/' + imgId + '/delete', {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf }
        }).then(() => window.location.reload());
    }
};
