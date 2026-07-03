# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
# ============================================================
"""
Router de datos del ERP para el Studio Dashboards.

Endpoints:
  GET  /api/datos/ventas               → Ventas por sucursal (30d o mes actual)
  GET  /api/datos/pedidos              → Pedidos activos por sucursal
  GET  /api/datos/kpis                 → Totales globales para tarjetas KPI
  GET  /api/datos/ventas-hoy           → Ventas pagadas del día
  GET  /api/datos/plantilla/{tipo}     → Datos de una plantilla predefinida
  GET  /api/datos/zonas                → Dashboard Zonas: mapa + ventas por sucursal
  GET  /api/datos/productos            → Dashboard Productos: top + lista para selector
  GET  /api/datos/productos/prediccion → Predicción de demanda por producto (tratamientos activos)
  GET  /api/datos/vendedores           → Dashboard Vendedores: ranking, por sucursal, tendencia mensual
  POST /api/datos/generar              → Genera dashboard completo con IA (gpt-5-nano)
  POST /api/datos/dashboards           → Guardar un dashboard
  GET  /api/datos/dashboards           → Listar dashboards guardados
  DELETE /api/datos/dashboards/{id}   → Eliminar un dashboard guardado
"""
import json
import time
import threading
from collections import defaultdict
from datetime import date as _date
from typing import Optional

import base64
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
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

# Caché en memoria para el mapa de ventas por CP
_mapa_cache: dict = {}
# CPs actualmente siendo geocodificados en background (evita lanzar 2 threads para el mismo mes)
_geocodificando: set = set()

# ── Inicialización de tablas de caché del mapa ───────────────────────────────
_mapa_tables_ready = False


def _init_mapa_tables() -> None:
    """Crea las tablas de caché del mapa en SQLite si no existen (se llama una sola vez)."""
    global _mapa_tables_ready
    if _mapa_tables_ready:
        return
    execute("""
        CREATE TABLE IF NOT EXISTS cp_coords (
            cp        TEXT PRIMARY KEY,
            lat       REAL NOT NULL,
            lng       REAL NOT NULL,
            cached_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Resultado completo del mapa por mes — persiste entre reinicios del servicio
    execute("""
        CREATE TABLE IF NOT EXISTS mapa_resultado_cache (
            key       TEXT PRIMARY KEY,   -- "YYYY-MM"
            label     TEXT,
            puntos    TEXT NOT NULL,      -- JSON array de puntos
            cached_at REAL NOT NULL       -- Unix timestamp
        )
    """)
    _mapa_tables_ready = True


def _geocode_cp(cp: str):
    """Geocodifica un CP mexicano via Nominatim. Retorna (lat, lng) o None."""
    import urllib.request as _urllib
    import json as _json
    try:
        url = (
            f"https://nominatim.openstreetmap.org/search"
            f"?postalcode={cp}&country=MX&format=json&limit=1"
        )
        req = _urllib.Request(url, headers={"User-Agent": "SuiteAnaliticaNentria/1.0"})
        with _urllib.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


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
            f"{c} >= DATEADD(DAY,-15,DATEADD(MONTH,-1,{h})) AND {c} < DATEADD(MONTH,-1,{h})",
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
            f"{fi[8:]}/{fi[5:7]}/{fi[:4]} → {ff[8:]}/{ff[5:7]}/{ff[:4]}",
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
    "stockouts":            {"titulo": "Productos sin existencia",          "layout": "ranking_hbar"},
    # Médicos
    "medicos_dashboard":    {"titulo": "Dashboard de Médicos",              "layout": "tab_medicos"},
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
  "dashboard de médicos"  /  "médicos"  /  "prescriptores"  → medicos_dashboard
  "gráfica de línea de ventas por mes"         → comparativo_meses + single_chart + chart_type:line
  "tendencia anual"                            → tendencia_anual
  "dona de pedidos"  /  "pedidos activos"      → pedidos_activos
  "productos más vendidos"                     → top_productos
  "clientes frecuentes"                        → clientes_frecuentes
  "variación de vendedores"                    → variacion_vendedores
  "inventario"  /  "stock"                     → reporte_inventario
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
  reporte_inventario   → Dashboard COMPLETO de inventario: stock + stockouts. layout: inventory_report
  inventario_stock     → Stock actual por sucursal: valor en MXN y unidades. layout: kpi_bar
  stockouts            → Sucursales con más productos sin existencia (stock = 0). layout: ranking_hbar
  medicos_dashboard    → Dashboard de Médicos: ranking de médicos por ventas, tendencia mensual, ventas por rep. layout: tab_medicos
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
    pdf_b64:    str  = ""


# ── Ventas por sucursal ───────────────────────────────────────────────────────

@router.get("/ventas")
def ventas_sucursales(modo: str = Query("30d"), fi: str = Query(None), ff: str = Query(None)):
    """
    Ventas por sucursal para los dashboards del Studio.

    Args:
        modo: 'hoy' | '15d' | '30d' | 'mes' | 'custom'
        fi:   fecha inicio ISO (solo cuando modo='custom')
        ff:   fecha fin ISO (solo cuando modo='custom')

    Returns:
        JSON con lista de sucursales, ventas, facturas y variación porcentual.
    """
    if fi and ff: modo = "custom"
    filtro_actual, filtro_anterior, _ = _filtros_periodo(modo, "t.Fecha_Documento", fi, ff)

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

    rows = [r for r in rows if float(r.get("ventas_actual") or 0) > 0]
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
            COUNT(CASE WHEN p.Estatus <> 'CN'
                        AND p.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                  AS activos,
            COUNT(CASE WHEN p.Estatus = 'TR'
                        AND p.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                 AS completados_30d
        FROM GN_Sucursales s
        LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal = s.Cve_Sucursal
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY activos DESC
    """)
    return JSONResponse({"sucursales": rows})


# ── Mapa de ventas por código postal ─────────────────────────────────────────

@router.get("/mapa")
def mapa_ventas(anio: int = Query(None), mes: int = Query(None)):
    """
    Ventas por código postal (domicilio de entrega) para el mapa de puntos.
    Parámetros anio+mes seleccionan un mes específico.
    Meses históricos se cachean permanentemente en SQLite.
    Mes actual: TTL 10 min en memoria + SQLite (persiste entre reinicios).
    Las coordenadas de cada CP se obtienen via Nominatim y se guardan en SQLite.

    Returns:
        JSON con anio, mes, label y lista de puntos con cp, lat, lng, ventas y pedidos.
    """
    _init_mapa_tables()

    hoy_d  = _date.today()
    _anio  = anio or hoy_d.year
    _mes   = mes  or hoy_d.month
    key    = f"{_anio}-{_mes:02d}"
    es_actual = (_anio == hoy_d.year and _mes == hoy_d.month)
    now    = time.time()
    TTL    = 600  # 10 min para el mes actual

    MESES_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    label = f"{MESES_ES[_mes]} {_anio}"

    # 1. Cache en memoria (más rápido — dentro de la misma sesión del proceso)
    entrada = _mapa_cache.get(key)
    if entrada:
        if not es_actual or (now - entrada["ts"]) < TTL:
            return JSONResponse({"anio": _anio, "mes": _mes,
                                 "label": entrada["label"], "puntos": entrada["data"]})

    # 2. Cache SQLite (persiste entre reinicios del servicio)
    row_sqlite = fetch_one(
        "SELECT label, puntos, cached_at FROM mapa_resultado_cache WHERE key=?", (key,)
    )
    if row_sqlite:
        usar = True
        if es_actual:
            edad = now - (row_sqlite["cached_at"] or 0)
            usar = edad < TTL
        if usar:
            puntos_cached = json.loads(row_sqlite["puntos"])
            _mapa_cache[key] = {"ts": row_sqlite["cached_at"], "label": row_sqlite["label"], "data": puntos_cached}
            return JSONResponse({"anio": _anio, "mes": _mes,
                                 "label": row_sqlite["label"], "puntos": puntos_cached, "pendientes": 0})

    # 3. Consultar SQL Server — todos los CPs del mes sin límite
    try:
        rows = query(f"""
            SELECT con.CP,
                   COUNT(DISTINCT p.Cve_Folio)                                  AS pedidos,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) AS bigint)  AS ventas
            FROM FT_Pedidos_C p
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
            INNER JOIN CM_Consignatarios con
              ON con.Cve_Consignatario=p.Cve_Consignatario
            WHERE p.Estatus<>'CN'
              AND p.Referencia_Cliente='PAGADO'
              AND p.Cve_Sucursal<>99
              AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
              AND YEAR(p.Fecha_Documento)={_anio}
              AND MONTH(p.Fecha_Documento)={_mes}
            GROUP BY con.CP
            ORDER BY ventas DESC
        """)
    except Exception:
        rows = []

    cps_con_ventas = [r["CP"] for r in rows if r.get("CP")]

    # Buscar coords en cp_coords SQLite
    coords_cache = {}
    if cps_con_ventas:
        cached = fetch_all(
            f"SELECT cp, lat, lng FROM cp_coords WHERE cp IN ({','.join(['?']*len(cps_con_ventas))})",
            cps_con_ventas,
        )
        coords_cache = {r["cp"]: (r["lat"], r["lng"]) for r in cached}

    todos_faltantes = [cp for cp in cps_con_ventas if cp not in coords_cache]

    # Construir resultado con coords ya disponibles — respuesta inmediata
    puntos = []
    for r in rows:
        cp = r.get("CP", "")
        if cp in coords_cache:
            lat, lng = coords_cache[cp]
            puntos.append({
                "cp":      cp,
                "lat":     lat,
                "lng":     lng,
                "ventas":  int(r.get("ventas") or 0),
                "pedidos": int(r.get("pedidos") or 0),
            })

    pendientes = len(todos_faltantes)

    # Guardar en cache SQLite + memoria
    # Si hay pendientes: TTL 10 min (se refresca cuando geocodificación termine)
    # Sin pendientes: permanente (ts=0 en memoria, sin expiración en SQLite)
    ts_cache = now if pendientes else 0.0
    if puntos:
        execute(
            "INSERT OR REPLACE INTO mapa_resultado_cache (key, label, puntos, cached_at) VALUES (?, ?, ?, ?)",
            (key, label, json.dumps(puntos), ts_cache),
        )
        _mapa_cache[key] = {"ts": ts_cache, "label": label, "data": puntos}

    # Geocodificación en background para CPs sin coords
    if todos_faltantes and key not in _geocodificando:
        def _geocodificar_bg(key_bg, faltantes_bg):
            _geocodificando.add(key_bg)
            try:
                for cp in faltantes_bg:
                    coords = _geocode_cp(cp)
                    if coords:
                        lat, lng = coords
                        execute(
                            "INSERT OR REPLACE INTO cp_coords (cp, lat, lng) VALUES (?, ?, ?)",
                            (cp, lat, lng),
                        )
                    time.sleep(1.1)
                # Invalidar caches para que la próxima visita reconstruya con los nuevos puntos
                _mapa_cache.pop(key_bg, None)
                execute("DELETE FROM mapa_resultado_cache WHERE key=?", (key_bg,))
            finally:
                _geocodificando.discard(key_bg)
        threading.Thread(target=_geocodificar_bg, args=(key, todos_faltantes), daemon=True).start()

    return JSONResponse({
        "anio": _anio, "mes": _mes, "label": label, "puntos": puntos,
        "pendientes": pendientes,
    })


