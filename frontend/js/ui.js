// Панели управления: библиотека, палитра, сборка, свойства выделенного объекта.

import {
  state, onChange, applyScheme, setRole, applyRolesObject, addObject,
  removeSelected, duplicateSelected, bringFront, sendBack,
  setSize, clearScene, setScene, deselect, updateSelected, setCanvas,
} from './scene.js';
import { lib, innerMarkup } from './library.js';
import {
  ROLE_ORDER, ROLE_LABELS, imageToColors, deriveRolesFromColors, schemeName,
} from './palette.js';
import * as api from './api.js';
import { downloadSvg } from './export.js';

let els = {};

export function setStatus(text) {
  if (els.status) els.status.innerHTML = text;
}

export function initUI() {
  const $ = (id) => document.getElementById(id);
  els = {
    input: $('compose-input'), size: $('size'), canvas: $('canvas'),
    emptyState: $('empty-state'),
    btnCompose: $('btn-compose'), btnClear: $('btn-clear'),
    libSearch: $('lib-search'), libList: $('lib-list'),
    schemeRow: $('scheme-row'), roles: $('roles'), paletteUpload: $('palette-upload'),
    palettePreview: $('palette-preview'),
    sizeToggle: $('size-toggle'), btnDup: $('btn-dup'), btnFront: $('btn-front'),
    btnBack: $('btn-back'), btnDel: $('btn-del'), btnExport: $('btn-export'),
    status: $('statusbar'),
    props: $('props'), propsName: $('props-name'), propsClose: $('props-close'),
    propsState: $('props-state'), rotRange: $('rot-range'), rotVal: $('rot-val'),
    scaleRange: $('scale-range'), scaleVal: $('scale-val'),
  };

  buildSchemes();
  buildRoles();
  bindCompose();
  bindToolbar();
  bindProps();
  bindPaletteUpload();

  els.libSearch.addEventListener('input', () => renderLibrary(els.libSearch.value));
  els.sizeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (btn) setSize(btn.dataset.size);
  });

  onChange(() => { syncControls(); updateProps(); updateStatus(); });
  syncControls();
  updateProps();
  updateStatus();
}

function buildSchemes() {
  els.schemeRow.innerHTML = '';
  for (const key of Object.keys(state.palettes)) {
    const btn = document.createElement('button');
    btn.dataset.scheme = key;
    btn.textContent = schemeName(key, state.palettes);
    if (key === state.scene.scheme) btn.classList.add('active');
    btn.addEventListener('click', () => {
      applyScheme(key);
      buildRoles();
    });
    els.schemeRow.appendChild(btn);
  }
}

function buildRoles() {
  els.roles.innerHTML = '';
  for (const role of ROLE_ORDER) {
    const wrap = document.createElement('label');
    wrap.className = 'role';
    const input = document.createElement('input');
    input.type = 'color';
    input.value = state.roles[role] || '#000000';
    input.dataset.role = role;
    input.addEventListener('input', () => setRole(role, input.value));
    const code = document.createElement('code');
    code.textContent = ROLE_LABELS[role] || role;
    wrap.append(input, code);
    els.roles.appendChild(wrap);
  }
}

function syncSchemeActive() {
  for (const btn of els.schemeRow.querySelectorAll('button')) {
    btn.classList.toggle('active', btn.dataset.scheme === state.scene.scheme);
  }
}

export function renderLibrary(filter = '') {
  if (!els.libList) return;
  const q = filter.trim().toLowerCase();
  const objects = lib.objects.filter((o) => {
    if (!q) return true;
    const hay = [o.id, o.name.toLowerCase(), ...o.tags.map((t) => t.toLowerCase())].join(' ');
    return hay.includes(q);
  });

  els.libList.innerHTML = '';
  if (!objects.length) {
    els.libList.innerHTML = '<div class="lib-empty">Ничего не найдено</div>';
    return;
  }
  for (const obj of objects) {
    const item = document.createElement('div');
    item.className = 'lib-item' + (obj.type === 'background' ? ' is-bg' : '');
    item.title = obj.name + ' — ' + obj.tags.join(', ');
    const thumb = document.createElement('div');
    thumb.innerHTML = innerMarkup(obj.id, 'l');
    const label = document.createElement('span');
    label.textContent = obj.name;
    item.append(thumb, label);
    item.addEventListener('click', () => addObject(obj.id));
    els.libList.appendChild(item);
  }
}

function bindCompose() {
  els.btnCompose.addEventListener('click', composeNow);
  els.canvas.addEventListener('change', () => setCanvas(els.canvas.value));
  els.btnClear.addEventListener('click', () => {
    clearScene();
    setStatus('Композиция очищена. Введите метафору или добавьте объекты из библиотеки.');
  });
  // Ctrl/Cmd+Enter в поле метафоры — быстрая сборка
  els.input.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') composeNow();
  });
}

let composeSeq = 0;

