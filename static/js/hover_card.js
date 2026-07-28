/*
 * Shared delayed hover-card: after the pointer rests on a trigger for a short
 * delay, shows a small floating card with that entity's full description.
 * Used by the POC graph (canvas nodes, no real DOM element per node — driven
 * manually via scheduleShow/cancel/reposition) and by the UC×Requirement
 * matrix headers (real <th> elements — via attach()), plus every chip that
 * opens an entity preview (static/js/entity_hover_preview.js).
 *
 * The card accepts pointer events and only hides after a short grace period
 * (HIDE_DELAY) — long enough for the mouse to travel from the trigger onto
 * the card itself to read a long description or scroll it (capped height,
 * internal scroll) without the card vanishing mid-move.
 */
(function () {
  const SHOW_DELAY = 600;
  const HIDE_DELAY = 250;
  const RESERVED_HEIGHT = 260; // keep in sync with the card's max-height below
  let card = null;
  let showTimer = null;
  let hideTimer = null;

  function ensureCard() {
    if (card) return card;
    card = document.createElement("div");
    card.className =
      "fixed z-[70] max-w-xs max-h-64 overflow-y-auto bg-surface-elevated border border-line rounded-xl shadow-xl p-3 text-sm hidden";
    card.innerHTML = '<p class="font-medium text-ink mb-1" data-hc-title></p><p class="text-ink-muted whitespace-pre-line" data-hc-desc></p>';
    // Entering the card cancels any pending hide; leaving it re-schedules one
    // — mirrors the trigger's own scheduleHide/cancel dance below.
    card.addEventListener("mouseenter", function () {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
    });
    card.addEventListener("mouseleave", scheduleHide);
    document.body.appendChild(card);
    return card;
  }

  function position(x, y) {
    const c = ensureCard();
    const pad = 14;
    const w = 280;
    let left = x + pad;
    if (left + w > window.innerWidth) left = Math.max(8, x - w - pad);
    let top = y + pad;
    if (top + RESERVED_HEIGHT > window.innerHeight) top = Math.max(8, y - RESERVED_HEIGHT);
    c.style.left = left + "px";
    c.style.top = top + "px";
  }

  function show(x, y, title, description) {
    const c = ensureCard();
    c.querySelector("[data-hc-title]").textContent = title || "";
    const descEl = c.querySelector("[data-hc-desc]");
    descEl.textContent = description || "No description.";
    descEl.classList.toggle("italic", !description);
    position(x, y);
    c.classList.remove("hidden");
  }

  function hide() {
    if (card) card.classList.add("hidden");
  }

  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      hideTimer = null;
      hide();
    }, HIDE_DELAY);
  }

  function cancel() {
    if (showTimer) {
      clearTimeout(showTimer);
      showTimer = null;
    }
    scheduleHide();
  }

  function scheduleShow(x, y, title, description) {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    if (showTimer) clearTimeout(showTimer);
    showTimer = setTimeout(function () {
      showTimer = null;
      show(x, y, title, description);
    }, SHOW_DELAY);
  }

  function reposition(x, y) {
    if (card && !card.classList.contains("hidden")) position(x, y);
  }

  // Wires a real DOM element (matrix <th> headers): getContent() is called on
  // hover-in and must return {title, description} or a falsy value to skip.
  function attach(el, getContent) {
    el.addEventListener("mouseenter", function (e) {
      const content = getContent();
      if (!content) return;
      scheduleShow(e.clientX, e.clientY, content.title, content.description);
    });
    el.addEventListener("mousemove", function (e) {
      reposition(e.clientX, e.clientY);
    });
    el.addEventListener("mouseleave", cancel);
  }

  window.HoverCard = { scheduleShow, cancel, reposition, attach };
})();
