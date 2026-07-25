// Zoom lightbox for the result pages: a tapped/clicked .page clones its SVG
// into a full-screen overlay on a dark blurred backdrop, pan- and pinch-zoomable
// (Pointer Events), with -/fit/+ buttons (the middle one toggles whole-page vs
// page-width fit). On desktop the wheel scrolls the sheet like a document: plain
// wheel scrolls vertically, Shift+wheel horizontally, Ctrl+wheel zooms;
// left-click-drag pans. Markup lives in index.html.
(function () {
  const overlay = document.getElementById("lightbox");
  const stage = overlay.querySelector("[data-lightbox-stage]");
  const focusable = overlay.querySelectorAll(".lightbox-btn");
  const MIN_SCALE = 1;
  // Zoom cap. A stacked two-page sheet fits the page so small that fit-width is
  // already ~5x, so a fixed cap would leave almost no room to zoom past it: the
  // cap is at least MAX_SCALE, but always MAX_OVER_FIT_WIDTH times fit-width.
  const MAX_SCALE = 8;
  const MAX_OVER_FIT_WIDTH = 4;
  const STEP = 1.4; // per button press / wheel notch
  const LINE_PX = 16; // deltaMode: lines -> px
  const NOTCH_PX = 100; // one mouse wheel notch, in px of deltaY
  const STAGE_FRACTION = 0.92; // matches the 92vw/92vh cap in style.css
  const fitBtn = overlay.querySelector('[data-lightbox-zoom="fit"]');
  const nav = Array.from(overlay.querySelectorAll("[data-lightbox-page]"));
  const SWIPE_MIN = 50; // px of horizontal travel that counts as a page swipe
  const NAV_GAP = 12; // px between the sheet edge and a page button
  const NAV_EDGE_MIN = 16; // px: how close to the viewport edge they may get

  let scale = 1,
    tx = 0,
    ty = 0;
  let baseW = 0,
    baseH = 0;
  let content = null; // the cloned svg/img being transformed
  let lastFocused = null;
  const pointers = new Map();
  let pinchStart = null; // {dist, scale, midX, midY, tx, ty}
  let dragOrigin = null; // {x, y} of the pointer that started a drag-pan
  let dragged = false; // a drag just ended: swallow the trailing click
  let swiping = false; // touch drag with no sideways pan travel: a page swipe
  let pages = []; // the .page elements of the current result
  let pageIndex = 0;

  function clampScale(s) {
    return Math.min(maxScale(), Math.max(MIN_SCALE, s));
  }

  // How far the content may travel from centered, per axis (0 when it fits).
  function travel() {
    const rect = stage.getBoundingClientRect();
    return {
      x: Math.max(0, (baseW * scale - rect.width) / 2),
      y: Math.max(0, (baseH * scale - rect.height) / 2),
    };
  }

  // Keep the (possibly scaled) content covering the viewport center: no drift
  // into empty space, snaps back to centered at scale 1.
  function clampTranslate() {
    const max = travel();
    tx = Math.min(max.x, Math.max(-max.x, tx));
    ty = Math.min(max.y, Math.max(-max.y, ty));
  }

  // Zoom is driven by actual width/height, not a CSS transform: scale(). A
  // scale() transform reuses the layer's rasterized-at-1x bitmap and stretches
  // it, blurring vector content (SVG text/paths) well before MAX_SCALE. Resizing
  // instead makes the SVG re-render at the true target resolution, so it stays
  // crisp at any zoom. Only pan uses transform: translate().
  function apply() {
    if (!content) return;
    clampTranslate();
    content.style.width = baseW * scale + "px";
    content.style.height = baseH * scale + "px";
    content.style.transform = "translate(" + tx + "px," + ty + "px)";
  }

  // Zoom to newScale keeping the content point under (px, py) fixed, where
  // (px, py) are offsets from the stage center.
  function zoomTo(newScale, px, py) {
    const s2 = clampScale(newScale);
    const ratio = s2 / scale;
    tx = px - ratio * (px - tx);
    ty = py - ratio * (py - ty);
    scale = s2;
    if (scale === 1) {
      tx = 0;
      ty = 0;
    }
    apply();
    setFitLabel();
    placeNav();
  }

  function centerPoint(clientX, clientY) {
    const rect = stage.getBoundingClientRect();
    return {
      px: clientX - (rect.left + rect.width / 2),
      py: clientY - (rect.top + rect.height / 2),
    };
  }

  // Show pages[index]. Keeps the current zoom (the sheets are all the same size,
  // so page 2 opens as large as page 1 was being read at) but re-measures, since
  // baseW/baseH must come from a fresh clone under the CSS cap.
  function showPage(index) {
    if (index < 0 || index >= pages.length) return;
    const svg = pages[index].querySelector("svg");
    if (!svg) return;
    pageIndex = index;
    content = svg.cloneNode(true);
    content.removeAttribute("role");
    content.removeAttribute("tabindex");
    stage.replaceChildren(content);
    // Measure at scale 1 (still under the CSS max-width/max-height cap) for the
    // pan bounds, then switch sizing over to explicit width/height so zooming
    // past that cap isn't clipped by it.
    const rect = content.getBoundingClientRect();
    baseW = rect.width;
    baseH = rect.height;
    content.style.maxWidth = "none";
    content.style.maxHeight = "none";
    scale = clampScale(scale);
    tx = 0;
    ty = 0;
    apply();
    if (scale > 1) {
      ty = travel().y;
      apply();
    } // start at the top of the sheet
    setFitLabel();
    setNavState();
  }

  function goToPage(delta) {
    const next = pageIndex + delta;
    if (next < 0 || next >= pages.length) return;
    showPage(next);
  }

  function setNavState() {
    const many = pages.length > 1;
    nav.forEach((btn) => {
      btn.hidden = !many;
      btn.disabled =
        btn.getAttribute("data-lightbox-page") === "prev"
          ? pageIndex === 0
          : pageIndex === pages.length - 1;
    });
    placeNav();
  }

  // Fitted, the page buttons sit just outside the sheet rather than out at the
  // viewport rim (measured from baseW, the fitted width). Zoomed in the sheet
  // leaves no free margin, so they move out to the window edges instead of
  // sitting on top of what you are reading. Never closer than NAV_EDGE_MIN.
  function placeNav() {
    if (!nav.length || nav[0].hidden) return;
    const inset =
      scale > 1.001
        ? NAV_EDGE_MIN
        : Math.max(
            NAV_EDGE_MIN,
            (stage.clientWidth - baseW) / 2 - nav[0].offsetWidth - NAV_GAP,
          );
    nav.forEach((btn) => {
      const prev = btn.getAttribute("data-lightbox-page") === "prev";
      btn.style[prev ? "left" : "right"] = inset + "px";
    });
  }

  window.addEventListener("resize", () => {
    if (overlay.hasAttribute("data-open")) placeNav();
  });

  function open(page) {
    if (!page.querySelector("svg")) return;
    lastFocused = document.activeElement;
    pages = Array.from(result.querySelectorAll(".page"));
    overlay.setAttribute("data-open", "");
    overlay.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    scale = 1;
    showPage(Math.max(0, pages.indexOf(page)));
    focusable[0].focus();
  }

  function close() {
    if (!overlay.hasAttribute("data-open")) return;
    overlay.removeAttribute("data-open");
    overlay.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    stage.replaceChildren();
    content = null;
    pages = [];
    pointers.clear();
    pinchStart = null;
    swiping = false;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
    lastFocused = null;
  }

  // --- open triggers (delegated; #result is re-populated by HTMX) ---
  const result = document.getElementById("result");
  result.addEventListener("click", (e) => {
    const page = e.target.closest(".page");
    if (page) open(page);
  });
  result.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const page = e.target.closest(".page");
    if (page) {
      e.preventDefault();
      open(page);
    }
  });
  // A fresh calculation swaps out the pages; drop any open clone.
  document.body.addEventListener("htmx:beforeSwap", (e) => {
    if (e.detail.target && e.detail.target.id === "result") close();
  });

  // --- close triggers ---
  overlay.addEventListener("click", (e) => {
    if (dragged) {
      dragged = false;
      return;
    } // a pan, not a click on the backdrop
    if (e.target === overlay || e.target === stage) close();
  });
  overlay
    .querySelector("[data-lightbox-close]")
    .addEventListener("click", close);

  // --- dismiss the controls hint (stays dismissed for the rest of the visit) ---
  const hint = overlay.querySelector(".lightbox-hint");
  const hintClose = overlay.querySelector("[data-lightbox-hint-close]");
  if (hint && hintClose) {
    hintClose.addEventListener("click", (e) => {
      e.stopPropagation(); // not a backdrop click
      hint.hidden = true;
    });
  }
  document.addEventListener("keydown", (e) => {
    if (!overlay.hasAttribute("data-open")) return;
    if (e.key === "Escape") {
      close();
      return;
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      goToPage(-1);
      return;
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      goToPage(1);
      return;
    }
    if (e.key === "Tab") {
      // focus trap over the control buttons
      e.preventDefault();
      // Built per keypress: the hint's dismiss button drops out once it is hidden.
      const list = Array.from(
        overlay.querySelectorAll(".lightbox-btn, .lightbox-hint-close"),
      ).filter((el) => el.offsetParent !== null && !el.disabled);
      let i = list.indexOf(document.activeElement);
      i = (i + (e.shiftKey ? -1 : 1) + list.length) % list.length;
      list[i].focus();
    }
  });

  // --- page buttons (desktop; touch swipes instead, see pointerup) ---
  nav.forEach((btn) => {
    btn.addEventListener("click", () => {
      goToPage(btn.getAttribute("data-lightbox-page") === "prev" ? -1 : 1);
    });
  });

  // --- zoom buttons ---
  overlay.querySelectorAll("[data-lightbox-zoom]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.getAttribute("data-lightbox-zoom");
      if (v === "fit") {
        toggleFit();
        return;
      }
      zoomTo(scale * (v === "1" ? STEP : 1 / STEP), 0, 0);
    });
  });

  // --- fit toggle: whole page <-> page width ---
  // scale 1 is the fitted page (baseW/baseH were measured under the CSS
  // max-width/max-height cap), so fit-width is just the scale that takes the
  // fitted width up to the same fraction of the stage the cap allows.
  function fitWidthScale() {
    if (!baseW) return 1;
    return Math.max(MIN_SCALE, (stage.clientWidth * STAGE_FRACTION) / baseW);
  }

  function maxScale() {
    return Math.max(MAX_SCALE, fitWidthScale() * MAX_OVER_FIT_WIDTH);
  }

  function setFitLabel() {
    const wide = scale > 1.001;
    fitBtn.setAttribute(
      "aria-label",
      wide ? "Fit whole page" : "Fit page width",
    );
    fitBtn.setAttribute("aria-pressed", wide ? "true" : "false");
  }

  function toggleFit() {
    if (scale > 1.001) {
      scale = 1;
      tx = 0;
      ty = 0;
    } else {
      scale = fitWidthScale();
      tx = 0;
      apply();
      ty = travel().y; // start at the top of the sheet
    }
    apply();
    setFitLabel();
    placeNav();
  }

  // --- wheel: scroll by default, zoom with Ctrl, horizontal with Shift (desktop) ---
  // Normalize the delta to px so line- and page-mode wheels move sensibly too.
  function wheelDelta(e) {
    const unit =
      e.deltaMode === 1 ? LINE_PX : e.deltaMode === 2 ? stage.clientHeight : 1;
    return { dx: e.deltaX * unit, dy: e.deltaY * unit };
  }

  stage.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const { dx, dy } = wheelDelta(e);
      // Ctrl+wheel zooms (this is also how trackpad pinch arrives): exponential in
      // the delta, so one mouse notch is exactly one STEP and trackpads stay smooth.
      if (e.ctrlKey || e.metaKey) {
        const { px, py } = centerPoint(e.clientX, e.clientY);
        zoomTo(scale * Math.pow(STEP, -dy / NOTCH_PX), px, py);
        return;
      }
      if (e.shiftKey) {
        // Browsers already fold Shift+wheel into deltaX; fall back to deltaY.
        tx -= dx !== 0 ? dx : dy;
      } else {
        tx -= dx;
        ty -= dy;
      }
      apply();
    },
    { passive: false },
  );

  // --- pointer pan + pinch (mouse + touch unified) ---
  function pinchDistMid() {
    const pts = Array.from(pointers.values());
    const dx = pts[0].x - pts[1].x,
      dy = pts[0].y - pts[1].y;
    const mid = centerPoint(
      (pts[0].x + pts[1].x) / 2,
      (pts[0].y + pts[1].y) / 2,
    );
    return { dist: Math.hypot(dx, dy), px: mid.px, py: mid.py };
  }

  stage.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return; // left button only
    stage.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    dragOrigin = { x: e.clientX, y: e.clientY };
    dragged = false;
    stage.setAttribute("data-grabbing", "");
    // On touch, a sideways drag with nowhere to pan sideways is a page swipe.
    swiping =
      pointers.size === 1 && e.pointerType !== "mouse" && travel().x === 0;
    if (pointers.size === 2) {
      swiping = false; // a pinch, not a swipe
      const pm = pinchDistMid();
      pinchStart = {
        dist: pm.dist,
        scale: scale,
        px: pm.px,
        py: pm.py,
        tx: tx,
        ty: ty,
      };
    }
  });
  stage.addEventListener("pointermove", (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2 && pinchStart) {
      const pm = pinchDistMid();
      zoomTo(pinchStart.scale * (pm.dist / pinchStart.dist), pm.px, pm.py);
    } else if (pointers.size === 1) {
      // Pan at any zoom; clampTranslate keeps a fitted sheet centered anyway.
      if (
        dragOrigin &&
        Math.hypot(e.clientX - dragOrigin.x, e.clientY - dragOrigin.y) > 4
      ) {
        dragged = true;
      }
      tx += e.clientX - prev.x;
      ty += e.clientY - prev.y;
      apply();
    }
  });
  function endPointer(e) {
    if (swiping && dragOrigin && pointers.size === 1) {
      const dx = e.clientX - dragOrigin.x;
      const dy = e.clientY - dragOrigin.y;
      if (Math.abs(dx) > SWIPE_MIN && Math.abs(dx) > Math.abs(dy)) {
        goToPage(dx < 0 ? 1 : -1); // drag left -> next page
      } else {
        apply(); // no page turn: undo whatever the drag panned
      }
    }
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinchStart = null;
    if (pointers.size === 0) {
      dragOrigin = null;
      swiping = false;
      stage.removeAttribute("data-grabbing");
    }
  }
  stage.addEventListener("pointerup", endPointer);
  stage.addEventListener("pointercancel", endPointer);
})();
