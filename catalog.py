"""Каталог SVG-объектов, палитры и словарь синонимов."""

import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, "assets")
DATA = os.path.join(BASE, "data")

_WORD_RE = re.compile(r"[0-9a-zа-яё]+")

# «Атмосферные» объекты — по умолчанию используются как фоновые (моно).
# Только светильник «стоит на заднем фоне и светит»; наушники/ваза — главные.
AMBIENT_BASES = {"lamp"}


def _root_match(kw, words):
    """Слово запроса совпадает с ключом точно или по корню (первые 3 буквы)."""
    for w in words:
        if kw == w:
            return True
        if len(kw) >= 4 and len(w) >= 4 and kw[:3] == w[:3]:
            return True
    return False


def _words(text):
    return _WORD_RE.findall((text or "").lower())


def _collect(words, mapping):
    """Собирает id по словам: сначала точное совпадение, иначе — по корню.

    Для слова с точным совпадением («лампочка») корневое сопоставление
    не применяется, поэтому оно не задевает близкие слова («лампа»).
    Для слов без точного совпадения («бизнеса», «идеи») срабатывает
    сравнение по первым трём символам — простая поддержка словоформ.
    """
    out = []
    for w in words:
        exact = [kw for kw in mapping if kw == w]
        if exact:
            for kw in exact:
                out.extend(mapping[kw])
            continue
        for kw, ids in mapping.items():
            if " " not in kw and len(kw) >= 4 and len(w) >= 4 and kw[:3] == w[:3]:
                out.extend(ids)
    return out



def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Catalog:
    def __init__(self):
        lib = _load(os.path.join(ASSETS, "library", "library.json"))
        self.objects = lib["objects"]
        self.by_id = {o["id"]: o for o in self.objects}
        self.palettes = _load(os.path.join(DATA, "palettes.json"))
        self.synonyms = _load(os.path.join(DATA, "synonyms.json"))
        self._index = {}
        for o in self.objects:
            keywords = [o["id"], o["name"].lower()]
            keywords += [t.lower() for t in o["tags"]]
            for kw in keywords:
                if kw:
                    self._index.setdefault(kw, set()).add(o["id"])

    def get(self, oid):
        return self.by_id.get(oid)

    def exists(self, oid):
        return oid in self.by_id

    def keyword_ids(self, text):
        """Подбор по словарю синонимов и метафор (детерминированный фолбэк)."""
        words = _words(text)
        ids = _collect(words, self.synonyms.get("keywords", {}))
        ids += _collect(words, self.synonyms.get("metaphors", {}))
        return _dedupe(ids, self.by_id)

    def theme_ids(self, text):
        """Тематический подбор с ролями из data/synonyms.json → themes.

        Ключ темы сопоставляется по словам запроса (с учётом корней слов).
        Возвращает id, где фоновые объекты уже как `<id>_bg`.
        """
        words = _words(text)
        best, best_len = None, -1
        for key, spec in (self.synonyms.get("themes") or {}).items():
            kws = _words(key)
            if kws and all(_root_match(kw, words) for kw in kws) and len(kws) > best_len:
                best, best_len = spec, len(kws)
        return self._spec_ids(best) if best else []

    def _spec_ids(self, spec):
        out = []
        for b in spec.get("main", []):
            if b in self.by_id:
                out.append(b)
        for b in spec.get("background", []):
            bid = b if b.endswith("_bg") else b + "_bg"
            if bid in self.by_id:
                out.append(bid)
        return _dedupe(out, self.by_id)

    def apply_roles(self, ids):
        """Роль по умолчанию: атмосферные объекты становятся фоновыми (моно)."""
        out = []
        for i in ids:
            base = i[:-3] if i.endswith("_bg") else i
            if not i.endswith("_bg") and base in AMBIENT_BASES and (base + "_bg") in self.by_id:
                out.append(base + "_bg")
            else:
                out.append(i)
        return _dedupe(out, self.by_id)

    def default_ids(self):
        return [i for i in self.synonyms.get("default", []) if i in self.by_id]


def _dedupe(ids, valid):
    seen, out = set(), []
    for i in ids:
        if i in valid and i not in seen:
            seen.add(i)
            out.append(i)
    return out


catalog = Catalog()
