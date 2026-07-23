# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_ventas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.6.0
# ============================================================
"""
Sub-router de datos: Ventas, Pedidos, KPIs, Ventas-Hoy, Plantillas.

Fuentes de datos:
  - Autoservicio (retail): FT_Remisiones_C (Importe_Neto) + FT_Remisiones_D (detalle)
    Filtros: Cve_Movimiento='VTA' AND Status='AC'
  - Mayoreo (wholesale): FT_Facturas_C (Importe_Total) + FT_Facturas_D (detalle)
    Filtros: Cve_Movimiento IN ('FM','FP') AND Status='AC'

Endpoints:
  GET  /ventas               → Ventas por sucursal (30d o mes actual)
  GET  /pedidos              → Compras activas por sucursal (MT_Ordenes_C)
  GET  /kpis                 → Totales globales para tarjetas KPI
  GET  /ventas-hoy           → Ventas del día
  GET  /plantilla/{tipo}     → Datos de una plantilla predefinida
"""
from collections import defaultdict
from datetime import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from shared.auth import get_current_user
from shared.database import query, hoy

from .datos_helpers import _filtros_periodo

router = APIRouter()


# ── Helper: subquery UNION ALL de ventas (Remisiones + Facturas) ─────────────

def _ventas_union(date_filter: str, *, campo_fecha: str = "Fecha_Documento",
                  extra_cols: str = "", extra_cols_fac: str = "") -> str:
    """
    Genera el subquery UNION ALL con filtros de fecha DENTRO de cada rama
    para evitar escanear 30M+ filas.

    Args:
        date_filter: condición SQL sobre el campo de fecha (ya formateada).
                     Debe referenciar el alias usado en campo_fecha.
        campo_fecha: nombre de la columna de fecha (default Fecha_Documento).
        extra_cols:  columnas adicionales para Remisiones (ej. ', Cve_Vendedor').
        extra_cols_fac: columnas adicionales para Facturas (si difiere de extra_cols;
                        si vacío, usa extra_cols).
    """
    if not extra_cols_fac:
        extra_cols_fac = extra_cols
    return f"""(
        SELECT Cve_Sucursal, Cve_Folio, {campo_fecha} AS Fecha_Documento,
               Importe_Neto AS Monto, 'AUTO' AS Canal{extra_cols}
        FROM FT_Remisiones_C
        WHERE Status='AC' AND Cve_Movimiento='VTA'
          AND {date_filter}
        UNION ALL
        SELECT Cve_Sucursal, Cve_Folio, {campo_fecha} AS Fecha_Documento,
               Importe_Total AS Monto, 'MAYO' AS Canal{extra_cols_fac}
        FROM FT_Facturas_C
        WHERE Status='AC' AND Cve_Movimiento IN ('FM','FP')
          AND {date_filter}
    )"""


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

    # Construir filtro de fecha amplio para las ramas internas del UNION
    # (debe cubrir tanto periodo actual como anterior)
    h = f"CAST({hoy()} AS DATE)"
    if modo == "custom" and fi and ff:
        # El filtro_anterior calcula el rango previo; abarcar todo desde el inicio del anterior
        inner_date = (f"CAST(Fecha_Documento AS DATE) >= "
                      f"DATEADD(DAY,-(DATEDIFF(DAY,'{fi}','{ff}')+1),'{fi}') "
                      f"AND CAST(Fecha_Documento AS DATE) <= '{ff}'")
    elif modo == "hoy":
        inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-1,{h})"
    elif modo == "15d":
        inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-15,DATEADD(MONTH,-1,{h}))"
    elif modo == "mes":
        inner_date = (f"CAST(Fecha_Documento AS DATE) >= "
                      f"DATEFROMPARTS(YEAR(DATEADD(MONTH,-1,{hoy()})),MONTH(DATEADD(MONTH,-1,{hoy()})),1)")
    else:  # 30d
        inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{h})"

    union = _ventas_union(inner_date)

    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                     AS cve_sucursal,
            s.Nombre                                                           AS sucursal,
            ISNULL(SUM(CASE WHEN {filtro_actual}   THEN t.Monto END), 0)      AS ventas_actual,
            ISNULL(SUM(CASE WHEN {filtro_anterior} THEN t.Monto END), 0)      AS ventas_anterior,
            COUNT(DISTINCT CASE WHEN {filtro_actual} THEN
                CAST(t.Canal AS VARCHAR(4)) + CAST(t.Cve_Folio AS VARCHAR(20)) END) AS facturas
        FROM GN_Sucursales s
        LEFT JOIN {union} t ON t.Cve_Sucursal = s.Cve_Sucursal
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


