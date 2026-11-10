// Загрузка манифеста библиотеки и кэширование SVG-разметки объектов.

import * as api from './api.js';

export const lib = {
  manifest: null,
  objects: [],
  byId: {},
  svg: new Map(), // "id:variant" -> полная разметка <svg>
};

async function prefetch(obj, variant) {
  const path = obj.variants && obj.variants[variant];
  if (!path) return;
  try {
    const res = await fetch('/assets/library/' + path);
    if (!res.ok) return;
    lib.svg.set(obj.id + ':' + variant, await res.text());
  } catch (e) {
    console.warn('Не удалось загрузить SVG', obj.id, variant, e);
  }
}

export async function loadLibrary() {
  const data = await api.fetchLibrary();
  lib.manifest = data;
  lib.objects = data.objects || [];
  lib.byId = Object.fromEntries(lib.objects.map((o) => [o.id, o]));
  await Promise.all(
    lib.objects.flatMap((o) => ['s', 'l'].map((v) => prefetch(o, v)))
  );
  return data;
}

export function innerMarkup(id, variant = 'l') {
  return lib.svg.get(id + ':' + variant) || lib.svg.get(id + ':l') || '';
}
