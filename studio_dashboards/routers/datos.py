# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Router de datos del ERP para el Studio Dashboards.

Endpoints:
  GET  /api/datos/ventas               → Ventas por sucursal (30d o mes actual)
  GET  /api/datos/pedidos              → Pedidos activos por sucursal
  GET  /api/datos/kpis                 → Totales globales para tarjetas KPI
  GET  /api/datos/ventas-hoy           → Ventas pagadas del día
  GET  /api/datos/plantilla/{tipo}     → Datos de una plantilla predefinida
  POST /api/datos/generar              → Genera dashboard completo con IA (gpt-5-nano)
  POST /api/datos/dashboards           → Guardar un dashboard
  GET  /api/datos/dashboards           → Listar dashboards guardados
  DELETE /api/datos/dashboards/{id}   → Eliminar un dashboard guardado
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.config import (
    OPENAI_API_KEY, STUDIO_IA_MODEL,
    IA_PRECIO_INPUT, IA_PRECIO_OUTPUT, IA_RATIO_STUDIO,
)
from shared.database import query, hoy
from shared.database_local import execute, fetch_all, fetch_one

router = APIRouter(prefix="/api/datos", dependencies=[Depends(get_current_user)])

_client = OpenAI(api_key=OPENAI_API_KEY)

# ── Specs por tipo: título y layout visual ────────────────────────────────────
_SPECS_TIPO: dict = {
    "ventas_hoy":           {"titulo": "Ventas de hoy (pagadas)",          "layout": "kpi_bar"},
    "ventas_sucursal":      {"titulo": "Ventas por sucursal",               "layout": "kpi_bar"},
    "top_vendedores":       {"titulo": "Top vendedores",                    "layout": "ranking_hbar"},
    "comparativo_meses":    {"titulo": "Comparativo de ventas por mes",     "layout": "trend_area"},
    "pedidos_activos":      {"titulo": "Pedidos activos por sucursal",      "layout": "donut_split"},
    "ventas_diario":        {"titulo": "Ventas diarias — últimos 30 días",  "layout": "trend_area"},
    "tendencia_anual":      {"titulo": "Tendencia anual de ventas",         "layout": "trend_area"},
    "top_productos":        {"titulo": "Top productos más vendidos",        "layout": "ranking_hbar"},
    "clientes_frecuentes":  {"titulo": "Clientes más frecuentes",           "layout": "ranking_hbar"},
    "variacion_vendedores": {"titulo": "Variación de vendedores",           "layout": "dual_compare"},
}

_SISTEMA_CLASIFICADOR = """
Eres el clasificador de dashboards del Studio Analítico de una empresa distribuidora farmacéutica.
Tu trabajo: leer la solicitud del usuario y elegir la función de datos más adecuada.

Funciones disponibles:
  ventas_hoy           → ventas pagadas del día actual. layout: kpi_bar
  ventas_sucursal      → ventas por sucursal vs período anterior. modo: 30d|mes. layout: kpi_bar
  top_vendedores       → top 10 vendedores por importe. modo: 30d|mes. layout: ranking_hbar
  comparativo_meses    → ventas mes a mes (últimos 6 meses). layout: trend_area
  ventas_diario        → ventas por día (últimos 30 días). layout: trend_area
  tendencia_anual      → ventas por mes (últimos 12 meses). layout: trend_area
  pedidos_activos      → pedidos activos por sucursal. layout: donut_split
  top_productos        → top 10 productos más vendidos. modo: 30d|mes. layout: ranking_hbar
  clientes_frecuentes  → top 15 clientes por importe comprado. modo: 30d|mes. layout: ranking_hbar
  variacion_vendedores → vendedores: período actual vs anterior. modo: 30d|mes. layout: dual_compare

Responde ÚNICAMENTE con JSON válido, sin markdown, sin explicación:
{"funcion":"<nombre>","modo":"30d","titulo":"<título conciso en español>","layout":"<layout>"}

Si no puedes clasificar con certeza, usa ventas_sucursal.
"""