# ── Zonas de ventas por sucursal ──────────────────────────────────────────────

@router.get("/zonas")
def zonas_ventas(anio: Optional[int] = None, mes: Optional[int] = None):
    """
    Dashboard de zonas: ventas + productos por sucursal con mapa de puntos.

    Returns:
        JSON con sucursales (ventas/piezas), top_productos por sucursal,
        y mapa_puntos (CPs con coords del caché SQLite y color de sucursal).
    """
    hoy_d = _date.today()
    _anio = anio or hoy_d.year
    _mes  = mes  or hoy_d.month

    MESES_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    label = f"{MESES_ES[_mes]} {_anio}"

    # 1. Comparativo por sucursal (ventas + piezas)
    try:
        comp_rows = query(f"""
            SELECT s.Cve_Sucursal,
                   s.Nombre                                                         AS sucursal,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) AS bigint)      AS ventas,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada,0))          AS bigint)      AS piezas,
                   COUNT(DISTINCT p.Cve_Folio)                                      AS pedidos
            FROM GN_Sucursales s
            LEFT JOIN FT_Pedidos_C p
              ON p.Cve_Sucursal=s.Cve_Sucursal
             AND p.Estatus<>'CN' AND p.Referencia_Cliente='PAGADO'
             AND YEAR(p.Fecha_Documento)={_anio} AND MONTH(p.Fecha_Documento)={_mes}
            LEFT JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre
            ORDER BY ventas DESC
        """)
    except Exception:
        comp_rows = []

    sucursales = [
        {"cve": int(r["Cve_Sucursal"]), "nombre": r["sucursal"],
         "ventas": int(r["ventas"] or 0), "piezas": int(r["piezas"] or 0),
         "pedidos": int(r["pedidos"] or 0)}
        for r in (comp_rows or []) if int(r.get("ventas") or 0) > 0
    ]

    # 2. Top productos por sucursal agrupados por código de barras
    try:
        prod_rows = query(f"""
            SELECT p.Cve_Sucursal,
                   MIN(prod.Descripcion)                                            AS producto,
                   cb.barcode_canon,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) AS bigint)      AS ventas,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada,0))          AS bigint)      AS piezas
            FROM FT_Pedidos_C p
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
            INNER JOIN (
                SELECT Cve_Producto, MIN(Codigo_Barras) AS barcode_canon
                FROM IM_Codigos_Barra GROUP BY Cve_Producto
            ) cb ON cb.Cve_Producto=d.Cve_Producto
            INNER JOIN IM_Productos_Gral prod ON prod.Cve_Producto=d.Cve_Producto
            WHERE p.Estatus<>'CN' AND p.Referencia_Cliente='PAGADO' AND p.Cve_Sucursal<>99
              AND YEAR(p.Fecha_Documento)={_anio} AND MONTH(p.Fecha_Documento)={_mes}
              AND prod.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Cve_Sucursal, cb.barcode_canon
            ORDER BY p.Cve_Sucursal, ventas DESC
        """)
    except Exception:
        prod_rows = []

    top_por_suc: dict = defaultdict(list)
    for r in (prod_rows or []):
        cve = int(r["Cve_Sucursal"])
        if len(top_por_suc[cve]) < 5:
            top_por_suc[cve].append({
                "producto": r["producto"] or "—",
                "barcode":  r["barcode_canon"] or "",
                "ventas":   int(r["ventas"] or 0),
                "piezas":   int(r["piezas"] or 0),
            })

    # 3. Mapa: todos los CPs con sucursal dominante, coords del caché SQLite
    try:
        mapa_rows = query(f"""
            SELECT con.CP, p.Cve_Sucursal,
                   CAST(SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) AS bigint) AS ventas,
                   COUNT(DISTINCT p.Cve_Folio)                                 AS pedidos
            FROM FT_Pedidos_C p
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
            INNER JOIN CM_Consignatarios con
              ON con.Cve_Consignatario=p.Cve_Consignatario
            WHERE p.Estatus<>'CN' AND p.Referencia_Cliente='PAGADO' AND p.Cve_Sucursal<>99
              AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
              AND YEAR(p.Fecha_Documento)={_anio} AND MONTH(p.Fecha_Documento)={_mes}
            GROUP BY con.CP, p.Cve_Sucursal
            ORDER BY ventas DESC
        """)
    except Exception:
        mapa_rows = []

    # Sucursal dominante por CP (mayor ventas)
    cp_data: dict = {}
    for r in (mapa_rows or []):
        cp = r.get("CP", "")
        if not cp:
            continue
        v = int(r.get("ventas") or 0)
        if cp not in cp_data or v > cp_data[cp]["ventas"]:
            cp_data[cp] = {
                "cp": cp, "cve_sucursal": int(r["Cve_Sucursal"]),
                "ventas": v, "pedidos": int(r.get("pedidos") or 0),
            }

    # Buscar coords en caché SQLite (sin geocodificar — el endpoint /mapa ya lo hace)
    puntos_mapa = []
    if cp_data:
        _init_mapa_tables()
        cps = list(cp_data.keys())
        cached = fetch_all(
            f"SELECT cp, lat, lng FROM cp_coords WHERE cp IN ({','.join(['?']*len(cps))})",
            cps,
        )
        coords = {r["cp"]: (r["lat"], r["lng"]) for r in cached}
        for cp, data in cp_data.items():
            if cp in coords:
                lat, lng = coords[cp]
                puntos_mapa.append({**data, "lat": lat, "lng": lng})

    return JSONResponse({
        "anio": _anio, "mes": _mes, "label": label,
        "sucursales":    sucursales,
        "top_productos": {str(k): v for k, v in top_por_suc.items()},
        "mapa_puntos":   puntos_mapa,
    })


# ── Dashboard de Productos ────────────────────────────────────────────────────

MESES_ES_P = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
              "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


