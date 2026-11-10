"""Арт-директор: Composition Plan (LLM через Ollama) + детерминированный фолбэк.

Модель принимает СМЫСЛОВЫЕ и КОМПОЗИЦИОННЫЕ решения (что взять, что подложка /
акцент / атмосфера, что на что положить, что фокус, насколько кропнуть), но НЕ
задаёт пиксели. Геометрию, физику и правила гарантирует `layout.build(plan=...)`.

Схема плана:
    {
      "composition": "still_life" | "hero_scene",
      "focus": "<id>",
      "crop": "tight" | "normal",
      "objects": [
        {"id": "book",   "role": "surface"},
        {"id": "tablet", "role": "accent", "on": "notepad"},
        {"id": "lamp",   "role": "atmosphere"}
      ]
    }
"""

import json
import os

import httpx

from catalog import catalog
from layout import (
    PLATFORM_BASES, TOPPER_BASES, PAPER_BASES, PAPER_ACCENT_BASES,
    UPRIGHT_BASES, VOLUME_BASES, _base_id, _natural_size,
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2:3b")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "25"))

_ROLES = ("surface", "accent", "atmosphere", "hero")


def _base(oid):
    return _base_id(oid or "")


# ── аффордансы объектов (что чем может быть) ────────────────────────────────
def _affordance(base):
    if base in UPRIGHT_BASES:
        return "атмосфера (дальний план/сбоку, может уходить за кадр)"
    if base in PAPER_BASES:
        return "подложка-сцена (моно; на неё кладут акценты)"
    if base in PLATFORM_BASES:
        return "подложка ИЛИ акцент (может лежать на большей подложке)"
    if base in TOPPER_BASES or base in PAPER_ACCENT_BASES or base in VOLUME_BASES:
        return "акцент (цветной, кладётся НА подложку)"
    return "акцент"


def _catalog_text():
    lines = []
    for o in catalog.objects:
        if o["type"] != "main":
            continue
        b = _base(o["id"])
        lines.append("- %s (%s): %s; размер≈%d; теги: %s" % (
            b, o["name"], _affordance(b), int(_natural_size(o)),
            ", ".join(o["tags"])))
    return "\n".join(lines)


_RULES = (
    "Ты — арт-директор иллюстраций. По запросу выбери 3–6 объектов из каталога "
    "и составь композицию по правилам:\n"
    "- Цельность: одна сцена на общей плоскости, без «разрозненных карточек».\n"
    "- Соподчинение: ровно один смысловой центр (focus), он самый заметный.\n"
    "- Сначала сопоставь запрос (и ассоциации, если даны) с ТЕГАМИ объектов и выбирай "
    "только связанные по смыслу предметы.\n"
    "- Плоские объекты-подложки — монохромные; на них лежат ЦВЕТНЫЕ акценты.\n"
    "- Акцент кладётся только НА подложку (поле on), не на другой акцент.\n"
    "- Объёмные/вертикальные объекты — атмосфера на дальнем плане/сбоку, могут уходить за кадр.\n"
    "- Баланс и правило третей: центр не в геометрическом центре, оставляй «воздух».\n"
    "- Не перегружай: 2–4 акцента максимум; не больше двух подложек.\n"
    "- Для натюрморта (подложки с акцентами) ставь composition=still_life, crop=tight.\n"
    "Нельзя выдумывать id — только из каталога.\n"
)


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "composition": {"type": "string", "enum": ["still_life", "hero_scene"]},
        "focus": {"type": "string"},
        "crop": {"type": "string", "enum": ["tight", "normal"]},
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "role": {"type": "string",
                             "enum": ["surface", "accent", "atmosphere", "hero"]},
                    "on": {"type": ["string", "null"]},
                },
                "required": ["id", "role"],
            },
        },
    },
    "required": ["composition", "focus", "objects"],
}


def _candidate_text(candidates):
    lines = []
    for o in candidates:
        b = _base(o["id"])
        lines.append("- %s (%s): %s; теги: %s" % (
            b, o["name"], _affordance(b), ", ".join(o["tags"])))
    return "\n".join(lines)


def _prompt(query, candidates):
    return (
        _RULES +
        "ВАЖНО: выбирай ТОЛЬКО из списка кандидатов ниже, не добавляй других объектов.\n"
        "Объекты должны подходить запросу по смыслу (по тегам). Не повторяй примеры.\n\n"
        "Кандидаты (id (имя): роль; теги):\n" + _candidate_text(candidates) +
        '\n\nЗапрос: "' + query + '"\n\n'
        "Верни JSON по схеме: composition (still_life|hero_scene), focus (id из кандидатов —\n"
        "смысловой центр), crop (tight|normal), objects [{id, role (surface|accent|\n"
        "atmosphere|hero), on (id подложки; только для accent)}]. Выбери 3–4 объекта, если\n"
        "кандидатов достаточно (подложка + 2 акцента); атмосферу (лампа/ваза) добавь фоном."
    )


