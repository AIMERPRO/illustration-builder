"""Вывод ролей палитры (c1..bg3, canvas) из произвольного набора цветов.

Используется endpoint /api/palette. Фронтенд имеет зеркальную JS-логику
для мгновенной перекраски без сетевого запроса.
"""


def _hex_to_rgb(value):
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _lum(rgb):
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _sat(rgb):
    mx = max(rgb)
    mn = min(rgb)
    return 0 if mx == 0 else (mx - mn) / 255.0


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _lighten(rgb, t):
    return _mix(rgb, (255, 255, 255), t)


def _darken(rgb, t):
    return _mix(rgb, (0, 0, 0), t)


def derive_roles(colors):
    rgbs = [p for p in (_hex_to_rgb(c) for c in colors) if p]
    if not rgbs:
        return {}

    ordered = sorted(rgbs, key=_lum)
    lightest = ordered[-1]
    darkest = ordered[0]
    avg = tuple(sum(c[i] for c in rgbs) / len(rgbs) for i in range(3))
    gray = int(_lum(avg))
    neutral = (gray, gray, gray)

    saturated = sorted(rgbs, key=_sat, reverse=True)
    uniq = []
    for c in saturated:
        if all(sum(abs(a - b) for a, b in zip(c, u)) > 60 for u in uniq):
            uniq.append(c)
    while len(uniq) < 5:
        uniq.append(_mix(lightest, darkest, 0.5 + 0.2 * len(uniq)))

    return {
        "canvas": _to_hex(_lighten(lightest, 0.82)),
        "c1": _to_hex(uniq[0]),
        "c2": _to_hex(uniq[1]),
        "c3": _to_hex(uniq[2]),
        "c4": _to_hex(uniq[3]),
        "c5": _to_hex(uniq[4]),
        "line": _to_hex(_darken(darkest, 0.45)),
        "bg1": _to_hex(_mix(neutral, (255, 255, 255), 0.85)),
        "bg2": _to_hex(_mix(neutral, (255, 255, 255), 0.70)),
        "bg3": _to_hex(_mix(neutral, (255, 255, 255), 0.52)),
    }
