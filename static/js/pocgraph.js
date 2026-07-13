/*
 * "Obsidian style" force-directed map: POC -> Use Cases -> Requirements ->
 * Tests. Deliberately dependency-free (canvas + a small physics loop) since
 * this network blocks CDN-hosted graph libraries — see CLAUDE build notes on
 * every front-end lib being vendored locally.
 *
 * Usage: initPocGraph(containerEl, dataUrl).onClick(node => ...)
 */
(function () {
  const STATUS_COLOR = {
    green: "#3dcd58",
    orange: "#f59e0b",
    red: "#ef4444",
    gray: "#9ca3af",
  };
  const TYPE_RADIUS = { poc: 15, usecase: 9, requirement: 7, test: 5 };

  function truncate(s, n) {
    return s && s.length > n ? s.slice(0, n - 1) + "…" : s || "";
  }

  function initPocGraph(container, dataUrl) {
    const canvas = document.createElement("canvas");
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.cursor = "grab";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    let nodes = [];
    let edges = [];
    let byId = {};
    let scale = 1;
    let offsetX = 0;
    let offsetY = 0;
    let dragging = null;
    let dragMoved = false;
    let panning = false;
    let panStart = null;
    let hovered = null;
    let width = 0;
    let height = 420;
    let onNodeClick = function () {};
    let raf = null;

    function resize() {
      width = container.clientWidth || 600;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.height = height + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    window.addEventListener("resize", resize);
    resize();

    fetch(dataUrl)
      .then((r) => r.json())
      .then((data) => {
        nodes = data.nodes.map((n) => ({
          ...n,
          x: width / 2 + (Math.random() - 0.5) * 240,
          y: height / 2 + (Math.random() - 0.5) * 240,
          vx: 0,
          vy: 0,
        }));
        byId = {};
        nodes.forEach((n) => {
          byId[n.id] = n;
        });
        edges = data.edges
          .map((e) => ({ source: byId[e.source], target: byId[e.target] }))
          .filter((e) => e.source && e.target);
        if (raf) cancelAnimationFrame(raf);
        tick();
      });

    function tick() {
      step();
      draw();
      raf = requestAnimationFrame(tick);
    }

    function step() {
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distSq = dx * dx + dy * dy || 0.01;
          if (distSq < 160000) {
            const dist = Math.sqrt(distSq);
            const force = 1800 / distSq;
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            a.vx += fx;
            a.vy += fy;
            b.vx -= fx;
            b.vy -= fy;
          }
        }
      }
      edges.forEach((e) => {
        const dx = e.target.x - e.source.x;
        const dy = e.target.y - e.source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (dist - 75) * 0.02;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        e.source.vx += fx;
        e.source.vy += fy;
        e.target.vx -= fx;
        e.target.vy -= fy;
      });
      nodes.forEach((n) => {
        n.vx += (width / 2 - n.x) * 0.001;
        n.vy += (height / 2 - n.y) * 0.001;
      });
      nodes.forEach((n) => {
        if (n === dragging) return;
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx;
        n.y += n.vy;
      });
    }

    function draw() {
      ctx.clearRect(0, 0, width, height);
      ctx.save();
      ctx.translate(offsetX, offsetY);
      ctx.scale(scale, scale);

      ctx.strokeStyle = "rgba(148,163,184,0.35)";
      ctx.lineWidth = 1 / scale;
      edges.forEach((e) => {
        ctx.beginPath();
        ctx.moveTo(e.source.x, e.source.y);
        ctx.lineTo(e.target.x, e.target.y);
        ctx.stroke();
      });

      nodes.forEach((n) => {
        const r = TYPE_RADIUS[n.type] || 6;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = STATUS_COLOR[n.status] || "#9ca3af";
        ctx.fill();
        ctx.lineWidth = (n === hovered ? 2.5 : 1) / scale;
        ctx.strokeStyle = n === hovered ? "#111827" : "rgba(17,24,39,0.25)";
        ctx.stroke();
        if (scale > 0.75 || n.type === "poc") {
          ctx.fillStyle = "#111827";
          ctx.font = `${11 / scale}px sans-serif`;
          ctx.textAlign = "center";
          ctx.fillText(truncate(n.label, 22), n.x, n.y + r + 12 / scale);
        }
      });
      ctx.restore();
    }

    function toWorld(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      return { x: (x - offsetX) / scale, y: (y - offsetY) / scale };
    }

    function nodeAt(clientX, clientY) {
      const p = toWorld(clientX, clientY);
      let best = null;
      let bestDist = Infinity;
      nodes.forEach((n) => {
        const r = (TYPE_RADIUS[n.type] || 6) + 4;
        const d = Math.hypot(n.x - p.x, n.y - p.y);
        if (d < r && d < bestDist) {
          best = n;
          bestDist = d;
        }
      });
      return best;
    }

    canvas.addEventListener("mousedown", (e) => {
      const n = nodeAt(e.clientX, e.clientY);
      dragMoved = false;
      if (n) {
        dragging = n;
      } else {
        panning = true;
        panStart = { x: e.clientX - offsetX, y: e.clientY - offsetY };
        canvas.style.cursor = "grabbing";
      }
    });
    window.addEventListener("mousemove", (e) => {
      if (dragging) {
        dragMoved = true;
        const p = toWorld(e.clientX, e.clientY);
        dragging.x = p.x;
        dragging.y = p.y;
        dragging.vx = 0;
        dragging.vy = 0;
      } else if (panning) {
        offsetX = e.clientX - panStart.x;
        offsetY = e.clientY - panStart.y;
      } else {
        const n = nodeAt(e.clientX, e.clientY);
        hovered = n;
        canvas.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", () => {
      if (dragging && !dragMoved) onNodeClick(dragging);
      dragging = null;
      if (panning) canvas.style.cursor = "grab";
      panning = false;
    });
    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const before = { x: (mx - offsetX) / scale, y: (my - offsetY) / scale };
        scale = Math.min(4, Math.max(0.2, scale * factor));
        offsetX = mx - before.x * scale;
        offsetY = my - before.y * scale;
      },
      { passive: false }
    );

    return {
      onClick(fn) {
        onNodeClick = fn;
      },
      resetView() {
        scale = 1;
        offsetX = 0;
        offsetY = 0;
      },
    };
  }

  window.initPocGraph = initPocGraph;
})();