# ── нормализация/валидация плана ────────────────────────────────────────────
def _normalize_plan(raw, driver="llm", allowed=None):
    if not isinstance(raw, dict):
        return None
    items, seen = [], set()
    for it in raw.get("objects") or []:
        if isinstance(it, str):
            oid, role, on = it, "accent", None
        elif isinstance(it, dict):
            oid = it.get("id")
            role = it.get("role") or "accent"
            on = it.get("on")
        else:
            continue
        b = _base(oid)
        if not b or b in seen or not catalog.exists(b):
            continue
        if allowed is not None and b not in allowed:
            continue
        role = role if role in _ROLES else "accent"
        items.append({"id": b, "role": role, "on": _base(on) if on else None})
        seen.add(b)

    if not items:
        return None

    # не больше двух подложек
    surfaces = [it for it in items if it["role"] == "surface"]
    if len(surfaces) > 2:
        for it in surfaces[2:]:
            it["role"] = "accent"

    # атмосфера — это «стоящий» предмет или подложка-фон; плоский мелкий предмет
    # (паззл/монета/наушники) в роли атмосферы попал бы в угол монохромным пятном
    keep_atmo = UPRIGHT_BASES + PLATFORM_BASES + PAPER_BASES
    for it in items:
        if it["role"] == "atmosphere" and it["id"] not in keep_atmo:
            it["role"] = "accent"

    # ровно один hero
    heroes = [it for it in items if it["role"] == "hero"]
    if len(heroes) > 1:
        for it in heroes[1:]:
            it["role"] = "accent"

    # в сцене должен быть смысловой центр: если план сделал всё фоном
    # (ваза + лампа), героем становится самый крупный «главный» предмет
    if not any(it["role"] in ("hero", "accent", "surface") for it in items):
        def _rank(it):
            o = catalog.get(it["id"])
            return (1 if (o and o["type"] == "main") else 0, _natural_size(o))
        max(items, key=_rank)["role"] = "hero"

    # подложка без содержимого — не «сцена», а обычный предмет: иначе кадр
    # остаётся полностью монохромным (нет ни одного цветного пятна)
    if items and not any(it["role"] in ("hero", "accent") for it in items):
        surf = [it for it in items if it["role"] == "surface"]
        if surf:
            max(surf, key=lambda it: _natural_size(catalog.get(it["id"])))["role"] = "hero"

    # подложка не может лежать на другой подложке — просто снимаем «on»
    for it in items:
        if it.get("on") and it["role"] == "surface":
            it["on"] = None

    comp = raw.get("composition")
    comp = comp if comp in ("still_life", "hero_scene") else "hero_scene"
    if comp == "still_life":
        surf_ids = {it["id"] for it in items if it["role"] == "surface"}
        if not surf_ids or not any(it["role"] in ("accent", "hero") for it in items):
            comp = "hero_scene"

    # ремонт связей on (акцент должен лежать на подложке)
    surf_ids = {it["id"] for it in items if it["role"] == "surface"}
    for it in items:
        if it["role"] == "accent" and it["on"] not in surf_ids:
            it["on"] = None

    ids = {it["id"] for it in items}
    focus = _base(raw.get("focus"))
    if focus not in ids:
        cand = [it for it in items if it["role"] in ("accent", "hero")]
        if not cand:
            cand = [it for it in items if it["role"] == "surface"] or items
        focus = max(cand, key=lambda it: _natural_size(catalog.get(it["id"])))["id"]

    crop = raw.get("crop")
    crop = crop if crop in ("tight", "normal") else ("tight" if comp == "still_life" else "normal")

    return {"composition": comp, "focus": focus, "crop": crop,
            "objects": items, "driver": driver}


def _cap_plan(plan, limit):
    objs = plan.get("objects") or []
    if len(objs) <= limit:
        return plan
    focus = plan.get("focus")

    def prio(it):
        if it["id"] == focus or it["role"] == "hero":
            return 0
        if it["role"] == "surface":
            return 1
        if it["role"] == "accent":
            return 2
        return 3

    plan = dict(plan)
    plan["objects"] = sorted(objs, key=prio)[:limit]
    return plan


def _plan_ids(plan):
    return [it["id"] for it in plan["objects"]]


