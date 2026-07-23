# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_helpers.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
# ============================================================
"""
Utilidades compartidas para los sub-módulos de datos del Studio.

Incluye:
  - Imports comunes
  - Constantes (MESES_ES, _SPECS_TIPO, prompts de IA)
  - Modelos Pydantic (DashboardGuardar, GenerarBody, PdfUpdate)
  - Estado global (caché mapa, cliente OpenAI)
  - Funciones helper (_geocode_cp, _proyectar, _holt_winters_forecast,
    _filtros_periodo, _init_mapa_tables, _fmt_mxn, _resumir_datos,
    _clasificar, _narrar)
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

_client = OpenAI(api_key=OPENAI_API_KEY)

# Caché en memoria para el mapa de ventas por CP
_mapa_cache: dict = {}
# CPs actualmente siendo geocodificados en background (evita lanzar 2 threads para el mismo mes)
_geocodificando: set = set()

# ── Inicialización de tablas de caché del mapa ───────────────────────────────
_mapa_tables_ready = False

MESES_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
            "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


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
    """Regresión lineal simple — proyecta el siguiente valor de la serie (fallback)."""
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


def _holt_winters_forecast(serie: list, pasos: int = 6) -> list:
    """
    Proyección con Holt-Winters (Triple Exponential Smoothing).
    Maneja tendencia + estacionalidad anual (período 12).

    - Si hay ≥ 24 puntos: modelo aditivo completo (trend + seasonal).
    - Si hay ≥ 12 puntos: solo tendencia (sin componente estacional).
    - Si hay < 12 puntos: fallback a regresión lineal.

    Retorna lista de `pasos` valores proyectados (float, ≥ 0).
    """
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        import numpy as np
        import warnings

        vals = [float(v) for v in serie if v is not None and float(v) >= 0]
        n = len(vals)
        if n < 6:
            last = vals[-1] if vals else 0.0
            return [round(last, 2)] * pasos

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if n >= 24:
                modelo = ExponentialSmoothing(
                    vals,
                    trend="add",
                    seasonal="add",
                    seasonal_periods=12,
                    initialization_method="estimated",
                ).fit(optimized=True)
            elif n >= 12:
                modelo = ExponentialSmoothing(
                    vals,
                    trend="add",
                    seasonal=None,
                    initialization_method="estimated",
                ).fit(optimized=True)
            else:
                modelo = ExponentialSmoothing(
                    vals,
                    trend=None,
                    seasonal=None,
                    initialization_method="estimated",
                ).fit(optimized=True)

            forecast = modelo.forecast(pasos)

        # No permitir proyecciones negativas
        return [max(0.0, round(float(v), 2)) for v in forecast]

    except Exception:
        # Fallback a regresión lineal si statsmodels falla
        base = _proyectar(serie)
        return [round(base, 2)] * pasos


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
    # Contactos
    "medicos_dashboard":    {"titulo": "Dashboard de Contactos",              "layout": "tab_medicos"},
}

_SISTEMA_CLASIFICADOR = """
Eres el clasificador de dashboards del Studio Analítico de una empresa distribuidora de abarrotes.
Tu trabajo: leer la solicitud del usuario y decidir qué dashboard visual generar.

REGLA PRINCIPAL — Studio SIEMPRE genera dashboards. Usa "ninguno" solo como último recurso.

Ejemplos de solicitudes → función correcta:
  "ventas de hoy"                              → ventas_hoy
  "dame ventas"  /  "muéstrame ventas"         → reporte_ventas
  "gráfica de sucursales"  /  "ventas por sucursal"  /  "comparativa de sucursales" → ventas_sucursal + single_chart
  "top vendedores"  /  "mejores vendedores"    → top_vendedores
  "dashboard de contactos"  /  "contactos"  /  "prescriptores"  → medicos_dashboard
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
  medicos_dashboard    → Dashboard de Contactos: ranking de contactos por ventas, tendencia mensual, ventas por rep. layout: tab_medicos
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
Eres analista de negocios de una distribuidora de abarrotes.
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


class GenerarBody(BaseModel):
    """Cuerpo para POST /api/datos/generar."""
    pregunta:     Optional[str] = None   # Texto libre del usuario
    tipo:         Optional[str] = None   # Tipo predefinido (omite clasificación IA)
    modo:         Optional[str] = "30d"  # hoy | 15d | 30d | mes | custom
    fecha_inicio: Optional[str] = None   # ISO 'YYYY-MM-DD' para modo='custom'
    fecha_fin:    Optional[str] = None   # ISO 'YYYY-MM-DD' para modo='custom'


class PdfUpdate(BaseModel):
    pdf_b64: str = ""


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
