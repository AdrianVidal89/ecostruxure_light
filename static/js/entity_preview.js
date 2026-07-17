/* Single shared read-only preview popup, reused by every link/chip that
 * references a Use Case, Requirement, Test or FA/Documentation section
 * anywhere in the app (specs tab, requirement/use case cross-reference
 * chips, the Overview map, phase sections, test rows, link pickers...).
 * The modal shell itself lives once in base.html (#entity-preview-modal);
 * this file only drives it, so there is exactly one implementation to keep
 * working instead of N copy-pasted ones. */
(function () {
  function modal() { return document.getElementById('entity-preview-modal'); }
  function body() { return document.getElementById('entity-preview-body'); }
  function goLink() { return document.getElementById('entity-preview-go'); }

  window.openEntityPreview = function (previewUrl, detailUrl, label) {
    var m = modal();
    if (!m) return;
    var b = body();
    b.innerHTML = '<div class="p-10 text-center text-ink-muted text-sm">Loading…</div>';
    var link = goLink();
    if (detailUrl) {
      link.href = detailUrl;
      link.querySelector('[data-go-label]').textContent = label || 'Go to item';
      link.classList.remove('hidden');
    } else {
      link.classList.add('hidden');
    }
    m.classList.remove('hidden');
    window.htmx.ajax('GET', previewUrl, { target: '#entity-preview-body', swap: 'innerHTML' });
  };

  window.closeEntityPreview = function () {
    var m = modal();
    if (m) m.classList.add('hidden');
  };

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') window.closeEntityPreview();
  });
})();
