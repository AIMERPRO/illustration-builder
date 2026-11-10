"""Детерминированная компоновка сцены 600x315 из выбранных объектов.

Алгоритм только расставляет готовые объекты (позиция, масштаб, поворот,
порядок слоёв) — ничего не генерирует и не меняет сами объекты.
"""

import base64
import hashlib
import json
import math
import os
import random

from catalog import catalog

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 600, 315
# Целевое заполнение кадра объектами (1 - это и есть допустимая доля фона).
COVERAGE_TARGET = 0.60
# Минимальная доля видимости главного объекта: ниже — кроп останавливаем (не теряем инфо).
MIN_MAIN_VISIBILITY = 0.88
# Допустимое перекрытие между главными объектами (доля меньшего bbox).
MAX_MAIN_OVERLAP = 0.12
FONT = "Inter, 'Segoe UI', system-ui, sans-serif"

MAIN_SCALE = {"l": 1.25, "s": 0.95}
BG_SCALE = {"l": 2.4, "s": 2.0}

# Форматы холста (как в примерах Figma): горизонтальный / квадратный / вертикальный.
CANVASES = {
    "horizontal": (600, 315),
    "square": (540, 540),
    "vertical": (540, 675),
}

# ── Семантика объектов для формата «Экспозиция» ──────────────────────────────
# Плоские объекты лежат на плоскости — на них можно положить другой предмет.
PLATFORM_BASES = ("book", "notepad", "tablet", "wallet")
# Акценты, которые кладутся НА плоскую поверхность (монета/карандаш).
TOPPER_BASES = ("coin", "pencil")
# «Бумажные» поверхности: на них логично положить паззл-акцент (книга/блокнот).
PAPER_BASES = ("book", "notepad")
PAPER_ACCENT_BASES = ("puzzle",)
# Подложки-сцены натюрморта (книга/блокнот) и «весомое» содержимое, из-за
# которого подложка становится монохромной, а содержимое — цветными акцентами.
STAGE_BASES = ("book", "notepad")
SUBSTANTIAL_CONTENT = ("puzzle", "pencil", "tablet", "wallet", "headphones")
# Объекты, которые стоят вертикально (атмосфера): лампа и ваза.
UPRIGHT_BASES = ("lamp", "vase")
# Объёмные предметы, которые могут и стоять, и лежать на плоскости (наушники).
VOLUME_BASES = ("headphones",)
# «Стоящие»/объёмные: атмосфера сзади и приоритетные герои (в отличие от подложек).
STANDING_BASES = UPRIGHT_BASES + VOLUME_BASES
# Во сколько раз «в реальности» объекты отличаются по размеру: библиотека
# нормализует каждый SVG до 190 px, поэтому относительный размер берём из
# исходных width/height манифеста (иначе монета и книга одного размера).
NATURAL_SIZE = {}          # id -> исходный max(width, height)
for _o in catalog.objects:
    NATURAL_SIZE[_o["id"]] = max(float(_o.get("width") or 200.0),
                                 float(_o.get("height") or 200.0))

# Фон не должен перевешивать героя: площадь его видимой части ограничена.
ATMO_AREA_LIMIT = 1.10
# Маски «чернил» (см. scripts/make_masks.py): размер маски на бокс ассета 200×200.
_MASK_N = 64
_MASKS = None
# Отступ героя от края кадра (доля меньшей стороны).
HERO_PAD = 0.016

try:
    with open(os.path.join(BASE, "data", "templates.json"), encoding="utf-8") as _f:
        TEMPLATES = json.load(_f).get("formats", {})
except Exception:  # шаблоны опциональны
    TEMPLATES = {}


def _seed(ids, size):
    raw = ("\u0001".join(ids) + "|" + size).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest(), 16)






def _mk(o, x, y, scale, rot, z, opacity=1.0):
    return {
        "id": o["id"],
        "name": o["name"],
        "type": o["type"],
        "x": round(x, 1),
        "y": round(y, 1),
        "scale": round(scale, 3),
        "rot": round(rot, 1),
        "z": z,
        "opacity": round(opacity, 2),
        "state": "background" if o["type"] == "background" else "main",
        "ink": _ink_frac(o),
    }




def _content_size(o):
    """Размер контента объекта в нормализованной коробке 200×200."""
    wn = float(o.get("width") or 200)
    hn = float(o.get("height") or 200)
    s0 = 190.0 / max(wn, hn)
    return wn * s0, hn * s0




def _base_id(oid):
    return oid[:-3] if oid.endswith("_bg") else oid


# Видимая («чернильная») часть ассета внутри его КОНТЕНТНОГО бокса, доли 0..1.
# Библиотека нормализует SVG в бокс 200×200 и обрезает рисунок вплотную, поэтому
# видимая часть совпадает с контентным боксом. Таблица оставлена для точечных
# исключений (старые/неужатые ассеты), по умолчанию — весь бокс.
INK_FRAC = {}


def _ink_frac(o):
    return INK_FRAC.get(_base_id(o["id"]), (0.0, 0.0, 1.0, 1.0))


def _natural_size(o):
    return NATURAL_SIZE.get(o["id"]) or _natural_size_fallback(o)


def _natural_size_fallback(o):
    return max(float(o.get("width") or 200.0), float(o.get("height") or 200.0))