async function doCompose(payload, useButton) {
  const token = ++composeSeq;
  document.body.classList.add('composing');
  setStatus('Собираю композицию…');
  if (useButton) {
    els.btnCompose.disabled = true;
    els.btnCompose.textContent = 'Собираю…';
  }
  try {
    const scene = await api.compose(payload);
    if (token !== composeSeq) return;   // устаревший ответ — игнорируем
    setScene(scene);
    syncControls();
    const r = scene.resolved || {};
    const src = {
      ollama: 'Llama (Ollama)', rules: 'правила (фолбэк)',
      theme: 'тема', fallback: 'словарь', default: 'набор по умолчанию',
    }[r.source] || r.source || '—';
    setStatus(
      'Готово · объектов: <b>' + (scene.items ? scene.items.length : 0) + '</b>' +
      ' · подобрано: <b>' + (r.ids ? r.ids.join(', ') : '') + '</b>' +
      ' · источник: ' + src
    );
  } catch (err) {
    if (token === composeSeq) setStatus('Ошибка сборки: ' + err.message);
  } finally {
    if (token === composeSeq) {
      document.body.classList.remove('composing');
      if (useButton) {
        els.btnCompose.disabled = false;
        els.btnCompose.textContent = 'Собрать';
      }
    }
  }
}

function buildPayload() {
  return {
    query: els.input.value.trim(),
    size: els.size.value,
    canvas: els.canvas.value,
    scheme: state.scene.scheme || 'bright',
    limit: 6,
  };
}

async function composeNow() {
  const payload = buildPayload();
  if (!payload.query) {
    // без метафоры ничего не собираем — подсвечиваем поле и подсказываем
    setStatus('Сначала введите метафору — например «уютный вечер с музыкой и светом».');
    els.input.focus();
    return;
  }
  await doCompose(payload, true);
}

function bindToolbar() {
  els.btnDup.addEventListener('click', duplicateSelected);
  els.btnDel.addEventListener('click', removeSelected);
  els.btnFront.addEventListener('click', bringFront);
  els.btnBack.addEventListener('click', sendBack);
  els.btnExport.addEventListener('click', exportNow);
}

function bindProps() {
  els.propsClose.addEventListener('click', deselect);
  els.propsState.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (btn) updateSelected({ state: btn.dataset.state });
  });
  els.rotRange.addEventListener('input', () => {
    const v = Number(els.rotRange.value);
    els.rotVal.textContent = Math.round(v) + '°';
    updateSelected({ rot: v });
  });
  els.scaleRange.addEventListener('input', () => {
    const v = Number(els.scaleRange.value);
    els.scaleVal.textContent = v + '%';
    updateSelected({ scale: v / 100 });
  });
}

function bindPaletteUpload() {
  els.paletteUpload.addEventListener('change', async () => {
    const file = els.paletteUpload.files && els.paletteUpload.files[0];
    if (!file) return;
    setStatus('Разбираю палитру из изображения…');
    try {
      const colors = await imageToColors(file);
      renderPreview(colors);
      let roles = deriveRolesFromColors(colors);
      try {
        const res = await api.palette(colors);
        if (res.roles && Object.keys(res.roles).length) roles = res.roles;
      } catch (e) { /* используем локальный маппинг */ }
      applyRolesObject(roles);
      buildRoles();
      els.paletteUpload.value = '';
      setStatus('Палитра загружена — композиция перекрашена (' + colors.length + ' цветов).');
    } catch (err) {
      setStatus('Не удалось разобрать картинку: ' + err.message);
    }
  });
}

function renderPreview(colors) {
  els.palettePreview.innerHTML = '';
  for (const color of colors) {
    const dot = document.createElement('i');
    dot.style.background = color;
    els.palettePreview.appendChild(dot);
  }
}

export function exportNow() {
  if (!state.scene.items.length) {
    setStatus('Скачивать пока нечего: сначала соберите композицию.');
    return;
  }
  downloadSvg('illustration.svg');
}

function syncControls() {
  els.size.value = state.scene.size;
  els.canvas.value = state.scene.canvas || 'horizontal';
  for (const btn of els.sizeToggle.querySelectorAll('button')) {
    btn.classList.toggle('active', btn.dataset.size === state.scene.size);
  }
  syncSchemeActive();
}

function updateProps() {
  const it = state.scene.items[state.selected];
  if (!it) {
    els.props.hidden = true;
    return;
  }
  els.props.hidden = false;
  els.propsName.textContent = it.name || it.id;
  for (const btn of els.propsState.querySelectorAll('button')) {
    btn.classList.toggle('active', btn.dataset.state === it.state);
  }
  els.rotRange.value = Math.round(it.rot);
  els.rotVal.textContent = Math.round(it.rot) + '°';
  els.scaleRange.value = Math.round(it.scale * 100);
  els.scaleVal.textContent = Math.round(it.scale * 100) + '%';
}

function syncEmptyState() {
  if (!els.emptyState) return;
  els.emptyState.hidden = state.scene.items.length > 0;
}

function updateStatus() {
  syncEmptyState();
  if (!els.status) return;
  const sc = state.scene;
  const r = sc.resolved;
  const parts = ['Объектов: <b>' + sc.items.length + '</b>'];
  if (r && r.ids && r.ids.length) parts.push('подбор: <b>' + r.ids.join(', ') + '</b>');
  if (r && r.source) parts.push('источник: ' + r.source);
  els.status.innerHTML = parts.join(' · ');
}

