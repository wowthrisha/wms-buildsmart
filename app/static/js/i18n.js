/**
 * BuildSmart — Tamil ↔ English Language Toggle
 * Uses data-i18n attributes on elements to swap text content.
 * Language preference is persisted in localStorage.
 */

const TRANSLATIONS = {
  ta: {
    // ── Navigation (Architect sidebar) ──────────────────────────
    "nav.workspace":        "பணியிடம்",
    "nav.projects":         "திட்டங்கள்",
    "nav.plot_analysis":    "மனை பகுப்பாய்வு",
    "nav.documents":        "ஆவணங்கள்",
    "nav.compliance":       "இணக்கம்",
    "nav.meetings":         "கூட்டங்கள்",
    "nav.activity":         "செயல்பாடு",
    "nav.settings":         "அமைப்புகள்",
    "nav.architect":        "கட்டிட வல்லுனர்",

    // ── Navigation (Client sidebar) ─────────────────────────────
    "nav.my_project":       "என் திட்டம்",
    "nav.dashboard":        "டாஷ்போர்டு",
    "nav.current_project":  "தற்போதைய திட்டம்",
    "nav.requirements":     "தேவைகள்",
    "nav.assistant":        "உதவியாளர்",
    "nav.updates":          "புதுப்பிப்புகள்",
    "nav.payments":         "கட்டணங்கள்",
    "nav.client_portal":    "வாடிக்கையாளர் போர்டல்",

    // ── Topbar ──────────────────────────────────────────────────
    "topbar.new_project":   "புதிய திட்டம்",
    "topbar.logout":        "வெளியேறு",
    "topbar.notifications": "அறிவிப்புகள்",
    "topbar.no_notifs":     "புதிய அறிவிப்புகள் இல்லை",
    "topbar.switch_project":"திட்டம் மாற்றவும்",
    "topbar.projects_bc":   "திட்டங்கள்",

    // ── Project tab nav ─────────────────────────────────────────
    "tab.overview":         "மேலோட்டம்",
    "tab.plot":             "மனை பகுப்பாய்வு",
    "tab.documents":        "ஆவணங்கள்",
    "tab.compliance":       "இணக்கம்",
    "tab.references":       "குறிப்புகள்",
    "tab.meetings":         "கூட்டங்கள்",
    "tab.requirements":     "தேவைகள்",
    "tab.assistant":        "உதவியாளர்",
    "tab.payments":         "கட்டணங்கள்",
    "tab.activity":         "செயல்பாடு",

    // ── New Project Modal ────────────────────────────────────────
    "modal.new_entry":      "புதிய பதிவு",
    "modal.create_project": "திட்டம் உருவாக்கு",
    "modal.project_name":   "திட்டத்தின் பெயர்",
    "modal.client":         "வாடிக்கையாளர்",
    "modal.select_client":  "வாடிக்கையாளரை தேர்ந்தெடுக்கவும்…",
    "modal.plot_zone":      "மனை வகை",
    "modal.residential":    "குடியிருப்பு",
    "modal.commercial":     "வணிக",
    "modal.industrial":     "தொழில்துறை",
    "modal.mixed":          "கலப்பு பயன்பாடு",
    "modal.cancel":         "ரத்து செய்",
    "modal.create":         "உருவாக்கு",

    // ── Auth pages ───────────────────────────────────────────────
    "auth.login":           "உள்நுழைவு",
    "auth.register":        "பதிவு செய்யுங்கள்",
    "auth.logout":          "வெளியேறு",
    "auth.email":           "மின்னஞ்சல்",
    "auth.password":        "கடவுச்சொல்",
    "auth.forgot_password": "கடவுச்சொல் மறந்தீர்களா?",
    "auth.remember_me":     "என்னை நினைவில் கொள்",
    "auth.no_account":      "கணக்கு இல்லையா?",
    "auth.have_account":    "ஏற்கெனவே கணக்கு இருக்கிறதா?",
    "auth.sign_in":         "உள்நுழைக",
    "auth.sign_up":         "பதிவு செய்க",
    "auth.name":            "பெயர்",
    "auth.role":            "பங்கு",
    "auth.architect_role":  "கட்டிட வல்லுனர்",
    "auth.client_role":     "வாடிக்கையாளர்",
    "auth.send_reset":      "மீட்டமைப்பு கோரிக்கை அனுப்பு",
    "auth.reset_password":  "கடவுச்சொல் மீட்டமை",
    "auth.new_password":    "புதிய கடவுச்சொல்",
    "auth.confirm_password":"கடவுச்சொல் உறுதிப்படுத்தவும்",
    "auth.back_to_login":   "உள்நுழைவு பக்கத்திற்கு திரும்பு",
    "auth.submit":          "சமர்ப்பி",

    // ── Dashboard ────────────────────────────────────────────────
    "dash.good_morning":    "காலை வணக்கம்",
    "dash.good_afternoon":  "மதிய வணக்கம்",
    "dash.good_evening":    "மாலை வணக்கம்",
    "dash.active_projects": "செயலில் உள்ள திட்டங்கள்",
    "dash.pending":         "நிலுவையில் உள்ளவை",
    "dash.completed":       "நிறைவு செய்யப்பட்டவை",
    "dash.recent_activity": "சமீபத்திய செயல்பாடு",
    "dash.view_all":        "அனைத்தையும் பார்",
    "dash.no_activity":     "செயல்பாடு எதுவும் இல்லை",
    "dash.create_first":    "முதல் திட்டத்தை உருவாக்கவும்",
    "dash.no_projects":     "திட்டங்கள் எதுவும் இல்லை",

    // ── Common UI ────────────────────────────────────────────────
    "common.save":          "சேமி",
    "common.cancel":        "ரத்து",
    "common.delete":        "நீக்கு",
    "common.edit":          "திருத்து",
    "common.upload":        "பதிவேற்று",
    "common.download":      "பதிவிறக்கு",
    "common.submit":        "சமர்ப்பி",
    "common.confirm":       "உறுதிப்படுத்து",
    "common.close":         "மூடு",
    "common.back":          "பின்செல்",
    "common.next":          "அடுத்து",
    "common.search":        "தேடு",
    "common.filter":        "வடிகட்டு",
    "common.status":        "நிலை",
    "common.actions":       "செயல்கள்",
    "common.date":          "தேதி",
    "common.name":          "பெயர்",
    "common.description":   "விளக்கம்",
    "common.type":          "வகை",
    "common.amount":        "தொகை",
    "common.notes":         "குறிப்புகள்",
    "common.add":           "சேர்",
    "common.view":          "பார்",
    "common.loading":       "ஏற்றுகிறது…",
    "common.no_records":    "பதிவுகள் இல்லை",
    "common.yes":           "ஆம்",
    "common.no":            "இல்லை",

    // ── Projects list ────────────────────────────────────────────
    "proj.title":           "திட்டங்கள்",
    "proj.new":             "புதிய திட்டம்",
    "proj.status":          "நிலை",
    "proj.client":          "வாடிக்கையாளர்",
    "proj.updated":         "புதுப்பிக்கப்பட்டது",
    "proj.design":          "வடிவமைப்பு",
    "proj.construction":    "கட்டுமானம்",
    "proj.completed":       "நிறைவடைந்தது",
    "proj.on_hold":         "நிறுத்தப்பட்டது",
    "proj.no_client":       "வாடிக்கையாளர் இல்லை",

    // ── Documents ────────────────────────────────────────────────
    "doc.upload":           "ஆவணம் பதிவேற்று",
    "doc.type":             "ஆவண வகை",
    "doc.uploaded_by":      "பதிவேற்றியவர்",
    "doc.uploaded_on":      "பதிவேற்றிய தேதி",
    "doc.visible_client":   "வாடிக்கையாளருக்கு தெரியும்",
    "doc.no_docs":          "ஆவணங்கள் இல்லை",
    "doc.select_file":      "கோப்பை தேர்ந்தெடுக்கவும்",

    // ── Compliance ───────────────────────────────────────────────
    "comp.title":           "இணக்க ஆய்வு",
    "comp.status":          "நிலை",
    "comp.pending":         "நிலுவையில்",
    "comp.approved":        "அனுமதிக்கப்பட்டது",
    "comp.rejected":        "நிராகரிக்கப்பட்டது",
    "comp.missing":         "கிடைக்கவில்லை",
    "comp.add_item":        "உருப்படி சேர்",

    // ── Meetings ─────────────────────────────────────────────────
    "meet.title":           "கூட்டங்கள்",
    "meet.schedule":        "கூட்டம் திட்டமிடு",
    "meet.date":            "தேதி",
    "meet.time":            "நேரம்",
    "meet.agenda":          "நிகழ்ச்சி நிரல்",
    "meet.status":          "நிலை",
    "meet.upcoming":        "வரவிருக்கும்",
    "meet.completed":       "நிறைவடைந்தது",
    "meet.no_meetings":     "கூட்டங்கள் இல்லை",
    "meet.propose":         "கூட்ட நேரம் முன்மொழிக",
    "meet.confirm":         "கூட்டம் உறுதிப்படுத்து",

    // ── Payments ─────────────────────────────────────────────────
    "pay.title":            "கட்டணங்கள்",
    "pay.add":              "கட்டணம் சேர்",
    "pay.amount":           "தொகை",
    "pay.date":             "தேதி",
    "pay.description":      "விளக்கம்",
    "pay.status":           "நிலை",
    "pay.paid":             "செலுத்தப்பட்டது",
    "pay.pending":          "நிலுவையில்",
    "pay.total":            "மொத்தம்",
    "pay.no_payments":      "கட்டண பதிவுகள் இல்லை",

    // ── Activity ─────────────────────────────────────────────────
    "act.title":            "செயல்பாடு பதிவு",
    "act.no_activity":      "செயல்பாடு எதுவும் இல்லை",

    // ── Plot Analysis ────────────────────────────────────────────
    "plot.title":           "மனை பகுப்பாய்வு",
    "plot.upload":          "மனை படம் பதிவேற்று",
    "plot.analyze":         "பகுப்பாய்வு செய்",
    "plot.area":            "மனை பரப்பு",
    "plot.zone":            "வகை",
    "plot.results":         "முடிவுகள்",
    "plot.no_analysis":     "பகுப்பாய்வு இல்லை",

    // ── Requirements / Kanban ────────────────────────────────────
    "req.title":            "தேவைகள்",
    "req.add":              "தேவை சேர்",
    "req.pending":          "நிலுவையில்",
    "req.in_progress":      "செயல்முறையில்",
    "req.done":             "முடிந்தது",
    "req.priority":         "முன்னுரிமை",
    "req.high":             "அதிக",
    "req.medium":           "நடுத்தர",
    "req.low":              "குறைந்த",

    // ── Settings ─────────────────────────────────────────────────
    "set.title":            "அமைப்புகள்",
    "set.profile":          "சுயவிவரம்",
    "set.name":             "பெயர்",
    "set.email":            "மின்னஞ்சல்",
    "set.phone":            "தொலைபேசி",
    "set.profession":       "தொழில்",
    "set.save":             "சேமி",
    "set.change_password":  "கடவுச்சொல் மாற்று",
    "set.current_password": "தற்போதைய கடவுச்சொல்",
    "set.new_password":     "புதிய கடவுச்சொல்",

    // ── Assistant ────────────────────────────────────────────────
    "asst.title":           "AI உதவியாளர்",
    "asst.placeholder":     "உங்கள் கேள்வியை தமிழில் அல்லது ஆங்கிலத்தில் கேளுங்கள்…",
    "asst.send":            "அனுப்பு",
    "asst.thinking":        "யோசிக்கிறது…",

    // ── Client portal specific ───────────────────────────────────
    "portal.welcome":       "வரவேற்கிறோம்",
    "portal.your_project":  "உங்கள் திட்டம்",
    "portal.architect":     "கட்டிட வல்லுனர்",
    "portal.status":        "திட்ட நிலை",
    "portal.no_project":    "திட்டம் ஒதுக்கப்படவில்லை",

    // ── Language toggle ──────────────────────────────────────────
    "lang.toggle_en":       "English",
    "lang.toggle_ta":       "தமிழ்",

    // ── Dev banner ───────────────────────────────────────────────
    "dev.mode":             "டெவலப்பர் பயன்முறை",
    "dev.clear":            "அழி",
  }
};