def _content_layout(stage_units, stage_pos, w_of, h_of):
    """Содержимое на «лице» подложки: страницы открытой книги или внутренняя сетка.

    Для книги акценты кладутся на страницы (пазл — левая, ручка — правая) и
    масштабируются под зону страницы, чтобы не свисать с краёв. Для остальных
    подложек — сетка 2×2 по диагонали, каждый предмет вписывается в свою ячейку.
    Возвращает (объект, cx, cy, fit), где fit — дополнительный масштаб предмета.
    """
    out = []
    cells = [(0, 0), (1, 1), (0, 1)]
    for i, u in enumerate(stage_units):
        cx, floor, w, h = stage_pos[i]
        b = _base_id(u["base"]["id"])
        items = sorted(u["content"], key=lambda c: w_of(c) * h_of(c), reverse=True)
        if not items:
            continue
        left, top = cx - w / 2.0, floor - h
        if b in ("book", "book_bg"):
            items = items[:2]

            def put(c, zx, zy, zw, zh):
                """Акцент в зоне подложки; возвращает позиции и размеры."""
                fx0, fy0, fx1, fy1 = _ink_frac(c)
                w0, h0 = w_of(c), h_of(c)
                ink_w0, ink_h0 = w0 * (fx1 - fx0), h0 * (fy1 - fy0)
                fit_ = min(1.0, (zw * w) / ink_w0, (zh * h) / ink_h0)
                iw, ih = w0 * fit_, h0 * fit_
                ox_ = ((fx0 + fx1) / 2.0 - 0.5) * iw
                oy_ = ((fy0 + fy1) / 2.0 - 0.5) * ih
                ihw, ihh = iw * (fx1 - fx0) / 2.0, ih * (fy1 - fy0) / 2.0
                icx = max(left + ihw, min(left + w - ihw, left + zx * w))
                icy = max(top + ihh, min(floor - ihh, top + zy * h))
                return (c, icx - ox_, icy - oy_, fit_, icx, icy, ihw, ihh)

            # крупный предмет (например наушники) лежит НА книге целиком —
            # его нельзя втискивать в страницу, иначе он становится «игрушечным»
            big_nat = 0.5 * _natural_size(u["base"])
            bigs = [c for c in items if _natural_size(c) >= big_nat]
            if bigs:
                placed = [put(bigs[0], 0.50, 0.50, 0.94, 0.94)]
                rest = [c for c in items if c is not bigs[0]][:1]
                for c in rest:
                    for zx, zy in ((0.20, 0.74), (0.80, 0.22), (0.20, 0.24)):
                        cand = put(c, zx, zy, 0.30, 0.24)
                        if not any(abs(cand[4] - p[4]) < cand[6] + p[6]
                                   and abs(cand[5] - p[5]) < cand[7] + p[7]
                                   for p in placed):
                            placed.append(cand)
                            break
                out.extend((c, px, py, fit) for c, px, py, fit, *_ in placed)
                continue

            zones = [(0.36, 0.60, 0.34, 0.26),    # левая (нижняя) страница
                     (0.64, 0.33, 0.34, 0.26)]    # правая (верхняя) страница
            if len(items) == 1:
                # единственный предмет на книге занимает её целиком (он один)
                zones = [(0.50, 0.50, 0.94, 0.94)]
                order = [0]
            else:
                order = [0, 1]
            for idx, c in zip(order, items):
                zx, zy, zw, zh = zones[idx]
                cand = put(c, zx, zy, zw, zh)
                out.append((cand[0], cand[1], cand[2], cand[3]))
            continue

        pad = 0.14 * min(w, h)
        gw = max(1.0, (w - 2.0 * pad) / 2.0)
        gh = max(1.0, (h - 2.0 * pad) / 2.0)
        # сортируем по «натуральному» размеру: крупный предмет — первым (по центру)
        items = sorted(items, key=lambda c: _natural_size(c), reverse=True)
        # единственный предмет (или крупный на маленькой подложке) занимает её целиком,
        # иначе он становится «игрушечным» рядом с фоном
        big = bool(items) and _natural_size(items[0]) >= 0.5 * _natural_size(u["base"])
        if len(items) == 1 or big:
            gw, gh = (w - 2.0 * pad) * 0.98, (h - 2.0 * pad) * 0.98
        placed = []
        for idx, c in enumerate(items[:3]):
            fx0, fy0, fx1, fy1 = _ink_frac(c)
            w0, h0 = w_of(c), h_of(c)
            ink_w0, ink_h0 = w0 * (fx1 - fx0), h0 * (fy1 - fy0)
            col, row = cells[idx]
            fit = min(1.0, 0.9 * gw / ink_w0, 0.9 * gh / ink_h0)
            iw, ih = w0 * fit, h0 * fit
            ox = ((fx0 + fx1) / 2.0 - 0.5) * iw
            oy = ((fy0 + fy1) / 2.0 - 0.5) * ih
            ink_hw, ink_hh = iw * (fx1 - fx0) / 2.0, ih * (fy1 - fy0) / 2.0
            ink_cx = left + pad + (col + 0.5) * gw
            ink_cy = top + pad + (row + 0.5) * gh
            ink_cx = max(left + pad + ink_hw, min(cx + w / 2.0 - pad - ink_hw, ink_cx))
            ink_cy = max(top + pad + ink_hh, min(floor - pad - ink_hh, ink_cy))
            if any(abs(ink_cx - px) < ink_hw + pw and abs(ink_cy - py) < ink_hh + ph
                   for _, px, py, pw, ph, _ in placed):
                continue
            placed.append((c, ink_cx - ox, ink_cy - oy, ink_hw, ink_hh, fit))
        out.extend((c, px, py, fit) for c, px, py, _, _, fit in placed)
    return out




