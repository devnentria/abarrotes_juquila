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
from shared.database import query, query_acu, hoy

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


# ── Helper: filtros de fecha para ACUMULADOS ─────────────────────────────────

def _acu_filtros(modo: str, fi: str = None, ff: str = None):
    """
    Devuelve (filtro_actual, filtro_anterior) para queries a ACUMULADOS.
    La columna de fecha es 'Fecha' (datetime).
    """
    h = f"CAST({hoy()} AS DATE)"
    if modo == "custom" and fi and ff:
        dias = f"DATEDIFF(DAY,'{fi}','{ff}')"
        actual   = f"CAST(Fecha AS DATE) >= '{fi}' AND CAST(Fecha AS DATE) <= '{ff}'"
        anterior = (f"CAST(Fecha AS DATE) >= DATEADD(DAY,-({dias}+1),'{fi}') "
                    f"AND CAST(Fecha AS DATE) < '{fi}'")
    elif modo == "hoy":
        actual   = f"CAST(Fecha AS DATE) = {h}"
        anterior = f"CAST(Fecha AS DATE) = DATEADD(DAY,-1,{h})"
    elif modo == "15d":
        actual   = f"CAST(Fecha AS DATE) >= DATEADD(DAY,-14,{h})"
        anterior = (f"CAST(Fecha AS DATE) >= DATEADD(DAY,-29,{h}) "
                    f"AND CAST(Fecha AS DATE) < DATEADD(DAY,-14,{h})")
    elif modo == "mes":
        actual   = (f"YEAR(Fecha)=YEAR({hoy()}) AND MONTH(Fecha)=MONTH({hoy()}) "
                    f"AND CAST(Fecha AS DATE) <= {h}")
        anterior = (f"YEAR(Fecha)=YEAR(DATEADD(MONTH,-1,{hoy()})) "
                    f"AND MONTH(Fecha)=MONTH(DATEADD(MONTH,-1,{hoy()})) "
                    f"AND DAY(Fecha)<=DAY({hoy()})")
    else:  # 30d
        actual   = f"CAST(Fecha AS DATE) >= DATEADD(DAY,-29,{h})"
        anterior = (f"CAST(Fecha AS DATE) >= DATEADD(DAY,-59,{h}) "
                    f"AND CAST(Fecha AS DATE) < DATEADD(DAY,-29,{h})")
    return actual, anterior


# ── Ventas por sucursal (ACUMULADOS) ─────────────────────────────────────────

