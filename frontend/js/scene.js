// Единое состояние приложения и операции над сценой.

import { lib } from './library.js';
import { schemeRoles, applyRoles } from './palette.js';

const listeners = new Set();
export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
export function emit() {
  for (const fn of listeners) fn();
}

export function emptyScene() {
  return {
    schema: 1, width: 600, height: 315, canvas: 'horizontal',
    size: 'l', scheme: 'bright', font: 'Inter, system-ui, sans-serif',
    items: [], resolved: null,
  };
}

export const state = {
  palettes: {},
  roles: {},
  scene: emptyScene(),
  selected: -1,
};

export function setPalettes(palettes) {
  state.palettes = palettes || {};
}

function normalizeScene(scene) {
  const base = emptyScene();
  const out = { ...base, ...scene };
  out.items = (scene.items || []).map((it, i) => ({
    id: it.id,
    name: it.name,
    type: it.type,
    x: Number(it.x) || 0,
    y: Number(it.y) || 0,
    scale: it.scale != null ? Number(it.scale) : 1,
    rot: Number(it.rot) || 0,
    z: it.z != null ? Number(it.z) : i,
    opacity: it.opacity != null ? Number(it.opacity) : 1,
    state: it.state || 'main',
    variant: it.variant || null,
  }));
  return out;
}

export function setScene(scene) {
  state.scene = normalizeScene(scene || emptyScene());
  state.selected = -1;
  if (state.scene.scheme && state.palettes[state.scene.scheme]) {
    applyScheme(state.scene.scheme, false);
  }
  emit();
}

export function clearScene() {
  state.scene = { ...emptyScene(), scheme: state.scene.scheme, size: state.scene.size };
  state.selected = -1;
  emit();
}

export function addObject(id) {
  const obj = lib.byId[id];
  if (!obj) return;
  const sc = state.scene;
  const n = sc.items.length;
  const maxZ = sc.items.reduce((m, it) => Math.max(m, it.z || 0), 0);
  const item = {
    id,
    name: obj.name,
    type: obj.type,
    x: sc.width / 2 + ((n % 5) - 2) * 46,
    y: sc.height / 2 + ((Math.floor(n / 5) % 3) - 1) * 46,
    scale: obj.type === 'background' ? 2.2 : 1.15,
    rot: 0,
    z: maxZ + 1,
    opacity: 1,
    state: obj.type === 'background' ? 'background' : 'main',
    variant: null,
  };
  sc.items.push(item);
  state.selected = sc.items.length - 1;
  emit();
}

export function select(index) {
  state.selected = index;
  emit();
}

export function deselect() {
  if (state.selected !== -1) {
    state.selected = -1;
    emit();
  }
}

export function removeSelected() {
  if (state.selected < 0 || state.selected >= state.scene.items.length) return;
  state.scene.items.splice(state.selected, 1);
  state.selected = Math.min(state.selected, state.scene.items.length - 1);
  emit();
}

export function duplicateSelected() {
  const it = state.scene.items[state.selected];
  if (!it) return;
  const maxZ = state.scene.items.reduce((m, o) => Math.max(m, o.z || 0), 0);
  const copy = { ...it, x: it.x + 34, y: it.y + 34, z: maxZ + 1 };
  state.scene.items.push(copy);
  state.selected = state.scene.items.length - 1;
  emit();
}

export function bringFront() {
  const it = state.scene.items[state.selected];
  if (!it) return;
  const maxZ = state.scene.items.reduce((m, o) => Math.max(m, o.z || 0), 0);
  it.z = maxZ + 1;
  emit();
}

export function sendBack() {
  const it = state.scene.items[state.selected];
  if (!it) return;
  const minZ = state.scene.items.reduce((m, o) => Math.min(m, o.z || 0), 0);
  it.z = minZ - 1;
  emit();
}

export function updateSelected(patch) {
  const it = state.scene.items[state.selected];
  if (!it) return;
  Object.assign(it, patch);
  emit();
}

export function setCanvas(canvas) {
  state.scene.canvas = canvas;
  emit();
}

export function setSize(size) {
  state.scene.size = size;
  emit();
}

export function setResolved(resolved) {
  state.scene.resolved = resolved;
}

export function applyScheme(name, notify = true) {
  const roles = schemeRoles(name, state.palettes);
  if (!roles) return;
  state.roles = roles;
  state.scene.scheme = name;
  applyRoles(roles);
  if (notify) emit();
}

export function setRole(role, value) {
  state.roles = { ...state.roles, [role]: value };
  applyRoles({ [role]: value });
  emit();
}

export function applyRolesObject(roles) {
  if (!roles) return;
  state.roles = { ...state.roles, ...roles };
  applyRoles(state.roles);
  emit();
}
