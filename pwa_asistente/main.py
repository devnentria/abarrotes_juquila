# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : main.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Punto de entrada — PWA Asistente Analítico.

Responsabilidades:
  - Crea la instancia de FastAPI
  - Monta archivos estáticos (CSS, JS, manifest, service worker)
  - Registra los routers de cada área funcional
  - Define el arranque del servidor

No contiene lógica de negocio ni acceso a BD.
"""
from pathlib import Path

import json

import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from shared.config import PWA_PORT
from shared.database_local import init_db
from pwa_asistente.routers import auth, chat, ia_flash, paginas, vistas

# ── Inicializar BD local al arrancar ─────────────────────────────────────────
init_db()

# Jobs que quedaron en "pending" de un proceso anterior no se pueden recuperar
from shared.database_local import execute as _execute
_execute(
    "UPDATE chat_jobs SET estado='error', respuesta='El servidor se reinició mientras procesaba tu consulta. Intenta de nuevo.', "
    "terminado_en=datetime('now') WHERE estado='pending'"
)

# ── Instancia principal ───────────────────────────────────────────────────────
app = FastAPI(
    title="PWA Asistente — Suite Analítica Nentria",
    docs_url=None,   # Deshabilitar Swagger en producción (opcional)
    redoc_url=None,
)

# ── SW "suicida" en ruta vieja — desregistra el SW anterior y recarga ────────
_SW_KILL = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => {
  self.registration.unregister().then(() => {
    self.clients.matchAll({ type: 'window' }).then(clients =>
      clients.forEach(c => c.navigate(c.url))
    );
  });
});
"""

@app.get("/manifest.json", include_in_schema=False)
async def serve_manifest():
    """Manifest en la raíz del app con rutas relativas.

    Manifest en '/' (local) o '/IA/' (servidor vía Apache).
    '.' resuelve al directorio actual → scope correcto en ambos entornos.
    Iconos: 'static/icons/...' relativo a la raíz del app.
    """
    manifest = {
        "name": "Suite Analítica",
        "short_name": "Analítica",
        "description": "Asistente analítico empresarial",
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#1a56db",
        "orientation": "portrait",
        "icons": [
            {"src": "static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(
        content=json.dumps(manifest),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/static/sw.js", include_in_schema=False)
async def serve_sw_kill():
    return Response(
        content=_SW_KILL,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

# ── Service Worker en raíz — sin caché HTTP, scope completo ─────────────────
@app.get("/sw.js", include_in_schema=False)
async def serve_sw():
    sw_path = Path(__file__).parent / "static" / "sw.js"
    return Response(
        content=sw_path.read_text(),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

# ── Archivos estáticos ────────────────────────────────────────────────────────
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)      # POST /auth/login, GET /auth/me, PATCH /auth/perfil
app.include_router(chat.router)      # GET|POST|DELETE /api/chat/*
app.include_router(ia_flash.router)  # GET /api/ia/* resúmenes flash con IA
app.include_router(paginas.router)   # GET / → shell de la app
app.include_router(vistas.router)    # GET /api/* datos del ERP


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Endpoint para verificar que el servidor está vivo."""
    return {"status": "ok", "modulo": "pwa_asistente"}


# ── Arranque directo ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "pwa_asistente.main:app",
        host="0.0.0.0",
        port=PWA_PORT,
        reload=True,   # Auto-reload al guardar archivos (solo desarrollo)
    )