_SISTEMA_NARRADOR = """
Eres analista de negocios de una distribuidora farmacéutica.
Dado el resumen de datos que te proporciono, escribe UN párrafo ejecutivo de 2-3 oraciones.
Menciona el dato más destacado con su cifra exacta, una comparación o tendencia, y una conclusión.
Sin markdown, sin bullets, sin encabezados. Solo texto directo y profesional.
"""


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

    # ── Ventas por día (últimos 30 días) ─────────────────────────────────────
    elif tipo == "ventas_diario":
        rows = query(f"""
            SELECT fecha, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT CAST(c.Fecha_Documento AS DATE) AS fecha,
                       c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-29,CAST({hoy()} AS DATE))
                GROUP BY CAST(c.Fecha_Documento AS DATE), c.Cve_Folio
            ) t GROUP BY fecha ORDER BY fecha
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Ventas diarias — últimos 30 días",
                             "total": total, "datos": rows})

    # ── Tendencia anual (últimos 12 meses) ────────────────────────────────────
    elif tipo == "tendencia_anual":
        rows = query(f"""
            SELECT anio, mes, mes_nombre, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT YEAR(c.Fecha_Documento) AS anio,
                       MONTH(c.Fecha_Documento) AS mes,
                       DATENAME(MONTH, c.Fecha_Documento) AS mes_nombre,
                       c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND c.Fecha_Documento >= DATEADD(MONTH,-11,
                      DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1))
                GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento),
                         DATENAME(MONTH, c.Fecha_Documento), c.Cve_Folio
            ) t GROUP BY anio, mes, mes_nombre ORDER BY anio, mes
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Tendencia anual de ventas",
                             "total": total, "datos": rows})

    # ── Top productos ─────────────────────────────────────────────────────────
    elif tipo == "top_productos":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        else:
            filtro = (f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) "
                      f"AND MONTH(c.Fecha_Documento)=MONTH({hoy()})")
        rows = query(f"""
            SELECT TOP 10
                ISNULL(p.Nombre_Producto, t.Cve_Producto) AS label,
                t.valor AS valor,
                t.unidades AS unidades
            FROM (
                SELECT d.Cve_Producto,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor,
                       SUM(d.Cantidad_Ordenada) AS unidades
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO' AND {filtro}
                GROUP BY d.Cve_Producto
            ) t
            LEFT JOIN IM_Productos_Gral p ON p.Cve_Producto=t.Cve_Producto
            ORDER BY t.valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Top productos ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "total": total, "datos": rows})

    # ── Clientes frecuentes ───────────────────────────────────────────────────
    elif tipo == "clientes_frecuentes":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        else:
            filtro = (f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) "
                      f"AND MONTH(c.Fecha_Documento)=MONTH({hoy()})")
        rows = query(f"""
            SELECT TOP 15
                ISNULL(cl.Nombre_Cliente, t.Cve_Cliente) AS label,
                SUM(t.valor) AS valor,
                COUNT(t.folio) AS pedidos
            FROM (
                SELECT c.Cve_Cliente, c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO' AND {filtro}
                GROUP BY c.Cve_Cliente, c.Cve_Folio
            ) t
            LEFT JOIN GC_Clientes cl ON cl.Cve_Cliente=t.Cve_Cliente
            GROUP BY t.Cve_Cliente, cl.Nombre_Cliente
            ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Clientes frecuentes ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "total": total, "datos": rows})

    # ── Variación de vendedores ───────────────────────────────────────────────
    elif tipo == "variacion_vendedores":
        if modo == "30d":
            fa = f"CAST(agg.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
            fb = (f"CAST(agg.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha}) "
                  f"AND CAST(agg.Fecha_Documento AS DATE) < DATEADD(DAY,-30,{hoy_fecha})")
        else:
            fa = (f"YEAR(agg.Fecha_Documento)=YEAR({hoy()}) "
                  f"AND MONTH(agg.Fecha_Documento)=MONTH({hoy()})")
            fb = (f"YEAR(agg.Fecha_Documento)=YEAR(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND MONTH(agg.Fecha_Documento)=MONTH(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND DAY(agg.Fecha_Documento)<=DAY({hoy()})")
        rows = query(f"""
            SELECT TOP 10
                ISNULL(v.Nombre, agg.Cve_Vendedor) AS label,
                ISNULL(SUM(CASE WHEN {fa} THEN agg.Monto END),0) AS actual,
                ISNULL(SUM(CASE WHEN {fb} THEN agg.Monto END),0) AS anterior
            FROM (
                SELECT c.Cve_Vendedor, c.Fecha_Documento,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                GROUP BY c.Cve_Vendedor, c.Cve_Folio, c.Fecha_Documento
            ) agg
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor=agg.Cve_Vendedor
            GROUP BY agg.Cve_Vendedor, v.Nombre
            ORDER BY actual DESC
        """)
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Variación de vendedores ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "series": ["Período actual", "Período anterior"],
                             "datos": rows})

    raise HTTPException(status_code=404, detail=f"Plantilla '{tipo}' no existe")


