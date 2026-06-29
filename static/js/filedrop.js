/*
 * filedrop.js — drag-and-drop file selection without the native file dialog.
 *
 * Some managed (Schneider) environments block/hang the OS file-open dialog that
 * a plain <input type="file"> triggers. To avoid depending on it, any element
 * marked [data-filedrop] becomes a drop zone: dropping a file assigns it to the
 * inner <input type="file"> via the DataTransfer API, so the form still submits
 * normally — no dialog involved. A "browse" control remains as a last-resort
 * fallback. For Markdown sources, the forms also offer a paste-text path.
 *
 * Idempotent and re-applied after HTMX swaps.
 */
(function () {
  function fileName(input) {
    return input && input.files && input.files.length ? input.files[0].name : "";
  }

  function wire(zone) {
    if (zone.dataset.filedropReady) return;
    zone.dataset.filedropReady = "1";

    var input = zone.querySelector('input[type="file"]');
    if (!input) return;
    var nameEl = zone.querySelector("[data-filedrop-name]");
    var browse = zone.querySelector("[data-filedrop-browse]");

    function showName() {
      if (nameEl) nameEl.textContent = fileName(input);
    }

    if (browse) {
      // Last-resort fallback: opens the native dialog (may be blocked).
      browse.addEventListener("click", function (e) {
        e.preventDefault();
        input.click();
      });
    }
    input.addEventListener("change", showName);

    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        zone.classList.add("filedrop-active");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === "drop" || e.target === zone) zone.classList.remove("filedrop-active");
      });
    });
    zone.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      try {
        var dt = new DataTransfer();
        dt.items.add(files[0]);
        input.files = dt.files; // submits like a normal selection
      } catch (err) {
        /* very old browsers: nothing we can do, leave the input as-is */
      }
      showName();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    showName();
  }

  function init(root) {
    (root || document).querySelectorAll("[data-filedrop]").forEach(wire);
  }

  document.addEventListener("DOMContentLoaded", function () {
    init(document);
  });
  if (document.body) {
    document.body.addEventListener("htmx:afterSwap", function (e) {
      init(e.target);
    });
  }
  window.initFileDrops = init;
})();
