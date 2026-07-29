/*
 * UC × Requirement matrix (improvement brief item 3): click-to-toggle cells,
 * row/column hover highlight, free-text (rows) + sub-system (columns)
 * filters, and delayed hover-cards on the row/column headers (shared
 * static/js/hover_card.js — same component as the POC graph, spec item 7).
 */
(function () {
  function csrfToken() {
    var meta = document.querySelector('meta[name=csrf-token]');
    return meta ? meta.content : '';
  }

  function wireToggle(table) {
    table.addEventListener('click', function (e) {
      var cell = e.target.closest('[data-toggle-url]');
      if (!cell) return;
      var dot = cell.querySelector('[data-matrix-dot]');
      fetch(cell.dataset.toggleUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken(),
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
          usecase_id: cell.dataset.usecaseId,
          requirement_id: cell.dataset.requirementId,
        }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!dot) return;
          if (data.linked) {
            dot.setAttribute('data-lucide', 'check');
            dot.setAttribute('class', 'w-3.5 h-3.5 mx-auto text-brand-dark');
          } else {
            dot.setAttribute('data-lucide', 'circle');
            dot.setAttribute('class', 'w-2 h-2 mx-auto text-line');
          }
          if (window.lucide) window.lucide.createIcons();
        });
    });
  }

  function wireHoverHighlight(table) {
    function toggle(e, on) {
      var cell = e.target.closest('[data-req-id]');
      if (!cell) return;
      table.querySelectorAll('[data-req-id="' + cell.dataset.reqId + '"]').forEach(function (el) {
        el.classList.toggle('matrix-hl', on);
      });
      var row = cell.closest('tr');
      if (row) row.classList.toggle('matrix-hl', on);
    }
    table.addEventListener('mouseover', function (e) { toggle(e, true); });
    table.addEventListener('mouseout', function (e) { toggle(e, false); });
  }

  function wireHoverCards(root) {
    if (!window.HoverCard) return;
    root.querySelectorAll('[data-hover-card]').forEach(function (el) {
      window.HoverCard.attach(el, function () {
        return { title: el.dataset.hcTitle, description: el.dataset.hcDescription };
      });
    });
  }

  function wireFilters(root) {
    var search = root.querySelector('[data-matrix-search]');
    var subsystem = root.querySelector('[data-matrix-subsystem]');
    if (!search && !subsystem) return;

    function apply() {
      var q = (search && search.value || '').trim().toLowerCase();
      root.querySelectorAll('[data-uc-row]').forEach(function (tr) {
        var text = (tr.dataset.ucTitle + ' ' + tr.dataset.ucCode).toLowerCase();
        tr.style.display = !q || text.indexOf(q) !== -1 ? '' : 'none';
      });
      var sub = subsystem && subsystem.value || '';
      root.querySelectorAll('[data-req-col]').forEach(function (th) {
        var show = !sub || th.dataset.subSystem === sub;
        th.style.display = show ? '' : 'none';
        root.querySelectorAll('td[data-req-id="' + th.dataset.reqId + '"]').forEach(function (td) {
          td.style.display = show ? '' : 'none';
        });
      });
    }
    if (search) search.addEventListener('input', apply);
    if (subsystem) subsystem.addEventListener('change', apply);
  }

  function init(root) {
    var table = root.querySelector('[data-matrix-table]');
    if (table && !table.dataset.matrixReady) {
      table.dataset.matrixReady = '1';
      wireToggle(table);
      wireHoverHighlight(table);
    }
    wireHoverCards(root);
    wireFilters(root);
  }

  window.initUCRequirementMatrix = init;
})();
