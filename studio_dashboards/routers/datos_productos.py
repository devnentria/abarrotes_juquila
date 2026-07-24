# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_productos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Sub-router de datos: Dashboard de Productos y Predicción de demanda.

Fuentes combinadas (UNION ALL):
  - Autoservicio: FT_Remisiones_D + FT_Remisiones_C (Status='AC', Cve_Movimiento='VTA')
  - Mayoreo:      FT_Facturas_D  + FT_Facturas_C  (Status='AC', Cve_Movimiento IN ('FM','FP'))

Endpoints:
  GET  /productos            → Dashboard Productos: top + lista para selector
  GET  /productos/prediccion → Predicción de demanda por producto
"""
from collections import defaultdict
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from shared.database import query, query_acu, hoy

from .datos_helpers import _proyectar, _holt_winters_forecast, MESES_ES

router = APIRouter()

MESES_ES_P = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
              "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


# ── Helpers: SQL fragments for the combined detail source ────────────────────

def _union_detalle(extra_where: str = "", extra_cols: str = "") -> str:
    """
    Returns a UNION ALL subquery combining Remisiones (autoservicio)
    and Facturas (mayoreo) at the detail level.

    extra_where  — additional WHERE conditions (applied inside each branch).
                   Use 'c.' for header alias, 'd.' for detail alias.
    extra_cols   — additional SELECT columns (e.g. ', c.Cve_Sucursal').
    """
    return f"""(
        SELECT d.Cve_Producto, d.Cantidad, d.Precio,
               d.Importe_Neto, c.Fecha_Documento, c.Cve_Sucursal
               {extra_cols}
        FROM FT_Remisiones_D d
        INNER JOIN FT_Remisiones_C c
            ON  d.Cve_Folio      = c.Cve_Folio
            AND d.Cve_Sucursal   = c.Cve_Sucursal
            AND d.Cve_Movimiento = c.Cve_Movimiento
        WHERE c.Status = 'AC'
          AND c.Cve_Movimiento = 'VTA'
          AND c.Cve_Sucursal <> 99
          {extra_where}

        UNION ALL

        SELECT d.Cve_Producto, d.Cantidad, d.Precio,
               d.Importe_Neto, c.Fecha_Documento, c.Cve_Sucursal
               {extra_cols}
        FROM FT_Facturas_D d
        INNER JOIN FT_Facturas_C c
            ON  d.Cve_Folio      = c.Cve_Folio
            AND d.Cve_Sucursal   = c.Cve_Sucursal
            AND d.Cve_Movimiento = c.Cve_Movimiento
        WHERE c.Status = 'AC'
          AND c.Cve_Movimiento IN ('FM','FP')
          AND c.Cve_Sucursal <> 99
          {extra_where}
    )"""


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

    # ── 1. Top 20 productos del período (ACUMULADOS) ───────────────────────
    _filtro_dia = f"AND DAY(Fecha) <= {_dia_corte}" if _dia_corte else ""
    try:
        top_rows = query_acu(f"""
            SELECT TOP 20
                Cve_Producto,
                Descripcion                AS descripcion,
                SUM(VentaUnidades)         AS piezas,
                SUM(VentaNeta)             AS importe
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE Año = {_anio} AND Mes = {_mes}
            GROUP BY Cve_Producto, Descripcion
            ORDER BY SUM(VentaNeta) DESC
        """)
    except Exception as _e:
        raise HTTPException(500, f"productos top_rows error: {_e}")

    # Importe del mes anterior (para variación)
    _filtro_dia_ant = f"AND DAY(Fecha) <= {_dia_corte}" if _dia_corte else ""
    cve_list = ",".join(f"'{r['Cve_Producto']}'" for r in top_rows) or "'0'"
    try:
        ant_rows = query_acu(f"""
            SELECT Cve_Producto,
                   SUM(VentaNeta) AS importe_ant
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE Año = {_anio_ant} AND Mes = {_mes_ant}
              AND Cve_Producto IN ({cve_list})
              {_filtro_dia_ant}
            GROUP BY Cve_Producto
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

    # ── 2. Lista de productos para el selector de predicción (ACUMULADOS) ──
    h = f"CAST({hoy()} AS DATE)"
    try:
        lista_rows = query_acu(f"""
            SELECT Cve_Producto,
                   Descripcion AS descripcion
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) >= DATEADD(MONTH, -6, {h})
            GROUP BY Cve_Producto, Descripcion
            ORDER BY Descripcion
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

    try:
        hist_rows = query_acu(f"""
            SELECT
                Año                AS anio,
                Mes                AS mes,
                Cve_Sucursal,
                Nombre             AS sucursal,
                SUM(VentaUnidades) AS piezas
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE Cve_Producto = ?
              AND Fecha >= DATEADD(YEAR, -3, GETDATE())
            GROUP BY Año, Mes, Cve_Sucursal, Nombre
            ORDER BY Año, Mes
        """, (str(cve_producto),))
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

    MESES_ES_C = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
                  "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

    mes_actual = (hoy_d.year, hoy_d.month)

    # Último mes completo (mes anterior al actual)
    if hoy_d.month > 1:
        ultimo_completo = (hoy_d.year, hoy_d.month - 1)
    else:
        ultimo_completo = (hoy_d.year - 1, 12)

    # Rellenar meses sin ventas con 0 para que la serie sea continua.
    # Esto permite que Holt-Winters detecte la estacionalidad y que
    # la proyección arranque siempre desde hoy, no desde el último mes con ventas.
    raw_keys = sorted(mes_totales.keys())
    if raw_keys:
        y, m = raw_keys[0]
        ey, em = ultimo_completo
        while (y, m) <= (ey, em):
            mes_totales.setdefault((y, m), 0.0)
            m += 1
            if m > 12:
                m = 1
                y += 1

    sorted_keys = sorted(mes_totales.keys())
    # Últimos 18 meses completos para el modelo (más historia = mejor estacionalidad)
    trend_keys = [k for k in sorted_keys if k != mes_actual][-18:]

    # Último mes con ventas reales (para el KPI "Último mes")
    ultimo_mes_key = next(
        (k for k in reversed(trend_keys) if mes_totales[k] > 0), None
    ) or (trend_keys[-1] if trend_keys else None)
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
        """Proyecta 6 meses usando Holt-Winters.
        La proyección siempre arranca desde el mes siguiente al último mes completo
        (mes anterior al actual), independientemente de cuándo fue la última venta.
        """
        if not keys:
            return []
        vals_trend = [totals[k] for k in keys]
        forecast   = _holt_winters_forecast(vals_trend, pasos=6)
        result = []
        # Arrancar desde el mes siguiente al último mes completo
        py, pm = ultimo_completo
        for i, val_p in enumerate(forecast):
            pm += 1
            if pm > 12:
                pm = 1
                py += 1
            result.append({
                "mes_label": f"{MESES_ES_C[pm]} {py}",
                "piezas":    round(val_p, 1),
            })
        return result

    # Global YoY factor
    yoy_factor = _calc_yoy_factor(trend_keys, mes_totales)

    # Determinar calidad del modelo para mostrar aviso en el frontend
    meses_con_ventas = sum(1 for k in trend_keys if mes_totales.get(k, 0) > 0)
    if meses_con_ventas >= 24:
        modelo_aviso = None  # Modelo estacional completo — sin aviso
    elif meses_con_ventas >= 12:
        modelo_aviso = "Proyección basada en tendencia · sin suficientes datos estacionales"
    elif meses_con_ventas >= 6:
        modelo_aviso = "Datos limitados · proyección aproximada"
    else:
        modelo_aviso = "Datos insuficientes para proyectar"

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

    # Monthly history — excluir mes actual (parcial). Incluye meses con 0 ventas
    # para que la gráfica muestre la caída completa sin saltos de tiempo.
    detalle = [
        {"mes_label": f"{MESES_ES_C[k[1]]} {k[0]}", "piezas": round(mes_totales[k], 1)}
        for k in sorted_keys if k != mes_actual and k <= ultimo_completo
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
        "modelo_aviso":      modelo_aviso,
    })