# ── Compras activas por sucursal (MT_Ordenes_C) ─────────────────────────────

@router.get("/pedidos")
def pedidos_sucursales():
    """
    Compras activas (órdenes de compra) por sucursal para el Studio.

    Fuente: MT_Ordenes_C
    Status: TR=transferida, RP=recibida parcial, AU=autorizada, CN=cancelada.

    Returns:
        JSON con lista de sucursales, órdenes activas y completadas.
    """
    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                    AS cve_sucursal,
            s.Nombre                                                          AS sucursal,
            COUNT(CASE WHEN o.Status IN ('AU','RP')
                        AND o.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                 AS activos,
            COUNT(CASE WHEN o.Status = 'TR'
                        AND o.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                 AS completados_30d
        FROM GN_Sucursales s
        LEFT JOIN MT_Ordenes_C o ON o.Cve_Sucursal = s.Cve_Sucursal
            AND o.Status <> 'CN'
            AND o.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY activos DESC
    """)
    return JSONResponse({"sucursales": rows})


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
    filtro, _, _ = _filtros_periodo(modo, "Fecha_Documento", fi, ff)

    # Filtro de fecha para las ramas internas del UNION
    inner_date = filtro.replace("CAST(Fecha_Documento AS DATE)", "CAST(Fecha_Documento AS DATE)")

    ventas_row = query(f"""
        SELECT COUNT(*) AS facturas_total, ISNULL(SUM(Monto), 0) AS ventas_total
        FROM (
            SELECT Importe_Neto AS Monto, Fecha_Documento
            FROM FT_Remisiones_C
            WHERE Status='AC' AND Cve_Movimiento='VTA'
              AND Cve_Sucursal <> 99
              AND {filtro}
            UNION ALL
            SELECT Importe_Total AS Monto, Fecha_Documento
            FROM FT_Facturas_C
            WHERE Status='AC' AND Cve_Movimiento IN ('FM','FP')
              AND Cve_Sucursal <> 99
              AND {filtro}
        ) AS t
    """)

    try:
        pedidos_row = query(f"""
            SELECT COUNT(DISTINCT Cve_Folio) AS pedidos_activos
            FROM MT_Ordenes_C
            WHERE Status IN ('AU','RP')
              AND Cve_Sucursal <> 99
        """)
    except Exception:
        pedidos_row = [{"pedidos_activos": 0}]

    sucursales_row = query(f"""
        SELECT COUNT(DISTINCT Cve_Sucursal) AS total FROM (
            SELECT Cve_Sucursal, Fecha_Documento
            FROM FT_Remisiones_C
            WHERE Status='AC' AND Cve_Movimiento='VTA'
              AND Cve_Sucursal <> 99
              AND {filtro}
            UNION ALL
            SELECT Cve_Sucursal, Fecha_Documento
            FROM FT_Facturas_C
            WHERE Status='AC' AND Cve_Movimiento IN ('FM','FP')
              AND Cve_Sucursal <> 99
              AND {filtro}
        ) t
    """)

    v = ventas_row[0] if ventas_row else {}
    ventas_total   = float(v.get("ventas_total") or 0)
    facturas_total = int(v.get("facturas_total") or 0)
    ticket_promedio = round(ventas_total / facturas_total, 2) if facturas_total > 0 else 0

    # Top sucursal del período
    top_suc_row = query(f"""
        SELECT TOP 1 s.Nombre AS nombre, SUM(t.Monto) AS total
        FROM (
            SELECT Cve_Sucursal, Importe_Neto AS Monto, Fecha_Documento
            FROM FT_Remisiones_C
            WHERE Status='AC' AND Cve_Movimiento='VTA'
              AND Cve_Sucursal <> 99
              AND {filtro}
            UNION ALL
            SELECT Cve_Sucursal, Importe_Total AS Monto, Fecha_Documento
            FROM FT_Facturas_C
            WHERE Status='AC' AND Cve_Movimiento IN ('FM','FP')
              AND Cve_Sucursal <> 99
              AND {filtro}
        ) t
        INNER JOIN GN_Sucursales s ON s.Cve_Sucursal=t.Cve_Sucursal
        GROUP BY t.Cve_Sucursal, s.Nombre ORDER BY total DESC
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


# ── Ventas del día (Remisiones + Facturas) ───────────────────────────────────

@router.get("/ventas-hoy")
def ventas_hoy():
    """
    Ventas del día actual por sucursal.

    Fuente: FT_Remisiones_C (Importe_Neto) + FT_Facturas_C (Importe_Total).

    Returns:
        JSON con lista de sucursales y su total de ventas de hoy, más el total global.
    """
    hoy_filter = f"CAST(Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)"
    union = _ventas_union(hoy_filter)

    rows = query(f"""
        SELECT
            s.Cve_Sucursal                         AS cve_sucursal,
            s.Nombre                               AS sucursal,
            COUNT(*)                               AS pedidos_hoy,
            ISNULL(SUM(t.Monto), 0)                AS ventas_hoy
        FROM GN_Sucursales s
        LEFT JOIN {union} t ON t.Cve_Sucursal = s.Cve_Sucursal
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
      pedidos_activos   → Dona: compras activas por sucursal
      ventas_hoy        → Tabla + KPI: ventas del día
      top_vendedores    → Barras horizontales: top vendedores del período
      comparativo_meses → Línea: ventas por mes (últimos 6 meses)
    """
    hoy_fecha = f"CAST({hoy()} AS DATE)"
    if fi and ff: modo = "custom"

    if tipo == "ventas_sucursal":
        fa, fb, _ = _filtros_periodo(modo, "t.Fecha_Documento", fi, ff)

        # Calcular inner_date que cubra ambos períodos
        h = f"CAST({hoy()} AS DATE)"
        if modo == "custom" and fi and ff:
            inner_date = (f"CAST(Fecha_Documento AS DATE) >= "
                          f"DATEADD(DAY,-(DATEDIFF(DAY,'{fi}','{ff}')+1),'{fi}') "
                          f"AND CAST(Fecha_Documento AS DATE) <= '{ff}'")
        elif modo == "hoy":
            inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-1,{h})"
        elif modo == "15d":
            inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-15,DATEADD(MONTH,-1,{h}))"
        elif modo == "mes":
            inner_date = (f"CAST(Fecha_Documento AS DATE) >= "
                          f"DATEFROMPARTS(YEAR(DATEADD(MONTH,-1,{hoy()})),MONTH(DATEADD(MONTH,-1,{hoy()})),1)")
        else:  # 30d
            inner_date = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{h})"

        union = _ventas_union(inner_date)

        rows = query(f"""
            SELECT s.Nombre AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN t.Monto END),0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN t.Monto END),0) AS anterior
            FROM GN_Sucursales s
            LEFT JOIN {union} t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY actual DESC
        """)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Ventas por sucursal ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "series": ["Período actual", "Período anterior"],
                             "datos": rows})

    elif tipo == "pedidos_activos":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(CASE WHEN o.Status IN ('AU','RP') THEN 1 END) AS valor,
                   COUNT(CASE WHEN o.Status = 'TR' THEN 1 END) AS transferidas
            FROM GN_Sucursales s
            LEFT JOIN MT_Ordenes_C o ON o.Cve_Sucursal=s.Cve_Sucursal
                AND o.Status <> 'CN'
                AND o.Fecha_Documento >= DATEADD(DAY,-90,{hoy_fecha})
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre HAVING COUNT(CASE WHEN o.Status IN ('AU','RP') THEN 1 END)>0
            ORDER BY valor DESC
        """)
        total = sum(r.get("valor") or 0 for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Compras activas por sucursal",
                             "total": total, "datos": rows})

    elif tipo == "ventas_hoy":
        hoy_filter = f"CAST(Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)"
        union = _ventas_union(hoy_filter)

        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(*) AS pedidos,
                   ISNULL(SUM(t.Monto),0) AS valor
            FROM GN_Sucursales s
            LEFT JOIN {union} t ON t.Cve_Sucursal=s.Cve_Sucursal
            WHERE s.Cve_Sucursal<>99
            GROUP BY s.Cve_Sucursal, s.Nombre ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Ventas del día",
                             "total": total, "datos": rows})

    elif tipo == "top_vendedores":
        # Vendedores solo existen en FT_Facturas_C (mayoreo)
        filtro, _, label = _filtros_periodo(modo, "c.Fecha_Documento")
        rows = query(f"""
            SELECT TOP 10 ISNULL(v.Nombre, c.Cve_Vendedor) AS label,
                   ISNULL(SUM(c.Importe_Total),0) AS valor,
                   COUNT(DISTINCT c.Cve_Folio) AS pedidos
            FROM FT_Facturas_C c
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor=c.Cve_Vendedor
            WHERE c.Status='AC' AND c.Cve_Movimiento IN ('FM','FP')
              AND c.Cve_Sucursal <> 99
              AND {filtro}
            GROUP BY c.Cve_Vendedor, v.Nombre ORDER BY valor DESC
        """)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Top vendedores ({label})",
                             "datos": rows})

    elif tipo == "comparativo_meses":
        try:
            _MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                      "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
            date_filter = f"CAST(Fecha_Documento AS DATE) >= DATEADD(MONTH,-5,{hoy_fecha})"
            union = _ventas_union(date_filter)

            daily = query(f"""
                SELECT fecha, sucursal, SUM(Monto) AS valor FROM (
                    SELECT CAST(t.Fecha_Documento AS DATE) AS fecha,
                           s.Nombre AS sucursal,
                           t.Monto
                    FROM {union} t
                    INNER JOIN GN_Sucursales s ON s.Cve_Sucursal=t.Cve_Sucursal
                    WHERE t.Cve_Sucursal <> 99
                ) sub GROUP BY fecha, sucursal ORDER BY fecha
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
        date_filter = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-29,{hoy_fecha})"
        union = _ventas_union(date_filter)

        rows = query(f"""
            SELECT CAST(t.Fecha_Documento AS DATE) AS fecha,
                   SUM(t.Monto) AS valor,
                   COUNT(*) AS pedidos
            FROM {union} t
            WHERE t.Cve_Sucursal <> 99
            GROUP BY CAST(t.Fecha_Documento AS DATE)
            ORDER BY fecha
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Ventas diarias — últimos 30 días",
                             "total": total, "datos": rows})

    # ── Tendencia (hasta 24 meses — mejora con el tiempo) ────────────────────
    elif tipo == "tendencia_anual":
        try:
            _MESES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
                      "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
            date_filter = f"CAST(Fecha_Documento AS DATE) >= DATEADD(MONTH,-23,{hoy_fecha})"
            union = _ventas_union(date_filter)

            daily = query(f"""
                SELECT CAST(t.Fecha_Documento AS DATE) AS fecha,
                       SUM(t.Monto) AS valor,
                       COUNT(*) AS pedidos
                FROM {union} t
                WHERE t.Cve_Sucursal <> 99
                GROUP BY CAST(t.Fecha_Documento AS DATE)
                ORDER BY fecha
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
            filtro = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        elif modo == "15d":
            filtro = f"CAST(Fecha_Documento AS DATE) >= DATEADD(DAY,-15,{hoy_fecha})"
        elif modo == "hoy":
            filtro = f"CAST(Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)"
        elif modo == "custom" and fi and ff:
            filtro = f"CAST(Fecha_Documento AS DATE) >= '{fi}' AND CAST(Fecha_Documento AS DATE) <= '{ff}'"
        else:
            filtro = (f"YEAR(Fecha_Documento)=YEAR({hoy()}) "
                      f"AND MONTH(Fecha_Documento)=MONTH({hoy()})")
        try:
            # Product-level: need detail tables
            rows = query(f"""
                SELECT TOP 10
                    MIN(pg.Descripcion)      AS label,
                    SUM(d.Importe_Neto)       AS valor,
                    SUM(d.Cantidad)           AS unidades
                FROM (
                    SELECT Cve_Sucursal, Cve_Folio, Cve_Producto, Cantidad, Importe_Neto, Fecha_Documento
                    FROM FT_Remisiones_D rd
                    INNER JOIN FT_Remisiones_C rc
                        ON rc.Cve_Folio=rd.Cve_Folio AND rc.Cve_Sucursal=rd.Cve_Sucursal
                    WHERE rc.Status='AC' AND rc.Cve_Movimiento='VTA'
                      AND rc.Cve_Sucursal <> 99
                      AND {filtro.replace('Fecha_Documento','rc.Fecha_Documento')}
                    UNION ALL
                    SELECT fd.Cve_Sucursal, fd.Cve_Folio, fd.Cve_Producto, fd.Cantidad, fd.Importe_Neto, fc.Fecha_Documento
                    FROM FT_Facturas_D fd
                    INNER JOIN FT_Facturas_C fc
                        ON fc.Cve_Folio=fd.Cve_Folio AND fc.Cve_Sucursal=fd.Cve_Sucursal
                    WHERE fc.Status='AC' AND fc.Cve_Movimiento IN ('FM','FP')
                      AND fc.Cve_Sucursal <> 99
                      AND {filtro.replace('Fecha_Documento','fc.Fecha_Documento')}
                ) d
                INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = d.Cve_Producto
                GROUP BY d.Cve_Producto
                ORDER BY SUM(d.Importe_Neto) DESC
            """)
        except Exception as _e:
            raise HTTPException(500, f"top_productos SQL error: {_e}")
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Top productos ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "total": total, "datos": rows})

    # ── Clientes frecuentes (solo mayoreo — autoservicio es anónimo) ─────────
    elif tipo == "clientes_frecuentes":
        if modo == "30d":
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
        elif modo == "custom" and fi and ff:
            filtro = f"CAST(c.Fecha_Documento AS DATE) >= '{fi}' AND CAST(c.Fecha_Documento AS DATE) <= '{ff}'"
        else:
            filtro = (f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) "
                      f"AND MONTH(c.Fecha_Documento)=MONTH({hoy()})")
        rows = query(f"""
            SELECT TOP 15
                ISNULL(cl.Razon_Social, c.Cve_Cliente) AS label,
                SUM(c.Importe_Total) AS valor,
                COUNT(DISTINCT c.Cve_Folio) AS pedidos
            FROM FT_Facturas_C c
            LEFT JOIN CM_Clientes cl ON cl.Cve_Cliente=c.Cve_Cliente
            WHERE c.Status='AC' AND c.Cve_Movimiento IN ('FM','FP')
              AND c.Cve_Sucursal <> 99
              AND c.Cve_Cliente <> '/'
              AND {filtro}
            GROUP BY c.Cve_Cliente, cl.Razon_Social
            HAVING ISNULL(cl.Razon_Social, c.Cve_Cliente) NOT LIKE '%MOSTRADOR%'
               AND c.Cve_Cliente <> '20000'
            ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "modo": modo,
                             "titulo": f"Clientes frecuentes ({'últ. 30 días' if modo=='30d' else 'mes actual'})",
                             "total": total, "datos": rows})

    # ── Variación de vendedores (solo mayoreo — FT_Facturas_C) ───────────────
    elif tipo == "variacion_vendedores":
        if modo == "30d":
            fa = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-30,{hoy_fecha})"
            fb = (f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha}) "
                  f"AND CAST(c.Fecha_Documento AS DATE) < DATEADD(DAY,-30,{hoy_fecha})")
            inner_date = f"CAST(c.Fecha_Documento AS DATE) >= DATEADD(DAY,-60,{hoy_fecha})"
        else:
            fa = (f"YEAR(c.Fecha_Documento)=YEAR({hoy()}) "
                  f"AND MONTH(c.Fecha_Documento)=MONTH({hoy()})")
            fb = (f"YEAR(c.Fecha_Documento)=YEAR(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND MONTH(c.Fecha_Documento)=MONTH(DATEADD(MONTH,-1,{hoy()})) "
                  f"AND DAY(c.Fecha_Documento)<=DAY({hoy()})")
            inner_date = (f"CAST(c.Fecha_Documento AS DATE) >= "
                          f"DATEFROMPARTS(YEAR(DATEADD(MONTH,-1,{hoy()})),MONTH(DATEADD(MONTH,-1,{hoy()})),1)")
        rows = query(f"""
            SELECT TOP 10
                ISNULL(v.Nombre, c.Cve_Vendedor) AS label,
                ISNULL(SUM(CASE WHEN {fa} THEN c.Importe_Total END),0) AS actual,
                ISNULL(SUM(CASE WHEN {fb} THEN c.Importe_Total END),0) AS anterior
            FROM FT_Facturas_C c
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor=c.Cve_Vendedor
            WHERE c.Status='AC' AND c.Cve_Movimiento IN ('FM','FP')
              AND c.Cve_Sucursal <> 99
              AND {inner_date}
            GROUP BY c.Cve_Vendedor, v.Nombre
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