def _exposition_items(mains, bgs, size, rng, W=W, H=H, hero_id=None,
                      main_ids=None, bg_ids=None):
    """Экспозиция — сцена с глубиной и одним смысловым центром.

    Детерминированная логика (роли не выбираются случайно):
    - герой — самый крупный смысловой объект; он единственный доминирующий
      цветной объект и ставится в точку третей;
    - стоящие объекты (лампа/ваза/наушники) → монохромная атмосфера сзади;
    - плоские (книга/блокнот/планшет/кошелёк) и прочие → монохромные подложки;
    - мелкие (монета/карандаш) → цветные акценты на герое/подложке;
    - физичный поворот: стоящие почти ровно, лежащие с лёгким наклоном,
      карандаш лежит под небольшим углом (ведёт взгляд к герою);
    - кроп в центр внимания: масштаб считается по герою (он доминирует),
      герой держится в точке третей с отступом от краёв, фон может уходить
      за кадр — это и есть «кроп»;
    - пропорции объектов сохранены (единый базовый масштаб × натуральный размер).
    """
    # ── дедупликация по базовому id (main-вариант приоритетнее bg) ──────────
    comp = {}
    for o in list(bgs) + list(mains):
        bid = _base_id(o["id"])
        prev = comp.get(bid)
        if prev is None or (prev["type"] != "main" and o["type"] == "main"):
            comp[bid] = o
    objs = list(comp.values())
    if not objs:
        return []

    def base(o):
        return _base_id(o["id"])

    ref = max(_natural_size(o) for o in objs) or 1.0

    def ns(o):
        return _natural_size(o) / ref

    def w_of(o):
        return _content_size(o)[0] * ns(o)

    def h_of(o):
        return _content_size(o)[1] * ns(o)

    def area(o):
        return float(o.get("width") or 200.0) * float(o.get("height") or 200.0)

    # ── категории объектов ──────────────────────────────────────────────────
    # Акценты: монета/карандаш — всегда; паззл — только если в наборе есть
    # «бумажная» подложка (книга/блокнот). Иначе паззл ведёт себя как обычный
    # плоский объект — иначе он слишком крупно ложится на планшет/кошелёк.
    paper_hosts = [o for o in objs if base(o) in PAPER_BASES]
    accent_bases = set(TOPPER_BASES)
    if paper_hosts:
        accent_bases |= set(PAPER_ACCENT_BASES)

    def is_topper(o):
        return base(o) in accent_bases

    def is_standing(o):
        return base(o) in STANDING_BASES

    def is_flat(o):
        return not is_topper(o) and not is_standing(o)

    toppers = [o for o in objs if is_topper(o)]
    standing = [o for o in objs if is_standing(o)]
    flat = [o for o in objs if is_flat(o)]

    # ── смысловой центр: самый крупный «главный» объект ─────────────────────
    # Подсказка резолвера «главный/фоновый» — лишь слабый приоритет, поэтому
    # роль не болтается от случайного ответа LLM: всегда есть один герой.
    pool = [o for o in objs if not is_topper(o)] or list(objs)

    def hero_weight(o):
        b = base(o)
        hint = 1.0 if o["type"] == "main" else 0.02
        # Приоритет смыслового центра:
        # - наушники — переносной предмет, хороший герой (можно положить на книгу);
        # - лампа/ваза — скорее атмосфера, поэтому уступают;
        # - плоские (книга/блокнот/планшет/кошелёк/паззл) — природные «подложки».
        if b in VOLUME_BASES:
            cls = 1.0
        elif b in UPRIGHT_BASES:
            cls = 0.2
        else:
            cls = 0.35
        return area(o) * hint * cls

    hero = max(pool, key=hero_weight)
    if hero_id:
        forced = next((o for o in objs if base(o) == _base_id(hero_id)), None)
        if forced is not None:
            hero = forced

    def wants_main(o):
        b = base(o)
        if bg_ids and b in bg_ids:
            return False
        if main_ids and b in main_ids:
            return True
        return b == base(hero) or is_topper(o)

    def render_obj(o):
        """Какой вариант объекта рендерить: цветной (main) или моно (`_bg`)."""
        if wants_main(o):
            return o if o["type"] == "main" else (catalog.get(base(o)) or o)
        return catalog.get(base(o) + "_bg") or o

    # ── юниты: герой, стоящие, плоские и одиночные акценты ──────────────────
    units = [{"base": hero, "toppers": [], "hero": True}]
    seen = {base(hero)}
    for o in standing + flat:
        if base(o) in seen:
            continue
        units.append({"base": o, "toppers": [], "hero": False})
        seen.add(base(o))

    # акценты кладутся на плоскую поверхность (в приоритете — на героя)
    flat_units = [u for u in units if is_flat(u["base"])]
    paper_units = [u for u in flat_units if base(u["base"]) in PAPER_BASES]
    leftover = []
    for i, t in enumerate(toppers):
        if base(t) == base(hero) and is_topper(hero):
            continue
        hosts = paper_units if base(t) in PAPER_ACCENT_BASES else flat_units
        if hosts:
            hosts[i % len(hosts)]["toppers"].append(t)
        else:
            leftover.append(t)
    for t in leftover:
        if base(t) in seen:
            continue
        units.append({"base": t, "toppers": [], "hero": False, "topper_only": True})
        seen.add(base(t))

    # ── порядок по глубине: атмосфера → подложки → герой → акценты ──────────
    def depth(u):
        if u.get("topper_only"):
            return 2
        if u["hero"]:
            return 2
        if is_standing(u["base"]):
            return 0
        return 1

    units.sort(key=lambda u: (depth(u), -area(u["base"])))
    n = len(units)

    avg_w = sum(w_of(u["base"]) for u in units) / n
    avg_h = sum(h_of(u["base"]) for u in units) / n
    # Плотность раскладки зависит от формата: в портрете/квадрате — диагональ,
    # заполняющая высоту; в горизонтали — ряд с лёгкой глубиной.
    if W > H * 1.05:            # горизонтальный
        stepx, stepy = 0.72 * avg_w, 0.18 * avg_h
    elif W < H * 0.95:          # вертикальный
        stepx, stepy = 0.55 * avg_w, 0.52 * avg_h
    else:                       # квадратный
        stepx, stepy = 0.62 * avg_w, 0.40 * avg_h

    # «пол» в мировых единицах: (cx, floor, w, h)
    floor_pos, cursor = [], 0.0
    for i, u in enumerate(units):
        w, h = w_of(u["base"]), h_of(u["base"])
        floor_pos.append((cursor + w / 2.0, i * stepy, w, h))
        cursor += stepx

    minx = min(p[0] - p[2] / 2.0 for p in floor_pos)
    maxx = max(p[0] + p[2] / 2.0 for p in floor_pos)
    miny = min(p[1] - p[3] for p in floor_pos)
    maxy = max(p[1] for p in floor_pos)
    bw, bh = maxx - minx, maxy - miny

    hero_i = next(i for i, u in enumerate(units) if u["hero"])
    hcx, hfloor, hw, hh = floor_pos[hero_i]
    hcy = hfloor - hh / 2.0

    # ── правило третей по горизонтали + асимметричный баланс ────────────────
    # Герой — последний в ряду (справа), поэтому ставим его на правую треть,
    # а подложки/атмосферу — на левую. Один объект — по центру (устойчивый кадр).
    fx = (2.0 / 3.0) if n > 1 else 0.5
    focal_x = fx * W
    margin = max(18.0, 0.06 * min(W, H))

    # ── масштаб: герой доминирует, но кадр не «взрывается» ──────────────────
    S_hero = (0.68 * H if W >= H else 0.56 * W) / (hh or 1.0)
    S_contain = min(W / (bw or 1.0), H / (bh or 1.0))
    # Высокий объёмный фон сзади не режем больше чем на ~10%: иначе он «выпадает»
    # из кадра и разрушает цельность сцены.
    tall_standing = [h_of(u["base"]) for u in units
                     if not u["hero"] and is_standing(u["base"])]
    S_atmo = (0.92 * H / max(tall_standing)) if tall_standing else 1e9
    S = min(
        S_hero,
        1.45 * S_contain,
        S_atmo,
        W / (bw or 1.0),
        2.0 * (focal_x - margin) / (hw or 1.0),
        2.0 * (W - margin - focal_x) / (hw or 1.0),
    )
    S = max(0.05, S)
    if size == "s":
        S *= 0.92

    # ── горизонтальное кадрирование: мягкое правило третей ──────────────────
    # Хотим героя на правой трети, но не ценой того, что ряд «уезжает» из кадра
    # (иначе атмосфера слева пропадает). Если треть не помещается — центрируем ряд.
    offx = (W - S * bw) / 2.0
    ideal = focal_x - S * (hcx - minx)
    bleed = 0.03 * W
    if ideal >= -bleed and ideal + S * bw <= W + bleed:
        offx = ideal
    # герой целиком в кадре с отступом (воздух и поля)
    hero_left = offx + S * (hcx - hw / 2.0 - minx)
    hero_right = offx + S * (hcx + hw / 2.0 - minx)
    if hero_left < margin:
        offx += margin - hero_left
    if hero_right > W - margin:
        offx -= hero_right - (W - margin)

    # ── вертикальное кадрирование: кроп в центр внимания ────────────────────
    comp_h = bh * S
    if comp_h >= H - 2.0 * margin:
        offy = H - margin - comp_h          # низ сцены к низу кадра, кроп сверху
    else:
        offy = (H - comp_h) * 0.5           # иначе воздух сверху и снизу поровну
    # герой всегда целиком в кадре с отступом (воздух и поля)
    hero_top = offy + S * (hcy - hh / 2.0 - miny)
    hero_bot = offy + S * (hcy + hh / 2.0 - miny)
    if hero_top < margin:
        offy += margin - hero_top
    if hero_bot > H - margin:
        offy -= hero_bot - (H - margin)

    # ── физичный поворот ────────────────────────────────────────────────────
    def phys_rot(o, toward=None):
        b = base(o)
        if b in UPRIGHT_BASES:
            return round(rng.uniform(-1.0, 1.0), 1)          # стоит ровно
        if b in VOLUME_BASES:
            return round(rng.uniform(-14.0, 14.0), 1)         # объёмник лежит/повёрнут
        if b == "pencil":
            if toward is not None:
                a = ((toward + 90.0) % 180.0) - 90.0
                a = max(-24.0, min(24.0, a))
                return round(a if abs(a) >= 5.0 else rng.choice([-8.0, 8.0]), 1)
            return round(rng.choice([-15.0, -10.0, 10.0, 15.0]), 1)
        if b == "coin":
            return round(rng.uniform(-22.0, 22.0), 1)         # круглая — наклон декоративный
        return round(rng.uniform(-6.0, 6.0), 1)               # лежит: лёгкий наклон

    # ── сборка items ────────────────────────────────────────────────────────
    items, z = [], 0
    for i, u in enumerate(units):
        o = u["base"]
        cx_c, floor_c, w_c, h_c = floor_pos[i]
        center_c = floor_c - h_c / 2.0
        px = offx + S * (cx_c - minx)
        py = offy + S * (center_c - miny)
        items.append(_mk(render_obj(o), px, py, S * ns(o), phys_rot(o), z))
        z += 1

        k = len(u["toppers"])
        for j, t in enumerate(u["toppers"]):
            th = h_of(t)
            tx = cx_c + (j - (k - 1) / 2.0) * 0.22 * w_c
            ty = center_c - h_c * 0.12 - th * 0.10
            toward = None
            if not u["hero"]:
                toward = math.degrees(math.atan2(hcy - ty, hcx - tx))
            items.append(_mk(render_obj(t), offx + S * (tx - minx),
                             offy + S * (ty - miny), S * ns(t),
                             phys_rot(t, toward), z))
            z += 1

    return items