@router.get("/ventas")
def ventas_sucursales(modo: str = Query("30d"), fi: str = Query(None), ff: str = Query(None)):
    """
    Ventas por sucursal para los dashboards del Studio.
    Fuente: ACUMULADOS.ACU_VTA_DEV_DIARIA_FAM_PROD (pre-agregado por día).
    """
    if fi and ff: modo = "custom"
    fa, fb = _acu_filtros(modo, fi, ff)

    rows = query_acu(f"""
        SELECT
            Cve_Sucursal                                              AS cve_sucursal,
            Nombre                                                    AS sucursal,
            ISNULL(SUM(CASE WHEN {fa} THEN VentaNeta END), 0)        AS ventas_actual,
            ISNULL(SUM(CASE WHEN {fb} THEN VentaNeta END), 0)        AS ventas_anterior,
            ISNULL(SUM(CASE WHEN {fa} THEN VentaUnidades END), 0)    AS facturas
        FROM ACU_VTA_DEV_DIARIA_FAM_PROD
        GROUP BY Cve_Sucursal, Nombre
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
    Fuente: ACUMULADOS (ventas) + ERP (órdenes de compra).
    """
    if fi and ff: modo = "custom"
    fa, _ = _acu_filtros(modo, fi, ff)

    ventas_row = query_acu(f"""
        SELECT ISNULL(SUM(VentaNeta), 0) AS ventas_total,
               ISNULL(SUM(VentaUnidades), 0) AS unidades_total,
               COUNT(DISTINCT Cve_Sucursal) AS sucursales_activas,
               COUNT(DISTINCT Cve_Producto) AS productos_vendidos
        FROM ACU_VTA_DEV_DIARIA_FAM_PROD
        WHERE {fa}
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

    top_suc_row = query_acu(f"""
        SELECT TOP 1 Nombre AS nombre, SUM(VentaNeta) AS total
        FROM ACU_VTA_DEV_DIARIA_FAM_PROD
        WHERE {fa}
        GROUP BY Cve_Sucursal, Nombre ORDER BY total DESC
    """)

    v = ventas_row[0] if ventas_row else {}
    ventas_total   = float(v.get("ventas_total") or 0)
    unidades_total = int(v.get("unidades_total") or 0)
    top_sucursal   = (top_suc_row[0].get("nombre") or "—") if top_suc_row else "—"

    return JSONResponse({
        "ventas_total":       ventas_total,
        "facturas_total":     unidades_total,
        "pedidos_activos":    int((pedidos_row[0] or {}).get("pedidos_activos") or 0),
        "sucursales_activas": int(v.get("sucursales_activas") or 0),
        "ticket_promedio":    0,
        "top_sucursal":       top_sucursal,
        "modo":               modo,
    })


# ── Ventas del día (Remisiones + Facturas) ───────────────────────────────────

@router.get("/ventas-hoy")
def ventas_hoy():
    """
    Ventas del día actual por sucursal.
    Fuente: ACUMULADOS.ACU_VTA_DEV_DIARIA_FAM_PROD.
    """
    h = f"CAST({hoy()} AS DATE)"
    rows = query_acu(f"""
        SELECT
            Cve_Sucursal                           AS cve_sucursal,
            Nombre                                 AS sucursal,
            ISNULL(SUM(VentaUnidades), 0)          AS pedidos_hoy,
            ISNULL(SUM(VentaNeta), 0)              AS ventas_hoy
        FROM ACU_VTA_DEV_DIARIA_FAM_PROD
        WHERE CAST(Fecha AS DATE) = {h}
        GROUP BY Cve_Sucursal, Nombre
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
        fa, fb = _acu_filtros(modo, fi, ff)
        rows = query_acu(f"""
            SELECT Nombre AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN VentaNeta END),0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN VentaNeta END),0) AS anterior
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            GROUP BY Cve_Sucursal, Nombre ORDER BY actual DESC
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
        h = f"CAST({hoy()} AS DATE)"
        rows = query_acu(f"""
            SELECT Nombre AS label,
                   ISNULL(SUM(VentaUnidades),0) AS pedidos,
                   ISNULL(SUM(VentaNeta),0) AS valor
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) = {h}
            GROUP BY Cve_Sucursal, Nombre ORDER BY valor DESC
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
            h = f"CAST({hoy()} AS DATE)"
            rows = query_acu(f"""
                SELECT Año AS anio, Mes AS mes, Nombre AS sucursal,
                       SUM(VentaNeta) AS valor
                FROM ACU_VTA_DEV_DIARIA_FAM_PROD
                WHERE CAST(Fecha AS DATE) >= DATEADD(MONTH,-5,{h})
                GROUP BY Año, Mes, Nombre
                ORDER BY Año, Mes, Nombre
            """)
            for r in rows:
                m = int(r.get("mes") or 0)
                r["mes_nombre"] = _MESES[m] if 0 < m <= 12 else "?"
                r["valor"] = round(float(r.get("valor") or 0), 2)
        except Exception:
            rows = []
        return JSONResponse({"tipo": tipo, "titulo": "Ventas por sucursal — últimos 6 meses", "datos": rows})

    # ── Ventas por día (últimos 30 días) ─────────────────────────────────────
    elif tipo == "ventas_diario":
        h = f"CAST({hoy()} AS DATE)"
        rows = query_acu(f"""
            SELECT CAST(Fecha AS DATE) AS fecha,
                   SUM(VentaNeta) AS valor,
                   SUM(VentaUnidades) AS pedidos
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) >= DATEADD(DAY,-29,{h})
            GROUP BY CAST(Fecha AS DATE)
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
            h = f"CAST({hoy()} AS DATE)"
            rows = query_acu(f"""
                SELECT Año AS anio, Mes AS mes,
                       SUM(VentaNeta) AS valor,
                       SUM(VentaUnidades) AS pedidos
                FROM ACU_VTA_DEV_DIARIA_FAM_PROD
                WHERE CAST(Fecha AS DATE) >= DATEADD(MONTH,-23,{h})
                GROUP BY Año, Mes
                ORDER BY Año, Mes
            """)
            for r in rows:
                m = int(r.get("mes") or 0)
                r["mes_nombre"] = _MESES[m] if 0 < m <= 12 else "?"
                r["valor"] = round(float(r.get("valor") or 0), 2)
                r["pedidos"] = int(r.get("pedidos") or 0)
            rows = [r for r in rows if r["valor"] > 0]
        except Exception:
            rows = []
        total = sum(float(r.get("valor") or 0) for r in rows)
        return JSONResponse({"tipo": tipo, "titulo": "Tendencia de ventas — 12 meses + proyección",
                             "total": total, "datos": rows})

    # ── Top productos ─────────────────────────────────────────────────────────
    elif tipo == "top_productos":
        fa, _ = _acu_filtros(modo, fi, ff)
        rows = query_acu(f"""
            SELECT TOP 10
                Descripcion              AS label,
                SUM(VentaNeta)           AS valor,
                SUM(VentaUnidades)       AS unidades
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE {fa}
            GROUP BY Descripcion
            ORDER BY SUM(VentaNeta) DESC
        """)
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
