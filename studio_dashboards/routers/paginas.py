# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/paginas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router de páginas HTML — Studio Dashboards.

Rutas:
  GET /       → Shell Studio (dashboards + menú por rol)
  GET /admin  → Portal de administración (lo sirve admin.py, aquí no aplica)
"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@router.get("/", response_class=HTMLResponse)
async def studio(request: Request):
    """Shell Studio — experiencia desktop (dashboards + menú por rol)."""
    return _templates.TemplateResponse("studio.html", {"request": request}, headers=_NO_CACHE)
