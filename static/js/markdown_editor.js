/* Mounts EasyMDE on every Markdown textarea (.md-editor) under ``root``.
 * Shared by the full-page spec form (spec_form.html, mounted on
 * DOMContentLoaded) and the in-modal edit form (spec_form_fields.html,
 * swapped into #entity-preview-body via htmx — mounted on htmx:afterSettle
 * instead, see entity_preview.js) so there's one place owning this. Guards
 * against double-mounting the same textarea with a data attribute, since a
 * modal "Edit" -> "Cancel"(back) -> "Edit" cycle can re-swap the same DOM. */
window.mountMarkdownEditors = function (root) {
  if (!window.EasyMDE) return;
  (root || document).querySelectorAll('textarea.md-editor:not([data-mde-mounted])').forEach(function (el) {
    el.setAttribute('data-mde-mounted', 'true');
    var mde = new EasyMDE({ element: el, spellChecker: false, status: false, minHeight: '120px',
      toolbar: ['bold','italic','heading','|','unordered-list','ordered-list','link','code','|','preview','guide'] });
    // CodeMirror never writes back to the original (now-hidden) textarea on
    // its own — keep it live on every keystroke instead of only at submit,
    // since a required field's native validation runs before 'submit' fires.
    mde.codemirror.on('change', function () { mde.codemirror.save(); });
  });
};