@router.get("/productos")
def productos_dashboard(anio: Optional[int] = None, mes: Optional[int] = None):
    """
    Dashboard de Productos.

    Retorna:
      - top_productos: top 20 productos del período (consolidados por Cve_Producto)
      - lista_productos: lista para el selector de predicción (cve_producto, descripcion)
      - label: etiqueta del período
    """
    hoy_d = _date.today()
    _anio = anio or hoy_d.year
    _mes  = mes  or hoy_d.month
    label = f"{MESES_ES_P[_mes]} {_anio}"

    # Mes anterior para variación
    _mes_ant  = _mes - 1 if _mes > 1 else 12
    _anio_ant = _anio if _mes > 1 else _anio - 1

    # Si estamos en el mes actual, comparar solo los días transcurridos
    _es_mes_actual = (_anio == hoy_d.year and _mes == hoy_d.month)
    _dia_corte     = hoy_d.day if _es_mes_actual else None

    # ── 1. Top 20 productos del período ──────────────────────────────────────
    try:
        top_rows = query(f"""
            SELECT TOP 20
                d.Cve_Producto,
                MIN(pg.Descripcion) AS descripcion,
                SUM(d.Cantidad_Ordenada)            AS piezas,
                SUM(d.Cantidad_Ordenada * d.Precio) AS importe
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
            WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND YEAR(c.Fecha_Documento) = {_anio}
              AND MONTH(c.Fecha_Documento) = {_mes}
              AND pg.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY d.Cve_Producto
            ORDER BY SUM(d.Cantidad_Ordenada * d.Precio) DESC
        """)
    except Exception as _e:
        raise HTTPException(500, f"productos top_rows error: {_e}")

    # Importe del mismo producto el mes anterior (para variación)
    # Si es el mes actual, limitar al mismo día del mes anterior para comparar igual vs igual
    _filtro_dia_ant = f"AND DAY(c.Fecha_Documento) <= {_dia_corte}" if _dia_corte else ""
    try:
        ant_rows = query(f"""
            SELECT d.Cve_Producto,
                   SUM(d.Cantidad_Ordenada * d.Precio) AS importe_ant
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND YEAR(c.Fecha_Documento) = {_anio_ant}
              AND MONTH(c.Fecha_Documento) = {_mes_ant}
              {_filtro_dia_ant}
              AND d.Cve_Producto IN ({','.join(str(r['Cve_Producto']) for r in top_rows) or '0'})
            GROUP BY d.Cve_Producto
        """)
        ant_map = {r["Cve_Producto"]: float(r["importe_ant"] or 0) for r in ant_rows}
    except Exception:
        ant_map = {}

    total_importe = sum(float(r["importe"] or 0) for r in top_rows)

    top_productos = []
    for r in top_rows:
        imp   = float(r["importe"] or 0)
        imp_a = ant_map.get(r["Cve_Producto"], 0)
        var   = round((imp - imp_a) / imp_a * 100, 1) if imp_a > 0 else None
        top_productos.append({
            "cve_producto": r["Cve_Producto"],
            "descripcion":  (r["descripcion"] or "").strip(),
            "piezas":       int(r["piezas"] or 0),
            "importe":      round(imp, 2),
            "importe_ant":  round(imp_a, 2),
            "variacion":    var,
            "pct_total":    round(imp / total_importe * 100, 1) if total_importe > 0 else 0,
        })

    # ── 2. Lista de productos para el selector de predicción ─────────────────
    # Todos los productos con ventas en los últimos 6 meses (activos)
    try:
        lista_rows = query(f"""
            SELECT DISTINCT d.Cve_Producto,
                   MIN(pg.Descripcion) AS descripcion
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
            WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND c.Fecha_Documento >= DATEADD(MONTH, -6, {hoy()})
              AND pg.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY d.Cve_Producto
            ORDER BY MIN(pg.Descripcion)
        """)
        lista_productos = [
            {"cve_producto": r["Cve_Producto"], "descripcion": (r["descripcion"] or "").strip()}
            for r in lista_rows
        ]
    except Exception as _e:
        raise HTTPException(500, f"productos lista error: {_e}")

    _mes_ant_nombre = MESES_ES_P[_mes_ant]
    label_ant = (f"1-{_dia_corte} {_mes_ant_nombre}" if _dia_corte
                 else _mes_ant_nombre)

    return JSONResponse({
        "anio": _anio, "mes": _mes, "label": label,
        "label_ant": label_ant,
        "top_productos":  top_productos,
        "lista_productos": lista_productos,
        "total_importe":  round(total_importe, 2),
    })


@router.get("/productos/prediccion")
def productos_prediccion(cve_producto: int):
    """
    Predicción de demanda para un producto específico.

    Modelo: regresión lineal sobre ventas mensuales (últimos 12 meses).
    La proyección se basa en la tasa de crecimiento y tendencia de ventas,
    no en supuestos de tratamiento.

    Returns:
        JSON con piezas vendidas, proyección 3m y 6m, desglose por sucursal
        e historial mensual.
    """
    try:
        prod_row = query(f"""
            SELECT TOP 1 pg.Descripcion
            FROM IM_Productos_Gral pg
            WHERE pg.Cve_Producto = {cve_producto}
        """)
        nombre_producto = (prod_row[0]["Descripcion"] or "").strip() if prod_row else f"Producto {cve_producto}"
    except Exception as e:
        raise HTTPException(500, f"prediccion-nombre: {e}")

    hoy_d = _date.today()
    fecha_corte_str = f"{hoy_d.year - 2}-{hoy_d.month:02d}-01"

    try:
        hist_rows = query(f"""
            SELECT
                YEAR(c.Fecha_Documento)  AS anio,
                MONTH(c.Fecha_Documento) AS mes,
                c.Cve_Sucursal,
                MIN(ISNULL(s.Nombre, CAST(c.Cve_Sucursal AS VARCHAR))) AS sucursal,
                SUM(d.Cantidad_Ordenada) AS piezas
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            LEFT JOIN GN_Sucursales s ON s.Cve_Sucursal = c.Cve_Sucursal
            WHERE d.Cve_Producto = {cve_producto}
              AND c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND CONVERT(DATE, c.Fecha_Documento) >= '{fecha_corte_str}'
            GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento), c.Cve_Sucursal
            ORDER BY anio, mes
        """)
    except Exception as e:
        raise HTTPException(500, f"prediccion-hist: {e}")

    if not hist_rows:
        return JSONResponse({
            "cve_producto":      cve_producto,
            "producto":          nombre_producto,
            "ultimo_mes_label":  "",
            "ultimo_mes_piezas": 0,
            "pred_mes_3":        0,
            "pred_mes_3_label":  "",
            "pred_mes_6":        0,
            "pred_mes_6_label":  "",
            "yoy_factor":        1.0,
            "por_sucursal":      [],
            "detalle":           [],
            "proyeccion":        [],
        })

    # Aggregate monthly totals (all sucursales)
    mes_totales: dict = defaultdict(float)              # (anio, mes) -> piezas
    suc_mes: dict = defaultdict(lambda: defaultdict(float))  # cve_suc -> (anio,mes) -> piezas
    suc_nombre: dict = {}

    for r in hist_rows:
        key = (int(r["anio"]), int(r["mes"]))
        mes_totales[key] += float(r["piezas"] or 0)
        cve_suc = r["Cve_Sucursal"]
        suc_mes[cve_suc][key] += float(r["piezas"] or 0)
        suc_nombre[cve_suc] = (r["sucursal"] or f"Suc {cve_suc}").strip()

    # Sort keys chronologically
    sorted_keys = sorted(mes_totales.keys())
    # Use only last 12 complete months for trend (exclude current partial month)
    mes_actual = (hoy_d.year, hoy_d.month)
    trend_keys = [k for k in sorted_keys if k != mes_actual][-12:]
    trend_vals = [mes_totales[k] for k in trend_keys]

    MESES_ES_C = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
                  "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

    # Last complete month actual sales
    ultimo_mes_key    = trend_keys[-1] if trend_keys else None
    ultimo_mes_piezas = round(mes_totales[ultimo_mes_key], 1) if ultimo_mes_key else 0
    ultimo_mes_label  = f"{MESES_ES_C[ultimo_mes_key[1]]} {ultimo_mes_key[0]}" if ultimo_mes_key else ""

    def _calc_yoy_factor(keys: list, totals: dict) -> float:
        """Calcula el factor YoY promedio de los últimos 6 ratios disponibles."""
        ratios = []
        for k in keys:
            anio_ant, mes_k = k[0] - 1, k[1]
            key_ant = (anio_ant, mes_k)
            if key_ant in totals and totals[key_ant] > 0:
                ratios.append(totals[k] / totals[key_ant])
        last_6 = ratios[-6:] if len(ratios) >= 6 else ratios
        if not last_6:
            return 1.0
        yoy = sum(last_6) / len(last_6)
        return max(0.1, min(5.0, yoy))

    def _seasonal_proyeccion(keys: list, totals: dict, yoy: float) -> list:
        """Proyecta 6 meses usando factor YoY estacional.
        Cada proyección se limita al 110% del máximo histórico para evitar cifras irreales."""
        if not keys:
            return []
        last_key = keys[-1]
        vals_trend = [totals[k] for k in keys]
        avg_last3 = sum(vals_trend[-3:]) / max(1, len(vals_trend[-3:]))
        max_hist  = max(vals_trend) if vals_trend else 0
        techo     = max_hist * 1.10  # nunca proyectar más del 110% del máximo histórico
        result = []
        for k in range(1, 7):
            mes_p = last_key[1] + k
            anio_p = last_key[0] + (mes_p - 1) // 12
            mes_p  = ((mes_p - 1) % 12) + 1
            key_ant = (anio_p - 1, mes_p)
            if key_ant in totals and totals[key_ant] > 0:
                val_p = totals[key_ant] * yoy
            else:
                val_p = avg_last3
            val_p = max(0.0, min(val_p, techo) if techo > 0 else val_p)
            result.append({
                "mes_label": f"{MESES_ES_C[mes_p]} {anio_p}",
                "piezas":    round(val_p, 1),
            })
        return result

    # Global YoY factor
    yoy_factor = _calc_yoy_factor(trend_keys, mes_totales)

    # Global projection (6 months)
    proyeccion = _seasonal_proyeccion(trend_keys, mes_totales, yoy_factor)

    # pred_mes_3 = valor puntual del mes 3 (índice 2)
    pred_mes_3       = int(round(proyeccion[2]["piezas"])) if len(proyeccion) >= 3 else 0
    pred_mes_3_label = proyeccion[2]["mes_label"] if len(proyeccion) >= 3 else ""
    pred_mes_6       = int(round(proyeccion[5]["piezas"])) if len(proyeccion) >= 6 else 0
    pred_mes_6_label = proyeccion[5]["mes_label"] if len(proyeccion) >= 6 else ""

    # Per-sucursal projection (seasonal model)
    por_sucursal = []
    for cve_suc, mes_dict in suc_mes.items():
        suc_keys = sorted(mes_dict.keys())
        suc_trend_keys = [k for k in suc_keys if k != mes_actual][-12:]
        suc_yoy = _calc_yoy_factor(suc_trend_keys, mes_dict)
        suc_proj = _seasonal_proyeccion(suc_trend_keys, mes_dict, suc_yoy)
        suc_vals_trend = [mes_dict[k] for k in suc_trend_keys]
        s_pred_3 = int(round(suc_proj[2]["piezas"])) if len(suc_proj) >= 3 else 0
        s_pred_6 = int(round(suc_proj[5]["piezas"])) if len(suc_proj) >= 6 else 0
        por_sucursal.append({
            "cve":       cve_suc,
            "sucursal":  suc_nombre.get(cve_suc, f"Suc {cve_suc}"),
            "piezas_12m": round(sum(suc_vals_trend), 1),
            "pred_mes_3": s_pred_3,
            "pred_mes_6": s_pred_6,
        })
    por_sucursal.sort(key=lambda x: x["pred_mes_6"], reverse=True)

    # Monthly history — excluir mes actual (parcial) para no distorsionar el gráfico
    detalle = [
        {"mes_label": f"{MESES_ES_C[k[1]]} {k[0]}", "piezas": round(mes_totales[k], 1)}
        for k in sorted_keys if k != mes_actual
    ]

    return JSONResponse({
        "cve_producto":      cve_producto,
        "producto":          nombre_producto,
        "ultimo_mes_label":  ultimo_mes_label,
        "ultimo_mes_piezas": ultimo_mes_piezas,
        "pred_mes_3":        pred_mes_3,
        "pred_mes_3_label":  pred_mes_3_label,
        "pred_mes_6":        pred_mes_6,
        "pred_mes_6_label":  pred_mes_6_label,
        "yoy_factor":        round(yoy_factor, 4),
        "por_sucursal":      por_sucursal,
        "detalle":           detalle,
        "proyeccion":        proyeccion,
    })