def _still_life_from_plan(surfaces, accents, atmos, plan, size, rng, W, H):
    """Натюрморт по явному плану: surfaces=[(obj,item)], accents=[(obj,item)]."""
    def base(o):
        return _base_id(o["id"])

    all_objs = [o for o, _ in surfaces] + [o for o, _ in accents] + list(atmos)
    fg = [o for o, _ in surfaces] + [o for o, _ in accents]
    ref = max(_natural_size(o) for o in (fg or all_objs)) or 1.0

    def ns(o):
        # фон не крупнее самого большого предмета переднего плана: иначе фокус
        # уезжает на лампу/вазу (она занимает полкадра монохромным пятном)
        v = _natural_size(o) / ref
        return min(v, 1.0) if any(o is a for a in atmos) else v

    def w_of(o):
        return _content_size(o)[0] * ns(o)

    def h_of(o):
        return _content_size(o)[1] * ns(o)

    def area(o):
        return float(o.get("width") or 200.0) * float(o.get("height") or 200.0)

    stages = sorted([o for o, _ in surfaces], key=area, reverse=True)
    if not stages:
        return None
    stage_units = [{"base": s, "content": []} for s in stages]
    by_base = {base(u["base"]): u for u in stage_units}
    primary = max(stage_units, key=lambda u: area(u["base"])) if stage_units else None
    small_accents = set(TOPPER_BASES) | set(PAPER_ACCENT_BASES)
    for o, it in accents:
        host = by_base.get(_base_id(it.get("on") or ""))
        if (base(o) in small_accents and primary is not None
                and _natural_size(primary["base"]) >= _natural_size(o)):
            host = primary          # мелочь — на главную подложку (книгу)
        if host is not None and _natural_size(host["base"]) < 0.9 * _natural_size(o):
            # подложка заметно мельче предмета: на ней он выглядел бы «игрушечным»,
            # поэтому ищем более крупную подложку (или уходим в другую раскладку)
            host = None
        if host is None:
            fit = [u for u in stage_units if _natural_size(u["base"]) >= _natural_size(o)]
            if not fit:
                return None
            host = sorted(fit, key=area, reverse=True)[0]
        host["content"].append(o)

    # подложка без содержимого — просто пустое пятно (например, блокнот, на который
    # ничего не положили): убираем её, чтобы в центре не было «дыры»
    if len(stage_units) > 1:
        stage_units = [u for u in stage_units if u["content"]] or stage_units[:1]
    if not any(u["content"] for u in stage_units):
        return None      # натюрморт без содержимого — пустой центр; отдаём другой раскладке

    avg_w = sum(w_of(u["base"]) for u in stage_units) / len(stage_units)
    avg_h = sum(h_of(u["base"]) for u in stage_units) / len(stage_units)
    gap_st, stepy = 0.10 * avg_w, 0.20 * avg_h
    stage_pos, cursor = [], 0.0
    for i, u in enumerate(stage_units):
        w, h = w_of(u["base"]), h_of(u["base"])
        stage_pos.append((cursor + w / 2.0, i * stepy, w, h))
        cursor += w + gap_st

    content_pos = _content_layout(stage_units, stage_pos, w_of, h_of)
    # объёмные предметы (наушники) — верхним слоем: их не накрывает подложка/акценты
    content_pos.sort(key=lambda c: 1 if _base_id(c[0]["id"]) in VOLUME_BASES else 0)

    if stage_pos:
        scx, sfloor, sw, sh = stage_pos[0]
    else:
        scx, sfloor, sw, sh = 0.0, 0.0, avg_w, avg_h
    atm_pos = []
    for i, a in enumerate(atmos):
        w, h = w_of(a), h_of(a)
        # атмосфера — за первой подложкой (лампа «выглядывает» сверху-слева)
        ax = scx - 0.32 * sw - i * 0.28 * w
        ay = sfloor - 0.55 * sh - h / 2.0
        atm_pos.append((a, ax, ay))

    xs, ys = [], []
    for (cx, floor, w, h) in stage_pos:
        xs += [cx - w / 2.0, cx + w / 2.0]
        ys += [floor - h, floor]
    for (c, cx, cy, fit) in content_pos:
        fx0, fy0, fx1, fy1 = _ink_frac(c)
        cw, ch = w_of(c) * fit, h_of(c) * fit
        x0, y0 = cx - cw / 2.0, cy - ch / 2.0
        xs += [x0 + fx0 * cw, x0 + fx1 * cw]
        ys += [y0 + fy0 * ch, y0 + fy1 * ch]
    # атмосфера — часть кадра: если её не учитывать при вписывании, после
    # кадрирования от лампы/вазы остаётся «полоска» у края
    for (a, cx, cy) in atm_pos:
        fx0, fy0, fx1, fy1 = _ink_frac(a)
        aw, ah = w_of(a), h_of(a)
        xs += [cx + (fx0 - 0.5) * aw, cx + (fx1 - 0.5) * aw]
        ys += [cy + (fy0 - 0.5) * ah, cy + (fy1 - 0.5) * ah]
    fw = (max(xs) - min(xs)) or 1.0
    fh = (max(ys) - min(ys)) or 1.0
    fcx, fcy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    crop = plan.get("crop", "tight")
    S = min(W / fw, H / fh)      # вписываем натюрморт целиком; докроп — в _ensure_coverage
    if size == "s":
        S *= 0.95
    offx, offy = W / 2.0 - S * fcx, H / 2.0 - S * fcy

    def phys_rot(o):
        b = base(o)
        if b in UPRIGHT_BASES:
            return round(rng.uniform(-1.0, 1.0), 1)
        if b in VOLUME_BASES:
            return round(rng.uniform(-12.0, 12.0), 1)
        if b == "pencil":
            return round(rng.choice([-16.0, -11.0, 11.0, 16.0]), 1)
        if b == "coin":
            return round(rng.uniform(-20.0, 20.0), 1)
        return round(rng.uniform(-6.0, 6.0), 1)

    def main_variant(o):
        return o if o["type"] == "main" else (catalog.get(base(o)) or o)

    def bg_variant(o):
        return catalog.get(base(o) + "_bg") or o

    items, z = [], 0
    for (a, cx, cy) in atm_pos:
        items.append(_mk(bg_variant(a), offx + S * cx, offy + S * cy,
                         S * ns(a), phys_rot(a), z))
        z += 1
    for i, u in enumerate(stage_units):
        cx, floor, w, h = stage_pos[i]
        items.append(_mk(bg_variant(u["base"]), offx + S * cx,
                         offy + S * (floor - h / 2.0), S * ns(u["base"]),
                         0.0, z))
        z += 1
    for (c, cx, cy, fit) in content_pos:
        it_ = _mk(main_variant(c), offx + S * cx, offy + S * cy,
                  S * ns(c) * fit, 0.0, z)
        if fit < 0.999:
            it_["fit"] = round(fit, 3)      # ужат «под страницу» — не «один мир»
        items.append(it_)
        z += 1
    return items






