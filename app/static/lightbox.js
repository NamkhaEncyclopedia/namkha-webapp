// Zoom lightbox for the result pages: a tapped/clicked .page clones its SVG
// into a full-screen overlay on a dark blurred backdrop, pan- and pinch-zoomable
// (Pointer Events), with wheel and +/-/reset buttons. Markup lives in index.html.
(function () {
  const overlay = document.getElementById('lightbox');
  const stage = overlay.querySelector('[data-lightbox-stage]');
  const focusable = overlay.querySelectorAll('.lightbox-btn');
  const MIN_SCALE = 1;
  const MAX_SCALE = 8;
  const STEP = 1.4; // per button press / wheel notch

  let scale = 1, tx = 0, ty = 0;
  let baseW = 0, baseH = 0;
  let content = null;      // the cloned svg/img being transformed
  let lastFocused = null;
  const pointers = new Map();
  let pinchStart = null;   // {dist, scale, midX, midY, tx, ty}

  function clampScale(s) { return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s)); }

  // Keep the (possibly scaled) content covering the viewport center: no drift
  // into empty space, snaps back to centered at scale 1.
  function clampTranslate() {
    const rect = stage.getBoundingClientRect();
    const maxX = Math.max(0, (baseW * scale - rect.width) / 2);
    const maxY = Math.max(0, (baseH * scale - rect.height) / 2);
    tx = Math.min(maxX, Math.max(-maxX, tx));
    ty = Math.min(maxY, Math.max(-maxY, ty));
  }

  function apply() {
    if (!content) return;
    clampTranslate();
    content.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
  }

  // Zoom to newScale keeping the content point under (px, py) fixed, where
  // (px, py) are offsets from the stage center.
  function zoomTo(newScale, px, py) {
    const s2 = clampScale(newScale);
    const ratio = s2 / scale;
    tx = px - ratio * (px - tx);
    ty = py - ratio * (py - ty);
    scale = s2;
    if (scale === 1) { tx = 0; ty = 0; }
    apply();
  }

  function centerPoint(clientX, clientY) {
    const rect = stage.getBoundingClientRect();
    return { px: clientX - (rect.left + rect.width / 2), py: clientY - (rect.top + rect.height / 2) };
  }

  function open(page) {
    const svg = page.querySelector('svg');
    if (!svg) return;
    lastFocused = document.activeElement;
    content = svg.cloneNode(true);
    content.removeAttribute('role');
    content.removeAttribute('tabindex');
    stage.replaceChildren(content);
    scale = 1; tx = 0; ty = 0;
    overlay.setAttribute('data-open', '');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    // Measure at scale 1 for the pan bounds.
    const r = content.getBoundingClientRect();
    baseW = r.width; baseH = r.height;
    apply();
    focusable[0].focus();
  }

  function close() {
    if (!overlay.hasAttribute('data-open')) return;
    overlay.removeAttribute('data-open');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    stage.replaceChildren();
    content = null;
    pointers.clear();
    pinchStart = null;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    lastFocused = null;
  }

  // --- open triggers (delegated; #result is re-populated by HTMX) ---
  const result = document.getElementById('result');
  result.addEventListener('click', (e) => {
    const page = e.target.closest('.page');
    if (page) open(page);
  });
  result.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const page = e.target.closest('.page');
    if (page) { e.preventDefault(); open(page); }
  });
  // A fresh calculation swaps out the pages; drop any open clone.
  document.body.addEventListener('htmx:beforeSwap', (e) => {
    if (e.detail.target && e.detail.target.id === 'result') close();
  });

  // --- close triggers ---
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay || e.target === stage) close();
  });
  overlay.querySelector('[data-lightbox-close]').addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (!overlay.hasAttribute('data-open')) return;
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'Tab') { // focus trap over the control buttons
      e.preventDefault();
      const list = Array.from(focusable);
      let i = list.indexOf(document.activeElement);
      i = (i + (e.shiftKey ? -1 : 1) + list.length) % list.length;
      list[i].focus();
    }
  });

  // --- zoom buttons ---
  overlay.querySelectorAll('[data-lightbox-zoom]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const v = btn.getAttribute('data-lightbox-zoom');
      if (v === 'reset') { zoomTo(1, 0, 0); return; }
      zoomTo(scale * (v === '1' ? STEP : 1 / STEP), 0, 0);
    });
  });

  // --- wheel zoom (desktop) ---
  stage.addEventListener('wheel', (e) => {
    e.preventDefault();
    const { px, py } = centerPoint(e.clientX, e.clientY);
    zoomTo(scale * (e.deltaY < 0 ? STEP : 1 / STEP), px, py);
  }, { passive: false });

  // --- pointer pan + pinch (mouse + touch unified) ---
  function pinchDistMid() {
    const pts = Array.from(pointers.values());
    const dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
    const mid = centerPoint((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2);
    return { dist: Math.hypot(dx, dy), px: mid.px, py: mid.py };
  }

  stage.addEventListener('pointerdown', (e) => {
    stage.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const pm = pinchDistMid();
      pinchStart = { dist: pm.dist, scale: scale, px: pm.px, py: pm.py, tx: tx, ty: ty };
    }
  });
  stage.addEventListener('pointermove', (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2 && pinchStart) {
      const pm = pinchDistMid();
      zoomTo(pinchStart.scale * (pm.dist / pinchStart.dist), pm.px, pm.py);
    } else if (pointers.size === 1 && scale > 1) {
      tx += e.clientX - prev.x;
      ty += e.clientY - prev.y;
      apply();
    }
  });
  function endPointer(e) {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinchStart = null;
  }
  stage.addEventListener('pointerup', endPointer);
  stage.addEventListener('pointercancel', endPointer);
})();