# ── Dashboard de Inventario ───────────────────────────────────────────────────

@router.get("/inventario")
def inventario_dashboard():
    """
    Dashboard de Inventario.

    Retorna:
      - valor_stock: valor total del inventario (costo × existencia)
      - unidades_totales: suma de existencias
      - productos_con_stock: productos con existencia > 0
      - criticos: productos con existencia total = 0 pero ventas en últimos 90 días
      - por_sucursal: valor, unidades y productos por sucursal
      - top_por_valor: top 15 productos por valor en stock
      - criticos_lista: top 20 críticos ordenados por importe de ventas 90d
    """
    _hoy = hoy()

    # ── 1. KPIs globales ──────────────────────────────────────────────────────
    try:
        kpi_rows = query(f"""
            SELECT
                ISNULL(SUM(e.Existencia * ISNULL(pg.Costo_Promedio, 0)), 0) AS valor_stock,
                ISNULL(SUM(e.Existencia), 0)                                 AS unidades_totales,
                COUNT(DISTINCT CASE WHEN e.Existencia > 0 THEN e.Cve_Producto END) AS productos_con_stock
            FROM IN_Existencias_Alm e
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99
        """)
        kpi = kpi_rows[0] if kpi_rows else {}
        valor_stock        = float(kpi.get("valor_stock") or 0)
        unidades_totales   = int(kpi.get("unidades_totales") or 0)
        productos_con_stock = int(kpi.get("productos_con_stock") or 0)
    except Exception as e:
        raise HTTPException(500, f"inventario-kpis: {e}")

    # ── 2. Críticos: sin stock pero con ventas en 90 días ─────────────────────
    try:
        criticos_count_rows = query(f"""
            SELECT COUNT(*) AS total
            FROM (
                SELECT e.Cve_Producto
                FROM IN_Existencias_Alm e
                WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99
                GROUP BY e.Cve_Producto
                HAVING SUM(e.Existencia) <= 0
            ) sin_stock
            WHERE sin_stock.Cve_Producto IN (
                SELECT DISTINCT d.Cve_Producto
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                    ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
                WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
                  AND c.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
            )
        """)
        criticos = int((criticos_count_rows[0] if criticos_count_rows else {}).get("total") or 0)
    except Exception as e:
        raise HTTPException(500, f"inventario-criticos-count: {e}")

    # ── 3. Stock por sucursal ─────────────────────────────────────────────────
    try:
        suc_rows = query(f"""
            SELECT s.Nombre AS sucursal,
                   ISNULL(SUM(e.Existencia * ISNULL(pg.Costo_Promedio, 0)), 0) AS valor,
                   ISNULL(SUM(e.Existencia), 0)                                AS unidades,
                   COUNT(DISTINCT CASE WHEN e.Existencia > 0 THEN e.Cve_Producto END) AS productos
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
                ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status = 'AC'
            LEFT JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING ISNULL(SUM(e.Existencia), 0) > 0
            ORDER BY SUM(e.Existencia * ISNULL(pg.Costo_Promedio, 0)) DESC
        """)
        por_sucursal = [
            {
                "sucursal":  (r["sucursal"] or "").strip(),
                "valor":     round(float(r["valor"] or 0), 2),
                "unidades":  int(r["unidades"] or 0),
                "productos": int(r["productos"] or 0),
            }
            for r in suc_rows
        ]
    except Exception as e:
        raise HTTPException(500, f"inventario-sucursal: {e}")

    # ── 4. Top 15 productos por valor en stock ────────────────────────────────
    try:
        top_rows = query(f"""
            SELECT TOP 15
                MIN(pg.Descripcion)          AS descripcion,
                SUM(e.Existencia)            AS unidades,
                MIN(ISNULL(pg.Precio_Minimo_Venta_Base, 0))   AS precio1,
                MIN(ISNULL(pg.Precio_Minimo_Venta_Base2, 0))   AS precio2,
                MIN(ISNULL(pg.Precio_Minimo_Venta_Base3, 0))   AS precio3
            FROM IN_Existencias_Alm e
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99 AND e.Existencia > 0
              AND pg.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY e.Cve_Producto
            ORDER BY SUM(e.Existencia) DESC
        """)
        top_por_valor = [
            {
                "descripcion": (r["descripcion"] or "").strip(),
                "unidades":    int(r["unidades"] or 0),
                "precio1":     round(float(r["precio1"] or 0), 2),
                "precio2":     round(float(r["precio2"] or 0), 2),
                "precio3":     round(float(r["precio3"] or 0), 2),
            }
            for r in top_rows
        ]
    except Exception as e:
        raise HTTPException(500, f"inventario-top: {e}")

    # ── 5. Lista críticos (top 20 por importe de ventas 90d) ──────────────────
    try:
        crit_rows = query(f"""
            SELECT TOP 20
                MIN(pg.Descripcion)                  AS descripcion,
                SUM(v.piezas_90d)                    AS piezas_90d,
                SUM(v.importe_90d)                   AS importe_90d
            FROM (
                SELECT e.Cve_Producto
                FROM IN_Existencias_Alm e
                WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99
                GROUP BY e.Cve_Producto
                HAVING SUM(e.Existencia) <= 0
            ) sin_stock
            INNER JOIN (
                SELECT d.Cve_Producto,
                       SUM(d.Cantidad_Ordenada)            AS piezas_90d,
                       SUM(d.Cantidad_Ordenada * d.Precio) AS importe_90d
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                    ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
                WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
                  AND c.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
                GROUP BY d.Cve_Producto
            ) v ON v.Cve_Producto = sin_stock.Cve_Producto
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = sin_stock.Cve_Producto
              AND pg.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY sin_stock.Cve_Producto
            ORDER BY SUM(v.importe_90d) DESC
        """)
        criticos_lista = [
            {
                "descripcion": (r["descripcion"] or "").strip(),
                "piezas_90d":  int(r["piezas_90d"] or 0),
                "importe_90d": round(float(r["importe_90d"] or 0), 2),
            }
            for r in crit_rows
        ]
    except Exception as e:
        raise HTTPException(500, f"inventario-criticos-lista: {e}")

    # Lista de productos con stock para el selector de consulta histórica
    try:
        lista_rows = query(f"""
            SELECT DISTINCT CAST(e.Cve_Producto AS VARCHAR) AS cve_producto,
                   MIN(pg.Descripcion) AS descripcion
            FROM IN_Existencias_Alm e
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99 AND e.Existencia > 0
              AND pg.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY e.Cve_Producto
            ORDER BY MIN(pg.Descripcion)
        """)
        lista_productos = [
            {"cve_producto": r["cve_producto"], "descripcion": (r["descripcion"] or "").strip()}
            for r in lista_rows
        ]
    except Exception:
        lista_productos = []

    return JSONResponse({
        "valor_stock":         round(valor_stock, 2),
        "unidades_totales":    unidades_totales,
        "productos_con_stock": productos_con_stock,
        "criticos":            criticos,
        "por_sucursal":        por_sucursal,
        "top_por_valor":       top_por_valor,
        "criticos_lista":      criticos_lista,
        "lista_productos":     lista_productos,
    })


