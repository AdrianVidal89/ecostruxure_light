/*
 * Cross-cutting search over a POC's Use Cases AND Requirements, Obsidian-style:
 * type once and get every *place* the term is mentioned — not just a filtered
 * list of cards, but the surrounding sentence, so content can be cross-checked
 * between both sides at a glance. Clicking a hit opens the shared entity
 * preview modal (static/js/entity_preview.js), which already carries the
 * "Go to …" link for editing/commenting — so search stays a way IN to the
 * normal flow instead of a dead end.
 *
 * Used as an Alpine component:
 *     x-data="specsSearch(JSON.parse(document.getElementById('…').textContent))"
 * The index itself is rendered server-side (POCDetailView.specs_search_index).
 * Exposed as a global rather than registered on `alpine:init` so it does not
 * depend on this file executing before Alpine's deferred bootstrap.
 *
 * Snippets are returned as {pre, hit, post} triples, never HTML strings: the
 * template renders them through x-text, so user-written descriptions can never
 * inject markup.
 */
(function () {
  var MIN_QUERY = 2;      // below this, searching is noise
  var BEFORE = 55;        // context characters kept before a hit
  var AFTER = 85;         // …and after it
  var HITS_PER_ITEM = 5;  // rest are summarised as "+N more"
  var MAX_ITEMS = 60;     // guardrail on very large POCs

  // Markdown is stored raw, so a description arrives with newlines, list
  // bullets and ** emphasis. Flatten to a single line for the snippet.
  function flatten(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function snippet(text, start, length) {
    var from = Math.max(0, start - BEFORE);
    var to = Math.min(text.length, start + length + AFTER);
    return {
      pre: (from > 0 ? '…' : '') + text.slice(from, start),
      hit: text.slice(start, start + length),
      post: text.slice(start + length, to) + (to < text.length ? '…' : ''),
    };
  }

  function hitsIn(field, needle) {
    var out = [];
    var at = field.hay.indexOf(needle);
    while (at !== -1) {
      var s = snippet(field.flat, at, needle.length);
      out.push({ label: field.label, pre: s.pre, hit: s.hit, post: s.post });
      at = field.hay.indexOf(needle, at + needle.length);
    }
    return out;
  }

  // Flatten + lowercase once per page load instead of on every keystroke.
  function prepare(index) {
    return index.map(function (item) {
      var fields = [];
      item.fields.forEach(function (f) {
        var flat = flatten(f.text);
        if (flat) fields.push({ label: f.label, flat: flat, hay: flat.toLowerCase() });
      });
      return Object.assign({}, item, { fields: fields });
    });
  }

  window.specsSearch = function (rawIndex) {
    var index = prepare(rawIndex);
    var memo = { query: null, groups: [] };

    return {
      search: '',

      get query() {
        return (this.search || '').trim().toLowerCase();
      },

      get searching() {
        return this.query.length >= MIN_QUERY;
      },

      // [{item, hits: [{label, pre, hit, post}], extra}] — one group per
      // use case / requirement that mentions the term anywhere. Alpine getters
      // are not cached and several bindings read this one per keystroke, so
      // memoise on the query itself.
      get results() {
        if (!this.searching) return [];
        var needle = this.query;
        if (memo.query === needle) return memo.groups;
        var groups = [];
        for (var i = 0; i < index.length && groups.length < MAX_ITEMS; i++) {
          var item = index[i];
          var hits = [];
          for (var f = 0; f < item.fields.length; f++) {
            hits = hits.concat(hitsIn(item.fields[f], needle));
          }
          if (!hits.length) continue;
          groups.push({
            item: item,
            hits: hits.slice(0, HITS_PER_ITEM),
            extra: Math.max(0, hits.length - HITS_PER_ITEM),
            total: hits.length,
          });
        }
        memo = { query: needle, groups: groups };
        return groups;
      },

      get mentionCount() {
        return this.results.reduce(function (n, g) { return n + g.total; }, 0);
      },

      open(item) {
        if (window.openEntityPreview) {
          window.openEntityPreview(item.previewUrl, item.detailUrl, item.goLabel);
        }
      },

      clear() {
        this.search = '';
      },
    };
  };
})();
