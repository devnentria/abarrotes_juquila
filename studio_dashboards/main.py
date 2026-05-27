# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : main.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Punto de entrada — Studio Dashboards.

Responsabilidades:
  - Gestión de usuarios y administración de la Suite
  - Dashboards ejecutivos (próximamente)

Rutas principales:
  GET  /            → Shell Studio (dashboards)
  GET  /admin       → Portal de administración
  POST /auth/login  → Autenticación JWT
  GET|POST|PATCH /api/admin/* → CRUD de usuarios
"""
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from shared.config import STUDIO_PORT
from shared.database_local import init_db
from pwa_asistente.routers import auth
from studio_dashboards.routers import admin, chat as studio_chat, datos, paginas

# ── Inicializar BD local al arrancar ─────────────────────────────────────────
init_db()

# ── Instancia principal ───────────────────────────────────────────────────────
app = FastAPI(
    title="Studio Dashboards — Suite Analítica Nentria",
    docs_url=None,
    redoc_url=None,
)

# ── Archivos estáticos ────────────────────────────────────────────────────────
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)        # POST /auth/login, GET /auth/me, PATCH /auth/perfil
app.include_router(admin.router)       # GET /admin, GET|POST|PATCH /api/admin/*
app.include_router(datos.router)       # GET /api/datos/* → datos ERP para dashboards
app.include_router(studio_chat.router) # POST /api/studio/chat/* → chat con IA superior
app.include_router(paginas.router)     # GET / → shell Studio


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Endpoint para verificar que el servidor está vivo."""
    return {"status": "ok", "modulo": "studio_dashboards"}


# ── Arranque directo ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "studio_dashboards.main:app",
        host="0.0.0.0",
        port=STUDIO_PORT,
        reload=True,
    )
