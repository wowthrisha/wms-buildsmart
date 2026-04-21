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

window.openBuildSmartModal = function(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.add('open');
    modal.style.display = '';
};

window.closeBuildSmartModal = function(id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.remove('open');
    if (!modal.classList.contains('mt-modal')) {
        modal.style.display = 'none';
    }
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
function appendRagMessage(messages, variant, text, id) {
  const node = document.createElement('div');
  node.className = 'assistant-message assistant-message-' + variant;
  if (id) node.id = id;
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

window.sendRagQuery = function(event) {
  if (event && typeof event.preventDefault === 'function') {
    event.preventDefault();
  }
  const input    = document.getElementById('rag-input');
  const messages = document.getElementById('rag-messages');
  const btn      = document.getElementById('rag-send-btn');
  if (!input || !messages) return;
  const query = input.value.trim();
  if (!query) {
    if (typeof window.showFlash === 'function') {
      window.showFlash('Please enter a question.', 'info');
    }
    return;
  }

  const queryText = query;
  appendRagMessage(messages, 'user', query);
  appendRagMessage(messages, 'system', 'Thinking…', 'rag-loading');

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
      'X-CSRFToken': csrf,
      'X-CSRF-Token': csrf,
      'X-Requested-With': 'XMLHttpRequest'
    },
    body: JSON.stringify({ question: query }),
    // ensure cookies/session are sent for CSRF validation
    credentials: 'same-origin'
  })
  .then(async r => {
    let data = {};
    try {
      data = await r.json();
    } catch (e) {
      data = {};
    }
    if (!r.ok) {
      throw { status: r.status, data: data };
    }
    return data;
  })
  .then(data => {
    const loading = document.getElementById('rag-loading');
    if (loading) loading.remove();
    appendRagMessage(
      messages,
      'assistant',
      data.answer || data.response || data.result || JSON.stringify(data)
    );
  })
  .catch(err => {
    const loading = document.getElementById('rag-loading');
    if (loading) loading.remove();
    const message = (err && err.data && (err.data.answer || err.data.error))
      || 'Could not get a response. Please try again.';
    if (input && !input.value) {
      input.value = queryText;
    }
    appendRagMessage(messages, 'error', message);
    if (typeof window.showFlash === 'function') {
      window.showFlash(message, 'error');
    }
  })
  .finally(() => { if (btn) btn.disabled = false; });

  return false;
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

window.getRequirementApiBase = function(requirementId) {
  const projectId = window.BUILDSMART_PROJECT_ID;
  if (projectId) {
    return '/projects/' + projectId + '/requirements/' + requirementId;
  }
  return '/requirements/' + requirementId;
};

window.handleRequirementError = function(message) {
  const text = message || 'Unable to update requirement right now.';
  if (typeof window.showFlash === 'function') {
    window.showFlash(text, 'error');
    return;
  }
  alert(text);
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
  window.openBuildSmartModal('edit-req-modal');
};

window.closeEditModal = function() {
  window.closeBuildSmartModal('edit-req-modal');
};

window.saveRequirement = function() {
  const id    = document.getElementById('edit-req-id').value;
  const title = document.getElementById('edit-req-title').value.trim();
  const desc  = document.getElementById('edit-req-desc').value;
  if (!title) {
    window.handleRequirementError('Title is required.');
    return;
  }

  const csrf = document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value || '';

  fetch(window.getRequirementApiBase(id), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify({ title: title, description: desc })
  })
  .then(r => r.json().then(data => ({ ok: r.ok, data: data })))
  .then(data => {
    if (data.ok && data.data.success) {
      const titleEl = document.getElementById('req-title-' + id);
      const descEl = document.getElementById('req-desc-' + id);
      if (titleEl) titleEl.textContent = data.data.title;
      if (descEl) {
        descEl.textContent = desc.length > 160 ? desc.substring(0, 160) + '...' : desc;
      }
      window.closeEditModal();
      if (typeof window.showFlash === 'function') {
        window.showFlash('Requirement updated.', 'success');
      }
      return;
    }
    window.handleRequirementError(data.data && data.data.error);
  });
};

window.deleteRequirement = function(id, title) {
  if (!confirm('Delete requirement "' + title + '"?\n\nThis cannot be undone.')) return;
  const csrf = document.querySelector('meta[name=csrf-token]')?.content
    || document.querySelector('input[name=csrf_token]')?.value || '';
  fetch(window.getRequirementApiBase(id) + '/delete', {
    method: 'DELETE',
    headers: { 'X-CSRFToken': csrf }
  })
  .then(r => r.json().then(data => ({ ok: r.ok, data: data })))
  .then(data => {
    if (data.ok && data.data.success) {
      const card = document.querySelector('[data-req-id="' + id + '"]');
      if (card) {
        const column = card.closest('.kanban-dropzone');
        const countEl = column
          ? document.getElementById('count-' + column.id.replace('drop-', ''))
          : null;
        card.style.transition = 'all 0.2s';
        card.style.opacity    = '0';
        card.style.transform  = 'scale(0.95)';
        setTimeout(() => {
          card.remove();
          if (countEl && column) {
            countEl.textContent = column.querySelectorAll('.kanban-card').length;
          }
        }, 200);
      }
      return;
    }
    window.handleRequirementError(data.data && data.data.error);
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