# ── Helpers internos para generar ─────────────────────────────────────────────

def _fmt_mxn(n: float) -> str:
    """Formatea número como moneda MXN sin decimales."""
    return f"${n:,.0f}"


def _resumir_datos(tipo: str, datos: dict) -> str:
    """Genera un resumen textual de los datos para alimentar al narrador IA."""
    lista = datos.get("datos", [])
    if not lista:
        return "Sin datos disponibles."

    if tipo in ("ventas_hoy",):
        total = float(datos.get("total", 0) or 0)
        top = lista[:3]
        resumen = f"Total del día: {_fmt_mxn(total)}. "
        resumen += "Líderes: " + ", ".join(
            f"{r.get('label','?')} {_fmt_mxn(float(r.get('valor', 0) or 0))}"
            for r in top
        )

    elif tipo in ("ventas_sucursal",):
        total = sum(float(r.get("actual", 0) or 0) for r in lista)
        top = lista[0] if lista else {}
        modo = datos.get("modo", "30d")
        periodo = "últimos 30 días" if modo == "30d" else "mes actual"
        resumen = (f"Total {periodo}: {_fmt_mxn(total)}. "
                   f"Líder: {top.get('label','?')} con {_fmt_mxn(float(top.get('actual', 0) or 0))}.")
        # Variaciones destacadas
        crece = [r for r in lista if (r.get("variacion_pct") or 0) > 10][:2]
        cae   = [r for r in lista if (r.get("variacion_pct") or 0) < -10][:1]
        if crece:
            resumen += " Crecen: " + ", ".join(f"{r['label']} +{r['variacion_pct']}%" for r in crece) + "."
        if cae:
            resumen += " Caen: " + ", ".join(f"{r['label']} {r['variacion_pct']}%" for r in cae) + "."

    elif tipo in ("top_vendedores", "top_productos", "clientes_frecuentes"):
        total = sum(float(r.get("valor", 0) or 0) for r in lista)
        top3 = lista[:3]
        resumen = f"Total general: {_fmt_mxn(total)}. Top 3: "
        resumen += " | ".join(
            f"{r.get('label','?')} {_fmt_mxn(float(r.get('valor', 0) or 0))}"
            for r in top3
        )

    elif tipo in ("comparativo_meses", "tendencia_anual"):
        meses = lista
        if len(meses) >= 2:
            ult   = meses[-1]
            penult = meses[-2]
            v_ult   = float(ult.get("valor", 0) or 0)
            v_penult = float(penult.get("valor", 0) or 0)
            var = round((v_ult - v_penult) / v_penult * 100, 1) if v_penult else 0
            signo = "+" if var >= 0 else ""
            peak = max(meses, key=lambda m: float(m.get("valor", 0) or 0))
            nombre_ult = ult.get("mes_nombre", str(ult.get("mes", "último")))
            nombre_peak = peak.get("mes_nombre", "?")
            resumen = (f"Mes más reciente ({nombre_ult}): {_fmt_mxn(v_ult)} "
                       f"({signo}{var}% vs anterior). Mejor mes: {nombre_peak} "
                       f"con {_fmt_mxn(float(peak.get('valor', 0) or 0))}.")
        else:
            resumen = f"Datos de {len(meses)} mes(es)."

    elif tipo == "ventas_diario":
        if lista:
            total = float(datos.get("total", 0) or 0)
            mejor = max(lista, key=lambda d: float(d.get("valor", 0) or 0))
            resumen = (f"Total últimos 30 días: {_fmt_mxn(total)}. "
                       f"Mejor día: {mejor.get('fecha','?')} con {_fmt_mxn(float(mejor.get('valor', 0) or 0))}.")
        else:
            resumen = "Sin ventas en los últimos 30 días."

    elif tipo == "pedidos_activos":
        total = int(datos.get("total", 0) or 0)
        top2 = lista[:2]
        resumen = f"Total pedidos activos: {total}. "
        resumen += " | ".join(f"{r.get('label','?')}: {r.get('valor', 0)}" for r in top2) + "."

    elif tipo == "variacion_vendedores":
        mejores = [r for r in lista if float(r.get("actual", 0) or 0) > float(r.get("anterior", 0) or 0)][:2]
        caidas  = [r for r in lista if float(r.get("actual", 0) or 0) < float(r.get("anterior", 0) or 0)][:2]
        top = lista[0] if lista else {}
        resumen = f"Líder del período: {top.get('label','?')} con {_fmt_mxn(float(top.get('actual', 0) or 0))}. "
        if mejores:
            resumen += "Mejoran: " + ", ".join(r.get("label", "?") for r in mejores) + ". "
        if caidas:
            resumen += "Bajan: " + ", ".join(r.get("label", "?") for r in caidas) + "."
    else:
        resumen = f"Dashboard '{tipo}' con {len(lista)} registros."

    return resumen


