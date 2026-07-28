/*
 * Warns before an unsaved edit is lost: closing/reloading the tab, or
 * clicking an internal link away from the page, while a form marked
 * data-unsaved-guard has changed from its initial state. Submitting the
 * form itself never prompts.
 *
 * Detection: a string-diff of the form's current FormData against a
 * baseline taken on load, PLUS a one-way "a file input changed" flag (File
 * values don't diff reliably as strings). EasyMDE forms keep their hidden
 * textarea's value live on every keystroke (see each form's own
 * `codemirror.on('change', ...)` — already required for native required-
 * field validation), so the diff picks up Markdown edits with no extra
 * wiring here.
 */
(function () {
  function serialize(form) {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (value, key) {
      if (!(value instanceof File)) params.append(key, value);
    });
    return params.toString();
  }

  function isInPageOrAjax(link) {
    var href = link.getAttribute("href") || "";
    if (!href || href === "#" || href.indexOf("javascript:") === 0) return true;
    for (var i = 0; i < link.attributes.length; i++) {
      if (link.attributes[i].name.indexOf("hx-") === 0) return true;
    }
    return false;
  }

  document.querySelectorAll("form[data-unsaved-guard]").forEach(function (form) {
    var baseline = serialize(form);
    var fileChanged = false;
    var submitting = false;

    form.addEventListener("change", function (e) {
      if (e.target && e.target.type === "file") fileChanged = true;
    });
    form.addEventListener("submit", function () {
      submitting = true;
    });

    function isDirty() {
      return !submitting && (fileChanged || serialize(form) !== baseline);
    }

    window.addEventListener("beforeunload", function (e) {
      if (!isDirty()) return;
      e.preventDefault();
      e.returnValue = "";
    });

    document.addEventListener("click", function (e) {
      if (!isDirty()) return;
      if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
      var link = e.target.closest("a[href]");
      if (!link || link.target === "_blank" || isInPageOrAjax(link)) return;
      if (!window.confirm("You have unsaved changes. Leave without saving?")) {
        e.preventDefault();
      }
    });
  });
})();
