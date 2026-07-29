/* Mounts EasyMDE on every Markdown textarea (.md-editor) under ``root``.
 * Self-wiring (loaded on every page, like entity_hover_preview.js): mounts
 * on DOMContentLoaded (spec_form.html's own full-page form) AND after the
 * entity-preview modal swaps in an edit form via htmx (spec_form_fields.html
 * has no page of its own to hang a DOMContentLoaded listener off).
 *
 * The EasyMDE vendor CSS/JS themselves are NOT loaded up front here — most
 * pages never show a Markdown field, so eagerly loading them on every
 * navigation was pure dead weight (and a visible load "jump"). They're
 * fetched once, lazily, the first time mountMarkdownEditors() actually finds
 * a textarea to mount, using the URLs base.html exposes as
 * window.EASYMDE_CSS_URL / window.EASYMDE_JS_URL ({% static %} can't be
 * resolved from a plain .js file).
 *
 * ``data-mde-mounted`` is the opt-out contract, not just internal bookkeeping:
 * a page that mounts its own EasyMDE with a bespoke toolbar (poc_form,
 * task_form, phase_document_form) sets it on the textarea while parsing, and
 * this mounter then leaves that field alone. Without it both run and EasyMDE
 * builds a second CodeMirror on the same textarea — two stacked, mirrored
 * editors per field, which is exactly the bug this note exists to prevent. */
(function () {
  var assetsPromise = null;

  function loadAssets() {
    if (window.EasyMDE) return Promise.resolve();
    if (!assetsPromise) {
      assetsPromise = new Promise(function (resolve, reject) {
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = window.EASYMDE_CSS_URL;
        document.head.appendChild(link);

        var script = document.createElement('script');
        script.src = window.EASYMDE_JS_URL;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
      });
    }
    return assetsPromise;
  }

  function mount(textareas) {
    textareas.forEach(function (el) {
      if (el.hasAttribute('data-mde-mounted')) return; // guard re-entry while assets were loading
      el.setAttribute('data-mde-mounted', 'true');
      var mde = new EasyMDE({ element: el, spellChecker: false, status: false, minHeight: '120px',
        toolbar: ['bold','italic','heading','|','unordered-list','ordered-list','link','code','|','preview','guide'] });
      // CodeMirror never writes back to the original (now-hidden) textarea on
      // its own — keep it live on every keystroke instead of only at submit,
      // since a required field's native validation runs before 'submit' fires.
      mde.codemirror.on('change', function () { mde.codemirror.save(); });
    });
  }

  window.mountMarkdownEditors = function (root) {
    var textareas = (root || document).querySelectorAll('textarea.md-editor:not([data-mde-mounted])');
    if (!textareas.length) return;
    loadAssets().then(function () { mount(textareas); });
  };

  document.addEventListener('DOMContentLoaded', function () { window.mountMarkdownEditors(document); });
  document.body.addEventListener('htmx:afterSettle', function (evt) {
    if (evt.target && evt.target.id === 'entity-preview-body') window.mountMarkdownEditors(evt.target);
  });
})();
