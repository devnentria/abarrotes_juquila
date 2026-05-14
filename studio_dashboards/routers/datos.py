# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router de datos del ERP para el Studio Dashboards.

Endpoints:
  GET  /api/datos/ventas               → Ventas por sucursal (30d o mes actual)
  GET  /api/datos/pedidos              → Pedidos activos por sucursal
  GET  /api/datos/kpis                 → Totales globales para tarjetas KPI
  GET  /api/datos/ventas-hoy           → Ventas pagadas del día
  GET  /api/datos/plantilla/{tipo}     → Datos de una plantilla predefinida
  POST /api/datos/dashboards           → Guardar un dashboard
  GET  /api/datos/dashboards           → Listar dashboards guardados
  DELETE /api/datos/dashboards/{id}   → Eliminar un dashboard guardado
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.database import query, hoy
from shared.database_local import execute, fetch_all, fetch_one

router = APIRouter(prefix="/api/datos", dependencies=[Depends(get_current_user)])


# ── Modelos ───────────────────────────────────────────────────────────────────

class DashboardGuardar(BaseModel):
    titulo:     str
    pregunta:   str = ""
    tipo:       str = "texto"
    datos_json: dict = {}


# ── Ventas por sucursal ───────────────────────────────────────────────────────

@router.get("/ventas")
def ventas_sucursales(modo: str = Query("30d", regex="^(30d|mes)$")):
    """
    Ventas por sucursal para los dashboards del Studio.

    Args:
        modo: '30d' → últimos 30 días vs 30 anteriores / 'mes' → mes actual vs anterior

    Returns:
        JSON con lista de sucursales, ventas, facturas y variación porcentual.
    """
    hoy_fecha = f"CAST({hoy()} AS DATE)"
    # En la query exterior el alias del subquery es "t", por eso t.Fecha_Documento
    if modo == "30d":
        filtro_actual   = f"CAST(t.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        filtro_anterior = (
            f"CAST(t.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha}) "
            f"AND CAST(t.Fecha_Documento AS DATE) < DATEADD(DAY,-30,{hoy_fecha})"
        )
    else:
        filtro_actual   = (
            f"YEAR(t.Fecha_Documento) = YEAR({hoy()}) "
            f"AND MONTH(t.Fecha_Documento) = MONTH({hoy()})"
        )
        filtro_anterior = (
            f"YEAR(t.Fecha_Documento) = YEAR(DATEADD(MONTH,-1,{hoy()})) "
            f"AND MONTH(t.Fecha_Documento) = MONTH(DATEADD(MONTH,-1,{hoy()})) "
            f"AND DAY(t.Fecha_Documento) <= DAY({hoy()})"
        )

    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                     AS cve_sucursal,
            s.Nombre                                                           AS sucursal,
            ISNULL(SUM(CASE WHEN {filtro_actual}   THEN t.Monto END), 0)      AS ventas_actual,
            ISNULL(SUM(CASE WHEN {filtro_anterior} THEN t.Monto END), 0)      AS ventas_anterior,
            COUNT(DISTINCT CASE WHEN {filtro_actual} THEN t.Cve_Folio END)    AS facturas
        FROM GN_Sucursales s
        LEFT JOIN (
            SELECT c.Cve_Sucursal, c.Cve_Folio, c.Fecha_Documento,
                   ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS Monto
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            WHERE c.Estatus <> 'CN'
              AND c.Referencia_Cliente = 'PAGADO'
            GROUP BY c.Cve_Sucursal, c.Cve_Folio, c.Fecha_Documento
        ) t ON t.Cve_Sucursal = s.Cve_Sucursal
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY ventas_actual DESC
    """)

    for r in rows:
        actual   = float(r.get("ventas_actual") or 0)
        anterior = float(r.get("ventas_anterior") or 0)
        r["variacion_pct"] = (
            round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
        )

    return JSONResponse({"sucursales": rows, "modo": modo})


# ── Pedidos activos por sucursal ──────────────────────────────────────────────

@router.get("/pedidos")
def pedidos_sucursales():
    """
    Pedidos activos e historial 30 días por sucursal para el Studio.

    Returns:
        JSON con lista de sucursales, pedidos activos e historial.
    """
    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                    AS cve_sucursal,
            s.Nombre                                                          AS sucursal,
            COUNT(CASE WHEN p.Estatus = 'AC' THEN 1 END)                     AS activos,
            COUNT(CASE WHEN p.Estatus IN ('TR','CN')
                        AND p.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                 AS completados_30d
        FROM GN_Sucursales s
        LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal = s.Cve_Sucursal
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY activos DESC
    """)
    return JSONResponse({"sucursales": rows})


# ── KPIs globales ─────────────────────────────────────────────────────────────