def _cell_scene_items(entries, plan, size, rng, W, H):
    """Сцена с общим («единым») масштабом объектов.

    Кадр подстраивается под предметы, а не наоборот: все объекты живут в одном
    масштабе и сохраняют натуральные пропорции друг относительно друга
    (лампа выше вазы, наушники крупнее монеты и т.п.). Атмосфера (лампа/ваза)
    ставится сзади/у края и может уходить за кадр как фон.
    """
    if not entries:
        return []

    def base(o):
        return _base_id(o["id"])

    def is_atmo(e):
        o, it = e
        if it.get("role") == "atmosphere":
            return True
        if it.get("role") in ("accent", "hero"):
            return False          # план явно сделал предмет смысловым центром
        return base(o) in UPRIGHT_BASES

    prio = {"surface": 0, "hero": 1, "accent": 2, "atmosphere": 3}
    entries = sorted(entries, key=lambda e: prio.get(e[1].get("role"), 2))
    back = [e for e in entries if is_atmo(e)]
    front = [e for e in entries if not is_atmo(e)]

    def main_variant(o):
        return o if o["type"] == "main" else (catalog.get(base(o)) or o)

    def bg_variant(o):
        return catalog.get(base(o) + "_bg") or o

    # ── ЕДИНЫЙ МАСШТАБ («один мир») ──────────────────────────────────────────
    # Ассеты нормализованы в коробку 190×190, поэтому вписывание каждого объекта
    # в свою ячейку ломает пропорции (ваза становится меньше наушников и т.п.).
    # Переводим всё в натуральные размеры (max(width,height) из библиотеки) и
    # держим ОДИН общий масштаб для всех объектов кадра: кадр подстраивается
    # под предметы, а не наоборот.
    ref = max(_natural_size(o) for o, _ in entries) or 1.0

    def ns(o):
        """Во сколько раз объект меньше самого крупного в наборе."""
        return _natural_size(o) / ref

    def unit_ink(o):
        """Размер видимой части при единичном общем масштабе."""
        ow, oh = _content_size(o)
        fx0, fy0, fx1, fy1 = _ink_frac(o)
        return (ow * (fx1 - fx0) * ns(o), oh * (fy1 - fy0) * ns(o))

    def fit_scale(o, rw, rh, pad=0.04):
        """Максимальный общий масштаб, при котором объект влезает в rw×rh."""
        iw, ih = unit_ink(o)
        return max(0.002, min(rw * (1 - 2 * pad) / iw, rh * (1 - 2 * pad) / ih))

    def place(o, it, cx, cy, S, z, rot=0.0):
        """Объект общего масштаба S; центр ВИДИМОЙ части — в (cx, cy)."""
        ow, oh = _content_size(o)
        fx0, fy0, fx1, fy1 = _ink_frac(o)
        s = S * ns(o)
        ox = ((fx0 + fx1) / 2.0 - 0.5) * ow * s
        oy = ((fy0 + fy1) / 2.0 - 0.5) * oh * s
        main = it.get("role") in ("accent", "hero")
        rend = main_variant(o) if main else bg_variant(o)
        return _mk(rend, cx - ox, cy - oy, s, rot, z)

    def phys_rot(o):
        """Физичный поворот: стоящее — почти ровно, объёмное — можно положить."""
        b = base(o)
        if b in UPRIGHT_BASES:
            return round(rng.uniform(-1.0, 1.0), 1)      # ваза/лампа стоят ровно
        if b in VOLUME_BASES:
            return round(rng.uniform(-7.0, 7.0), 1)      # наушники можно положить
        return round(rng.uniform(-3.0, 3.0), 1)          # плоское лежит с лёгким наклоном

    items, z = [], 0
    single = len(front) == 1

    if not front:
        # только атмосфера (план без героя): ставим колонками — единый масштаб,
        # без наездов; одиночный предмет — по центру
        cols = min(len(back), 2)
        rows = math.ceil(len(back) / cols)
        cell_w = W / cols
        cell_h = H * 0.92 / rows
        S = min(fit_scale(o, cell_w * 0.9, cell_h, 0.04) for o, _ in back)
        for i, (o, it) in enumerate(back):
            r, c = divmod(i, cols)
            cy = H * 0.04 + (r + 0.5) * cell_h
            items.append(place(o, it, (c + 0.5) * cell_w, cy, S, z, phys_rot(o)))
            z += 1
        return items

    if single:
        o, it = front[0]
        uw, uh = unit_ink(o)
        pad = HERO_PAD * min(W, H)
        padx = pady = pad
        # «Один мир»: фон живёт в ТОМ ЖЕ масштабе, что и герой. Композиция
        # подстраивается (фон рядом или сверху), а не масштаб фона.
        arot = [phys_rot(b) for b, _ in back]
        hrot = phys_rot(o)

        def atmo_at(tm, side):
            """Фон при общем масштабе tm: слева от героя или в левом верхнем углу."""
            out = []
            for j, (b, bt) in enumerate(back):
                itm = place(b, bt, 0.0, 0.0, tm, z + len(out), arot[j])
                bb = _item_bbox(itm)
                itm["x"] += padx - bb[0]
                if side:
                    itm["y"] += (H - (bb[3] - bb[1])) / 2.0 - bb[1]
                else:
                    itm["y"] += pady - bb[1]
                out.append(itm)
            return out

        def bbox_of(moved):
            box = None
            for m in moved:
                b = _item_bbox(m)
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                             max(box[2], b[2]), max(box[3], b[3]))
            return box

        # выбираем компоновку: фон сверху или сбоку — где общий масштаб больше
        if not back:
            S = min((W - 2.0 * pad) / uw, (H - 2.0 * pad) / uh)
            if size == "s":
                S *= 0.94
            items.append(place(o, it, W / 2.0, H / 2.0, S, z, hrot))
            return items

        gap = 0.018 * min(W, H)
        probe = _item_bbox(place(o, it, 0.0, 0.0, 1.0, z, hrot))
        pw, ph = probe[2] - probe[0], probe[3] - probe[1]
        paw = pah = 0.0
        for (b, bt), r in zip(back, arot):
            bb = _item_bbox(place(b, bt, 0.0, 0.0, 1.0, z, r))
            paw, pah = max(paw, bb[2] - bb[0]), max(pah, bb[3] - bb[1])
        s_stack = min((W - 2.0 * pad) / max(pw, paw, 1e-9),
                      (H - 2.0 * pad - gap) / max(ph + pah, 1e-9))
        s_side = min((W - 2.0 * pad - gap) / max(pw + paw, 1e-9),
                     (H - 2.0 * pad) / max(ph, pah, 1e-9))
        # при почти равных вариантах предпочитаем «фон сверху»: герой остаётся
        # по центру по горизонтали
        side = s_side > s_stack * 1.04
        S = max(s_stack, s_side) * 0.995
        if size == "s":
            S *= 0.94

        atmo = atmo_at(S, side)
        ab = bbox_of(atmo)
        hero = place(o, it, 0.0, 0.0, S, z + len(back), hrot)
        hb = _item_bbox(hero)
        hw_, hh_ = hb[2] - hb[0], hb[3] - hb[1]
        if side:
            # герой — справа от фона, по центру по вертикали
            hero["x"] += (ab[2] + gap) - hb[0]
            hero["y"] += (H - hh_) / 2.0 - hb[1]
        else:
            # герой — под фоном, по центру по горизонтали
            hero["x"] += (W - hw_) / 2.0 - hb[0]
            hero["y"] += (ab[3] + gap) - hb[1]
        items.extend(atmo)
        items.append(hero)
        return items

    # ── несколько объектов переднего плана: КЛАСТЕР вокруг центра ────────────
    # Не сетка по ячейкам: предметы должны читаться как одна композиция —
    # крупный предмет в центре, остальные плотно вокруг (с легким перекрытием),
    # фон (лампа/ваза) — позади в левом верхнем углу.
    order = sorted(front, key=lambda e: _natural_size(e[0]), reverse=True)
    rots = [phys_rot(o) for o, _ in order]
    aw, ah = unit_ink(order[0][0])          # «якорь» — самый крупный предмет
    # кольцо вокруг «якоря»: плотнее — значит предметы крупнее при том же кадре
    ring = [(0.00, 0.00), (0.28, -0.18), (-0.27, 0.16), (0.26, 0.22),
            (-0.25, -0.19), (0.02, 0.28), (0.02, -0.27)]
    pad = 0.02 * min(W, H)

    # объёмные предметы (наушники) — ВСЕГДА верхним слоем: их никто не накрывает
    zmap, nxt = {}, z
    for i, (o, _) in enumerate(order):
        if _base_id(o["id"]) not in VOLUME_BASES:
            zmap[i] = nxt
            nxt += 1
    for i, (o, _) in enumerate(order):
        if _base_id(o["id"]) in VOLUME_BASES:
            zmap[i] = nxt
            nxt += 1

    def cluster(S, z_off):
        """Кластер при общем масштабе S: предметы вокруг центра, bbox кластера."""
        out, xs, ys = [], [], []
        for i, (o, it) in enumerate(order):
            dx, dy = ring[i % len(ring)]
            itm = place(o, it, dx * aw * S, dy * ah * S, S, zmap[i] + z_off, rots[i])
            bb = _item_bbox(itm)
            out.append(itm)
            xs += [bb[0], bb[2]]
            ys += [bb[1], bb[3]]
        ox = (W - (max(xs) - min(xs))) / 2.0 - min(xs)
        oy = (H - (max(ys) - min(ys))) / 2.0 - min(ys)
        for itm in out:
            itm["x"] += ox
            itm["y"] += oy
        return out, (max(xs) - min(xs), max(ys) - min(ys))

    probe, (bw1, bh1) = cluster(1.0, 0)
    sv = min((W - 2.0 * pad) / max(bw1, 1e-9), (H - 2.0 * pad) / max(bh1, 1e-9))
    if size == "s":
        sv *= 0.95
    arot = [phys_rot(b) for b, _ in back]

    def atmo_size(scale):
        """Габариты фона при данном масштабе (общий «один мир»)."""
        w = h = 0.0
        for (b, bt), r in zip(back, arot):
            bb = _item_bbox(place(b, bt, 0.0, 0.0, scale, z, r))
            w = max(w, bb[2] - bb[0])
            h = max(h, bb[3] - bb[1])
        return w, h

    side = False
    S = sv
    atmo_k = 1.0
    if back:
        aw1, ah1 = atmo_size(1.0)
        # фон не крупнее самого большого предмета переднего плана: иначе лампа
        # занимает кадр монохромным пятном и отбирает место у цветных объектов
        f_area = 0.0
        for m in probe:
            b = _item_bbox(m)
            f_area = max(f_area, (b[2] - b[0]) * (b[3] - b[1]))
        if aw1 > 0 and ah1 > 0 and f_area > 0:
            atmo_k = min(1.0, math.sqrt(ATMO_AREA_LIMIT * f_area / (aw1 * ah1)))
        aw1, ah1 = aw1 * atmo_k, ah1 * atmo_k
        gap = 0.014 * min(W, H)
        # два варианта: фон слева от кластера или сверху; берём тот, где всё крупнее
        s_side = min((W - 2.0 * pad - gap) / max(bw1 + aw1, 1e-9),
                     (H - 2.0 * pad) / max(bh1, ah1, 1e-9))
        s_stack = min((W - 2.0 * pad) / max(bw1, aw1, 1e-9),
                      (H - 2.0 * pad - gap) / max(bh1 + ah1, 1e-9))
        side = s_side >= s_stack
        S = max(s_side, s_stack) * 0.995
    front_items, (fw, fh) = cluster(S, len(back))

    if back:
        pad2 = 0.022 * min(W, H)

        def atmo_at(Sa):
            """Фон целиком в углу (слева от кластера или над ним)."""
            out = []
            for j, (b, bt) in enumerate(back):
                itm = place(b, bt, 0.0, 0.0, Sa, z + j, arot[j])
                bb = _item_bbox(itm)
                if side:
                    itm["x"] += pad2 - bb[0]
                    itm["y"] += (H - (bb[3] - bb[1])) / 2.0 - bb[1]
                else:
                    itm["x"] += pad2 - bb[0]
                    itm["y"] += pad2 - bb[1]
                out.append(itm)
            return out

        # ставим кластер рядом с фоном: справа (side) или снизу
        def shift(moved, dx, dy):
            out = []
            for m in moved:
                m2 = dict(m)
                m2["x"] += dx
                m2["y"] += dy
                out.append(m2)
            return out

        atmo = atmo_at(S * atmo_k)
        ab = None
        for a_ in atmo:
            b = _item_bbox(a_)
            ab = b if ab is None else (min(ab[0], b[0]), min(ab[1], b[1]),
                                       max(ab[2], b[2]), max(ab[3], b[3]))
        fb = None
        for m in front_items:
            b = _item_bbox(m)
            fb = b if fb is None else (min(fb[0], b[0]), min(fb[1], b[1]),
                                       max(fb[2], b[2]), max(fb[3], b[3]))
        if ab and fb:
            if side:
                front_items = shift(front_items,
                                    (ab[2] + gap) - fb[0],
                                    (H - (fb[3] - fb[1])) / 2.0 - fb[1])
            else:
                front_items = shift(front_items,
                                    (W - (fb[2] - fb[0])) / 2.0 - fb[0],
                                    (ab[3] + gap) - fb[1])
        items.extend(atmo)

    items.extend(front_items)
    return items


