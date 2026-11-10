// Сборка одного векторного SVG из сцены и скачивание.

import { state } from './scene.js';
import { innerMarkup, lib } from './library.js';

function effectiveRoles(it) {
  const roles = { ...state.roles };
  const native = lib.byId[it.id] && lib.byId[it.id].type;
  if (native === 'main' && it.state === 'background') {
    roles.c1 = state.roles.bg1;
    roles.c2 = state.roles.bg2;
    roles.c3 = state.roles.bg3;
    roles.c4 = state.roles.bg2;
    roles.c5 = state.roles.bg1;
    roles.line = state.roles.bg3;
  } else if (native === 'background' && it.state === 'main') {
    roles.bg1 = state.roles.c1;
    roles.bg2 = state.roles.c2;
    roles.bg3 = state.roles.c3;
  }
  return roles;
}

// Подстановка цветов ролей в SVG с учётом inline-алиасов `--c1:var(--bg1)`
// (так фоновые ассеты остаются монохромными и в скачанном SVG).
const ALIAS_RE = /--([a-zA-Z0-9]+)\s*:\s*var\(--([a-zA-Z0-9]+)\)/g;
const VAR_RE = /var\(--([a-zA-Z0-9]+)\)/g;

function substitute(markup, roles) {
  const aliases = {};
  markup.replace(ALIAS_RE, (m, a, b) => { aliases[a] = b; return m; });
  const resolve = (name) => {
    const seen = new Set();
    while (aliases[name] && !seen.has(name)) { seen.add(name); name = aliases[name]; }
    return name;
  };
  return markup.replace(VAR_RE, (m, role) => roles[resolve(role)] || m);
}

export function buildSvg() {
  const sc = state.scene;
  const order = [...sc.items].sort((a, b) => (a.z || 0) - (b.z || 0));
  let body = '';
  for (const it of order) {
    const roles = effectiveRoles(it);
    const inner = substitute(innerMarkup(it.id, it.variant || sc.size), roles);
    const tf = `translate(${it.x} ${it.y}) rotate(${it.rot}) scale(${it.scale}) translate(-100 -100)`;
    body += `<g transform="${tf}" opacity="${it.opacity != null ? it.opacity : 1}">${inner}</g>`;
  }

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${sc.width}" height="${sc.height}" ` +
    `viewBox="0 0 ${sc.width} ${sc.height}">` +
    `<rect width="${sc.width}" height="${sc.height}" fill="${(sc.backgrounds && sc.backgrounds[sc.scheme]) || sc.background || state.roles.canvas}"/>` +
    (sc.decor || '') +
    body +
    '</svg>'
  );
}

export function downloadSvg(filename = 'composition.svg') {
  const markup = buildSvg();
  const blob = new Blob([markup], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
