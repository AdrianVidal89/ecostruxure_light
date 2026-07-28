/*
 * "Define Test order" modal (spec item 10 follow-up) — a titles-only
 * reorderable list (see templates/pocs/partials/test_order_modal.html).
 * Dragging is purely client-side (SortableJS, no auto-post); the explicit
 * "Save order" button posts the final id order once to test_reorder, which
 * replaces the main #phase-tests-list card list, then closes this modal via
 * a custom event (Alpine's testOrderModalOpen lives on an ancestor scope,
 * out of this plain-JS file's reach otherwise).
 */
(function () {
  function wire() {
    var list = document.getElementById("test-order-list");
    if (!list || list._sortableWired || !window.Sortable) return;
    list._sortableWired = true;
    Sortable.create(list, { handle: ".drag-handle", animation: 150 });

    var btn = document.getElementById("save-test-order-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        var url = list.getAttribute("data-reorder-url");
        var ids = Array.prototype.map.call(
          list.querySelectorAll("[data-test-id]"),
          function (el) { return el.getAttribute("data-test-id"); }
        );
        htmx.ajax("POST", url, { target: "#phase-tests-list", swap: "outerHTML", values: { test_ids: ids } })
          .then(function () { window.dispatchEvent(new CustomEvent("test-order-saved")); });
      });
    }
  }

  wire();
  document.body.addEventListener("htmx:afterSwap", wire);
})();
