/*
 * Drag-and-drop reordering for a phase's tests (spec item 10). Auto-wires
 * any [data-sortable] container (templates/pocs/partials/phase_tests_list.html)
 * via the vendored SortableJS — dragging a card by its .drag-handle posts the
 * new id order to data-reorder-url, which reassigns each test's execution
 * order AND renumbers its test_code to match. The response replaces the
 * whole list, so re-wiring runs again after every HTMX swap (same pattern as
 * the Lucide icon re-render in base.html).
 */
(function () {
  function wire() {
    if (!window.Sortable) return;
    document.querySelectorAll("[data-sortable]").forEach(function (el) {
      if (el._sortableWired) return;
      el._sortableWired = true;
      Sortable.create(el, {
        handle: ".drag-handle",
        animation: 150,
        onEnd: function () {
          var url = el.getAttribute("data-reorder-url");
          var ids = Array.prototype.map.call(
            el.querySelectorAll("[data-test-id]"),
            function (row) { return row.getAttribute("data-test-id"); }
          );
          htmx.ajax("POST", url, {
            target: "#" + el.id,
            swap: "outerHTML",
            values: { test_ids: ids },
          });
        },
      });
    });
  }

  wire();
  document.body.addEventListener("htmx:afterSwap", wire);
})();
