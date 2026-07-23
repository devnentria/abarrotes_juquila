# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : routers/paginas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router de páginas HTML — PWA Asistente.

Rutas:
  GET /  → Shell PWA (móvil — chat IA)
"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from shared.config import PWA_BASE_PATH

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Shell PWA — experiencia móvil (chat, inventario, médicos)."""
    return _templates.TemplateResponse(
        "index.html",
        {"request": request, "base_path": PWA_BASE_PATH},
        headers=_NO_CACHE,
    )


