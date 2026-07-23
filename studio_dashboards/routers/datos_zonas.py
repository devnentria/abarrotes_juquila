# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_zonas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Sub-router de datos: Mapa de ventas por CP y Zonas.

Endpoints:
  GET  /mapa   → Ventas por código postal (mapa de puntos)
  GET  /zonas  → Dashboard Zonas: mapa + ventas por sucursal

Fuentes de venta:
  - FT_Remisiones_C/D  (Status='AC', Cve_Movimiento='VTA')  → Importe_Neto en header
  - FT_Facturas_C/D    (Status='AC', Cve_Movimiento IN ('FM','FP')) → Importe_Total en header
  Mapa por CP solo usa FT_Facturas_C (tiene Cve_Consignatario).
"""
import json
import time
import threading
from collections import defaultdict
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from shared.database import query, hoy
from shared.database_local import execute, fetch_all, fetch_one

from .datos_helpers import (
    _mapa_cache, _geocodificando, _init_mapa_tables, _geocode_cp, MESES_ES,
)

router = APIRouter()


# ── Helpers: SQL fragments ──────────────────────────────────────────────────

def _ventas_union(anio: int, mes: int, *, cols_extra: str = "",
                  join_extra: str = "", where_extra: str = "") -> str:
    """
    UNION ALL of remisiones + facturas for aggregate queries (ventas/piezas).
    Both branches carry date filters inside to avoid full-table scans.
    Returns a CTE named `vta` with columns: Cve_Sucursal, Cve_Folio, ventas_header.
    Caller may request extra columns via cols_extra.
    """
    return f"""
    vta AS (
        SELECT r.Cve_Sucursal, r.Cve_Folio,
               r.Importe_Neto AS ventas_header
               {(',' + cols_extra) if cols_extra else ''}
        FROM FT_Remisiones_C r
        {join_extra}
        WHERE r.Status = 'AC'
          AND r.Cve_Movimiento = 'VTA'
          AND r.Cve_Sucursal <> 99
          AND YEAR(r.Fecha_Documento) = {anio}
          AND MONTH(r.Fecha_Documento) = {mes}
          {where_extra}

        UNION ALL

        SELECT f.Cve_Sucursal, f.Cve_Folio,
               f.Importe_Total AS ventas_header
               {(',' + cols_extra.replace('r.', 'f.')) if cols_extra else ''}
        FROM FT_Facturas_C f
        {join_extra.replace('r.', 'f.')}
        WHERE f.Status = 'AC'
          AND f.Cve_Movimiento IN ('FM','FP')
          AND f.Cve_Sucursal <> 99
          AND YEAR(f.Fecha_Documento) = {anio}
          AND MONTH(f.Fecha_Documento) = {mes}
          {where_extra.replace('r.', 'f.')}
    )"""


def _detail_union(anio: int, mes: int) -> str:
    """
    UNION ALL of detail tables joined to their headers, producing:
    Cve_Sucursal, Cve_Folio, Cve_Producto, Cantidad, Precio, Importe_Neto.
    """
    return f"""
    det AS (
        SELECT r.Cve_Sucursal, d.Cve_Folio, d.Cve_Producto,
               d.Cantidad, d.Precio, d.Importe_Neto
        FROM FT_Remisiones_D d
        INNER JOIN FT_Remisiones_C r
          ON r.Cve_Folio = d.Cve_Folio
         AND r.Cve_Sucursal = d.Cve_Sucursal
         AND r.Cve_Movimiento = d.Cve_Movimiento
        WHERE r.Status = 'AC'
          AND r.Cve_Movimiento = 'VTA'
          AND r.Cve_Sucursal <> 99
          AND YEAR(r.Fecha_Documento) = {anio}
          AND MONTH(r.Fecha_Documento) = {mes}

        UNION ALL

        SELECT f.Cve_Sucursal, d.Cve_Folio, d.Cve_Producto,
               d.Cantidad, d.Precio, d.Importe_Neto
        FROM FT_Facturas_D d
        INNER JOIN FT_Facturas_C f
          ON f.Cve_Folio = d.Cve_Folio
         AND f.Cve_Sucursal = d.Cve_Sucursal
         AND f.Cve_Movimiento = d.Cve_Movimiento
        WHERE f.Status = 'AC'
          AND f.Cve_Movimiento IN ('FM','FP')
          AND f.Cve_Sucursal <> 99
          AND YEAR(f.Fecha_Documento) = {anio}
          AND MONTH(f.Fecha_Documento) = {mes}
    )"""


# ── Mapa de ventas por código postal ─────────────────────────────────────────

@router.get("/mapa")
def mapa_ventas(anio: int = Query(None), mes: int = Query(None)):
    """
    Ventas por código postal (domicilio de entrega) para el mapa de puntos.
    Parámetros anio+mes seleccionan un mes específico.
    Meses históricos se cachean permanentemente en SQLite.
    Mes actual: TTL 10 min en memoria + SQLite (persiste entre reinicios).
    Las coordenadas de cada CP se obtienen via Nominatim y se guardan en SQLite.

    Solo usa FT_Facturas_C porque FT_Remisiones_C no tiene Cve_Consignatario.

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

    # 3. Consultar SQL Server — solo FT_Facturas_C (tiene Cve_Consignatario)
    try:
        rows = query(f"""
            SELECT con.CP,
                   COUNT(DISTINCT f.Cve_Folio)               AS pedidos,
                   CAST(SUM(ISNULL(f.Importe_Total, 0)) AS bigint) AS ventas
            FROM FT_Facturas_C f
            INNER JOIN CM_Consignatarios con
              ON con.Cve_Consignatario = f.Cve_Consignatario
             AND con.Cve_Cliente = CAST(f.Cve_Cliente AS int)
            WHERE f.Status = 'AC'
              AND f.Cve_Movimiento IN ('FM','FP')
              AND f.Cve_Sucursal <> 99
              AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
              AND YEAR(f.Fecha_Documento) = {_anio}
              AND MONTH(f.Fecha_Documento) = {_mes}
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
            if 14.0 <= lat <= 33.0 and -119.0 <= lng <= -86.0:
                puntos.append({
                    "cp":      cp,
                    "lat":     lat,
                    "lng":     lng,
                    "ventas":  int(r.get("ventas") or 0),
                    "pedidos": int(r.get("pedidos") or 0),
                })

    pendientes = len(todos_faltantes)

    # Guardar en cache SQLite + memoria
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

    label = f"{MESES_ES[_mes]} {_anio}"

    # 1. Comparativo por sucursal (ventas + piezas) — UNION ALL remisiones + facturas
    try:
        comp_rows = query(f"""
            WITH {_detail_union(_anio, _mes)}
            SELECT s.Cve_Sucursal,
                   s.Nombre                                              AS sucursal,
                   CAST(SUM(ISNULL(det.Importe_Neto, 0)) AS bigint)     AS ventas,
                   CAST(SUM(ISNULL(det.Cantidad, 0))     AS bigint)     AS piezas,
                   COUNT(DISTINCT det.Cve_Folio)                         AS pedidos
            FROM GN_Sucursales s
            LEFT JOIN det
              ON det.Cve_Sucursal = s.Cve_Sucursal
            WHERE s.Cve_Sucursal <> 99
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

    # 2. Top productos por sucursal — UNION ALL remisiones + facturas detail
    try:
        prod_rows = query(f"""
            WITH {_detail_union(_anio, _mes)}
            SELECT det.Cve_Sucursal,
                   MIN(prod.Descripcion)                                    AS producto,
                   cb.barcode_canon,
                   CAST(SUM(ISNULL(det.Importe_Neto, 0)) AS bigint)        AS ventas,
                   CAST(SUM(ISNULL(det.Cantidad, 0))     AS bigint)        AS piezas
            FROM det
            INNER JOIN (
                SELECT Cve_Producto, MIN(Codigo_Barras) AS barcode_canon
                FROM IM_Codigos_Barra GROUP BY Cve_Producto
            ) cb ON cb.Cve_Producto = det.Cve_Producto
            INNER JOIN IM_Productos_Gral prod ON prod.Cve_Producto = det.Cve_Producto
            WHERE prod.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY det.Cve_Sucursal, cb.barcode_canon
            ORDER BY det.Cve_Sucursal, ventas DESC
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

    # 3. Mapa: cache pre-computado por cron → fallback a query en vivo
    #    Solo FT_Facturas_C (tiene Cve_Consignatario para CP lookup)
    puntos_mapa = []
    _init_mapa_tables()
    cache_key_zonas = f"zonas_{_anio:04d}-{_mes:02d}"
    cached_zonas = fetch_one(
        "SELECT puntos FROM mapa_resultado_cache WHERE key=?", (cache_key_zonas,)
    )
    if cached_zonas:
        puntos_mapa = json.loads(cached_zonas["puntos"] or "[]")
    if not puntos_mapa:
        # Fallback: query en vivo — solo facturas (tienen Cve_Consignatario)
        try:
            mapa_rows = query(f"""
                SELECT con.CP, f.Cve_Sucursal,
                       CAST(SUM(ISNULL(f.Importe_Total, 0)) AS bigint) AS ventas,
                       COUNT(DISTINCT f.Cve_Folio)                     AS pedidos
                FROM FT_Facturas_C f
                INNER JOIN CM_Consignatarios con
                  ON con.Cve_Consignatario = f.Cve_Consignatario
                 AND con.Cve_Cliente = CAST(f.Cve_Cliente AS int)
                WHERE f.Status = 'AC'
                  AND f.Cve_Movimiento IN ('FM','FP')
                  AND f.Cve_Sucursal <> 99
                  AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
                  AND YEAR(f.Fecha_Documento) = {_anio}
                  AND MONTH(f.Fecha_Documento) = {_mes}
                GROUP BY con.CP, f.Cve_Sucursal
                ORDER BY ventas DESC
            """)
        except Exception:
            mapa_rows = []

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

        if cp_data:
            cps = list(cp_data.keys())
            cached_coords = fetch_all(
                f"SELECT cp, lat, lng FROM cp_coords WHERE cp IN ({','.join(['?']*len(cps))})",
                cps,
            )
            coords = {r["cp"]: (r["lat"], r["lng"]) for r in cached_coords}
            for cp, data in cp_data.items():
                if cp in coords:
                    lat, lng = coords[cp]
                    # Descartar geocodificaciones fuera de México
                    if 14.0 <= lat <= 33.0 and -119.0 <= lng <= -86.0:
                        puntos_mapa.append({**data, "lat": lat, "lng": lng})

    return JSONResponse({
        "anio": _anio, "mes": _mes, "label": label,
        "sucursales":    sucursales,
        "top_productos": {str(k): v for k, v in top_por_suc.items()},
        "mapa_puntos":   puntos_mapa,
    })