def _plan_items(plan, size, rng, W, H):
    """Исполнитель Composition Plan: смысл/роли из плана → геометрия и физика."""
    entries = []
    for it in plan.get("objects", []):
        o = catalog.get(_base_id(it.get("id", "")))
        if o is not None:
            entries.append((o, it))
    if not entries:
        return []

    surfaces = [(o, it) for o, it in entries if it.get("role") == "surface"]
    atmos = [o for o, it in entries if it.get("role") == "atmosphere"]
    accents = [(o, it) for o, it in entries if it.get("role") in ("accent", "hero")]
    surf_ids = {_base_id(o["id"]) for o, _ in surfaces}
    has_on = any(_base_id(it.get("on") or "") in surf_ids for _, it in accents)

    # Если в плане есть «акцент НА подложке» — уважаем это (натюрморт),
    # а не раскладываем всё по отдельным ячейкам.
    if surfaces and accents and (has_on or plan.get("composition") == "still_life"):
        geom = _still_life_from_plan(surfaces, accents, atmos, plan, size, rng, W, H)
        if geom is not None:
            return geom

    # ≥2 объектов — сцена с заполнением ячеек (атмосфера сзади, объекты в сетке)
    if len(entries) >= 2:
        return _cell_scene_items(entries, plan, size, rng, W, H)

    objs = [o for o, _ in entries]
    mains = [o for o in objs if o["type"] == "main"] or objs
    bgs = [o for o in objs if o["type"] == "background"]

    if plan.get("driver") == "llm":
        main_ids = {_base_id(o["id"]) for o, _ in accents}
        bg_ids = {_base_id(o["id"]) for o, _ in surfaces}
        bg_ids |= {_base_id(a["id"]) for a in atmos}
        hero_base = next((o["id"] for o, it in accents if it.get("role") == "hero"), None)
        if hero_base is None and plan.get("focus"):
            hero_base = plan["focus"]
        if hero_base:
            hb = _base_id(hero_base)
            bg_ids.discard(hb)
            main_ids.add(hb)
        # прочие лампа/ваза — моно-фон, чтобы на экране не было двух цветных пятен
        hb = _base_id(hero_base or "")
        for o in objs:
            b = _base_id(o["id"])
            if b in UPRIGHT_BASES and b != hb:
                main_ids.discard(b)
                bg_ids.add(b)
        return _exposition_items(mains, bgs, size, rng, W, H, hero_id=hero_base,
                                 main_ids=main_ids, bg_ids=bg_ids)
    return _exposition_items(mains, bgs, size, rng, W, H)