@router.get("/inventario/consulta")
def inventario_consulta(cve_producto: str, fecha: str):
    """
    Consulta el stock histórico de un producto en una fecha dada.
    Si no hay dato = no había existencia ese día.
    """
    rows = fetch_all(
        "SELECT cve_sucursal, sucursal, descripcion, existencia, "
        "precio1, precio2, precio3 "
        "FROM inventario_historico_productos WHERE cve_producto=? AND fecha=? "
        "ORDER BY existencia DESC",
        (cve_producto, fecha)
    )

    if not rows:
        descripcion = (fetch_one(
            "SELECT descripcion FROM inventario_historico_productos WHERE cve_producto=? LIMIT 1",
            (cve_producto,)
        ) or {}).get("descripcion", f"Producto {cve_producto}")
        return JSONResponse({
            "cve_producto": cve_producto, "fecha": fecha,
            "descripcion": descripcion,
            "sin_existencia": True, "sucursales": [], "total_existencia": 0,
        })

    r0 = rows[0]
    descripcion = (r0.get("descripcion") or f"Producto {cve_producto}").strip()
    precios = {
        "precio1": round(float(r0.get("precio1") or 0), 2),
        "precio2": round(float(r0.get("precio2") or 0), 2),
        "precio3": round(float(r0.get("precio3") or 0), 2),
    }
    sucursales = [
        {"sucursal":   (r["sucursal"] or str(r["cve_sucursal"])).strip(),
         "existencia": round(float(r["existencia"] or 0), 2)}
        for r in rows
    ]
    return JSONResponse({
        "cve_producto":     cve_producto,
        "fecha":            fecha,
        "descripcion":      descripcion,
        "sin_existencia":   False,
        "precios":          precios,
        "sucursales":       sucursales,
        "total_existencia": sum(s["existencia"] for s in sucursales),
    })


@router.get("/inventario/historico")
def inventario_historico():
    """
    Devuelve todo el histórico de snapshots de inventario guardados por el cron.
    Retorna lista completa de fechas con valor_stock, unidades, criticos, por_sucursal.
    """
    rows = fetch_all(
        "SELECT fecha, valor_stock, unidades, productos_stock, criticos, por_sucursal "
        "FROM inventario_historico ORDER BY fecha ASC"
    )
    historico = []
    for r in (rows or []):
        historico.append({
            "fecha":          r["fecha"],
            "valor_stock":    round(float(r["valor_stock"] or 0), 2),
            "unidades":       int(r["unidades"] or 0),
            "productos_stock": int(r["productos_stock"] or 0),
            "criticos":       int(r["criticos"] or 0),
            "por_sucursal":   json.loads(r["por_sucursal"] or "[]"),
        })
    return JSONResponse({"historico": historico, "total_dias": len(historico)})


# ── KPIs globales ─────────────────────────────────────────────────────────────

