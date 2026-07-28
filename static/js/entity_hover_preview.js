/*
 * Delayed hover-card on EVERY chip/link that opens an entity preview — reuses
 * the shared HoverCard component (static/js/hover_card.js, same one used by
 * the POC graph and the UC×Requirement matrix) so hovering a Requirement or
 * Use Case link anywhere in the app (linked-requirement chips, use-case
 * cards, task/test cross-references, checkbox lists, …) shows its full
 * description without waiting for a click.
 *
 * No per-template wiring needed: any element whose `onclick`/`@click`/
 * `@click.prevent` attribute calls `openEntityPreview('<previewUrl>', ...)`
 * is found via event delegation on `document`, and `<previewUrl>?brief=1`
 * (the same read-only preview endpoint, a fast JSON path — see
 * requirement_preview/usecase_preview/test_preview in apps/pocs/views.py)
 * supplies the {title, description} shown in the card. Results are cached
 * per URL for the page's lifetime.
 */
(function () {
  var PREVIEW_RE = /openEntityPreview\('([^']+)'/;
  var cache = {};

  function briefUrl(previewUrl) {
    return previewUrl + (previewUrl.indexOf('?') === -1 ? '?' : '&') + 'brief=1';
  }

  function fetchInfo(previewUrl, cb) {
    if (Object.prototype.hasOwnProperty.call(cache, previewUrl)) {
      cb(cache[previewUrl]);
      return;
    }
    fetch(briefUrl(previewUrl))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        cache[previewUrl] = data;
        cb(data);
      })
      .catch(function () { cb(null); });
  }

  // No CSS attribute-selector escaping headaches (Alpine's "@click.prevent"
  // has a literal dot in the attribute name) — just walk up reading raw
  // attributes directly.
  function findTrigger(el) {
    while (el && el.nodeType === 1) {
      var raw = (el.getAttribute('onclick') || "")
        + (el.getAttribute('@click.prevent') || "")
        + (el.getAttribute('@click') || "");
      if (raw.indexOf('openEntityPreview(') !== -1) return el;
      el = el.parentElement;
    }
    return null;
  }

  document.addEventListener('mouseover', function (e) {
    if (!window.HoverCard) return;
    var el = findTrigger(e.target);
    if (!el || el._hcArmed) return;
    var m = PREVIEW_RE.exec(
      (el.getAttribute('onclick') || "") + (el.getAttribute('@click.prevent') || "") + (el.getAttribute('@click') || "")
    );
    if (!m) return;
    el._hcArmed = true;
    var x = e.clientX;
    var y = e.clientY;
    fetchInfo(m[1], function (data) {
      // Bail if the pointer already left this element — mouseout below
      // clears the flag, so a late response never pops up a stale card.
      if (!data || !el._hcArmed) return;
      window.HoverCard.scheduleShow(x, y, data.title, data.description);
    });
  });

  document.addEventListener('mouseout', function (e) {
    var el = findTrigger(e.target);
    if (!el) return;
    el._hcArmed = false;
    if (window.HoverCard) window.HoverCard.cancel();
  });

  document.addEventListener('mousemove', function (e) {
    var el = findTrigger(e.target);
    if (el && window.HoverCard) window.HoverCard.reposition(e.clientX, e.clientY);
  });
})();