def _decor_svg(w, h):
    """Декоративные векторы фона (как «ФОН» в Figma): тонкие диагональные
    линии и мелкие детали. Возвращает SVG-разметку в координатах w×h."""
    stroke = "#ffffff"
    lw = max(2.0, w * 0.0022)
    parts = []

    def line(x1, y1, x2, y2, op, width=None):
        parts.append("<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' "
                     "stroke='%s' stroke-width='%.2f' opacity='%.2f' "
                     "stroke-linecap='round'/>"
                     % (x1 * w, y1 * h, x2 * w, y2 * h, stroke,
                        width if width else lw, op))

    # две длинные диагонали (лёгкие, как на референсе)
    line(-0.02, 0.30, 0.62, 1.12, 0.18)
    line(0.34, -0.12, 1.04, 0.82, 0.18)
    # короткий штрих слева сверху
    line(0.055, 0.115, 0.11, 0.115, 0.30, width=max(2.5, w * 0.003))
    # крошечный «лучик» справа сверху
    line(0.90, 0.095, 0.945, 0.08, 0.28, width=max(2.5, w * 0.003))
    # маленькая «пилюля» слева снизу
    cx, cy = 0.075 * w, 0.865 * h
    parts.append("<ellipse cx='%.1f' cy='%.1f' rx='%.1f' ry='%.1f' "
                 "fill='%s' opacity='0.35' transform='rotate(-18 %.1f %.1f)'/>"
                 % (cx, cy, w * 0.022, w * 0.011, stroke, cx, cy))
    return "".join(parts)


def _masks():
    """Попиксельные маски «чернил» (N×N на бокс ассета 200×200), ленивая загрузка.

    Нужны, чтобы проверять наложения по РИСУНКУ: у вазы/букета bbox заметно больше
    самих чернил, и по bbox герой не мог подойти близко к фону. Маски готовятся
    скриптом `scripts/make_masks.py`, рантайму cairosvg/Pillow не нужны.
    """
    global _MASKS, _MASK_N
    if _MASKS is not None:
        return _MASKS
    out = {}
    try:
        with open(os.path.join(BASE, "data", "masks.json"), encoding="utf-8") as f:
            data = json.load(f)
        n = int(data.get("n") or _MASK_N)
        _MASK_N = n
        for oid, variants in (data.get("masks") or {}).items():
            per = {}
            for v, b64 in variants.items():
                raw = base64.b64decode(b64)
                bits = bytearray(n * n)
                for i in range(n * n):
                    bits[i] = (raw[i >> 3] >> (7 - (i & 7))) & 1
                per[v] = _dilate(bits, n)
            out[oid] = per
    except Exception:                 # масок нет — работаем по bbox
        out = {}
    _MASKS = out
    return _MASKS


def _dilate(bits, n, pad=1):
    """Расширяем маску на 1 клетку: гарантирует визуальный зазор между объектами."""
    out = bytearray(n * n)
    for v in range(n):
        row = v * n
        for u in range(n):
            if not bits[row + u]:
                continue
            for dv in range(-pad, pad + 1):
                vv = v + dv
                if vv < 0 or vv >= n:
                    continue
                base = vv * n
                for du in range(-pad, pad + 1):
                    uu = u + du
                    if 0 <= uu < n:
                        out[base + uu] = 1
    return out


def _mask_of(it, n=None):
    """Маска объекта для конкретного варианта (l/s); None, если её нет."""
    n = n or _MASK_N
    per = _masks().get(it.get("id", ""))
    if not per:
        return None
    return per.get(it.get("variant") or "l") or next(iter(per.values()))


def _mask_ink(mask, it, X, Y, n=None):
    """Есть ли «чернила» объекта в точке кадра (X, Y)."""
    n = n or _MASK_N
    s = it.get("scale") or 0.0
    if s <= 0 or not mask:
        return False
    dx, dy = X - it["x"], Y - it["y"]
    a = math.radians(it.get("rot", 0.0))
    ca, sa = math.cos(a), math.sin(a)
    # обратное преобразование рендера: translate(x y) rotate(rot) scale(s) translate(-100 -100)
    lx = (dx * ca + dy * sa) / s + 100.0
    ly = (-dx * sa + dy * ca) / s + 100.0
    u = int(lx / 200.0 * n)
    v = int(ly / 200.0 * n)
    if u < 0 or v < 0 or u >= n or v >= n:
        return False
    return bool(mask[v * n + u])


def _pair_ink_overlap(a, b, limit=350):
    """Доля «общих чернил» двух объектов (0 — фигуры не пересекаются).

    Сэмплируем пересечение bbox по сетке: маска первого объекта расширена на
    клетку, поэтому 0.0 значит ещё и «есть визуальный зазор».
    """
    bb_a, bb_b = _item_bbox(a), _item_bbox(b)
    if not bb_a or not bb_b:
        return 0.0
    x0, x1 = max(bb_a[0], bb_b[0]), min(bb_a[2], bb_b[2])
    y0, y1 = max(bb_a[1], bb_b[1]), min(bb_a[3], bb_b[3])
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return 0.0
    ma, mb = _mask_of(a), _mask_of(b)
    if ma is None or mb is None:
        return 1.0 if (x1 > x0 and y1 > y0) else 0.0
    area = (x1 - x0) * (y1 - y0)
    step = max(2.0, math.sqrt(area / float(limit)))
    hits_a = hits_b = common = 0
    y = y0 + step * 0.5
    while y < y1:
        x = x0 + step * 0.5
        while x < x1:
            ia = _mask_ink(ma, a, x, y)
            ib = _mask_ink(mb, b, x, y)
            if ia:
                hits_a += 1
            if ib:
                hits_b += 1
            if ia and ib:
                common += 1
            x += step
        y += step
    if not common:
        return 0.0
    return common / float(max(1, min(hits_a, hits_b)))