@router.get("/kpis")
def kpis_globales(modo: str = Query("30d"), fi: str = Query(None), ff: str = Query(None)):
    """
    Totales globales para las tarjetas KPI del Studio.

    Args:
        modo: 'hoy' | '15d' | '30d' | 'mes' | 'custom'
        fi:   fecha inicio ISO (solo cuando modo='custom')
        ff:   fecha fin ISO (solo cuando modo='custom')

    Returns:
        JSON con ventas_total, facturas_total, pedidos_activos, sucursales_activas.
    """
    if fi and ff: modo = "custom"
    filtro, _, _ = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)

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

    try:
        pedidos_row = query(f"""
            SELECT COUNT(DISTINCT c.Cve_Folio) AS pedidos_activos
            FROM FT_Pedidos_C c
            WHERE c.Estatus <> 'CN' AND c.Cve_Sucursal <> 99 AND {filtro}
        """)
    except Exception:
        pedidos_row = [{"pedidos_activos": 0}]

    sucursales_row = query(f"""
        SELECT COUNT(DISTINCT c.Cve_Sucursal) AS total
        FROM FT_Pedidos_C c
        WHERE c.Estatus <> 'CN'
          AND c.Referencia_Cliente = 'PAGADO'
          AND c.Cve_Sucursal <> 99
          AND {filtro}
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
def plantilla(tipo: str, modo: str = Query("30d"), fi: str = Query(None), ff: str = Query(None)):
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
    if fi and ff: modo = "custom"

    if tipo == "ventas_sucursal":
        fa, fb, _ = _filtros_periodo(modo, "t.Fecha_Documento", fi, ff)
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
            SELECT s.Nombre AS label, COUNT(CASE WHEN p.Estatus<>'CN' THEN 1 END) AS valor
            FROM GN_Sucursales s
            LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre HAVING COUNT(CASE WHEN p.Estatus<>'CN' THEN 1 END)>0
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
        try:
            from collections import defaultdict
            from datetime import datetime as _dt
            _MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                      "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
            daily = query(f"""
                SELECT fecha, sucursal, SUM(valor) AS valor FROM (
                    SELECT CAST(c.Fecha_Documento AS DATE) AS fecha,
                           s.Nombre AS sucursal,
                           c.Cve_Folio AS folio,
                           ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                    FROM FT_Pedidos_C c
                    INNER JOIN FT_Pedidos_Dia d
                      ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                    INNER JOIN GN_Sucursales s ON s.Cve_Sucursal=c.Cve_Sucursal
                    WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                      AND c.Cve_Sucursal <> 99
                      AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(MONTH,-5,CAST({hoy()} AS DATE))
                    GROUP BY CAST(c.Fecha_Documento AS DATE), s.Nombre, c.Cve_Folio
                ) t GROUP BY fecha, sucursal ORDER BY fecha
            """)
            # Agregación por (mes, sucursal) en Python
            monthly: dict = defaultdict(lambda: defaultdict(float))
            for r in daily:
                f = r.get("fecha")
                if f is None:
                    continue
                k = (f.year, f.month) if hasattr(f, "year") else (
                    _dt.strptime(str(f)[:10], "%Y-%m-%d").year,
                    _dt.strptime(str(f)[:10], "%Y-%m-%d").month,
                )
                suc = r.get("sucursal") or "—"
                monthly[k][suc] += float(r.get("valor") or 0)
            rows = []
            for k, suc_dict in sorted(monthly.items()):
                for suc, val in sorted(suc_dict.items()):
                    rows.append({
                        "anio": k[0], "mes": k[1], "mes_nombre": _MESES[k[1]],
                        "sucursal": suc, "valor": round(val, 2),
                    })
        except Exception:
            rows = []
        return JSONResponse({"tipo": tipo, "titulo": "Ventas por sucursal — últimos 6 meses", "datos": rows})

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

    # ── Tendencia (hasta 24 meses — mejora con el tiempo) ────────────────────
    elif tipo == "tendencia_anual":
        try:
            from collections import defaultdict
            from datetime import datetime as _dt
            _MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                      "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
            daily = query(f"""
                SELECT fecha, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                    SELECT CAST(c.Fecha_Documento AS DATE) AS fecha,
                           c.Cve_Folio AS folio,
                           ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                    FROM FT_Pedidos_C c
                    INNER JOIN FT_Pedidos_Dia d
                      ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                    WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                      AND CAST(c.Fecha_Documento AS DATE) >= DATEADD(MONTH,-23,CAST({hoy()} AS DATE))
                    GROUP BY CAST(c.Fecha_Documento AS DATE), c.Cve_Folio
                ) t GROUP BY fecha ORDER BY fecha
            """)
            monthly: dict = defaultdict(lambda: {"valor": 0.0, "pedidos": 0})
            for r in daily:
                f = r.get("fecha")
                if f is None:
                    continue
                k = (f.year, f.month) if hasattr(f, "year") else (
                    _dt.strptime(str(f)[:10], "%Y-%m-%d").year,
                    _dt.strptime(str(f)[:10], "%Y-%m-%d").month,
                )
                monthly[k]["valor"]   += float(r.get("valor") or 0)
                monthly[k]["pedidos"] += int(r.get("pedidos") or 0)
            rows = [
                {"anio": k[0], "mes": k[1], "mes_nombre": _MESES[k[1]],
                 "valor": round(v["valor"], 2), "pedidos": v["pedidos"]}
                for k, v in sorted(monthly.items()) if v["valor"] > 0
            ]
        except Exception:
            rows = []
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Tendencia de ventas — 12 meses + proyección",
                             "total": total, "datos": rows})

    # ── Top productos ─────────────────────────────────────────────────────────
    elif tipo == "top_productos":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        elif modo == "15d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-15,{hoy_fecha})"
        elif modo == "hoy":
            filtro = f"CAST(c.Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)"
        else:
            filtro = (f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) "
                      f"AND MONTH(c.Fecha_Documento)=MONTH({hoy()})")
        try:
            rows = query(f"""
                SELECT TOP 10
                    MIN(pg.Descripcion)                 AS label,
                    SUM(d.Cantidad_Ordenada * d.Precio) AS valor,
                    SUM(d.Cantidad_Ordenada)            AS unidades
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                    ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
                INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
                WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
                  AND c.Cve_Sucursal <> 99
                  AND {filtro}
                GROUP BY d.Cve_Producto
                ORDER BY SUM(d.Cantidad_Ordenada * d.Precio) DESC
            """)
        except Exception as _e:
            raise HTTPException(500, f"top_productos SQL error: {_e}")
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
               AND t.Cve_Cliente <> '20000'
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
        out_data   = _inner.get("stockouts", {})
        total_v    = float(stock_data.get("total_valor", 0) or 0)
        total_u    = float(stock_data.get("total_unidades", 0) or 0)
        criticos   = sum(int(r.get("criticos", 0) or 0) for r in stock_data.get("datos", []))
        n_out      = int(out_data.get("total", 0) or 0)
        resumen = (f"Inventario total: {_fmt_mxn(total_v)} · {int(total_u):,} unidades. "
                   f"Productos críticos: {criticos}. "
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
        # Filtrar sucursales sin ventas en el período actual
        rows = [r for r in rows if float(r.get("actual") or 0) > 0]
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        _series = {
            "hoy":  ["Hoy",           "Ayer"],
            "15d":  ["Últ. 15 días",  "15 días previos"],
            "mes":  ["Mes actual",    "Mes ant. (comparable)"],
            "30d":  ["Últ. 30 días",  "30 días anteriores"],
        }
        series = _series.get(modo, ["Período actual", "30 días anteriores"])
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Ventas por sucursal ({label})",
                "series": series, "datos": rows}

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
        # Fetch 24 months to enable YoY seasonal projection
        rows = query(f"""
            SELECT anio, mes, mes_nombre, SUM(valor) AS valor, COUNT(folio) AS pedidos FROM (
                SELECT YEAR(c.Fecha_Documento) AS anio, MONTH(c.Fecha_Documento) AS mes,
                       DATENAME(MONTH, c.Fecha_Documento) AS mes_nombre, c.Cve_Folio AS folio,
                       ISNULL(SUM(d.Cantidad_Ordenada*d.Precio),0) AS valor
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal
                WHERE c.Estatus<>'CN' AND c.Referencia_Cliente='PAGADO'
                  AND c.Fecha_Documento >= DATEADD(MONTH,-23,
                      DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1))
                GROUP BY YEAR(c.Fecha_Documento), MONTH(c.Fecha_Documento),
                         DATENAME(MONTH, c.Fecha_Documento), c.Cve_Folio
            ) t GROUP BY anio, mes, mes_nombre ORDER BY anio, mes
        """)
        import calendar as _cal2
        from datetime import date as _d3
        _hd2 = _d3.today()
        _mes_actual = (_hd2.year, _hd2.month)

        # Build dict (anio, mes) -> valor
        mes_val: dict = {}
        for r in rows:
            k = (int(r["anio"]), int(r["mes"]))
            mes_val[k] = float(r.get("valor") or 0)

        # Last 12 complete months (exclude current partial month)
        all_keys = sorted(mes_val.keys())
        trend_keys_ta = [k for k in all_keys if k != _mes_actual][-12:]

        # YoY factor: compare each trend month vs same month previous year
        yoy_ratios_ta = []
        for k in trend_keys_ta:
            prev_k = (k[0] - 1, k[1])
            if prev_k in mes_val and mes_val[prev_k] > 0:
                yoy_ratios_ta.append(mes_val[k] / mes_val[prev_k])
        yoy_ta = sum(yoy_ratios_ta[-6:]) / len(yoy_ratios_ta[-6:]) if yoy_ratios_ta else 1.0
        yoy_ta = min(max(yoy_ta, 0.1), 5.0)

        # Recent 3-month average as fallback
        recent_avg_ta = sum(mes_val[k] for k in trend_keys_ta[-3:]) / 3 if len(trend_keys_ta) >= 3 else (mes_val[trend_keys_ta[-1]] if trend_keys_ta else 0)
        max_hist_ta = max((mes_val[k] for k in trend_keys_ta), default=0)
        techo_ta    = max_hist_ta * 1.10  # nunca proyectar más del 110% del máximo histórico

        # Project next 3 months seasonally
        MESES_ES_TA = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
                       "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        last_k_ta = trend_keys_ta[-1] if trend_keys_ta else (_hd2.year, _hd2.month - 1)
        proyeccion_meses = []
        for step in range(1, 4):
            mp = last_k_ta[1] + step
            ap = last_k_ta[0] + (mp - 1) // 12
            mp = ((mp - 1) % 12) + 1
            prev_year_k = (ap - 1, mp)
            val_p = mes_val[prev_year_k] * yoy_ta if prev_year_k in mes_val and mes_val[prev_year_k] > 0 else recent_avg_ta
            val_p = max(0.0, min(val_p, techo_ta) if techo_ta > 0 else val_p)
            proyeccion_meses.append({
                "mes_label": f"{MESES_ES_TA[mp]} {ap}",
                "valor": round(val_p, 2),
            })

        # Keep single proyeccion for backward compat (next month)
        proyeccion = proyeccion_meses[0]["valor"] if proyeccion_meses else 0.0

        # Only expose last 12 months in datos for chart
        datos_12 = [r for r in rows if (int(r["anio"]), int(r["mes"])) in set(trend_keys_ta)]
        total = sum(float(r.get("valor") or 0) for r in datos_12)

        return {
            "tipo": tipo, "titulo": "Tendencia anual de ventas",
            "total": total,
            "proyeccion": proyeccion,
            "proyeccion_label": proyeccion_meses[0]["mes_label"] if proyeccion_meses else "",
            "proyeccion_meses": proyeccion_meses,
            "yoy_factor": round(yoy_ta, 2),
            "datos": datos_12,
        }

    elif tipo == "top_productos":
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento", fi, ff)
        rows = query(f"""
            SELECT TOP 10
                MIN(pg.Descripcion)                 AS label,
                SUM(d.Cantidad_Ordenada * d.Precio) AS valor,
                SUM(d.Cantidad_Ordenada)            AS unidades
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
            WHERE c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO'
              AND c.Cve_Sucursal <> 99
              AND {filtro}
            GROUP BY d.Cve_Producto
            ORDER BY SUM(d.Cantidad_Ordenada * d.Precio) DESC
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
               AND t.Cve_Cliente <> '20000'
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
                    "total_actual":     total_actual,
                    "total_anterior":   total_anterior,
                    "variacion":        variacion,
                    "n_sucursales":     n_sucursales,
                    "total_pedidos":    total_pedidos,
                    "ticket_promedio":  ticket_promedio,
                    "proyeccion":       proyeccion,
                    "mes_proyeccion":   mes_sig_nombre,
                    "pedidos_activos":  int(pedid.get("total", 0)),
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
        out   = _fetch_tipo("stockouts",        modo)

        # Tendencia histórica de valor de stock (últimos 4 meses)
        # IN_Existencias_Alm_Diario puede no existir en todos los ERP — si falla, lista vacía
        try:
            tendencia_stock = query(f"""
                SELECT TOP 4 anio, mes, mes_nombre, SUM(valor) AS valor, SUM(unidades) AS unidades
                FROM (
                    SELECT YEAR(h.Fecha) AS anio, MONTH(h.Fecha) AS mes,
                           DATENAME(MONTH, h.Fecha) AS mes_nombre,
                           ISNULL(h.Existencia * ISNULL(pg.Costo_Promedio,0), 0) AS valor,
                           ISNULL(h.Existencia, 0) AS unidades
                    FROM IN_Existencias_Alm_Diario h
                    JOIN GN_Sucursales s ON s.Cve_Sucursal = h.Cve_Sucursal
                    JOIN IM_Productos_Gral pg ON CAST(pg.Cve_Producto AS VARCHAR) = h.Cve_Producto
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
                   ISNULL(SUM(e.Existencia * ISNULL(pg.Costo_Promedio,0)),0) AS valor_stock,
                   ISNULL(SUM(v.ventas_30d),0) AS ventas_30d,
                   CASE WHEN SUM(e.Existencia * ISNULL(pg.Costo_Promedio,0)) > 0
                        THEN ROUND(SUM(v.ventas_30d) / SUM(e.Existencia * ISNULL(pg.Costo_Promedio,0)), 2)
                        ELSE 0 END AS rotacion,
                   CASE WHEN SUM(v.ventas_diaria) > 0
                        THEN ROUND(SUM(e.Existencia) / SUM(v.ventas_diaria), 0)
                        ELSE NULL END AS dias_cobertura
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status='AC'
            LEFT JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
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
                   ISNULL(SUM(e.Existencia * ISNULL(pg.Costo_Promedio, 0)), 0) AS actual,
                   ISNULL(SUM(e.Existencia), 0)                                AS unidades,
                   COUNT(CASE WHEN e.Existencia > 0 AND e.Existencia <= 5 THEN 1 END) AS criticos
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status = 'AC'
            LEFT JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
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


class PdfUpdate(BaseModel):
    pdf_b64: str = ""


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


# ── Dashboard Vendedores ──────────────────────────────────────────────────────
@router.get("/vendedores")
def vendedores_dashboard(modo: str = "30d", mes: str = None, fi: str = None, ff: str = None):
    """
    Dashboard de Vendedores.

    Retorna:
      - total_ventas, lider_nombre, lider_importe, total_pedidos, vendedores_activos
      - ranking: todos los vendedores del período con variación vs período anterior
      - por_sucursal: vendedor líder por sucursal
      - por_mes: ventas mensuales de los top 5 vendedores (últimos 6 meses)
      - detalle: tabla completa con sucursal principal
    """
    _hoy = hoy()

    # ── Construir filtro de fechas — anterior = período inmediatamente previo ──
    if fi and ff:
        # Rango personalizado: el anterior es el mismo número de días previos al rango
        fecha_ini = f"CAST('{fi}' AS DATE)"
        fecha_fin = f"CAST('{ff}' AS DATE)"
        fecha_ini_a = f"DATEADD(DAY, -DATEDIFF(DAY, CAST('{fi}' AS DATE), CAST('{ff}' AS DATE)) - 1, CAST('{fi}' AS DATE))"
        fecha_fin_a = f"DATEADD(DAY, -1, CAST('{fi}' AS DATE))"
    elif mes:
        # mes = "YYYY-MM"
        try:
            anio_m, num_m = int(mes[:4]), int(mes[5:7])
        except (ValueError, IndexError):
            raise HTTPException(400, "Formato de mes inválido, use YYYY-MM")
        anio_p = anio_m - 1 if num_m == 1 else anio_m
        mes_p  = 12        if num_m == 1 else num_m - 1
        fecha_ini   = f"CAST('{anio_m:04d}-{num_m:02d}-01' AS DATE)"
        fecha_fin   = f"EOMONTH(CAST('{anio_m:04d}-{num_m:02d}-01' AS DATE))"
        fecha_ini_a = f"CAST('{anio_p:04d}-{mes_p:02d}-01' AS DATE)"
        fecha_fin_a = f"EOMONTH(CAST('{anio_p:04d}-{mes_p:02d}-01' AS DATE))"
    elif modo == "mes":
        fecha_ini   = f"DATEFROMPARTS(YEAR({_hoy}), MONTH({_hoy}), 1)"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEFROMPARTS(YEAR(DATEADD(MONTH,-1,{_hoy})), MONTH(DATEADD(MONTH,-1,{_hoy})), 1)"
        fecha_fin_a = f"EOMONTH(DATEADD(MONTH,-1,{_hoy}))"
    elif modo == "hoy":
        fecha_ini   = _hoy
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEADD(DAY, -1, {_hoy})"
        fecha_fin_a = f"DATEADD(DAY, -1, {_hoy})"
    elif modo == "15d":
        fecha_ini   = f"DATEADD(DAY, -15, {_hoy})"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEADD(DAY, -30, {_hoy})"
        fecha_fin_a = f"DATEADD(DAY, -16, {_hoy})"
    else:
        # modo "30d" — últimos 30 días (default)
        fecha_ini   = f"DATEADD(DAY, -30, {_hoy})"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEADD(DAY, -60, {_hoy})"
        fecha_fin_a = f"DATEADD(DAY, -31, {_hoy})"

    where_periodo   = f"c.Fecha_Documento >= {fecha_ini} AND c.Fecha_Documento <= {fecha_fin}"
    where_anterior  = f"c.Fecha_Documento >= {fecha_ini_a} AND c.Fecha_Documento <= {fecha_fin_a}"
    filtro_base     = "c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO' AND c.Cve_Sucursal <> 99"

    # ── 1. Ventas del período por vendedor ────────────────────────────────────
    try:
        vend_rows = query(f"""
            SELECT
                v.Nombre                                         AS nombre,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0)  AS importe,
                COUNT(DISTINCT c.Cve_Folio)                     AS pedidos
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
            WHERE {filtro_base} AND {where_periodo}
            GROUP BY v.Nombre
            ORDER BY importe DESC
        """)
    except Exception as e:
        raise HTTPException(500, f"vendedores-ranking: {e}")

    # ── 2. Ventas año anterior por vendedor ───────────────────────────────────
    try:
        ant_rows = query(f"""
            SELECT
                v.Nombre                                         AS nombre,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0)  AS importe
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
            WHERE {filtro_base} AND {where_anterior}
            GROUP BY v.Nombre
        """)
        ant_map = {r["nombre"]: float(r["importe"] or 0) for r in ant_rows}
    except Exception as e:
        raise HTTPException(500, f"vendedores-anterior: {e}")

    # ── 3. Construir ranking con variación ────────────────────────────────────
    ranking = []
    for r in vend_rows:
        importe   = round(float(r["importe"] or 0), 2)
        pedidos   = int(r["pedidos"] or 0)
        ant       = round(ant_map.get(r["nombre"], 0.0), 2)
        if ant > 0:
            variacion = round((importe - ant) / ant * 100, 1)
        else:
            variacion = None
        ranking.append({
            "nombre":           (r["nombre"] or "").strip(),
            "importe":          importe,
            "pedidos":          pedidos,
            "ticket_promedio":  round(importe / pedidos, 2) if pedidos > 0 else 0.0,
            "importe_anterior": ant,
            "variacion":        variacion,
        })

    # ── 4. KPIs globales ──────────────────────────────────────────────────────
    total_ventas       = round(sum(r["importe"] for r in ranking), 2)
    total_pedidos_set  = sum(r["pedidos"] for r in ranking)
    vendedores_activos = len(ranking)
    lider = ranking[0] if ranking else {}
    lider_nombre = lider.get("nombre", "—")
    lider_importe = lider.get("importe", 0.0)

    # ── 5. Top vendedor por sucursal ──────────────────────────────────────────
    try:
        suc_rows = query(f"""
            SELECT
                s.Nombre                                        AS sucursal,
                v.Nombre                                        AS vendedor,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN GN_Sucursales s ON s.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
            WHERE {filtro_base} AND {where_periodo}
            GROUP BY s.Nombre, v.Nombre
        """)
        # Para cada sucursal: quedarse con el vendedor de mayor importe
        suc_dict: dict = {}
        for r in suc_rows:
            suc  = (r["sucursal"] or "").strip()
            vend = (r["vendedor"]  or "").strip()
            imp  = round(float(r["importe"] or 0), 2)
            if suc not in suc_dict or imp > suc_dict[suc]["importe"]:
                suc_dict[suc] = {"sucursal": suc, "vendedor": vend, "importe": imp}
        por_sucursal = sorted(suc_dict.values(), key=lambda x: x["importe"], reverse=True)
    except Exception as e:
        raise HTTPException(500, f"vendedores-sucursal: {e}")

    # ── 6. Ventas mensuales top 5 vendedores (últimos 6 meses) ───────────────
    try:
        top5_nombres = [r["nombre"] for r in ranking[:5]]
        if top5_nombres:
            placeholders = ", ".join(["?" for _ in top5_nombres])
            mes_rows = query(f"""
                SELECT
                    FORMAT(c.Fecha_Documento, 'yyyy-MM') AS mes,
                    v.Nombre                              AS vendedor,
                    ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                    ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
                INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
                WHERE {filtro_base}
                  AND c.Cve_Sucursal <> 99
                  AND c.Fecha_Documento >= DATEADD(MONTH, -6, {_hoy})
                  AND v.Nombre IN ({placeholders})
                GROUP BY FORMAT(c.Fecha_Documento, 'yyyy-MM'), v.Nombre
                ORDER BY mes ASC
            """, params=top5_nombres)
            por_mes = [
                {
                    "mes":      r["mes"],
                    "vendedor": (r["vendedor"] or "").strip(),
                    "importe":  round(float(r["importe"] or 0), 2),
                }
                for r in mes_rows
            ]
        else:
            por_mes = []
    except Exception as e:
        raise HTTPException(500, f"vendedores-por-mes: {e}")

    # ── 7. Detalle: sucursal principal por vendedor ───────────────────────────
    try:
        det_rows = query(f"""
            SELECT
                v.Nombre                                        AS vendedor,
                s.Nombre                                        AS sucursal,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
            FROM FT_Pedidos_C c
            INNER JOIN FT_Pedidos_Dia d
                ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
            INNER JOIN GN_Sucursales s ON s.Cve_Sucursal = c.Cve_Sucursal
            WHERE {filtro_base} AND {where_periodo}
            GROUP BY v.Nombre, s.Nombre
        """)
        # Sucursal principal = la que más vendió para ese vendedor
        vend_suc: dict = {}
        for r in det_rows:
            vend = (r["vendedor"] or "").strip()
            suc  = (r["sucursal"] or "").strip()
            imp  = float(r["importe"] or 0)
            if vend not in vend_suc or imp > vend_suc[vend]["_max"]:
                vend_suc[vend] = {"sucursal_principal": suc, "_max": imp}
        suc_principal_map = {k: v["sucursal_principal"] for k, v in vend_suc.items()}
    except Exception as e:
        raise HTTPException(500, f"vendedores-detalle: {e}")

    detalle = [
        {
            "nombre":             r["nombre"],
            "importe":            r["importe"],
            "pedidos":            r["pedidos"],
            "ticket_promedio":    r["ticket_promedio"],
            "variacion":          r["variacion"],
            "sucursal_principal": suc_principal_map.get(r["nombre"], "—"),
        }
        for r in ranking
    ]

    # ── 8. Top 5 productos por vendedor (top 8 vendedores) ───────────────────
    top8_nombres = [r["nombre"] for r in ranking[:8]]
    prod_por_vendedor = []
    if top8_nombres:
        try:
            placeholders = ", ".join(["?" for _ in top8_nombres])
            pvp_rows = query(f"""
                SELECT
                    v.Nombre                                                AS vendedor,
                    pg.Descripcion                                          AS producto,
                    ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0)        AS importe
                FROM FT_Pedidos_C c
                INNER JOIN FT_Pedidos_Dia d
                    ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
                INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = c.Cve_Vendedor
                INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
                WHERE {filtro_base} AND {where_periodo}
                  AND v.Nombre IN ({placeholders})
                GROUP BY v.Nombre, pg.Descripcion
                ORDER BY v.Nombre, importe DESC
            """, params=top8_nombres)
            from collections import defaultdict as _dd
            _vp: dict = _dd(list)
            for r in pvp_rows:
                vn = (r["vendedor"] or "").strip()
                if len(_vp[vn]) < 5:
                    _vp[vn].append({
                        "producto": (r["producto"] or "").strip()[:45],
                        "importe":  round(float(r["importe"] or 0), 2),
                    })
            prod_por_vendedor = [
                {"vendedor": n, "productos": _vp[n]}
                for n in top8_nombres if _vp[n]
            ]
        except Exception:
            prod_por_vendedor = []

    return JSONResponse({
        "total_ventas":        total_ventas,
        "lider_nombre":        lider_nombre,
        "lider_importe":       lider_importe,
        "total_pedidos":       total_pedidos_set,
        "vendedores_activos":  vendedores_activos,
        "ranking":             ranking,
        "por_sucursal":        por_sucursal,
        "por_mes":             por_mes,
        "detalle":             detalle,
        "prod_por_vendedor":   prod_por_vendedor,
    })


@router.get("/medicos")
def medicos_dashboard(modo: str = "30d", mes: str = None, fi: str = None, ff: str = None):
    """
    Dashboard de Médicos.
    Ventas atribuidas a médicos vía CM_Clientes.Cve_Ruta → GC_Medicos.Cve_Medico.
    """
    _hoy = hoy()

    # ── Filtros de período — anterior = período inmediatamente previo ────────────
    if fi and ff:
        fecha_ini   = f"CAST('{fi}' AS DATE)"
        fecha_fin   = f"CAST('{ff}' AS DATE)"
        fecha_ini_a = f"DATEADD(DAY, -DATEDIFF(DAY, CAST('{fi}' AS DATE), CAST('{ff}' AS DATE)) - 1, CAST('{fi}' AS DATE))"
        fecha_fin_a = f"DATEADD(DAY, -1, CAST('{fi}' AS DATE))"
    elif mes:
        try:
            anio_m, num_m = int(mes[:4]), int(mes[5:7])
        except (ValueError, IndexError):
            raise HTTPException(400, "Formato de mes inválido, use YYYY-MM")
        anio_p = anio_m - 1 if num_m == 1 else anio_m
        mes_p  = 12        if num_m == 1 else num_m - 1
        fecha_ini   = f"CAST('{anio_m:04d}-{num_m:02d}-01' AS DATE)"
        fecha_fin   = f"EOMONTH(CAST('{anio_m:04d}-{num_m:02d}-01' AS DATE))"
        fecha_ini_a = f"CAST('{anio_p:04d}-{mes_p:02d}-01' AS DATE)"
        fecha_fin_a = f"EOMONTH(CAST('{anio_p:04d}-{mes_p:02d}-01' AS DATE))"
    elif modo == "mes":
        # Actual: del 1 al día de hoy; anterior: mes calendario completo anterior
        fecha_ini   = f"DATEFROMPARTS(YEAR({_hoy}), MONTH({_hoy}), 1)"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEFROMPARTS(YEAR(DATEADD(MONTH,-1,{_hoy})), MONTH(DATEADD(MONTH,-1,{_hoy})), 1)"
        fecha_fin_a = f"EOMONTH(DATEADD(MONTH,-1,{_hoy}))"
    elif modo == "hoy":
        fecha_ini = fecha_fin = _hoy
        fecha_ini_a = fecha_fin_a = f"DATEADD(DAY, -1, {_hoy})"
    elif modo == "15d":
        fecha_ini   = f"DATEADD(DAY, -15, {_hoy})"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEADD(DAY, -30, {_hoy})"
        fecha_fin_a = f"DATEADD(DAY, -16, {_hoy})"
    else:  # 30d
        fecha_ini   = f"DATEADD(DAY, -30, {_hoy})"
        fecha_fin   = _hoy
        fecha_ini_a = f"DATEADD(DAY, -60, {_hoy})"
        fecha_fin_a = f"DATEADD(DAY, -31, {_hoy})"

    filtro_base    = "c.Estatus <> 'CN' AND c.Referencia_Cliente = 'PAGADO' AND c.Cve_Sucursal <> 99"
    filtro_medico  = "cl.Cve_Ruta IS NOT NULL AND cl.Cve_Ruta <> 0 AND cl.Cve_Ruta <> 1"
    where_periodo  = f"c.Fecha_Documento >= {fecha_ini} AND c.Fecha_Documento <= {fecha_fin}"
    where_anterior = f"c.Fecha_Documento >= {fecha_ini_a} AND c.Fecha_Documento <= {fecha_fin_a}"
    joins_base     = """
        INNER JOIN FT_Pedidos_Dia d
            ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
        INNER JOIN CM_Clientes cl ON CAST(c.Cve_Cliente AS INT) = cl.Cve_Cliente
        INNER JOIN GC_Medicos m ON m.Cve_Medico = cl.Cve_Ruta
        LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = m.cve_vendedor
    """

    # ── 1. Ventas del período por médico ──────────────────────────────────────
    try:
        med_rows = query(f"""
            SELECT
                m.Cve_Medico                                            AS cve_medico,
                m.Nombre                                                AS nombre,
                ISNULL(v.Nombre, 'Sin rep')                             AS vendedor,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0)        AS importe,
                COUNT(DISTINCT c.Cve_Folio)                             AS pedidos,
                COUNT(DISTINCT c.Cve_Cliente)                           AS clientes
            FROM FT_Pedidos_C c {joins_base}
            WHERE {filtro_base} AND {filtro_medico} AND {where_periodo}
            GROUP BY m.Cve_Medico, m.Nombre, v.Nombre
            ORDER BY importe DESC
        """)
    except Exception as e:
        raise HTTPException(500, f"medicos-ranking: {e}")

    # ── 2. Período anterior ───────────────────────────────────────────────────
    try:
        ant_rows = query(f"""
            SELECT
                m.Cve_Medico                                     AS cve_medico,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
            FROM FT_Pedidos_C c {joins_base}
            WHERE {filtro_base} AND {filtro_medico} AND {where_anterior}
            GROUP BY m.Cve_Medico
        """)
        ant_map = {int(r["cve_medico"]): round(float(r["importe"] or 0), 2) for r in ant_rows}
    except Exception as e:
        raise HTTPException(500, f"medicos-anterior: {e}")

    # ── 3. Construir ranking con variación ────────────────────────────────────
    ranking = []
    for r in med_rows:
        importe = round(float(r["importe"] or 0), 2)
        pedidos = int(r["pedidos"] or 0)
        clientes = int(r["clientes"] or 0)
        ant     = ant_map.get(int(r["cve_medico"]), 0.0)
        variacion = round((importe - ant) / ant * 100, 1) if ant > 0 else None
        ranking.append({
            "nombre":    (r["nombre"]   or "").strip(),
            "vendedor":  (r["vendedor"] or "").strip(),
            "importe":   importe,
            "pedidos":   pedidos,
            "clientes":  clientes,
            "ticket":    round(importe / pedidos, 2) if pedidos > 0 else 0.0,
            "variacion": variacion,
        })

    # ── 4. KPIs ───────────────────────────────────────────────────────────────
    total_ventas   = round(sum(r["importe"] for r in ranking), 2)
    medicos_activos = len(ranking)
    lider           = ranking[0] if ranking else {}
    lider_nombre    = lider.get("nombre", "—")
    lider_importe   = lider.get("importe", 0.0)

    # Rep con más médicos activos
    from collections import Counter as _Counter
    rep_count = _Counter(r["vendedor"] for r in ranking if r["vendedor"] != "Sin rep")
    top_rep   = rep_count.most_common(1)[0][0] if rep_count else "—"

    # ── 5. Ventas por representante (agrupa médicos de cada rep) ─────────────
    try:
        rep_rows = query(f"""
            SELECT
                ISNULL(v.Nombre, 'Sin rep')                             AS rep,
                ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0)        AS importe,
                COUNT(DISTINCT m.Cve_Medico)                            AS medicos
            FROM FT_Pedidos_C c {joins_base}
            WHERE {filtro_base} AND {filtro_medico} AND {where_periodo}
            GROUP BY v.Nombre
            ORDER BY importe DESC
        """)
        por_rep = [
            {"rep": (r["rep"] or "").strip(), "importe": round(float(r["importe"] or 0), 2), "medicos": int(r["medicos"] or 0)}
            for r in rep_rows
        ]
    except Exception:
        por_rep = []

    # ── 6. Tendencia mensual top 5 médicos (últimos 6 meses) ─────────────────
    top5_nombres = [r["nombre"] for r in ranking[:5]]
    por_mes = []
    if top5_nombres:
        try:
            placeholders = ", ".join(["?" for _ in top5_nombres])
            mes_rows = query(f"""
                SELECT
                    FORMAT(c.Fecha_Documento, 'yyyy-MM') AS mes,
                    m.Nombre                              AS medico,
                    ISNULL(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
                FROM FT_Pedidos_C c {joins_base}
                WHERE {filtro_base} AND {filtro_medico}
                  AND c.Fecha_Documento >= DATEADD(MONTH, -6, {_hoy})
                  AND m.Nombre IN ({placeholders})
                GROUP BY FORMAT(c.Fecha_Documento, 'yyyy-MM'), m.Nombre
                ORDER BY mes ASC
            """, params=top5_nombres)
            por_mes = [
                {"mes": r["mes"], "medico": (r["medico"] or "").strip(),
                 "importe": round(float(r["importe"] or 0), 2)}
                for r in mes_rows
            ]
        except Exception:
            por_mes = []

    return JSONResponse({
        "total_ventas":     total_ventas,
        "lider_nombre":     lider_nombre,
        "lider_importe":    lider_importe,
        "medicos_activos":  medicos_activos,
        "top_rep":          top_rep,
        "ranking":          ranking,
        "por_rep":          por_rep,
        "por_mes":          por_mes,
    })