def _from_ollama(query, candidates):
    if not candidates:
        return None, "нет кандидатов"
    allowed = {_base(o["id"]) for o in candidates}
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            resp = client.post(
                OLLAMA_BASE_URL + "/api/generate",
                json={
                    "model": LLM_MODEL,
                    "prompt": _prompt(query, candidates),
                    "stream": False,
                    "format": _PLAN_SCHEMA,
                    "options": {"temperature": 0.1, "num_predict": 400},
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return None, "ollama недоступна: %s" % type(exc).__name__

    raw = (data.get("response") or "").strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return None, "невалидный JSON от модели"
    return _normalize_plan(parsed, driver="llm", allowed=allowed), None


# ── детерминированный фолбэк (аффордансы + правила) ─────────────────────────
def _fallback_ids(text):
    ids = catalog.theme_ids(text)
    if not ids:
        ids = catalog.apply_roles(catalog.keyword_ids(text))
    if not ids:
        ids = catalog.apply_roles(catalog.default_ids())
    return ids


def plan_from_ids(ids, driver="rules"):
    """Строит план по набору id (сценарий «по названиям» и фолбэк)."""
    orig, seen = [], set()
    for i in ids:
        b = _base(i)
        if b and catalog.exists(b) and b not in seen:
            seen.add(b)
            orig.append(i)
    if not orig:
        return None

    objs = [(catalog.get(_base(i)), i.endswith("_bg")) for i in orig]
    objs = [(o, bg) for o, bg in objs if o is not None]

    paper = [o for o, bg in objs if _base(o["id"]) in PAPER_BASES and not bg]
    surfaces = paper[:2]
    if not surfaces:
        surfaces = [o for o, bg in objs
                    if _base(o["id"]) in PLATFORM_BASES and not bg][:2]
    atmos = [o for o, bg in objs if bg or _base(o["id"]) in UPRIGHT_BASES]
    surf_ids = {_base(o["id"]) for o in surfaces}
    atm_ids = {_base(o["id"]) for o in atmos}
    accents = [o for o, bg in objs
               if _base(o["id"]) not in surf_ids
               and _base(o["id"]) not in atm_ids]

    # Если в наборе только «стоящие» предметы (ваза + лампа — нейтральный
    # фолбэк), героем становится главный из них: иначе в кадре нет цветового
    # центра и всё уходит в монохромный фон.
    if not surfaces and not accents and atmos:
        order = {_base(i): k for k, i in enumerate(orig)}
        mains = [o for o in atmos if o["type"] == "main"]
        if mains:
            pick = min(mains, key=lambda o: order.get(_base(o["id"]), 99))
            atmos.remove(pick)
            accents = [pick]

    # Подложка без содержимого — не «сцена», а обычный предмет: иначе весь
    # кадр остаётся монохромным (книга/планшет/кошелёк без акцентов)
    if surfaces and not accents:
        pick = max(surfaces, key=_natural_size)
        surfaces.remove(pick)
        accents = [pick]

    comp = "still_life" if (len(surfaces) >= 2 and accents) else "hero_scene"

    items = [{"id": _base(o["id"]), "role": "surface", "on": None} for o in surfaces]
    counts = {_base(o["id"]): 0 for o in surfaces}

    def host_for(o):
        if not surfaces:
            return None
        fit = [s for s in surfaces if _natural_size(s) >= _natural_size(o)]
        cand = [s for s in fit if _base(s["id"]) in PAPER_BASES] or fit or surfaces
        cand = sorted(cand, key=lambda s: (counts[_base(s["id"])], -_natural_size(s)))
        pick = cand[0]
        counts[_base(pick["id"])] += 1
        return _base(pick["id"])

    for o in accents:
        items.append({"id": _base(o["id"]), "role": "accent", "on": host_for(o)})
    for o in atmos:
        items.append({"id": _base(o["id"]), "role": "atmosphere", "on": None})

    pool = accents or surfaces or [o for o, _ in objs]
    focus = max(pool, key=_natural_size)["id"] if pool else orig[0]
    return {"composition": comp, "focus": _base(focus),
            "crop": "tight" if comp == "still_life" else "normal",
            "objects": items, "driver": driver, "ids": orig}


def _candidates(orig):
    seen, out = set(), []
    for i in orig:
        b = _base(i)
        if b and catalog.exists(b) and b not in seen:
            seen.add(b)
            out.append(b)
    return [catalog.get(b) for b in out][:8]


# Нейтральный минималистичный набор для запросов вне словаря: ваза на переднем
# плане + лампа фоном. Никаких «папок»/случайных предметов.
_NEUTRAL_IDS = ("vase", "lamp")


def _semantic_ids(text):
    """Семантический шорт-лист из словаря: (ids, matched)."""
    ids = catalog.theme_ids(text)
    if ids:
        return ids, True
    ids = catalog.apply_roles(catalog.keyword_ids(text))
    if ids:
        return ids, True
    return [], False


def _associations(query):
    """LLM: 6–8 существительных-ассоциаций к запросу (для запросов вне словаря)."""
    prompt = (
        "Перечисли 6–8 коротких существительных-ассоциаций к запросу «" + query +
        "» (предметы, объекты, атмосфера). Только список через запятую, без пояснений."
    )
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as client:
            resp = client.post(
                OLLAMA_BASE_URL + "/api/generate",
                json={"model": LLM_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.2, "num_predict": 120}},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []
    txt = (data.get("response") or "").replace("\n", ",")
    words = []
    for part in txt.split(","):
        w = part.strip(" .;:!?—-–\"'«»()").lower()
        if 2 < len(w) < 30 and w not in words:
            words.append(w)
    return words


def _query_words(query):
    """Слова самого запроса (для сопоставления с ассоциациями/тегами)."""
    out = []
    for w in (query or "").lower().replace(",", " ").split():
        w = w.strip(" .;:!?—-–\"'«»()")
        if 2 < len(w) < 30 and w not in out:
            out.append(w)
    return out


def _ids_from_associations(words):
    """Сопоставляет ассоциации с объектами по словарю «ассоциация → id» и тегам."""
    assoc = catalog.synonyms.get("associations", {}) or {}
    out, seen = [], set()

    def add(i):
        b = _base(i)
        if catalog.exists(b) and b not in seen:
            seen.add(b)
            out.append(b)

    for w in words:
        for k, ids in assoc.items():
            if w == k or w.startswith(k) or (len(w) >= 4 and k.startswith(w)):
                for i in ids:
                    add(i)
        for o in catalog.objects:
            if o["type"] != "main":
                continue
            toks = [o["name"].lower()] + [t.lower() for t in o["tags"]]
            if any(t == w or (len(w) >= 4 and (w.startswith(t) or t.startswith(w)))
                   for t in toks):
                add(o["id"])
    return out


def plan(query, limit=6):
    """Возвращает {'plan','ids','source','reason'} — LLM-план или фолбэк."""
    orig, matched = _semantic_ids(query)

    if not matched:
        # 1) Пробуем собрать по ассоциациям: LLM → словарь «ассоциация→объект» → план
        words = _associations(query)
        ids = _ids_from_associations(list(words) + _query_words(query))
        if len(ids) >= 2:
            candidates = _candidates(ids)
            qtext = query + (" (ассоциации: %s)" % ", ".join(words[:8]) if words else "")
            llm, err = _from_ollama(qtext, candidates)
            if llm:
                present = {o["id"] for o in llm["objects"]}
                for o in candidates:
                    b = _base(o["id"])
                    if b in UPRIGHT_BASES and b not in present:
                        llm["objects"].append({"id": b, "role": "atmosphere", "on": None})
                        present.add(b)
                llm = _cap_plan(llm, limit)
                return {"plan": llm, "ids": _plan_ids(llm)[:limit], "source": "ollama",
                        "reason": "вне словаря — по ассоциациям: " + ", ".join(words[:6])}
            p = plan_from_ids(ids, driver="rules")
            if p:
                return {"plan": p, "ids": p["ids"][:limit], "source": "rules",
                        "reason": "вне словаря — ассоциации (план не получен)"}

        # 2) Совсем ничего не нашлось → нейтральная сцена: ваза + фон-лампа
        candidates = [catalog.get(i) for i in _NEUTRAL_IDS]
        llm, err = _from_ollama(query, candidates)
        crop = llm.get("crop") if isinstance(llm, dict) else None
        crop = crop if crop in ("tight", "normal") else "normal"
        fixed = {
            "composition": "hero_scene", "focus": "vase", "crop": crop,
            "objects": [
                {"id": "vase", "role": "hero", "on": None},
                {"id": "lamp", "role": "atmosphere", "on": None},
            ],
            "driver": "llm",
        }
        return {"plan": fixed, "ids": ["vase", "lamp"],
                "source": "ollama" if llm else "rules",
                "reason": "вне словаря — нейтральная сцена: ваза + фон-лампа"}

    candidates = _candidates(orig)
    llm, err = _from_ollama(query, candidates)
    if llm:
        # атмосфера (лампа/ваза) — безопасный фон: если модель её не взяла, добавляем
        present = {o["id"] for o in llm["objects"]}
        for o in candidates:
            b = _base(o["id"])
            if b in UPRIGHT_BASES and b not in present:
                llm["objects"].append({"id": b, "role": "atmosphere", "on": None})
                present.add(b)
        llm = _cap_plan(llm, limit)
        return {"plan": llm, "ids": _plan_ids(llm)[:limit], "source": "ollama",
                "reason": "арт-директор (LLM) собрал план из кандидатов"}

    p = plan_from_ids(orig[:limit] if len(orig) > limit else orig, driver="rules")
    if p:
        return {"plan": p, "ids": p["ids"][:limit], "source": "rules",
                "reason": "план по аффордансам (LLM: %s)" % (err or "n/a")}
    return {"plan": None, "ids": catalog.default_ids(), "source": "default",
            "reason": "набор по умолчанию"}
