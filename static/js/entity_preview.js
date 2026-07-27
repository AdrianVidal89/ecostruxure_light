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

  function modal() { return document.getElementById('entity-preview-modal'); }
  function body() { return document.getElementById('entity-preview-body'); }
  function goLink() { return document.getElementById('entity-preview-go'); }
  function backBtn() { return document.getElementById('entity-preview-back'); }
  function trail() { return document.getElementById('entity-preview-trail'); }

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
    modal().classList.remove('hidden');
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
    if (m) m.classList.add('hidden');
    stack = [];
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') window.closeEntityPreview();
  });
})();
