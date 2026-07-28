/*
 * Shared delayed hover-card: after the pointer rests on a trigger for a short
 * delay, shows a small floating card with that entity's full description.
 * Used by the POC graph (canvas nodes, no real DOM element per node — driven
 * manually via scheduleShow/cancel/reposition) and by the UC×Requirement
 * matrix headers (real <th> elements — via attach()). One shared component so
 * both places behave identically (spec: graph item 7 + matrix item 3).
 */
(function () {
  const DELAY = 600;
  let card = null;
  let timer = null;

  function ensureCard() {
    if (card) return card;
    card = document.createElement("div");
    card.className =
      "fixed z-[70] max-w-xs bg-surface-elevated border border-line rounded-xl shadow-xl p-3 text-sm hidden";
    card.style.pointerEvents = "none";
    card.innerHTML = '<p class="font-medium text-ink mb-1" data-hc-title></p><p class="text-ink-muted whitespace-pre-line" data-hc-desc></p>';
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
    if (top + 140 > window.innerHeight) top = Math.max(8, y - 140);
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

  function cancel() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    hide();
  }

  function scheduleShow(x, y, title, description) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      timer = null;
      show(x, y, title, description);
    }, DELAY);
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
