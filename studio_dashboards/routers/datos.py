# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.3.0
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
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.config import (
    OPENAI_API_KEY, STUDIO_IA_MODEL,
    STUDIO_PRECIO_INPUT, STUDIO_PRECIO_OUTPUT, IA_RATIO_STUDIO,
)
from shared.database import query, hoy
from shared.database_local import execute, fetch_all, fetch_one

router = APIRouter(prefix="/api/datos", dependencies=[Depends(get_current_user)])

_client = OpenAI(api_key=OPENAI_API_KEY)


def _proyectar(valores: list) -> float:
    """Regresión lineal simple — proyecta el siguiente valor de la serie."""
    serie = [float(v) for v in valores if v is not None]
    n = len(serie)
    if n < 2:
        return serie[-1] if serie else 0.0
    xs = list(range(n))
    x_m = sum(xs) / n
    y_m = sum(serie) / n
    num = sum((xs[i] - x_m) * (serie[i] - y_m) for i in range(n))
    den = sum((x - x_m) ** 2 for x in xs)
    slope = num / den if den else 0.0
    return max(0.0, round(y_m + slope * (n - x_m), 2))


def _filtros_periodo(modo: str, campo: str, fi: str = None, ff: str = None):
    """
    Devuelve (fa, fb, label) para construir filtros SQL de período.

    fa    — condición SQL período actual
    fb    — condición SQL período anterior (para comparación)
    label — texto legible del período (ej. 'Últ. 30 días')

    campo: campo SQL de fecha (ej. 't.Fecha_Documento')
    fi, ff: fechas ISO 'YYYY-MM-DD' cuando modo='custom'
    """
    h = f"CAST({hoy()} AS DATE)"
    c = f"CAST({campo} AS DATE)"
    if modo == "hoy":
        return (
            f"{c} = {h}",
            f"{c} = DATEADD(DAY,-1,{h})",
            "Hoy",
        )
    if modo == "15d":
        return (
            f"{c} >= DATEADD(DAY,-15,{h})",
            f"{c} >= DATEADD(DAY,-30,{h}) AND {c} < DATEADD(DAY,-15,{h})",
            "Últ. 15 días",
        )
    if modo == "mes":
        return (
            f"YEAR({campo})=YEAR({hoy()}) AND MONTH({campo})=MONTH({hoy()})",
            (f"YEAR({campo})=YEAR(DATEADD(MONTH,-1,{hoy()})) "
             f"AND MONTH({campo})=MONTH(DATEADD(MONTH,-1,{hoy()})) "
             f"AND DAY({campo})<=DAY({hoy()})"),
            "Mes actual",
        )
    if modo == "custom" and fi and ff:
        return (
            f"{c} >= '{fi}' AND {c} <= '{ff}'",
            f"{c} < '{fi}' AND {c} >= DATEADD(DAY,-(DATEDIFF(DAY,'{fi}','{ff}')+1),'{fi}')",
            f"{fi[5:]} → {ff[5:]}",
        )
    # default: 30d
    return (
        f"{c} >= DATEADD(DAY,-30,{h})",
        f"{c} >= DATEADD(DAY,-60,{h}) AND {c} < DATEADD(DAY,-30,{h})",
        "Últ. 30 días",
    )


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
    "reporte_ventas":       {"titulo": "Dashboard de Ventas",               "layout": "full_report"},
    "ventas_producto":      {"titulo": "Ventas de producto por sucursal",   "layout": "ranking_hbar"},
    # Inventario
    "reporte_inventario":   {"titulo": "Dashboard de Inventario",           "layout": "inventory_report"},
    "inventario_stock":     {"titulo": "Stock actual por sucursal",         "layout": "kpi_bar"},
    "caducidades":          {"titulo": "Productos por caducar (90 días)",   "layout": "ranking_hbar"},
    "stockouts":            {"titulo": "Productos sin existencia",          "layout": "ranking_hbar"},
}