// ─── Core engine ────────────────────────────────────────────────────────────

const I18N = (() => {
  const STORAGE_KEY = 'bs_lang';
  let currentLang = localStorage.getItem(STORAGE_KEY) || 'en';

  function t(key) {
    if (currentLang === 'en') return null;          // keep original
    return TRANSLATIONS.ta[key] || null;
  }

  function applyLang(lang) {
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);

    // Update <html> lang attribute
    document.documentElement.lang = lang === 'ta' ? 'ta' : 'en';

    // Walk all elements with data-i18n
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const translated = TRANSLATIONS.ta[key];
      if (!translated) return;

      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        // Handle placeholders
        const origPh = el.getAttribute('data-i18n-orig-placeholder') || el.placeholder;
        if (!el.getAttribute('data-i18n-orig-placeholder') && origPh) {
          el.setAttribute('data-i18n-orig-placeholder', origPh);
        }
        el.placeholder = lang === 'ta' ? translated : (el.getAttribute('data-i18n-orig-placeholder') || el.placeholder);
      } else {
        // Store original text on first run
        if (!el.hasAttribute('data-i18n-orig')) {
          // Only store text, preserve child elements (icons, badges, etc.)
          el.setAttribute('data-i18n-orig', el.innerText.trim());
        }
        if (lang === 'ta') {
          // Replace only text nodes, keep child elements intact
          replaceTextNodes(el, translated);
        } else {
          // Restore original — we need to handle mixed content carefully
          restoreTextNodes(el);
        }
      }
    });

    // Update toggle button label
    document.querySelectorAll('.lang-toggle-btn').forEach(btn => {
      btn.textContent = lang === 'ta' ? '🌐 English' : '🌐 தமிழ்';
      btn.title = lang === 'ta' ? 'Switch to English' : 'தமிழில் மாற்று';
    });
  }

  function replaceTextNodes(el, newText) {
    // Find first direct text node and replace it; preserve child elements (SVGs, badges)
    let replaced = false;
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
        if (!node._origText) node._origText = node.textContent;
        node.textContent = (replaced ? '' : (' ' + newText + ' ').replace(/\s+/, ' '));
        replaced = true;
      }
    }
    if (!replaced) {
      // fallback: no text node found, just swap innerText of element itself
      if (!el._origHTML) el._origHTML = el.innerHTML;
      el.innerHTML = newText;
    }
  }

  function restoreTextNodes(el) {
    if (el._origHTML !== undefined) {
      el.innerHTML = el._origHTML;
      return;
    }
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node._origText !== undefined) {
        node.textContent = node._origText;
      }
    }
  }

  function toggle() {
    applyLang(currentLang === 'en' ? 'ta' : 'en');
  }

  function init() {
    applyLang(currentLang);
  }

  return { init, toggle, applyLang, currentLang: () => currentLang };
})();

// ─── Init on DOM ready ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  I18N.init();
  document.querySelectorAll('.lang-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => I18N.toggle());
  });
});
