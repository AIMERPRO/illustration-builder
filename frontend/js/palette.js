// Цветовые схемы, применение ролей к CSS-переменным и разбор палитры.

export const ROLE_ORDER = ['canvas', 'c1', 'c2', 'c3', 'c4', 'c5', 'line', 'bg1', 'bg2', 'bg3'];
export const ROLE_LABELS = {
  canvas: 'Холст',
  c1: 'Объект c1',
  c2: 'Объект c2',
  c3: 'Объект c3',
  c4: 'Объект c4',
  c5: 'Объект c5',
  line: 'Обводка',
  bg1: 'Фон bg1',
  bg2: 'Фон bg2',
  bg3: 'Фон bg3',
};

export function schemeRoles(name, palettes) {
  const scheme = palettes && palettes[name];
  return scheme ? { ...scheme.roles } : null;
}

export function schemeName(name, palettes) {
  const scheme = palettes && palettes[name];
  return scheme ? scheme.name : name;
}

// Записывает роли в CSS-переменные корня документа: все inline-SVG,
// использующие var(--c1).. var(--bg3), мгновенно перекрашиваются.
export function applyRoles(roles) {
  const root = document.documentElement;
  for (const [role, value] of Object.entries(roles || {})) {
    root.style.setProperty('--' + role, value);
  }
}

function hexToRgb(value) {
  let v = String(value || '').trim().replace('#', '');
  if (v.length === 3) v = v.split('').map((c) => c + c).join('');
  if (v.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(v)) return null;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

function toHex(rgb) {
  return '#' + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c)))
    .toString(16).padStart(2, '0')).join('').toUpperCase();
}

const lum = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
const sat = (rgb) => {
  const mx = Math.max(...rgb);
  const mn = Math.min(...rgb);
  return mx === 0 ? 0 : (mx - mn) / 255;
};
const mix = (a, b, t) => a.map((_, i) => a[i] + (b[i] - a[i]) * t);
const lighten = (rgb, t) => mix(rgb, [255, 255, 255], t);
const darken = (rgb, t) => mix(rgb, [0, 0, 0], t);

// Зеркало backend/palette.py: раскладывает произвольные цвета по ролям.
export function deriveRolesFromColors(hexList) {
  const rgbs = (hexList || []).map(hexToRgb).filter(Boolean);
  if (!rgbs.length) return null;

  const ordered = [...rgbs].sort((a, b) => lum(a) - lum(b));
  const lightest = ordered[ordered.length - 1];
  const darkest = ordered[0];
  const avg = [0, 1, 2].map((i) => rgbs.reduce((s, c) => s + c[i], 0) / rgbs.length);
  const gray = Math.round(lum(avg));
  const neutral = [gray, gray, gray];

  const saturated = [...rgbs].sort((a, b) => sat(b) - sat(a));
  const uniq = [];
  for (const c of saturated) {
    if (uniq.every((u) => u.reduce((s, v, i) => s + Math.abs(v - c[i]), 0) > 60)) {
      uniq.push(c);
    }
  }
  let k = 0;
  while (uniq.length < 5) {
    uniq.push(mix(lightest, darkest, 0.5 + 0.2 * k));
    k += 1;
  }

  return {
    canvas: toHex(lighten(lightest, 0.82)),
    c1: toHex(uniq[0]),
    c2: toHex(uniq[1]),
    c3: toHex(uniq[2]),
    c4: toHex(uniq[3]),
    c5: toHex(uniq[4]),
    line: toHex(darken(darkest, 0.45)),
    bg1: toHex(mix(neutral, [255, 255, 255], 0.85)),
    bg2: toHex(mix(neutral, [255, 255, 255], 0.70)),
    bg3: toHex(mix(neutral, [255, 255, 255], 0.52)),
  };
}

function colorDist(a, b) {
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]);
}

function loadImageFromFile(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
    img.src = url;
  });
}

// Извлекает до 8 доминирующих цветов изображения через <canvas>.
export async function imageToColors(file) {
  const img = await loadImageFromFile(file);
  const size = 96;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, size, size);
  const { data } = ctx.getImageData(0, 0, size, size);

  const buckets = new Map();
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 128) continue;
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const key = (r >> 4) + ',' + (g >> 4) + ',' + (b >> 4);
    const bkt = buckets.get(key) || { r: 0, g: 0, b: 0, n: 0 };
    bkt.r += r; bkt.g += g; bkt.b += b; bkt.n += 1;
    buckets.set(key, bkt);
  }

  const arr = [...buckets.values()]
    .map((b) => [b.r / b.n, b.g / b.n, b.b / b.n, b.n])
    .sort((a, b) => b[3] - a[3]);

  const picked = [];
  for (const c of arr) {
    if (picked.length >= 8) break;
    if (picked.every((p) => colorDist(p, c) > 40)) picked.push(c);
  }
  return picked.map((c) => toHex(c));
}

export { hexToRgb, toHex };