@router.get("/kpis")
def kpis_globales(modo: str = Query("30d", regex="^(30d|mes)$")):
    """
    Totales globales para las tarjetas KPI del Studio.

    Args:
        modo: '30d' o 'mes' — mismo filtro que ventas.

    Returns:
        JSON con ventas_total, facturas_total, pedidos_activos, sucursales_activas.
    """
    hoy_fecha = f"CAST({hoy()} AS DATE)"
    if modo == "30d":
        filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
    else:
        filtro = (
            f"YEAR(c.Fecha_Documento) = YEAR({hoy()}) "
            f"AND MONTH(c.Fecha_Documento) = MONTH({hoy()})"
        )

    ventas_row = query(f"""
        SELECT COUNT(Cve_Folio) AS facturas_total, ISNULL(SUM(Monto), 0) AS ventas_total
        FROM (
            SELECT c.Cve_Folio, ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS Monto
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            WHERE c.Estatus <> 'CN'
              AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND {filtro}
            GROUP BY c.Cve_Folio
        ) AS t
    """)

    pedidos_row = query("""
        SELECT COUNT(*) AS pedidos_activos
        FROM FT_Pedidos_C
        WHERE Estatus = 'AC' AND Cve_Sucursal <> 99
    """)

    sucursales_row = query("""
        SELECT COUNT(*) AS total
        FROM GN_Sucursales
        WHERE Cve_Sucursal <> 99
    """)

    v = ventas_row[0] if ventas_row else {}
    return JSONResponse({
        "ventas_total":       float(v.get("ventas_total") or 0),
        "facturas_total":     int(v.get("facturas_total") or 0),
        "pedidos_activos":    int((pedidos_row[0] or {}).get("pedidos_activos") or 0),
        "sucursales_activas": int((sucursales_row[0] or {}).get("total") or 0),
        "modo":               modo,
    })


# ── Ventas pagadas de hoy (FT_Pedidos_Dia) ───────────────────────────────────

@router.get("/ventas-hoy")
def ventas_hoy():
    """
    Ventas pagadas del día actual por sucursal.

    Fuente correcta: FT_Pedidos_C + FT_Pedidos_Dia WHERE Referencia_Cliente = 'PAGADO'.
    Importe = SUM(Cantidad_Ordenada * Precio), agrupado por folio para evitar duplicados.

    Returns:
        JSON con lista de sucursales y su total de ventas de hoy, más el total global.
    """
    rows = query(f"""
        SELECT
            s.Cve_Sucursal                         AS cve_sucursal,
            s.Nombre                               AS sucursal,
            COUNT(t.Cve_Folio)                     AS pedidos_hoy,
            ISNULL(SUM(t.Monto), 0)                AS ventas_hoy
        FROM GN_Sucursales s
        LEFT JOIN (
            SELECT c.Cve_Sucursal, c.Cve_Folio,
                   ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS Monto
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            WHERE c.Estatus <> 'CN'
              AND c.Referencia_Cliente = 'PAGADO'
              AND CAST(c.Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)
            GROUP BY c.Cve_Sucursal, c.Cve_Folio
        ) t ON t.Cve_Sucursal = s.Cve_Sucursal
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY ventas_hoy DESC
    """)

    total = sum(float(r.get("ventas_hoy") or 0) for r in rows)
    return JSONResponse({"sucursales": rows, "total_hoy": total})


# ── Plantillas predefinidas ───────────────────────────────────────────────────

