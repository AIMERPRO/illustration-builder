// Тонкая обёртка над JSON API бэкенда.

export async function fetchLibrary() {
  const res = await fetch('/api/library');
  if (!res.ok) throw new Error('Не удалось загрузить библиотеку (' + res.status + ')');
  return res.json();
}

export async function compose(payload) {
  const res = await fetch('/api/compose', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error('Ошибка сборки композиции (' + res.status + ')');
  return res.json();
}

export async function palette(colors) {
  const res = await fetch('/api/palette', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ colors }),
  });
  if (!res.ok) throw new Error('Ошибка разбора палитры (' + res.status + ')');
  return res.json();
}
