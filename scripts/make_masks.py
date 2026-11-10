#!/usr/bin/env python3
"""Генерация попиксельных масок объектов (`data/masks.json`).

Маски нужны движку компоновки, чтобы проверять наложения по РИСУНКУ, а не по
bbox: у вазы/лампы/букета bbox сильно больше «чернил», поэтому по bbox герой не
мог подойти близко к фону. Маски считаются заранее (библиотека статична), чтобы
рантайму не требовались cairosvg/Pillow: `layout.py` их только читает.

Запуск:  python3 scripts/make_masks.py   (нужны cairosvg и Pillow)
"""

import base64
import json
import os
import re
import sys

import cairosvg
from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(BASE, "assets", "library")
OUT = os.path.join(BASE, "data", "masks.json")
N = 64                      # маска N×N на бокс ассета 200×200
_VAR_RE = re.compile(r"var\(--([a-zA-Z0-9]+)\)")


def pack(alpha_bytes):
    """alpha (N*N байт) → битовая маска, base64."""
    out = bytearray()
    acc = 0
    bits = 0
    for a in alpha_bytes:
        acc = (acc << 1) | (1 if a > 16 else 0)
        bits += 1
        if bits == 8:
            out.append(acc)
            acc = 0
            bits = 0
    if bits:
        out.append(acc << (8 - bits))
    return base64.b64encode(bytes(out)).decode("ascii")


def mask_of(rel):
    with open(os.path.join(ASSETS, rel), encoding="utf-8") as f:
        markup = f.read()
    # cairosvg не понимает var(--c1) — для альфы достаточно любого цвета
    markup = _VAR_RE.sub("#000", markup)
    png = cairosvg.svg2png(bytestring=markup.encode("utf-8"),
                           output_width=N, output_height=N)
    img = Image.open(__import__("io").BytesIO(png)).convert("RGBA")
    return pack(img.getchannel("A").tobytes())


def main():
    with open(os.path.join(ASSETS, "library.json"), encoding="utf-8") as f:
        lib = json.load(f)
    out = {}
    for o in lib["objects"]:
        variants = {}
        for v, rel in (o.get("variants") or {}).items():
            variants[v] = mask_of(rel)
        out[o["id"]] = variants
    data = {"n": N, "box": 200, "masks": out}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("маски: %d объектов -> %s (%d КБ)"
          % (len(out), os.path.relpath(OUT, BASE), os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    sys.exit(main())