def _clasificar(pregunta: str) -> dict:
    """Clasifica la pregunta con gpt-5-nano y devuelve el spec del dashboard."""
    try:
        resp = _client.chat.completions.create(
            model=STUDIO_IA_MODEL,
            messages=[
                {"role": "system", "content": _SISTEMA_CLASIFICADOR},
                {"role": "user",   "content": pregunta},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        texto = resp.choices[0].message.content.strip()
        parsed = json.loads(texto)
        # Validar que la función exista
        if parsed.get("funcion") not in _SPECS_TIPO:
            raise ValueError(f"Función desconocida: {parsed.get('funcion')}")
        return parsed
    except Exception:
        return {
            "funcion": "ventas_sucursal", "modo": "30d",
            "titulo": "Ventas por sucursal", "layout": "kpi_bar",
        }


def _narrar(pregunta: str, tipo: str, modo: str, datos: dict) -> tuple[str, float]:
    """Genera una narrativa ejecutiva con gpt-5-nano. Devuelve (texto, costo_usd)."""
    resumen = _resumir_datos(tipo, datos)
    user_prompt = f"Solicitud: {pregunta}\nDatos: {resumen}"
    try:
        resp = _client.chat.completions.create(
            model=STUDIO_IA_MODEL,
            messages=[
                {"role": "system", "content": _SISTEMA_NARRADOR},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        texto = resp.choices[0].message.content.strip()
        costo = 0.0
        if resp.usage:
            costo = (
                resp.usage.prompt_tokens     * IA_PRECIO_INPUT
                + resp.usage.completion_tokens * IA_PRECIO_OUTPUT
            )
        return texto, costo
    except Exception:
        return "Análisis generado con datos del ERP en tiempo real.", 0.0


# ── Modelos adicionales ───────────────────────────────────────────────────────

class GenerarBody(BaseModel):
    """Cuerpo para POST /api/datos/generar."""
    pregunta: Optional[str] = None   # Texto libre del usuario
    tipo:     Optional[str] = None   # Tipo predefinido (omite clasificación IA)
    modo:     Optional[str] = "30d"  # 30d | mes


# ── Generar dashboard con IA ──────────────────────────────────────────────────

@router.post("/generar")
def generar_dashboard(body: GenerarBody, usuario=Depends(get_current_user)):
    """
    Genera un dashboard completo usando gpt-5-nano.

    Flujo:
      1. Si se pasa 'tipo', lo usa directamente (sin clasificación IA).
         Si solo viene 'pregunta', clasifica con gpt-5-nano.
      2. Obtiene los datos del ERP mediante la función correspondiente.
      3. Genera narrativa ejecutiva de 2-3 oraciones con gpt-5-nano.
      4. Descuenta IA_RATIO_STUDIO consultas del usuario.

    Returns:
        JSON con tipo, layout, titulo, modo, narrativa, datos.
    """
    # Verificar límite de IA
    u = fetch_one(
        "SELECT consultas_ia, limite_ia FROM usuarios WHERE id=?", (usuario["id"],)
    )
    if u and u["limite_ia"] > 0 and u["consultas_ia"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu límite de consultas de IA. Contacta a tu administrador.",
        )

    tipo     = body.tipo
    modo     = body.modo or "30d"
    pregunta = body.pregunta or ""
    layout   = None
    titulo   = None

    # Paso 1: Clasificar si no hay tipo predefinido
    if not tipo:
        if not pregunta:
            raise HTTPException(400, "Se requiere 'pregunta' o 'tipo'.")
        clasificacion = _clasificar(pregunta)
        tipo   = clasificacion.get("funcion", "ventas_sucursal")
        modo   = clasificacion.get("modo", "30d")
        titulo = clasificacion.get("titulo")
        layout = clasificacion.get("layout")

    if tipo not in _SPECS_TIPO:
        raise HTTPException(400, f"Tipo '{tipo}' no reconocido.")

    spec   = _SPECS_TIPO[tipo]
    titulo = titulo or spec["titulo"]
    layout = layout or spec["layout"]

    # Paso 2: Obtener datos del ERP
    try:
        datos = _fetch_tipo(tipo, modo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Error al obtener datos del ERP: {e}")

    # Paso 3: Narrativa con gpt-5-nano
    costo = 0.0
    try:
        narrativa, costo = _narrar(pregunta or titulo, tipo, modo, datos)
    except Exception:
        narrativa = "Análisis generado con datos del ERP en tiempo real."

    # Paso 4: Descontar créditos
    execute(
        "UPDATE usuarios SET consultas_ia = consultas_ia + ?, "
        "costo_ia_usd = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
        (IA_RATIO_STUDIO, costo, usuario["id"]),
    )

    return JSONResponse({
        "tipo":      tipo,
        "layout":    layout,
        "titulo":    titulo,
        "modo":      modo,
        "narrativa": narrativa,
        "datos":     datos,
    })


def _fetch_tipo(tipo: str, modo: str) -> dict:
    """Llama internamente a la función de datos correcta según el tipo."""
    hoy_fecha = f"CAST({hoy()} AS DATE)"

    if tipo == "ventas_hoy":
        rows = query(f"""
            SELECT s.Nombre AS label, COUNT(t.Cve_Folio) AS pedidos,
                   ISNULL(SUM(t.Monto),0) AS valor
            FROM GN_Sucursales s
            LEFT JOIN (
                SELECT c.Cve_Sucursal, c.Cve_Folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                  ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE)=CAST({hoy()} AS DATE)
                GROUP BY c.Cve_Sucursal, c.Cve_Folio
            ) t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "titulo": "Ventas del día (pagadas)", "total": total, "datos": rows}

    elif tipo == "ventas_sucursal":
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
                GROUP BY c.Cve_Sucursal, c.Cve_Folio, c.Fecha_Documento
            ) t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY actual DESC
        """)
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Ventas por sucursal ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                "series": ["Período actual", "Período anterior"], "datos": rows}

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
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Top vendedores ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                "datos": rows}

    elif tipo == "comparativo_meses":
        rows = query(f"""
            SELECT TOP 6 anio, mes, mes_nombre, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT YEAR(c.Fecha_Documento) AS anio, MONTH(c.Fecha_Documento) AS mes,
                       DATENAME(MONTH, c.Fecha_Documento) AS mes_nombre,
                       c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND c.Fecha_Documento >= DATEADD(MONTH,-5,{hoy()})
                GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento),
                         DATENAME(MONTH, c.Fecha_Documento), c.Cve_Folio
            ) t GROUP BY anio, mes, mes_nombre ORDER BY anio, mes
        """)
        return {"tipo": tipo, "titulo": "Ventas últimos 6 meses", "datos": rows}

    elif tipo == "pedidos_activos":
        rows = query(f"""
            SELECT s.Nombre AS label, COUNT(CASE WHEN p.Estatus='AC' THEN 1 END) AS valor
            FROM GN_Sucursales s
            LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING COUNT(CASE WHEN p.Estatus='AC' THEN 1 END)>0
            ORDER BY valor DESC
        """)
        total = sum(r.get("valor") or 0 for r in rows)
        return {"tipo": tipo, "titulo": "Pedidos activos por sucursal", "total": total, "datos": rows}

    elif tipo == "ventas_diario":
        rows = query(f"""
            SELECT fecha, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT CAST(c.Fecha_Documento AS DATE) AS fecha, c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-29,CAST({hoy()} AS DATE))
                GROUP BY CAST(c.Fecha_Documento AS DATE), c.Cve_Folio
            ) t GROUP BY fecha ORDER BY fecha
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "titulo": "Ventas diarias — últimos 30 días", "total": total, "datos": rows}

    elif tipo == "tendencia_anual":
        rows = query(f"""
            SELECT anio, mes, mes_nombre, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT YEAR(c.Fecha_Documento) AS anio, MONTH(c.Fecha_Documento) AS mes,
                       DATENAME(MONTH, c.Fecha_Documento) AS mes_nombre, c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND c.Fecha_Documento >= DATEADD(MONTH,-11,
                      DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1))
                GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento),
                         DATENAME(MONTH, c.Fecha_Documento), c.Cve_Folio
            ) t GROUP BY anio, mes, mes_nombre ORDER BY anio, mes
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "titulo": "Tendencia anual de ventas", "total": total, "datos": rows}

    elif tipo == "top_productos":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        else:
            filtro = f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) AND MONTH(c.Fecha_Documento)=MONTH({hoy()})"
        rows = query(f"""
            SELECT TOP 10 ISNULL(p.Nombre_Producto, t.Cve_Producto) AS label,
                   t.valor AS valor, t.unidades AS unidades
            FROM (
                SELECT d.Cve_Producto,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor,
                       SUM(d.Cantidad_Ordenada) AS unidades
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO' AND {filtro}
                GROUP BY d.Cve_Producto
            ) t
            LEFT JOIN IM_Productos_Gral p ON p.Cve_Producto=t.Cve_Producto
            ORDER BY t.valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Top productos ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                "total": total, "datos": rows}

    elif tipo == "clientes_frecuentes":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        else:
            filtro = f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) AND MONTH(c.Fecha_Documento)=MONTH({hoy()})"
        rows = query(f"""
            SELECT TOP 15 ISNULL(cl.Nombre_Cliente, t.Cve_Cliente) AS label,
                   SUM(t.valor) AS valor, COUNT(t.folio) AS pedidos
            FROM (
                SELECT c.Cve_Cliente, c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO' AND {filtro}
                GROUP BY c.Cve_Cliente, c.Cve_Folio
            ) t
            LEFT JOIN GC_Clientes cl ON cl.Cve_Cliente=t.Cve_Cliente
            GROUP BY t.Cve_Cliente, cl.Nombre_Cliente ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Clientes frecuentes ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                "total": total, "datos": rows}

    elif tipo == "variacion_vendedores":
        if modo == "30d":
            fa = f"CAST(agg.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
            fb = (f"CAST(agg.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha}) "
                  f"AND CAST(agg.Fecha_Documento AS DATE) < DATEADD(DAY,-30,{hoy_fecha})")
        else:
            fa = (f"YEAR(agg.Fecha_Documento)=YEAR({hoy()}) "
                  f"AND MONTH(agg.Fecha_Documento)=MONTH({hoy()})")
            fb = (f"YEAR(agg.Fecha_Documento)=YEAR(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND MONTH(agg.Fecha_Documento)=MONTH(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND DAY(agg.Fecha_Documento)<=DAY({hoy()})")
        rows = query(f"""
            SELECT TOP 10 ISNULL(v.Nombre, agg.Cve_Vendedor) AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN agg.Monto END),0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN agg.Monto END),0) AS anterior
            FROM (
                SELECT c.Cve_Vendedor, c.Fecha_Documento,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS Monto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                GROUP BY c.Cve_Vendedor, c.Cve_Folio, c.Fecha_Documento
            ) agg
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor=agg.Cve_Vendedor
            GROUP BY agg.Cve_Vendedor, v.Nombre ORDER BY actual DESC
        """)
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Variación de vendedores ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                "series": ["Período actual", "Período anterior"], "datos": rows}

    raise HTTPException(status_code=404, detail=f"Tipo '{tipo}' no existe")


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
