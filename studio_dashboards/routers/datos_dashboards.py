# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_dashboards.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
# ============================================================
"""
Sub-router de datos: Dashboards guardados y uso-selector.

Endpoints:
  GET    /dashboards                         → Listar dashboards guardados
  POST   /dashboards                         → Guardar un dashboard
  GET    /dashboards/{id}/pdf                → Obtener PDF de un dashboard
  PATCH  /dashboards/{id}/pdf                → Actualizar PDF de un dashboard
  DELETE /dashboards/{id}                    → Eliminar un dashboard
  PATCH  /dashboards/{id}/compartir          → Compartir con la PWA
  PATCH  /dashboards/{id}/descompartir       → Quitar de la PWA
  POST   /uso-selector                       → Registrar uso del selector de período
"""
import json
import base64

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from shared.auth import get_current_user
from shared.database_local import execute, fetch_all, fetch_one

from .datos_helpers import DashboardGuardar, PdfUpdate

router = APIRouter()


# ── Dashboards guardados ──────────────────────────────────────────────────────

@router.get("/dashboards")
def listar_dashboards(usuario=Depends(get_current_user)):
    """Lista todos los dashboards guardados, del más reciente al más antiguo."""
    rows = fetch_all(
        "SELECT id, titulo, pregunta, tipo, datos_json, creado_en, "
        "       CASE WHEN pdf_b64 <> '' THEN 1 ELSE 0 END AS has_pdf "
        "FROM dashboards WHERE guardado=1 AND creado_por=? ORDER BY creado_en DESC",
        (usuario["id"],)
    )
    for r in rows:
        try:
            r["datos_json"] = json.loads(r["datos_json"])
        except Exception:
            r["datos_json"] = {}
    return JSONResponse({"dashboards": rows})


@router.post("/dashboards")
def guardar_dashboard(body: DashboardGuardar, usuario=Depends(get_current_user)):
    """Guarda un dashboard generado en el historial permanente."""
    nuevo_id = execute(
        "INSERT INTO dashboards (titulo, pregunta, tipo, datos_json, guardado, creado_por, pdf_b64) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (body.titulo, body.pregunta, body.tipo,
         json.dumps(body.datos_json, ensure_ascii=False), usuario["id"], body.pdf_b64)
    )
    return JSONResponse({"id": nuevo_id, "mensaje": "Dashboard guardado"})


@router.get("/dashboards/{dashboard_id}/pdf")
def obtener_pdf_dashboard(dashboard_id: int, usuario=Depends(get_current_user)):
    """Devuelve el PDF de un dashboard guardado como respuesta binaria."""
    row = fetch_one(
        "SELECT pdf_b64 FROM dashboards WHERE id=? AND guardado=1",
        (dashboard_id,)
    )
    if not row or not row.get("pdf_b64"):
        raise HTTPException(404, "PDF no disponible para este dashboard")
    try:
        pdf_bytes = base64.b64decode(row["pdf_b64"])
    except Exception:
        raise HTTPException(500, "Error al decodificar el PDF")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="dashboard_{dashboard_id}.pdf"'},
    )


@router.patch("/dashboards/{dashboard_id}/pdf")
def actualizar_pdf_dashboard(dashboard_id: int, body: PdfUpdate, usuario=Depends(get_current_user)):
    """Actualiza el PDF de un dashboard ya guardado."""
    dash = fetch_one("SELECT id FROM dashboards WHERE id=? AND guardado=1", (dashboard_id,))
    if not dash:
        raise HTTPException(404, "Dashboard no encontrado")
    execute("UPDATE dashboards SET pdf_b64=? WHERE id=?", (body.pdf_b64, dashboard_id))
    return JSONResponse({"ok": True})


@router.delete("/dashboards/{dashboard_id}")
def eliminar_dashboard(dashboard_id: int, usuario=Depends(get_current_user)):
    """Elimina un dashboard guardado."""
    dash = fetch_one("SELECT id FROM dashboards WHERE id=? AND guardado=1", (dashboard_id,))
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard no encontrado")
    execute("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
    return JSONResponse({"mensaje": "Dashboard eliminado"})


@router.patch("/dashboards/{dashboard_id}/compartir")
def compartir_dashboard(dashboard_id: int, usuario=Depends(get_current_user)):
    """Marca un dashboard como compartido con la PWA."""
    dash = fetch_one("SELECT id FROM dashboards WHERE id=? AND guardado=1", (dashboard_id,))
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard no encontrado")
    execute(
        "UPDATE dashboards SET compartido=1, compartido_en=datetime('now') WHERE id=?",
        (dashboard_id,),
    )
    return JSONResponse({"mensaje": "Dashboard compartido con la PWA"})


@router.patch("/dashboards/{dashboard_id}/descompartir")
def descompartir_dashboard(dashboard_id: int, usuario=Depends(get_current_user)):
    """Quita el dashboard de la PWA."""
    execute("UPDATE dashboards SET compartido=0, compartido_en=NULL WHERE id=?", (dashboard_id,))
    return JSONResponse({"mensaje": "Dashboard quitado de la PWA"})


@router.post("/uso-selector")
def registrar_uso_selector(usuario=Depends(get_current_user)):
    """
    Descuenta 1 consulta cuando el usuario cambia el selector de período.
    Se llama desde el frontend tras cargar los datos del ERP con éxito.
    No aplica si el usuario es ilimitado (limite_ia = 0).
    """
    from shared.database_local import verificar_mes_ia, periodo_ia_actual
    verificar_mes_ia(usuario["id"], periodo_ia_actual())
    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + 1, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + 1, 2) "
        "WHERE id = ? AND limite_ia > 0",
        (usuario["id"],),
    )