_SISTEMA_CLASIFICADOR = """
Eres el clasificador de dashboards del Studio Analítico de una empresa distribuidora farmacéutica.
Tu trabajo: leer la solicitud del usuario y decidir qué dashboard visual generar.

REGLA PRINCIPAL — Studio SIEMPRE genera dashboards. Usa "ninguno" solo como último recurso.

Ejemplos de solicitudes → función correcta:
  "ventas de hoy"                              → ventas_hoy
  "dame ventas"  /  "muéstrame ventas"         → reporte_ventas
  "gráfica de sucursales"  /  "ventas por sucursal"  /  "comparativa de sucursales" → ventas_sucursal + single_chart
  "top vendedores"  /  "mejores vendedores"    → top_vendedores
  "gráfica de línea de ventas por mes"         → comparativo_meses + single_chart + chart_type:line
  "tendencia anual"                            → tendencia_anual
  "dona de pedidos"  /  "pedidos activos"      → pedidos_activos
  "productos más vendidos"                     → top_productos
  "clientes frecuentes"                        → clientes_frecuentes
  "variación de vendedores"                    → variacion_vendedores
  "inventario"  /  "stock"                     → reporte_inventario
  "caducidades"                                → caducidades
  "sin existencia"  /  "sin stock"             → stockouts

Usa "ninguno" SOLO para preguntas que NO encajan (ej: precio de un medicamento específico,
soporte técnico, preguntas conceptuales sin datos del ERP).

Funciones disponibles:
  ninguno              → No encaja en ninguna función. Solo texto.
  reporte_ventas       → Dashboard COMPLETO: sucursales + productos + vendedores + tendencia. layout: full_report
  ventas_hoy           → Ventas pagadas del día actual. layout: kpi_bar
  ventas_sucursal      → Ventas por sucursal vs período anterior. layout: kpi_bar
  top_vendedores       → Top 10 vendedores por importe. layout: ranking_hbar
  comparativo_meses    → Ventas mes a mes (últimos 6 meses) con proyección del siguiente mes. layout: trend_area
  ventas_diario        → Ventas por día (últimos 30 días) con proyección. layout: trend_area
  tendencia_anual      → Ventas por mes (últimos 12 meses) con proyección. layout: trend_area
  pedidos_activos      → Pedidos activos por sucursal. layout: donut_split
  top_productos        → Top 10 productos más vendidos. layout: ranking_hbar
  clientes_frecuentes  → Top 15 clientes por importe comprado. layout: ranking_hbar
  variacion_vendedores → Vendedores: período actual vs anterior. layout: dual_compare
  reporte_inventario   → Dashboard COMPLETO de inventario: stock + caducidades + stockouts. layout: inventory_report
  inventario_stock     → Stock actual por sucursal: valor en MXN y unidades. layout: kpi_bar
  caducidades          → Productos con lotes próximos a caducar (90 días). layout: ranking_hbar
  stockouts            → Sucursales con más productos sin existencia (stock = 0). layout: ranking_hbar
  ventas_producto      → Ventas de un producto específico por sucursal. Requiere campo extra "producto". layout: ranking_hbar
                         Usar cuando el usuario mencione un producto concreto: "ventas de Omnitrope en mayo",
                         "gráfica de Saizen por sucursal", "cuánto vendimos de Norditropin en enero".

Modos de período disponibles:
  "hoy"    → solo el día actual
  "15d"    → últimos 15 días
  "30d"    → últimos 30 días (por defecto)
  "mes"    → mes en curso
  "custom" → rango de fechas específico indicado por el usuario

REGLAS:
- Si el usuario pide ventas/dashboard general sin especificar tipo, usa reporte_ventas con modo 30d.
- Si el usuario indica un rango de fechas específico (ej. "del 1 al 15 de enero de 2026",
  "entre marzo y mayo"), usa modo "custom" e incluye "fecha_inicio" y "fecha_fin" en formato
  "YYYY-MM-DD". Resuelve meses por nombre usando el año indicado o el año de la fecha de hoy.
- Si el usuario dice "hoy" o "del día" → modo "hoy".
- Si el usuario dice "últimos 15 días" → modo "15d".
- Si el usuario dice "mes actual" o "este mes" → modo "mes".

Layouts disponibles:
  kpi_bar          → KPIs en tarjetas + gráfica de barras + tabla. Default para ventas por sucursal.
  ranking_hbar     → Ranking horizontal con top 10. Default para vendedores y productos.
  trend_area       → Línea/área de tendencia. Default para comparativo de meses y tendencia anual.
  donut_split      → Dona + KPIs. Default para pedidos activos.
  dual_compare     → Comparación de dos períodos lado a lado. Default para variación de vendedores.
  full_report      → Dashboard multi-panel completo. Default para reporte_ventas.
  inventory_report → Dashboard de inventario multi-panel. Default para reporte_inventario.
  single_chart     → UNA sola gráfica a pantalla completa. Usar cuando el usuario pida
                     explícitamente un tipo de gráfica (barra, línea, donut, comparativa, etc.)
                     o cuando la solicitud no encaje bien en ningún layout predefinido.
                     Requiere campo adicional "chart_type": "bar" | "hbar" | "line" | "area" | "donut"

JSON estándar (sin fechas custom):
{"funcion":"<nombre>","modo":"30d","titulo":"<título conciso en español>","layout":"<layout>"}

JSON con rango custom:
{"funcion":"<nombre>","modo":"custom","titulo":"<título conciso en español>","layout":"<layout>","fecha_inicio":"YYYY-MM-DD","fecha_fin":"YYYY-MM-DD"}

JSON con single_chart (agrega chart_type):
{"funcion":"<nombre>","modo":"30d","layout":"single_chart","chart_type":"bar","titulo":"<título>"}
JSON con single_chart + rango custom:
{"funcion":"<nombre>","modo":"custom","layout":"single_chart","chart_type":"line","titulo":"<título>","fecha_inicio":"YYYY-MM-DD","fecha_fin":"YYYY-MM-DD"}

JSON para ventas_producto (campo extra "producto" obligatorio):
{"funcion":"ventas_producto","modo":"mes","titulo":"Ventas de Omnitrope por sucursal — Mayo 2026","layout":"ranking_hbar","producto":"OMNITROPE"}
JSON para ventas_producto con rango custom:
{"funcion":"ventas_producto","modo":"custom","titulo":"...","layout":"ranking_hbar","producto":"SAIZEN 20","fecha_inicio":"2026-05-01","fecha_fin":"2026-05-31"}

Si no aplica dashboard: {"funcion":"ninguno","modo":"","titulo":"","layout":""}
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
def ventas_sucursales(modo: str = Query("30d", regex="^(hoy|15d|30d|mes)$")):
    """
    Ventas por sucursal para los dashboards del Studio.

    Args:
        modo: '30d' → últimos 30 días vs 30 anteriores / 'mes' → mes actual vs anterior

    Returns:
        JSON con lista de sucursales, ventas, facturas y variación porcentual.
    """
    filtro_actual, filtro_anterior, _ = _filtros_periodo(modo, "t.Fecha_Documento")

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
def kpis_globales(modo: str = Query("30d", regex="^(hoy|15d|30d|mes)$")):
    """
    Totales globales para las tarjetas KPI del Studio.

    Args:
        modo: 'hoy' | '15d' | '30d' | 'mes'

    Returns:
        JSON con ventas_total, facturas_total, pedidos_activos, sucursales_activas.
    """
    filtro, _, _ = _filtros_periodo(modo, "c.Fecha_Documento")

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
    ventas_total   = float(v.get("ventas_total") or 0)
    facturas_total = int(v.get("facturas_total") or 0)
    ticket_promedio = round(ventas_total / facturas_total, 2) if facturas_total > 0 else 0

    # Top sucursal del período
    top_suc_row = query(f"""
        SELECT TOP 1 s.Nombre AS nombre,
               ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS total
        FROM FT_Pedidos_C c
        INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
        INNER JOIN GN_Sucursales s ON s.Cve_Sucursal=c.Cve_Sucursal
        WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
          AND c.Cve_Sucursal<>99 AND {filtro}
        GROUP BY c.Cve_Sucursal, s.Nombre ORDER BY total DESC
    """)
    top_sucursal = (top_suc_row[0].get("nombre") or "—") if top_suc_row else "—"

    return JSONResponse({
        "ventas_total":       ventas_total,
        "facturas_total":     facturas_total,
        "pedidos_activos":    int((pedidos_row[0] or {}).get("pedidos_activos") or 0),
        "sucursales_activas": int((sucursales_row[0] or {}).get("total") or 0),
        "ticket_promedio":    ticket_promedio,
        "top_sucursal":       top_sucursal,
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
def plantilla(tipo: str, modo: str = Query("30d", regex="^(hoy|15d|30d|mes)$")):
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
        fa, fb, _ = _filtros_periodo(modo, "t.Fecha_Documento")
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
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento")
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
                             "titulo": f"Top vendedores ({label})",
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
                ISNULL(p.Descripcion, t.Cve_Producto) AS label,
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
            HAVING ISNULL(cl.Nombre_Cliente, t.Cve_Cliente) NOT LIKE '%MOSTRADOR%'
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
    # Dashboards multi-panel: datos["datos"] es un dict, no una lista — no hacer early-return
    if not lista and tipo not in ("reporte_inventario", "reporte_ventas"):
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
    elif tipo == "reporte_inventario":
        # _fetch_tipo devuelve { ..., datos: { inventario_stock, ... } } — desenvolver
        _inner     = datos.get("datos") if isinstance(datos.get("datos"), dict) else datos
        stock_data = _inner.get("inventario_stock", {})
        cad_data   = _inner.get("caducidades", {})
        out_data   = _inner.get("stockouts", {})
        total_v    = float(stock_data.get("total_valor", 0) or 0)
        total_u    = float(stock_data.get("total_unidades", 0) or 0)
        criticos   = sum(int(r.get("criticos", 0) or 0) for r in stock_data.get("datos", []))
        n_cad      = len(cad_data.get("datos", []))
        n_out      = int(out_data.get("total", 0) or 0)
        resumen = (f"Inventario total: {_fmt_mxn(total_v)} · {int(total_u):,} unidades. "
                   f"Productos críticos: {criticos}. "
                   f"Lotes por caducar (90d): {n_cad}. "
                   f"Productos sin stock: {n_out:,}.")
    elif tipo == "inventario_stock":
        total_v = float(datos.get("total_valor", 0) or 0)
        total_u = float(datos.get("total_unidades", 0) or 0)
        top = lista[0] if lista else {}
        criticos = sum(int(r.get("criticos", 0) or 0) for r in lista)
        resumen = (f"Valor total en stock: {_fmt_mxn(total_v)}. "
                   f"Unidades: {int(total_u):,}. "
                   f"Sucursal con mayor stock: {top.get('label','?')} ({_fmt_mxn(float(top.get('actual', 0) or 0))}). "
                   f"Productos críticos (≤5 piezas): {criticos}.")

    elif tipo == "caducidades":
        total_uds = sum(float(r.get("unidades", 0) or 0) for r in lista)
        urgentes  = [r for r in lista if int(r.get("dias", 999) or 999) <= 30]
        resumen = (f"Lotes próximos a caducar (90 días): {len(lista)}. "
                   f"Unidades en riesgo: {int(total_uds):,}. "
                   f"Urgentes (≤30 días): {len(urgentes)}.")
        if lista:
            prox = lista[0]
            resumen += f" Más próximo: {prox.get('label','?')} en {prox.get('dias','?')} días."

    elif tipo == "stockouts":
        total_prod = int(sum(float(r.get("valor", 0) or 0) for r in lista))
        top = lista[0] if lista else {}
        resumen = (f"Total de productos sin existencia: {total_prod:,} en {len(lista)} sucursal(es). "
                   f"Más afectada: {top.get('label','?')} con {int(float(top.get('valor', 0) or 0)):,} productos sin stock.")

    else:
        resumen = f"Dashboard '{tipo}' con {len(lista)} registros."

    return resumen


def _clasificar(pregunta: str) -> dict:
    """Clasifica la pregunta con gpt-5-nano y devuelve el spec del dashboard."""
    try:
        pregunta_con_fecha = f"[Hoy es {_date.today().strftime('%Y-%m-%d')}]\n{pregunta}"
        resp = _client.chat.completions.create(
            model=STUDIO_IA_MODEL,
            messages=[
                {"role": "system", "content": _SISTEMA_CLASIFICADOR},
                {"role": "user",   "content": pregunta_con_fecha},
            ],
            response_format={"type": "json_object"},
        )
        texto = resp.choices[0].message.content.strip()
        print(f"[clasificar] pregunta={pregunta!r} → raw={texto}", flush=True)
        parsed = json.loads(texto)
        funcion = parsed.get("funcion")
        # "ninguno" es válido — significa que no se requiere dashboard
        if funcion == "ninguno":
            return {"funcion": "ninguno"}
        if funcion not in _SPECS_TIPO:
            raise ValueError(f"Función desconocida: {funcion}")
        # Validar fechas si modo custom
        if parsed.get("modo") == "custom":
            if not parsed.get("fecha_inicio") or not parsed.get("fecha_fin"):
                parsed["modo"] = "30d"
        return parsed
    except Exception as e:
        print(f"[clasificar] ERROR: {e}", flush=True)
        return {"funcion": "ninguno"}


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
            max_tokens=200,
        )
        texto = resp.choices[0].message.content.strip()
        costo = 0.0
        if resp.usage:
            costo = (
                resp.usage.prompt_tokens     * STUDIO_PRECIO_INPUT
                + resp.usage.completion_tokens * STUDIO_PRECIO_OUTPUT
            )
        return texto, costo
    except Exception:
        return "Análisis generado con datos del ERP en tiempo real.", 0.0


# ── Modelos adicionales ───────────────────────────────────────────────────────

class GenerarBody(BaseModel):
    """Cuerpo para POST /api/datos/generar."""
    pregunta:     Optional[str] = None   # Texto libre del usuario
    tipo:         Optional[str] = None   # Tipo predefinido (omite clasificación IA)
    modo:         Optional[str] = "30d"  # hoy | 15d | 30d | mes | custom
    fecha_inicio: Optional[str] = None   # ISO 'YYYY-MM-DD' para modo='custom'
    fecha_fin:    Optional[str] = None   # ISO 'YYYY-MM-DD' para modo='custom'


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
        "SELECT COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, limite_ia FROM usuarios WHERE id=?", (usuario["id"],)
    )
    if u and u["limite_ia"] > 0 and u["consultas_ia_r"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu límite de consultas de IA. Contacta a tu administrador.",
        )

    tipo     = body.tipo
    modo     = body.modo or "30d"
    fi       = body.fecha_inicio
    ff       = body.fecha_fin
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
        fi     = clasificacion.get("fecha_inicio") or fi
        ff     = clasificacion.get("fecha_fin") or ff
        titulo = clasificacion.get("titulo")
        layout = clasificacion.get("layout")
        producto = clasificacion.get("producto")

    if tipo not in _SPECS_TIPO:
        raise HTTPException(400, f"Tipo '{tipo}' no reconocido.")

    spec   = _SPECS_TIPO[tipo]
    titulo = titulo or spec["titulo"]
    layout = layout or spec["layout"]

    # Paso 2: Obtener datos del ERP
    try:
        datos = _fetch_tipo(tipo, modo, fi, ff, producto=producto)
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
    # Dashboards: 3 consultas (múltiples SQL + clasificación + narración)
    # Chat Studio sin dashboard: IA_RATIO_STUDIO = 1.5 (o4-mini razonamiento)
    _RATIO_DASHBOARD = 3
    ratio = float(_RATIO_DASHBOARD if tipo != "ninguno" else IA_RATIO_STUDIO)
    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
        "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
        (ratio, ratio, costo, usuario["id"]),
    )

    chart_type = clasificacion.get("chart_type", "bar") if not body.tipo else "bar"
    return JSONResponse({
        "tipo":       tipo,
        "layout":     layout,
        "chart_type": chart_type,
        "titulo":     titulo,
        "modo":       modo,
        "narrativa":  narrativa,
        "datos":      datos,
    })


def _fetch_tipo(tipo: str, modo: str, fi: str = None, ff: str = None, producto: str = None) -> dict:
    """
    Llama internamente a la función de datos correcta según el tipo.
    fi, ff: fechas ISO 'YYYY-MM-DD' para modo='custom'
    """
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
        # Ayer
        ayer_row = query(f"""
            SELECT ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS total_ayer,
                   COUNT(DISTINCT c.Cve_Folio) AS pedidos_ayer
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
            WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
              AND CAST(c.Fecha_Documento AS DATE)=CAST(DATEADD(DAY,-1,{hoy()}) AS DATE)
              AND c.Cve_Sucursal<>99
        """)
        # Semana pasada mismo día
        semana_row = query(f"""
            SELECT ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS total_sem
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
            WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
              AND CAST(c.Fecha_Documento AS DATE)=CAST(DATEADD(DAY,-7,{hoy()}) AS DATE)
              AND c.Cve_Sucursal<>99
        """)
        total         = sum(float(r.get("valor") or 0) for r in rows)
        total_pedidos = sum(int(r.get("pedidos") or 0) for r in rows)
        total_ayer    = float((ayer_row[0] if ayer_row else {}).get("total_ayer") or 0)
        pedidos_ayer  = int((ayer_row[0] if ayer_row else {}).get("pedidos_ayer") or 0)
        total_sem     = float((semana_row[0] if semana_row else {}).get("total_sem") or 0)
        ticket_hoy    = round(total / total_pedidos, 2) if total_pedidos else 0
        ticket_ayer   = round(total_ayer / pedidos_ayer, 2) if pedidos_ayer else 0
        var_ayer      = round((total - total_ayer) / total_ayer * 100, 1) if total_ayer else None
        var_sem       = round((total - total_sem)  / total_sem  * 100, 1) if total_sem  else None
        return {
            "tipo":          tipo,
            "titulo":        "Ventas del día (pagadas)",
            "total":         total,
            "total_pedidos": total_pedidos,
            "total_ayer":    total_ayer,
            "total_sem":     total_sem,
            "ticket_hoy":    ticket_hoy,
            "ticket_ayer":   ticket_ayer,
            "var_ayer":      var_ayer,
            "var_sem":       var_sem,
            "datos":         rows,
        }

    elif tipo == "ventas_sucursal":
        fa, fb, label = _filtros_periodo(modo, "t.Fecha_Documento", fi, ff)
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
                "titulo": f"Ventas por sucursal ({label})",
                "series": ["Período actual", "Período anterior"], "datos": rows}

    elif tipo == "top_vendedores":
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
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
                "titulo": f"Top vendedores ({label})",
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
        import calendar as _cal
        from datetime import date as _d2
        _hd = _d2.today()
        _ms = _hd.month % 12 + 1
        _as = _hd.year + (1 if _ms == 1 else 0)
        proyeccion = _proyectar([float(r.get("valor") or 0) for r in rows])
        return {
            "tipo": tipo, "titulo": "Ventas últimos 6 meses",
            "proyeccion": proyeccion,
            "proyeccion_label": _cal.month_abbr[_ms],
            "datos": rows,
        }

    elif tipo == "pedidos_activos":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(CASE WHEN p.Estatus='AC' THEN 1 END) AS valor,
                   ISNULL(SUM(CASE WHEN p.Estatus='AC'
                       THEN (SELECT ISNULL(SUM(d2.Cantidad_Ordenada*d2.Precio),0)
                             FROM FT_Pedidos_Dia d2
                             WHERE d2.Cve_Folio=p.Cve_Folio AND d2.Cve_Sucursal=p.Cve_Sucursal) END),0)
                       AS valor_mxn
            FROM GN_Sucursales s
            LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING COUNT(CASE WHEN p.Estatus='AC' THEN 1 END)>0
            ORDER BY valor DESC
        """)
        # Pedidos generados en los últimos 7 días (incluye activos no pagados)
        tendencia = query(f"""
            SELECT CAST(c.Fecha_Documento AS DATE) AS fecha,
                   COUNT(DISTINCT c.Cve_Folio) AS pedidos,
                   ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
            WHERE c.Estatus<>'CN'
              AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-6,CAST({hoy()} AS DATE))
              AND c.Cve_Sucursal<>99
            GROUP BY CAST(c.Fecha_Documento AS DATE)
            ORDER BY fecha
        """)
        total     = sum(r.get("valor") or 0 for r in rows)
        val_total = sum(float(r.get("valor_mxn") or 0) for r in rows)
        return {
            "tipo":      tipo,
            "titulo":    "Pedidos activos por sucursal",
            "total":     total,
            "val_total": val_total,
            "tendencia": tendencia,
            "datos":     rows,
        }

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
        proyeccion = _proyectar([float(r.get("valor") or 0) for r in rows])
        return {
            "tipo": tipo, "titulo": "Ventas diarias — últimos 30 días",
            "total": total, "proyeccion": proyeccion,
            "proyeccion_label": "Próx. día",
            "datos": rows,
        }

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
        import calendar as _cal2
        from datetime import date as _d3
        _hd2 = _d3.today()
        _ms2 = _hd2.month % 12 + 1
        total = sum(float(r.get("valor") or 0) for r in rows)
        proyeccion = _proyectar([float(r.get("valor") or 0) for r in rows])
        return {
            "tipo": tipo, "titulo": "Tendencia anual de ventas",
            "total": total, "proyeccion": proyeccion,
            "proyeccion_label": _cal2.month_abbr[_ms2],
            "datos": rows,
        }

    elif tipo == "top_productos":
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
        rows = query(f"""
            SELECT TOP 10 ISNULL(p.Descripcion, t.Cve_Producto) AS label,
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
                "titulo": f"Top productos ({label})",
                "total": total, "datos": rows}

    elif tipo == "clientes_frecuentes":
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
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
            GROUP BY t.Cve_Cliente, cl.Nombre_Cliente
            HAVING ISNULL(cl.Nombre_Cliente, t.Cve_Cliente) NOT LIKE '%MOSTRADOR%'
            ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Clientes frecuentes ({label})",
                "total": total, "datos": rows}

    elif tipo == "variacion_vendedores":
        fa, fb, label = _filtros_periodo(modo, "agg.Fecha_Documento", fi, ff)
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
                "titulo": f"Variación de vendedores ({label})",
                "series": ["Período actual", "Período anterior"], "datos": rows}

    elif tipo == "reporte_ventas":
        # ── Dashboard multi-panel: todos los datos en un solo dict ────────────
        suc   = _fetch_tipo("ventas_sucursal",  modo, fi, ff)
        prod  = _fetch_tipo("top_productos",    modo, fi, ff)
        vend  = _fetch_tipo("top_vendedores",   modo, fi, ff)
        dia   = _fetch_tipo("ventas_diario",    modo)
        pedid = _fetch_tipo("pedidos_activos",  modo)
        meses = _fetch_tipo("comparativo_meses", modo)

        # KPIs globales
        suc_datos      = suc.get("datos", [])
        total_actual   = sum(float(r.get("actual")   or 0) for r in suc_datos)
        total_anterior = sum(float(r.get("anterior") or 0) for r in suc_datos)
        variacion      = (
            round((total_actual - total_anterior) / total_anterior * 100, 1)
            if total_anterior > 0 else None
        )
        n_sucursales = len([r for r in suc_datos if float(r.get("actual") or 0) > 0])

        # Ticket promedio y total pedidos del período
        _ft, _, _ = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
        ticket_data = query(f"""
            SELECT COUNT(DISTINCT c.Cve_Folio) AS total_pedidos,
                   ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS total_importe
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
            WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
              AND c.Cve_Sucursal<>99
              AND {_ft}
        """)
        t = ticket_data[0] if ticket_data else {}
        total_pedidos  = int(t.get("total_pedidos") or 0)
        ticket_promedio = round(
            float(t.get("total_importe") or 0) / total_pedidos, 2
        ) if total_pedidos else 0

        # Proyección del siguiente mes basada en tendencia de 6 meses
        meses_datos  = meses.get("datos", [])
        valores_mes  = [float(r.get("valor") or 0) for r in meses_datos]
        proyeccion   = _proyectar(valores_mes)

        # Nombre del mes siguiente
        import calendar
        from datetime import date
        hoy_d = date.today()
        mes_sig = hoy_d.month % 12 + 1
        anio_sig = hoy_d.year + (1 if mes_sig == 1 else 0)
        mes_sig_nombre = calendar.month_name[mes_sig]

        return {
            "tipo":   tipo,
            "modo":   modo,
            "titulo": "Dashboard de Ventas",
            "datos": {
                "kpis": {
                    "total_actual":    total_actual,
                    "total_anterior":  total_anterior,
                    "variacion":       variacion,
                    "n_sucursales":    n_sucursales,
                    "total_pedidos":   total_pedidos,
                    "ticket_promedio": ticket_promedio,
                    "proyeccion":      proyeccion,
                    "mes_proyeccion":  mes_sig_nombre,
                },
                "ventas_sucursal":  suc,
                "top_productos":    prod,
                "top_vendedores":   vend,
                "ventas_diario":    dia,
                "pedidos_activos":  pedid,
                "comparativo_meses": meses,
            },
        }

    elif tipo == "reporte_inventario":
        stock = _fetch_tipo("inventario_stock", modo)
        cad   = _fetch_tipo("caducidades",      modo)
        out   = _fetch_tipo("stockouts",        modo)

        # Tendencia histórica de valor de stock (últimos 4 meses)
        # IN_Existencias_Alm_Diario puede no existir en todos los ERP — si falla, lista vacía
        try:
            tendencia_stock = query(f"""
                SELECT TOP 4 anio, mes, mes_nombre, SUM(valor) AS valor, SUM(unidades) AS unidades
                FROM (
                    SELECT YEAR(h.Fecha) AS anio, MONTH(h.Fecha) AS mes,
                           DATENAME(MONTH, h.Fecha) AS mes_nombre,
                           ISNULL(h.Existencia * ISNULL(h.Costo_Promedio,0), 0) AS valor,
                           ISNULL(h.Existencia, 0) AS unidades
                    FROM IN_Existencias_Alm_Diario h
                    JOIN GN_Sucursales s ON s.Cve_Sucursal = h.Cve_Sucursal
                    WHERE h.Fecha >= DATEADD(MONTH,-3,DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1))
                      AND h.Fecha <  DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1)
                      AND s.Cve_Sucursal <> 99
                      AND DAY(h.Fecha) = 1
                ) t GROUP BY anio, mes, mes_nombre ORDER BY anio, mes
            """)
        except Exception as _te:
            print(f"[inventario] tendencia_stock omitida (tabla no disponible): {_te}", flush=True)
            tendencia_stock = []

        # Proyección de valor de stock siguiente mes
        valores_stock = [float(r.get("valor") or 0) for r in tendencia_stock]
        proyeccion_stock = _proyectar(valores_stock) if len(valores_stock) >= 2 else None

        # Rotación de inventario: ventas 30d / valor_stock_actual (por sucursal)
        rotacion_rows = query(f"""
            SELECT s.Nombre AS label,
                   ISNULL(SUM(e.Existencia * ISNULL(e.Costo_Promedio,0)),0) AS valor_stock,
                   ISNULL(SUM(v.ventas_30d),0) AS ventas_30d,
                   CASE WHEN SUM(e.Existencia * ISNULL(e.Costo_Promedio,0)) > 0
                        THEN ROUND(SUM(v.ventas_30d) / SUM(e.Existencia * ISNULL(e.Costo_Promedio,0)), 2)
                        ELSE 0 END AS rotacion,
                   CASE WHEN SUM(v.ventas_diaria) > 0
                        THEN ROUND(SUM(e.Existencia) / SUM(v.ventas_diaria), 0)
                        ELSE NULL END AS dias_cobertura
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status='AC'
            LEFT JOIN (
                SELECT d.Cve_Sucursal,
                       ISNULL(SUM(d.Cantidad_Ordenada),0) AS ventas_30d,
                       ISNULL(SUM(d.Cantidad_Ordenada)/30.0,0) AS ventas_diaria
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,CAST({hoy()} AS DATE))
                GROUP BY d.Cve_Sucursal
            ) v ON v.Cve_Sucursal = s.Cve_Sucursal
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING ISNULL(SUM(e.Existencia),0) > 0
            ORDER BY rotacion DESC
        """)

        # Productos con stock pero sin ventas en últimos 30 días (LEFT JOIN, más rápido que NOT EXISTS)
        sin_mov = query(f"""
            SELECT COUNT(DISTINCT e.Cve_Producto) AS total
            FROM IN_Existencias_Alm e
            LEFT JOIN (
                SELECT DISTINCT d.Cve_Producto
                FROM FT_Pedidos_Dia d
                INNER JOIN FT_Pedidos_C c ON c.Cve_Folio=d.Cve_Folio AND c.Cve_Sucursal=d.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,CAST({hoy()} AS DATE))
            ) v ON v.Cve_Producto = e.Cve_Producto
            WHERE e.Existencia > 0 AND e.Status='AC' AND e.Cve_Sucursal<>99
              AND v.Cve_Producto IS NULL
        """)
        sin_movimiento = int((sin_mov[0] if sin_mov else {}).get("total") or 0)

        # KPIs de rotación agregados
        rot_vals  = [float(r.get("rotacion") or 0) for r in rotacion_rows if r.get("rotacion")]
        cob_vals  = [float(r.get("dias_cobertura") or 0) for r in rotacion_rows if r.get("dias_cobertura")]
        rot_prom  = round(sum(rot_vals) / len(rot_vals), 2) if rot_vals else 0
        cob_prom  = round(sum(cob_vals) / len(cob_vals), 0) if cob_vals else 0

        import calendar
        from datetime import date
        hoy_d = date.today()
        mes_sig = hoy_d.month % 12 + 1
        mes_sig_nombre = calendar.month_name[mes_sig]

        return {
            "tipo":   tipo,
            "titulo": "Dashboard de Inventario",
            "datos": {
                "inventario_stock":  stock,
                "caducidades":       cad,
                "stockouts":         out,
                "tendencia_stock":   tendencia_stock,
                "rotacion_rows":     rotacion_rows,
                "sin_movimiento":    sin_movimiento,
                "rot_prom":          rot_prom,
                "cob_prom":          int(cob_prom),
                "proyeccion_stock":  proyeccion_stock,
                "mes_proyeccion":    mes_sig_nombre,
            },
        }

    elif tipo == "inventario_stock":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   ISNULL(SUM(e.Existencia * ISNULL(e.Costo_Promedio, 0)), 0) AS actual,
                   ISNULL(SUM(e.Existencia), 0)                               AS unidades,
                   COUNT(CASE WHEN e.Existencia > 0 AND e.Existencia <= 5 THEN 1 END) AS criticos
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status = 'AC'
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING ISNULL(SUM(e.Existencia), 0) > 0
            ORDER BY actual DESC
        """)
        for r in rows:
            r["anterior"] = 0   # kpi_bar layout espera este campo
        total_valor    = sum(float(r.get("actual")   or 0) for r in rows)
        total_unidades = sum(float(r.get("unidades") or 0) for r in rows)
        return {
            "tipo":           tipo,
            "titulo":         "Stock actual por sucursal",
            "total_valor":    total_valor,
            "total_unidades": total_unidades,
            "datos":          rows,
        }

    elif tipo == "caducidades":
        rows = query(f"""
            SELECT TOP 20
                p.Descripcion COLLATE DATABASE_DEFAULT + ' · Lote: ' + ISNULL(l.Num_Lote COLLATE DATABASE_DEFAULT, '—') AS label,
                ISNULL(l.Existencia, 0) AS unidades,
                ISNULL(l.Existencia * ISNULL(e.Costo_Promedio, 0), 0)  AS valor,
                DATEDIFF(DAY, CAST({hoy()} AS DATE),
                         CAST(l.Fecha_Caducidad AS DATE))               AS dias,
                CONVERT(varchar(10), l.Fecha_Caducidad, 103)            AS fecha_caducidad,
                s.Nombre AS sucursal
            FROM IN_Existencias_Lote l
            JOIN IM_Productos_Gral p
              ON p.Cve_Producto = l.Cve_Producto
            JOIN GN_Sucursales s
              ON s.Cve_Sucursal = l.Cve_Sucursal
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = l.Cve_Sucursal
             AND e.Cve_Producto = l.Cve_Producto
             AND e.Cve_Almacen  = l.Cve_Almacen
             AND e.Status = 'AC'
            WHERE l.Existencia > 0
              AND l.Fecha_Caducidad IS NOT NULL
              AND CAST(l.Fecha_Caducidad AS DATE) >= CAST({hoy()} AS DATE)
              AND CAST(l.Fecha_Caducidad AS DATE) <= DATEADD(DAY, 90, CAST({hoy()} AS DATE))
              AND s.Cve_Sucursal <> 99
            ORDER BY l.Fecha_Caducidad ASC
        """)
        return {
            "tipo":   tipo,
            "titulo": "Productos por caducar — próximos 90 días",
            "datos":  rows,
        }

    elif tipo == "stockouts":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(e.Cve_Producto) AS valor,
                   COUNT(CASE WHEN e.Existencia > 0 AND e.Existencia <= 5 THEN 1 END) AS criticos
            FROM GN_Sucursales s
            JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status = 'AC'
            WHERE s.Cve_Sucursal <> 99
              AND e.Existencia <= 0
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING COUNT(e.Cve_Producto) > 0
            ORDER BY valor DESC
        """)
        total = int(sum(float(r.get("valor") or 0) for r in rows))
        return {
            "tipo":   tipo,
            "titulo": "Productos sin existencia por sucursal",
            "total":  total,
            "datos":  rows,
        }

    elif tipo == "ventas_producto":
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
        like_sql = f"AND p.Descripcion LIKE '%{producto.upper()}%'" if producto else ""
        rows = query(f"""
            SELECT s.Nombre AS label,
                   ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS valor,
                   SUM(d.Cantidad_Ordenada) AS unidades
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
            JOIN IM_Productos_Gral p ON p.Cve_Producto=d.Cve_Producto
            JOIN GN_Sucursales s ON s.Cve_Sucursal=c.Cve_Sucursal
            WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
              AND c.Cve_Sucursal<>99
              AND d.Precio > 1
              AND p.Descripcion NOT LIKE '%GRATIS%'
              {like_sql}
              AND {filtro}
            GROUP BY s.Cve_Sucursal, s.Nombre
            ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        nombre_prod = producto.upper() if producto else "Producto"
        return {
            "tipo":    tipo,
            "modo":    modo,
            "titulo":  f"Ventas de {nombre_prod} por sucursal ({label})",
            "total":   total,
            "producto": nombre_prod,
            "datos":   rows,
        }

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
    from shared.database_local import verificar_mes_ia
    from datetime import date as _d
    verificar_mes_ia(usuario["id"], _d.today().strftime("%Y-%m"))
    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + 1, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + 1, 2) "
        "WHERE id = ? AND limite_ia > 0",
        (usuario["id"],),
    )
    return JSONResponse({"ok": True})
