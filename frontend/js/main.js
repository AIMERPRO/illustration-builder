// Точка входа: загрузка библиотеки, инициализация холста и панелей.

import { loadLibrary } from './library.js';
import { initCanvas } from './canvas.js';
import { initUI, renderLibrary, setStatus } from './ui.js';
import { setPalettes, applyScheme } from './scene.js';

async function boot() {
  initCanvas();

  try {
    const data = await loadLibrary();
    setPalettes(data.palettes);
    applyScheme('bright', false);

    initUI();
    renderLibrary('');
    // ничего не собираем сами: ждём метафору от пользователя
    setStatus('Введите метафору и нажмите «Собрать» — или добавьте объекты из библиотеки.');
  } catch (err) {
    console.error(err);
    setStatus('Не удалось загрузить библиотеку: ' + err.message);
  }
}

boot();