@router.get("/plantilla/{tipo}")
def plantilla(tipo: str, modo: str = Query("30d", regex="^(30d|mes)$")):
    """
    Devuelve datos listos para renderizar según la plantilla solicitada.

    Tipos disponibles:
      ventas_sucursal   → Barras: ventas por sucursal vs período anterior
      pedidos_activos   → Dona: pedidos activos por sucursal
      ventas_hoy        → Tabla + KPI: ventas pagadas del día
      top_vendedores    → Barras horizontales: top vendedores del período
      comparativo_meses → Línea: ventas por mes (últimos 6 meses)
    """
    hoy_fecha = f"CAST({hoy()} AS DATE)"

    if tipo == "ventas_sucursal":
        if modo == "30d":
            fa = f"CAST(t.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
            fb = (f"CAST(t.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha}) "
                  f"AND CAST(t.Fecha_Documento AS DATE) < DATEADD(DAY,-30,{hoy_fecha})")
        else:
            fa = f"YEAR(t.Fecha_Documento)=YEAR({hoy()}) AND MONTH(t.Fecha_Documento)=MONTH({hoy()})"
            fb = (f"YEAR(t.Fecha_Documento)=YEAR(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND MONTH(t.Fecha_Documento)=MONTH(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND DAY(t.Fecha_Documento)<=DAY({hoy()})")
        rows = query(f"""
            SELECT s.Nombre AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN t.Monto END),0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN t.Monto END),0) AS anterior
            FROM GN_Sucursales s
            LEFT JOIN (
                SELECT c.Cve_Sucursal, c.Fecha_Documento,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                GROUP BY c.Cve_Sucursal, c.Fecha_Documento
            ) t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY actual DESC
        """)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Ventas por sucursal ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "series": ["Período actual", "Período anterior"],
                             "datos": rows})

    elif tipo == "pedidos_activos":
        rows = query(f"""
            SELECT s.Nombre AS label, COUNT(CASE WHEN p.Estatus='AC' THEN 1 END) AS valor
            FROM GN_Sucursales s
            LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre HAVING COUNT(CASE WHEN p.Estatus='AC' THEN 1 END)>0
            ORDER BY valor DESC
        """)
        total = sum(r.get("valor") or 0 for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Pedidos activos por sucursal",
                             "total": total, "datos": rows})

    elif tipo == "ventas_hoy":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(t.Cve_Folio) AS pedidos,
                   ISNULL(SUM(t.Monto),0) AS valor
            FROM GN_Sucursales s
            LEFT JOIN (
                SELECT c.Cve_Sucursal, c.Cve_Folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE)=CAST({hoy()} AS DATE)
                GROUP BY c.Cve_Sucursal, c.Cve_Folio
            ) t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Ventas del día (pagadas)",
                             "total": total, "datos": rows})

    elif tipo == "top_vendedores":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        else:
            filtro = f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) AND MONTH(c.Fecha_Documento)=MONTH({hoy()})"
        rows = query(f"""
            SELECT TOP 10 ISNULL(v.Nombre, c.Cve_Vendedor) AS label,
                   ISNULL(SUM(t.Monto),0) AS valor,
                   COUNT(DISTINCT t.Cve_Folio) AS pedidos
            FROM (
                SELECT c.Cve_Vendedor, c.Cve_Folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO' AND {filtro}
                GROUP BY c.Cve_Vendedor, c.Cve_Folio
            ) t
            JOIN FT_Pedidos_C c ON c.Cve_Folio=t.Cve_Folio AND c.Cve_Vendedor=t.Cve_Vendedor
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor=c.Cve_Vendedor
            GROUP BY c.Cve_Vendedor, v.Nombre ORDER BY valor DESC
        """)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Top vendedores ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "datos": rows})

    elif tipo == "comparativo_meses":
        rows = query(f"""
            SELECT TOP 6
                YEAR(c.Fecha_Documento) AS anio,
                MONTH(c.Fecha_Documento) AS mes,
                DATENAME(MONTH, c.Fecha_Documento) AS mes_nombre,
                ISNULL(SUM(t.Monto),0) AS valor
            FROM (
                SELECT c.Cve_Folio, c.Fecha_Documento,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND c.Fecha_Documento >= DATEADD(MONTH,-5,{hoy()})
                GROUP BY c.Cve_Folio, c.Fecha_Documento
            ) t
            JOIN FT_Pedidos_C c ON c.Cve_Folio=t.Cve_Folio
            WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
              AND c.Fecha_Documento >= DATEADD(MONTH,-5,{hoy()})
            GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento), DATENAME(MONTH, c.Fecha_Documento)
            ORDER BY anio, mes
        """)
        return JSONResponse({"tipo": tipo, "titulo": "Ventas últimos 6 meses", "datos": rows})

    raise HTTPException(status_code=404, detail=f"Plantilla '{tipo}' no existe")


# ── Dashboards guardados ──────────────────────────────────────────────────────

@router.get("/dashboards")
def listar_dashboards(usuario=Depends(get_current_user)):
    """Lista todos los dashboards guardados, del más reciente al más antiguo."""
    rows = fetch_all(
        "SELECT id, titulo, pregunta, tipo, datos_json, creado_en "
        "FROM dashboards WHERE guardado=1 ORDER BY creado_en DESC"
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
        "INSERT INTO dashboards (titulo, pregunta, tipo, datos_json, guardado, creado_por) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (body.titulo, body.pregunta, body.tipo,
         json.dumps(body.datos_json, ensure_ascii=False), usuario["id"])
    )
    return JSONResponse({"id": nuevo_id, "mensaje": "Dashboard guardado"})


@router.delete("/dashboards/{dashboard_id}")
def eliminar_dashboard(dashboard_id: int, usuario=Depends(get_current_user)):
    """Elimina un dashboard guardado."""
    dash = fetch_one("SELECT id FROM dashboards WHERE id=? AND guardado=1", (dashboard_id,))
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard no encontrado")
    execute("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
    return JSONResponse({"mensaje": "Dashboard eliminado"})
