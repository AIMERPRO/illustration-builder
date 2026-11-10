// Рендер сцены в единый SVG и редактирование объектов мышью.

import { state, onChange, emit, deselect } from './scene.js';
import { innerMarkup, lib } from './library.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const ACCENT = '#2f5bea';
const parser = new DOMParser();
const nodeCache = new Map(); // "id:variant" -> <svg> шаблон

let svg = null;
let layers = null;
let drag = null;
let resizeRaf = 0;
// Визуальный масштаб холста на странице (1 = вписан в экран; 0.5 = вдвое меньше).
const DISPLAY_SCALE = 0.5;

function el(name, attrs = {}) {
  const node = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function template(id, variant) {
  const key = id + ':' + variant;
  if (nodeCache.has(key)) return nodeCache.get(key);
  const markup = innerMarkup(id, variant);
  if (!markup) return null;
  const doc = parser.parseFromString(markup, 'image/svg+xml');
  const node = doc.documentElement;
  nodeCache.set(key, node);
  return node;
}

function itemTransform(it) {
  return `translate(${it.x} ${it.y}) rotate(${it.rot}) scale(${it.scale}) translate(-100 -100)`;
}

function stateStyle(it, nativeType) {
  if (nativeType === 'main' && it.state === 'background') {
    return '--c1:var(--bg1);--c2:var(--bg2);--c3:var(--bg3);--c4:var(--bg2);--c5:var(--bg1);--line:var(--bg3);';
  }
  if (nativeType === 'background' && it.state === 'main') {
    return '--bg1:var(--c1);--bg2:var(--c2);--bg3:var(--c3);';
  }
  return '';
}

function localToScene(it, lx, ly) {
  const cx = (lx - 100) * it.scale;
  const cy = (ly - 100) * it.scale;
  const a = (it.rot * Math.PI) / 180;
  return {
    x: it.x + cx * Math.cos(a) - cy * Math.sin(a),
    y: it.y + cx * Math.sin(a) + cy * Math.cos(a),
  };
}

export function initCanvas() {
  svg = document.getElementById('stage-svg');
  layers = {
    bg: el('rect', { id: 'scene-bg', x: 0, y: 0, width: 600, height: 315, fill: 'var(--canvas)' }),
    decor: el('g', { id: 'layer-decor' }),
    items: el('g', { id: 'layer-items' }),
    overlay: el('g', { id: 'layer-overlay' }),
  };
  svg.append(layers.bg, layers.decor, layers.items, layers.overlay);

  svg.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('keydown', onKeyDown);

  onChange(render);
  render();

  // при ресайзе окна / панелей — вписываем холст в доступную область
  const scheduleRender = () => {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(render);
  };
  window.addEventListener('resize', scheduleRender);
  if (window.ResizeObserver) {
    const wrap = document.getElementById('canvas-frame');
    if (wrap && wrap.parentElement) {
      new ResizeObserver(scheduleRender).observe(wrap.parentElement);
    }
  }
}

function render() {
  if (!svg) return;
  const sc = state.scene;
  layers.bg.setAttribute('width', sc.width);
  layers.bg.setAttribute('height', sc.height);
  layers.bg.setAttribute('fill',
    (sc.backgrounds && sc.backgrounds[sc.scheme]) || sc.background || 'var(--canvas)');

  // холст: динамический viewBox и вписывание в доступную область (ширина И высота)
  svg.setAttribute('viewBox', '0 0 ' + sc.width + ' ' + sc.height);
  const frame = document.getElementById('canvas-frame');
  if (frame) {
    const wrap = frame.parentElement;
    const ratio = sc.width / sc.height;
    frame.style.aspectRatio = sc.width + ' / ' + sc.height;
    if (wrap) {
      const cs = getComputedStyle(wrap);
      const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
      const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      const availW0 = Math.max(0, wrap.clientWidth - padX);
      const availH0 = Math.max(0, wrap.clientHeight - padY);
      let availW = availW0;
      let availH = availH0;
      if (!(availW > 8 && availH > 8)) {
        // запасной вариант, пока контейнер не измерен
        availW = window.innerWidth * 0.9;
        availH = window.innerHeight * 0.7;
      }
      if (availW > 0 && availH > 0) {
        let w = availW;
        let h = w / ratio;
        if (h > availH) { h = availH; w = h * ratio; }
        w *= DISPLAY_SCALE;
        h *= DISPLAY_SCALE;
        frame.style.width = Math.round(w) + 'px';
        frame.style.height = Math.round(h) + 'px';
      }
    }
  }

  // декоративные векторы фона (линии, точки) — под объектами
  layers.decor.innerHTML = sc.decor || '';

  layers.items.innerHTML = '';
  const order = sc.items
    .map((it, i) => ({ it, i }))
    .sort((a, b) => (a.it.z || 0) - (b.it.z || 0));

  for (const { it, i } of order) {
    const nativeType = lib.byId[it.id] && lib.byId[it.id].type;
    const g = el('g', {
      class: 'item',
      transform: itemTransform(it),
      opacity: it.opacity != null ? it.opacity : 1,
    });
    g.dataset.index = i;
    const style = stateStyle(it, nativeType);
    if (style) g.setAttribute('style', style);

    const tpl = template(it.id, it.variant || sc.size);
    if (tpl) g.appendChild(document.importNode(tpl, true));
    layers.items.appendChild(g);
  }

  renderOverlay();
}

function renderOverlay() {
  layers.overlay.innerHTML = '';
  const idx = state.selected;
  const it = state.scene.items[idx];
  if (!it) return;

  const corners = [[0, 0], [200, 0], [200, 200], [0, 200]]
    .map(([lx, ly]) => localToScene(it, lx, ly));
  layers.overlay.appendChild(el('polygon', {
    points: corners.map((p) => p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' '),
    fill: 'none', stroke: ACCENT, 'stroke-width': 2.4,
    'stroke-dasharray': '7 5', 'pointer-events': 'none',
  }));

  const topCenter = localToScene(it, 100, 0);
  const rotateP = localToScene(it, 100, -34);
  layers.overlay.appendChild(el('line', {
    x1: topCenter.x, y1: topCenter.y, x2: rotateP.x, y2: rotateP.y,
    stroke: ACCENT, 'stroke-width': 2.2, 'pointer-events': 'none',
  }));
  layers.overlay.appendChild(handle(rotateP, 'rotate'));

  const scaleP = localToScene(it, 200, 200);
  layers.overlay.appendChild(handle(scaleP, 'scale'));
}

function handle(point, kind) {
  return el('circle', {
    cx: point.x, cy: point.y, r: 11,
    fill: '#ffffff', stroke: ACCENT, 'stroke-width': 2.5,
    'data-handle': kind, 'pointer-events': 'all', style: 'cursor:pointer',
  });
}

function toScene(evt) {
  const p = svg.createSVGPoint();
  p.x = evt.clientX;
  p.y = evt.clientY;
  const m = svg.getScreenCTM();
  if (!m) return { x: 0, y: 0 };
  const q = p.matrixTransform(m.inverse());
  return { x: q.x, y: q.y };
}

function onPointerDown(e) {
  const handleEl = e.target.closest('[data-handle]');
  const itemEl = e.target.closest('.item');

  if (handleEl && state.selected >= 0) {
    const it = state.scene.items[state.selected];
    const p = toScene(e);
    if (handleEl.dataset.handle === 'rotate') {
      drag = {
        type: 'rotate', idx: state.selected,
        startAngle: (Math.atan2(p.y - it.y, p.x - it.x) * 180) / Math.PI,
        startRot: it.rot,
      };
    } else {
      drag = {
        type: 'scale', idx: state.selected,
        startDist: Math.hypot(p.x - it.x, p.y - it.y) || 1,
        startScale: it.scale,
      };
    }
    e.preventDefault();
    return;
  }

  if (itemEl) {
    const idx = Number(itemEl.dataset.index);
    const it = state.scene.items[idx];
    const p = toScene(e);
    state.selected = idx;
    drag = { type: 'move', idx, dx: p.x - it.x, dy: p.y - it.y };
    emit();
    e.preventDefault();
    return;
  }

  deselect();
}

function onPointerMove(e) {
  if (!drag) return;
  const it = state.scene.items[drag.idx];
  if (!it) return;
  const p = toScene(e);

  if (drag.type === 'move') {
    it.x = p.x - drag.dx;
    it.y = p.y - drag.dy;
  } else if (drag.type === 'rotate') {
    const angle = (Math.atan2(p.y - it.y, p.x - it.x) * 180) / Math.PI;
    it.rot = drag.startRot + (angle - drag.startAngle);
  } else if (drag.type === 'scale') {
    const d = Math.hypot(p.x - it.x, p.y - it.y);
    it.scale = Math.min(6, Math.max(0.2, drag.startScale * (d / drag.startDist)));
  }

  const node = layers.items.querySelector('.item[data-index="' + drag.idx + '"]');
  if (node) node.setAttribute('transform', itemTransform(it));
  renderOverlay();
}

function onPointerUp() {
  if (!drag) return;
  drag = null;
  emit();
}

function onKeyDown(e) {
  const tag = (document.activeElement && document.activeElement.tagName) || '';
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
  if ((e.key === 'Delete' || e.key === 'Backspace') && state.selected >= 0) {
    import('./scene.js').then((m) => m.removeSelected());
    e.preventDefault();
  } else if ((e.ctrlKey || e.metaKey) && (e.key === 'd' || e.key === 'D')) {
    import('./scene.js').then((m) => m.duplicateSelected());
    e.preventDefault();
  } else if (e.key === 'Escape') {
    deselect();
  }
}