def _item_bbox(it):
    """bbox видимой части объекта на холсте (с учётом поворота и полей SVG)."""
    o = catalog.get(it["id"])
    if not o:
        return None
    cw, ch = _content_size(o)
    s = it["scale"]
    fx0, fy0, fx1, fy1 = it.get("ink") or (0.0, 0.0, 1.0, 1.0)
    lx0, lx1 = (fx0 - 0.5) * cw, (fx1 - 0.5) * cw
    ly0, ly1 = (fy0 - 0.5) * ch, (fy1 - 0.5) * ch
    a = math.radians(it.get("rot", 0.0))
    ca, sa = math.cos(a), math.sin(a)
    pts = [(s * (lx * ca - ly * sa), s * (lx * sa + ly * ca))
           for lx in (lx0, lx1) for ly in (ly0, ly1)]
    xs = [it["x"] + px for px, _ in pts]
    ys = [it["y"] + py for _, py in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _visible_fraction(it, W, H):
    """Доля bbox объекта, попавшая в кадр (1.0 = объект целиком виден)."""
    bb = _item_bbox(it)
    if not bb:
        return 1.0
    x0, y0, x1, y1 = bb
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return 1.0
    iw = max(0.0, min(W, x1) - max(0.0, x0))
    ih = max(0.0, min(H, y1) - max(0.0, y0))
    return (iw * ih) / area


def _max_main_overlap_ratio(items):
    mains = [it for it in items if it.get("state") == "main"]
    worst = 0.0
    for i in range(len(mains)):
        a = _item_bbox(mains[i])
        if not a:
            continue
        aa = (a[2] - a[0]) * (a[3] - a[1])
        for j in range(i + 1, len(mains)):
            b = _item_bbox(mains[j])
            if not b:
                continue
            bb = (b[2] - b[0]) * (b[3] - b[1])
            ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = ox * oy
            small = min(aa, bb) or 1.0
            worst = max(worst, inter / small)
    return worst




def _coverage(items, W, H, grid=96):
    """Доля кадра, занятая объектами (union их bbox), приблизительно по сетке."""
    total = grid * grid
    if total <= 0:
        return 0.0
    covered = bytearray(total)
    sx, sy = grid / float(W), grid / float(H)
    for it in items:
        bb = _item_bbox(it)
        if not bb:
            continue
        x0, y0, x1, y1 = bb
        gx0 = max(0, int(x0 * sx))
        gx1 = min(grid, int(math.ceil(x1 * sx)))
        gy0 = max(0, int(y0 * sy))
        gy1 = min(grid, int(math.ceil(y1 * sy)))
        if gx1 <= gx0 or gy1 <= gy0:
            continue
        row = b"\x01" * (gx1 - gx0)
        for yy in range(gy0, gy1):
            base = yy * grid
            covered[base + gx0:base + gx1] = row
    return sum(covered) / float(total)


def _pair_gap_ok(items, W, H, gap_frac=0.012):
    """Сцена с одиноким героем: герой целиком в кадре и без наездов.

    Фон «натюрморта» (ваза/лампа) может уходить за верхний/левый край кадра —
    целостность фона не требуется. Важно лишь, чтобы он не «схлопнулся»
    и не наезжал на героя.
    """
    mains = [it for it in items if it.get("state") == "main"]
    if not mains:
        return True
    m = 0.008 * min(W, H)
    for it in items:
        if it.get("state") != "main" and _base_id(it.get("id", "")) in UPRIGHT_BASES:
            if _visible_fraction(it, W, H) < 0.40:
                return False
            continue
        b = _item_bbox(it)
        if b and (b[0] < m or b[1] < m or b[2] > W - m or b[3] > H - m):
            return False
    gap = gap_frac * min(W, H)
    for m in mains:
        a = _item_bbox(m)
        if not a:
            continue
        for it in items:
            if it is m or it.get("state") == "main":
                continue
            b = _item_bbox(it)
            if not b:
                continue
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[1], b[1])
            if ox > -gap and oy > -gap:
                return False
    return True


def _ensure_coverage(items, W, H, target=None, max_t=8.0):
    """Кроп/зум сцены, пока фон не станет меньше ~(1-target).

    Если на переднем плане ≤1 объект — растём «на месте» (перекрытие только с фоном).
    Если ≥2 — чистый зум (позиции ×t), чтобы передние объекты не начали наезжать.
    """
    if not items:
        return items
    if target is None:
        target = COVERAGE_TARGET

    front_n = sum(1 for it in items if it.get("state") == "main")
    p = 0.6 if front_n <= 1 else 0.4
    cx, cy = W / 2.0, H / 2.0

    def scaled(t):
        tp = t ** p
        out = []
        for it in items:
            it2 = dict(it)
            it2["x"] = cx + (it["x"] - cx) * tp
            it2["y"] = cy + (it["y"] - cy) * tp
            it2["scale"] = it["scale"] * t
            out.append(it2)
        return out

    def min_vis(cand):
        # защищаем главные объекты и «подложки» (книга/блокнот/планшет/кошелёк),
        # атмосфера (лампа/ваза) может уходить за кадр
        vals = [_visible_fraction(it, W, H) for it in cand
                if it.get("state") == "main"
                or _base_id(it.get("id", "")) in PLATFORM_BASES]
        return min(vals) if vals else 1.0

    def min_vis_all(cand):
        vals = [_visible_fraction(it, W, H) for it in cand]
        return min(vals) if vals else 1.0

    base_cov = _coverage(items, W, H)
    if base_cov >= target:
        return items

    best_items, best_cov = items, base_cov
    t = 1.0
    while t < max_t - 1e-9:
        t = min(max_t, t + 0.05)
        cand = scaled(t)
        # не жертвуем информацией: главные объекты целиком и без наездов друг на друга.
        # Одинокому герою разрешаем чуть выходить за кадр (он крупный по замыслу),
        # атмосфера при этом может уходить за край сильнее.
        # одинокий герой должен быть виден ЦЕЛИКОМ (акцент размером, а не обрезкой)
        need_vis = 1.0 if front_n <= 1 else MIN_MAIN_VISIBILITY
        # фону (лампа/ваза) можно уходить за кадр сильнее: он «часть фона»,
        # а главные объекты и подложки остаются целиком (см. min_vis выше)
        need_vis_all = 0.32 if front_n <= 1 else 0.40
        if (_max_main_overlap_ratio(cand) > MAX_MAIN_OVERLAP
                or min_vis(cand) < need_vis
                or min_vis_all(cand) < need_vis_all
                or (front_n <= 1 and len(cand) > 1
                    and not _pair_gap_ok(cand, W, H))):
            break
        cov = _coverage(cand, W, H)
        if cov > best_cov:
            best_cov, best_items = cov, cand
        if cov >= target:
            return cand
    return best_items


def build(ids, size="l", scheme="bright", canvas="horizontal", plan=None):
    """Собрать сцену «Экспозиция» по набору id (и, если есть, по плану арт-директора)."""
    canvas = canvas if canvas in CANVASES else "horizontal"
    CW, CH = CANVASES[canvas]
    size = size if size in MAIN_SCALE else "l"
    rng = random.Random(_seed(ids, size))

    objs = [catalog.get(i) for i in ids if catalog.get(i)]
    if not objs:
        objs = [catalog.get(i) for i in catalog.default_ids()]
    mains = [o for o in objs if o["type"] == "main"]
    bgs = [o for o in objs if o["type"] == "background"]
    if not mains:
        mains = list(objs)

    if plan:
        # план арт-директора (LLM/фолбэк) → геометрия и физика
        items = _plan_items(plan, size, rng, CW, CH)
    else:
        items = _exposition_items(mains, bgs, size, rng, CW, CH)
        if items:
            # кроп/зум для «ручной» раскладки: фон не больше ~40% кадра.
            # В сценах по плану геометрию (центр героя, пределы) задаёт план.
            items = _ensure_coverage(items, CW, CH, target=COVERAGE_TARGET)

    bg = TEMPLATES.get("exposition", {}).get("background")
    backgrounds = bg if isinstance(bg, dict) else None
    background = (bg.get(scheme) or next(iter(bg.values()), None)) \
        if isinstance(bg, dict) else bg

    scene = {
        "schema": 1,
        "size": size,
        "scheme": scheme,
        "width": CW,
        "height": CH,
        "canvas": canvas,
        "background": background,
        "backgrounds": backgrounds,
        "font": FONT,
        "decor": _decor_svg(CW, CH),
        "items": sorted(items, key=lambda it: it["z"]),
    }
    if plan:
        scene["plan"] = plan
    return scene
