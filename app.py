"""FastAPI-приложение: статика фронтенда + JSON API конструктора иллюстраций."""

import os

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import layout
import planner
from catalog import catalog
from palette import derive_roles

BASE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Illustration Builder", version="1.0.0")


class NoCacheStaticFiles(StaticFiles):
    """Статика фронтенда без кэша, чтобы правки JS/CSS сразу подхватывались."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


class ComposeRequest(BaseModel):
    query: str = ""
    format: str = "exposition"   # единственный поддерживаемый формат — «Экспозиция»
    size: str = "l"
    scheme: str = "bright"
    canvas: str = "horizontal"
    limit: int = 6


class PaletteRequest(BaseModel):
    colors: list[str] = []


def _dedupe_valid(ids):
    seen, out = set(), []
    for i in ids:
        if catalog.exists(i) and i not in seen:
            seen.add(i)
            out.append(i)
    return out


@app.get("/api/library")
def api_library():
    return {
        "objects": catalog.objects,
        "palettes": catalog.palettes,
        "synonyms": catalog.synonyms,
    }


@app.post("/api/compose")
def api_compose(req: ComposeRequest):
    query = (req.query or "").strip()
    if not query:
        # без метафоры ничего не сочиняем: пустой кадр — осознанное состояние
        raise HTTPException(status_code=400, detail="Пустой запрос: введите метафору")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="Слишком длинный запрос (макс. 200 символов)")
    if (req.format or "exposition") != "exposition":
        raise HTTPException(status_code=400,
                            detail="Поддерживается только формат «Экспозиция»")

    limit = max(1, min(req.limit, 10))
    try:
        res = planner.plan(query, limit=limit)
        ids = _dedupe_valid(res["ids"])[:limit]
        plan = res.get("plan")
        source = res["source"]
        reason = res["reason"]
        if not ids:
            ids = _dedupe_valid(catalog.default_ids())[:limit]
            source += "+default"
            plan = None

        scene = layout.build(ids, req.size, req.scheme, req.canvas, plan=plan)
        scene["resolved"] = {"ids": ids, "source": source, "reason": reason}
        return scene
    except HTTPException:
        raise
    except Exception as exc:
        # страховка: не отдаём 500 — показываем безопасный набор без плана
        ids = _dedupe_valid(catalog.default_ids())[:limit]
        scene = layout.build(ids, req.size, req.scheme, req.canvas)
        scene["resolved"] = {"ids": ids, "source": "safe-fallback",
                             "reason": "аварийный набор: %s" % type(exc).__name__}
        return scene


@app.post("/api/palette")
def api_palette(req: PaletteRequest):
    roles = derive_roles(req.colors)
    return {"roles": roles}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "objects": len(catalog.objects)}


# Статика: SVG-библиотека, затем SPA в корне.
app.mount("/assets", StaticFiles(directory=os.path.join(BASE, "assets")), name="assets")
app.mount("/", NoCacheStaticFiles(directory=os.path.join(BASE, "frontend"), html=True), name="frontend")
