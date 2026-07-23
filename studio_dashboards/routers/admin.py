# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/admin.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Portal de administración — Studio (centro de operaciones).

Studio es la fuente de verdad para la gestión de usuarios de la Suite.
Los usuarios creados aquí tienen acceso a PWA y/o Studio según sus módulos.

Rutas:
  GET    /admin                               → Portal HTML de admin
  DELETE /api/admin/usuarios/{id}             → Elimina un usuario (solo Studio)
  + todas las rutas de shared.admin_api
"""
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.admin_api import build_api_router
from shared.auth import require_rol
from shared.database_local import execute, fetch_one

router = build_api_router()

_templates = Jinja2Templates(
    directory=str(Path(__file__).parent.parent / "templates")
)


@router.get("/admin", response_class=HTMLResponse)
def portal_admin(request: Request):
    """Sirve el portal de administración de usuarios."""
    return _templates.TemplateResponse("admin.html", {"request": request})


@router.delete("/api/admin/usuarios/{usuario_id}", dependencies=[Depends(require_rol("admin"))])
def eliminar_usuario(usuario_id: int):
    """
    Elimina permanentemente un usuario de la Suite.
    No se puede eliminar al único administrador activo.

    Args:
        usuario_id (int): ID del usuario a eliminar.

    Returns:
        JSONResponse: { mensaje }

    Raises:
        HTTPException 404: Si el usuario no existe.
        HTTPException 400: Si se intenta eliminar al único admin.
    """
    usuario = fetch_one("SELECT id, rol, activo FROM usuarios WHERE id = ?", (usuario_id,))
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if usuario["rol"] == "admin":
        admins_activos = fetch_one(
            "SELECT COUNT(*) as total FROM usuarios WHERE rol = 'admin' AND activo = 1"
        )
        if admins_activos["total"] <= 1:
            raise HTTPException(status_code=400, detail="No puedes eliminar al único administrador")

    execute("DELETE FROM consumo_ia_mensual WHERE usuario_id = ?", (usuario_id,))
    execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
    return JSONResponse({"mensaje": "Usuario eliminado"})
