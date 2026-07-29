/* Single shared read-only preview popup, reused by every link/chip that
 * references a Use Case, Requirement, Test or FA/Documentation section
 * anywhere in the app (specs tab, requirement/use case cross-reference
 * chips, the Overview map, phase sections, test rows, link pickers...).
 * The modal shell itself lives once in base.html (#entity-preview-modal);
 * this file only drives it, so there is exactly one implementation to keep
 * working instead of N copy-pasted ones.
 *
 * Navigation stack: opening a preview from INSIDE another preview (e.g. a
 * requirement chip clicked from a test's preview, or a Map node) pushes
 * onto ``stack`` instead of replacing it, so "Back" can restore the exact
 * previous context instead of forcing the user to reopen it from scratch
 * (predictability — no forced linear path, see spec item 1). */
(function () {
  var stack = []; // [{previewUrl, detailUrl, label}, ...]

  var CLOSE_MS = 200; // matches the duration-200 transition on #entity-preview-modal / -panel

  function modal() { return document.getElementById('entity-preview-modal'); }
  function panel() { return document.getElementById('entity-preview-panel'); }
  function body() { return document.getElementById('entity-preview-body'); }
  function goLink() { return document.getElementById('entity-preview-go'); }
  function backBtn() { return document.getElementById('entity-preview-back'); }
  function trail() { return document.getElementById('entity-preview-trail'); }

  // Fade + scale open/close transition. classList add/remove (not toggle)
  // so it's idempotent whether the modal is already open (navigating within
  // the stack) or was closed.
  function setOpenState(open) {
    var m = modal(), p = panel();
    if (!m || !p) return;
    m.classList.toggle('opacity-0', !open);
    m.classList.toggle('opacity-100', open);
    p.classList.toggle('opacity-0', !open);
    p.classList.toggle('scale-95', !open);
    p.classList.toggle('opacity-100', open);
    p.classList.toggle('scale-100', open);
  }

  function renderChrome() {
    var entry = stack[stack.length - 1];

    var link = goLink();
    if (entry.detailUrl) {
      link.href = entry.detailUrl;
      link.querySelector('[data-go-label]').textContent = entry.label || 'Go to item';
      link.classList.remove('hidden');
    } else {
      link.classList.add('hidden');
    }

    var back = backBtn();
    back.classList.toggle('hidden', stack.length < 2);

    var t = trail();
    t.innerHTML = '';
    stack.forEach(function (e, i) {
      if (i > 0) {
        var sep = document.createElement('i');
        sep.setAttribute('data-lucide', 'chevron-right');
        sep.className = 'w-3.5 h-3.5 shrink-0';
        t.appendChild(sep);
      }
      var span = document.createElement('span');
      span.textContent = e.label || 'Item';
      span.className = (i === stack.length - 1)
        ? 'text-ink font-medium truncate max-w-[10rem]'
        : 'text-ink-muted truncate max-w-[8rem]';
      t.appendChild(span);
    });
    if (window.lucide) window.lucide.createIcons();
  }

  function load() {
    var entry = stack[stack.length - 1];
    var b = body();
    b.innerHTML = '<div class="p-10 text-center text-ink-muted text-sm">Loading…</div>';
    renderChrome();
    var m = modal();
    var wasHidden = m.classList.contains('hidden');
    m.classList.remove('hidden');
    if (wasHidden) {
      // Force a reflow so the browser paints the closed state at least once
      // before the class flip below, otherwise it jumps straight to open
      // instead of animating from it.
      void m.offsetWidth;
      requestAnimationFrame(function () { setOpenState(true); });
    }
    window.htmx.ajax('GET', entry.previewUrl, { target: '#entity-preview-body', swap: 'innerHTML' });
  }

  window.openEntityPreview = function (previewUrl, detailUrl, label) {
    if (!modal()) return;
    stack.push({ previewUrl: previewUrl, detailUrl: detailUrl, label: label });
    load();
  };

  window.goBackEntityPreview = function () {
    if (stack.length < 2) return;
    stack.pop();
    load();
  };

  window.closeEntityPreview = function () {
    var m = modal();
    if (m && !m.classList.contains('hidden')) {
      setOpenState(false);
      window.setTimeout(function () { m.classList.add('hidden'); }, CLOSE_MS);
    }
    stack = [];
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') window.closeEntityPreview();
  });
})();
