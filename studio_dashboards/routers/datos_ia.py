# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Modulo   : studio_dashboards
# Archivo  : routers/datos_ia.py
# Autor    : Geovani Daniel Nolasco
# Version  : 3.0.0
# ============================================================
"""
Sub-router de datos: Generacion de dashboards con IA.

Endpoints:
  POST /generar -> Genera dashboard completo con IA (gpt-5-nano)

v4.0.0 — Ventas migradas a ACUMULADOS (ACU_VTA_DEV_DIARIA_FAM_PROD):
  - ventas_hoy, ventas_sucursal, comparativo_meses, ventas_diario,
    tendencia_anual, top_productos, ventas_producto, reporte_ventas
    → query_acu() sobre datos pre-agregados (rapido, sin UNION ALL)
  - top_vendedores, clientes_frecuentes, variacion_vendedores
    → query() sobre FT_Facturas_C (campos no disponibles en ACUMULADOS)
  - pedidos_activos → query() sobre MT_Ordenes_C
  - inventario → query() sobre ERP + ventas_30d desde ACUMULADOS
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from shared.auth import get_current_user
from shared.config import IA_RATIO_STUDIO
from shared.database import query, query_acu, hoy
from shared.database_local import execute, fetch_one

from .datos_helpers import (
    _filtros_periodo, _proyectar, _holt_winters_forecast,
    _SPECS_TIPO, _clasificar, _narrar,
    GenerarBody,
)

router = APIRouter()


# ── Helpers ACUMULADOS ──────────────────────────────────────────────────────

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


def _acu_label(modo: str) -> str:
    """Etiqueta legible del periodo."""
    return {"hoy": "hoy", "15d": "ult. 15 dias", "mes": "mes actual",
            "30d": "ult. 30 dias", "custom": "periodo"}.get(modo, "ult. 30 dias")


# ── Generar dashboard con IA ────────────────────────────────────────────────

@router.post("/generar")
def generar_dashboard(body: GenerarBody, usuario=Depends(get_current_user)):
    """
    Genera un dashboard completo usando gpt-5-nano.

    Flujo:
      1. Si se pasa 'tipo', lo usa directamente (sin clasificacion IA).
         Si solo viene 'pregunta', clasifica con gpt-5-nano.
      2. Obtiene los datos del ERP mediante la funcion correspondiente.
      3. Genera narrativa ejecutiva de 2-3 oraciones con gpt-5-nano.
      4. Descuenta IA_RATIO_STUDIO consultas del usuario.

    Returns:
        JSON con tipo, layout, titulo, modo, narrativa, datos.
    """
    # Verificar limite de IA
    u = fetch_one(
        "SELECT COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, limite_ia FROM usuarios WHERE id=?", (usuario["id"],)
    )
    if u and u["limite_ia"] > 0 and u["consultas_ia_r"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu limite de consultas de IA. Contacta a tu administrador.",
        )

    tipo     = body.tipo
    modo     = body.modo or "30d"
    fi       = body.fecha_inicio
    ff       = body.fecha_fin
    pregunta = body.pregunta or ""
    layout   = None
    titulo   = None
    clasificacion = {}
    producto = None

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
        narrativa = "Analisis generado con datos del ERP en tiempo real."

    # Paso 4: Descontar creditos
    # Dashboards: 3 consultas (multiples SQL + clasificacion + narracion)
    # Chat Studio sin dashboard: IA_RATIO_STUDIO = 1.5 (o4-mini razonamiento)
    from shared.database_local import verificar_mes_ia, periodo_ia_actual
    verificar_mes_ia(usuario["id"], periodo_ia_actual())
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
    Llama internamente a la funcion de datos correcta segun el tipo.
    fi, ff: fechas ISO 'YYYY-MM-DD' para modo='custom'

    Ventas se obtienen de ACUMULADOS.ACU_VTA_DEV_DIARIA_FAM_PROD (pre-agregado).
    Vendedores/clientes siguen en ERP (FT_Facturas_C).
    """
    hoy_fecha = f"CAST({hoy()} AS DATE)"

    # ── VENTAS HOY (ACUMULADOS) ─────────────────────────────────────────────
    if tipo == "ventas_hoy":
        h = f"CAST({hoy()} AS DATE)"
        rows = query_acu(f"""
            SELECT Nombre AS label,
                   ISNULL(SUM(VentaUnidades), 0) AS pedidos,
                   ISNULL(SUM(VentaNeta), 0) AS valor
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) = {h}
            GROUP BY Cve_Sucursal, Nombre ORDER BY valor DESC
        """)
        ayer_row = query_acu(f"""
            SELECT ISNULL(SUM(VentaNeta), 0) AS total_ayer,
                   ISNULL(SUM(VentaUnidades), 0) AS pedidos_ayer
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) = DATEADD(DAY,-1,{h})
        """)
        semana_row = query_acu(f"""
            SELECT ISNULL(SUM(VentaNeta), 0) AS total_sem
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) = DATEADD(DAY,-7,{h})
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
            "titulo":        "Ventas del dia",
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

    # ── VENTAS POR SUCURSAL (ACUMULADOS) ────────────────────────────────────
    elif tipo == "ventas_sucursal":
        fa, fb = _acu_filtros(modo, fi, ff)
        label  = _acu_label(modo)
        rows = query_acu(f"""
            SELECT Nombre AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN VentaNeta END), 0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN VentaNeta END), 0) AS anterior
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            GROUP BY Cve_Sucursal, Nombre ORDER BY actual DESC
        """)
        rows = [r for r in rows if float(r.get("actual") or 0) > 0]
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        _series = {
            "hoy":  ["Hoy",           "Ayer"],
            "15d":  ["Ult. 15 dias",  "15 dias previos"],
            "mes":  ["Mes actual",    "Mes ant. (comparable)"],
            "30d":  ["Ult. 30 dias",  "30 dias anteriores"],
        }
        series = _series.get(modo, ["Periodo actual", "30 dias anteriores"])
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Ventas por sucursal ({label})",
                "series": series, "datos": rows}

    # ── TOP VENDEDORES (solo FT_Facturas_C — Cve_Vendedor confiable) ────────
    elif tipo == "top_vendedores":
        filtro, _, label = _filtros_periodo(modo, "f.Fecha_Documento", fi, ff)
        rows = query(f"""
            SELECT TOP 10
                   ISNULL(v.Nombre, f.Cve_Vendedor) AS label,
                   ISNULL(SUM(f.Importe_Total), 0) AS valor,
                   COUNT(f.Cve_Folio) AS pedidos
            FROM FT_Facturas_C f
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = f.Cve_Vendedor
            WHERE f.Status = 'AC' AND f.Cve_Movimiento IN ('FM','FP')
              AND f.Cve_Sucursal <> 99
              AND {filtro}
            GROUP BY f.Cve_Vendedor, v.Nombre ORDER BY valor DESC
        """)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Top vendedores ({label})",
                "datos": rows}

    # ── COMPARATIVO MESES (ACUMULADOS) ──────────────────────────────────────
    elif tipo == "comparativo_meses":
        rows = query_acu(f"""
            SELECT TOP 6 Año AS anio, Mes AS mes,
                   DATENAME(MONTH, MIN(Fecha)) AS mes_nombre,
                   SUM(VentaNeta) AS valor,
                   SUM(VentaUnidades) AS pedidos
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE Fecha >= DATEADD(MONTH,-5,{hoy()})
            GROUP BY Año, Mes ORDER BY Año, Mes
        """)
        import calendar as _cal
        from datetime import date as _d2
        _hd = _d2.today()
        _ms = _hd.month % 12 + 1
        _as = _hd.year + (1 if _ms == 1 else 0)
        proyeccion = _proyectar([float(r.get("valor") or 0) for r in rows])
        return {
            "tipo": tipo, "titulo": "Ventas ultimos 6 meses",
            "proyeccion": proyeccion,
            "proyeccion_label": _cal.month_abbr[_ms],
            "datos": rows,
        }

    # ── PEDIDOS ACTIVOS -> MT_Ordenes_C (ordenes de compra) ──────────────────
    elif tipo == "pedidos_activos":
        rows = query(f"""
            SELECT s.Nombre AS label,
                   COUNT(CASE WHEN o.Status IN ('AU','RP') THEN 1 END) AS valor,
                   ISNULL(SUM(CASE WHEN o.Status IN ('AU','RP') THEN o.imp_total END), 0)
                       AS valor_mxn
            FROM GN_Sucursales s
            LEFT JOIN MT_Ordenes_C o ON o.Cve_Sucursal = s.Cve_Sucursal
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING COUNT(CASE WHEN o.Status IN ('AU','RP') THEN 1 END) > 0
            ORDER BY valor DESC
        """)
        # Ordenes generadas en los ultimos 7 dias
        tendencia = query(f"""
            SELECT CAST(o.fecha AS DATE) AS fecha,
                   COUNT(o.Cve_Folio) AS pedidos,
                   ISNULL(SUM(o.imp_total), 0) AS valor
            FROM MT_Ordenes_C o
            WHERE o.Status IN ('AU','RP')
              AND CAST(o.fecha AS DATE) >= DATEADD(DAY, -6, {hoy_fecha})
              AND o.Cve_Sucursal <> 99
            GROUP BY CAST(o.fecha AS DATE)
            ORDER BY fecha
        """)
        total     = sum(r.get("valor") or 0 for r in rows)
        val_total = sum(float(r.get("valor_mxn") or 0) for r in rows)
        return {
            "tipo":      tipo,
            "titulo":    "Ordenes de compra activas por sucursal",
            "total":     total,
            "val_total": val_total,
            "tendencia": tendencia,
            "datos":     rows,
        }

    # ── VENTAS DIARIO (ACUMULADOS) ──────────────────────────────────────────
    elif tipo == "ventas_diario":
        h = f"CAST({hoy()} AS DATE)"
        rows = query_acu(f"""
            SELECT CAST(Fecha AS DATE) AS fecha,
                   SUM(VentaNeta) AS valor,
                   SUM(VentaUnidades) AS pedidos
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) >= DATEADD(DAY,-29,{h})
            GROUP BY CAST(Fecha AS DATE) ORDER BY fecha
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        proyeccion = _proyectar([float(r.get("valor") or 0) for r in rows])
        return {
            "tipo": tipo, "titulo": "Ventas diarias - ultimos 30 dias",
            "total": total, "proyeccion": proyeccion,
            "proyeccion_label": "Prox. dia",
            "datos": rows,
        }

    # ── TENDENCIA ANUAL (ACUMULADOS) ────────────────────────────────────────
    elif tipo == "tendencia_anual":
        # Fetch 24 months to enable YoY seasonal projection
        rows = query_acu(f"""
            SELECT Año AS anio, Mes AS mes,
                   DATENAME(MONTH, MIN(Fecha)) AS mes_nombre,
                   SUM(VentaNeta) AS valor,
                   SUM(VentaUnidades) AS pedidos
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE Fecha >= DATEADD(MONTH,-23, DATEFROMPARTS(YEAR({hoy()}),MONTH({hoy()}),1))
            GROUP BY Año, Mes ORDER BY Año, Mes
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

        # Project next 3 months with Holt-Winters
        MESES_ES_TA = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
                       "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        vals_ta   = [mes_val[k] for k in trend_keys_ta]
        forecast_ta = _holt_winters_forecast(vals_ta, pasos=3)
        last_k_ta = trend_keys_ta[-1] if trend_keys_ta else (_hd2.year, _hd2.month - 1)
        proyeccion_meses = []
        for i, val_p in enumerate(forecast_ta):
            mp = last_k_ta[1] + i + 1
            ap = last_k_ta[0] + (mp - 1) // 12
            mp = ((mp - 1) % 12) + 1
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

    # ── TOP PRODUCTOS (ACUMULADOS) ──────────────────────────────────────────
    elif tipo == "top_productos":
        fa, _ = _acu_filtros(modo, fi, ff)
        label = _acu_label(modo)
        rows = query_acu(f"""
            SELECT TOP 10
                Descripcion AS label,
                SUM(VentaNeta) AS valor,
                SUM(VentaUnidades) AS unidades
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE {fa}
            GROUP BY Cve_Producto, Descripcion
            ORDER BY SUM(VentaNeta) DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Top productos ({label})",
                "total": total, "datos": rows}

    # ── CLIENTES FRECUENTES (solo FT_Facturas_C — Cve_Cliente confiable) ────
    elif tipo == "clientes_frecuentes":
        filtro, _, label = _filtros_periodo(modo, "f.Fecha_Documento", fi, ff)
        rows = query(f"""
            SELECT TOP 15
                   ISNULL(cl.Razon_Social, f.Cve_Cliente) AS label,
                   ISNULL(SUM(f.Importe_Total), 0) AS valor,
                   COUNT(f.Cve_Folio) AS pedidos
            FROM FT_Facturas_C f
            LEFT JOIN CM_Clientes cl ON cl.Cve_Cliente = f.Cve_Cliente
            WHERE f.Status = 'AC' AND f.Cve_Movimiento IN ('FM','FP')
              AND f.Cve_Sucursal <> 99
              AND f.Cve_Cliente <> '/'
              AND {filtro}
            GROUP BY f.Cve_Cliente, cl.Razon_Social
            HAVING ISNULL(cl.Razon_Social, f.Cve_Cliente) NOT LIKE '%MOSTRADOR%'
            ORDER BY valor DESC
        """)
        total = sum(float(r.get("valor") or 0) for r in rows)
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Clientes frecuentes ({label})",
                "total": total, "datos": rows}

    # ── VARIACION VENDEDORES (solo FT_Facturas_C) ───────────────────────────
    elif tipo == "variacion_vendedores":
        fa, fb, label = _filtros_periodo(modo, "f.Fecha_Documento", fi, ff)
        rows = query(f"""
            SELECT TOP 10
                   ISNULL(v.Nombre, f.Cve_Vendedor) AS label,
                   ISNULL(SUM(CASE WHEN {fa} THEN f.Importe_Total END), 0) AS actual,
                   ISNULL(SUM(CASE WHEN {fb} THEN f.Importe_Total END), 0) AS anterior
            FROM FT_Facturas_C f
            LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = f.Cve_Vendedor
            WHERE f.Status = 'AC' AND f.Cve_Movimiento IN ('FM','FP')
              AND f.Cve_Sucursal <> 99
            GROUP BY f.Cve_Vendedor, v.Nombre ORDER BY actual DESC
        """)
        for r in rows:
            actual   = float(r.get("actual") or 0)
            anterior = float(r.get("anterior") or 0)
            r["variacion_pct"] = (
                round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
            )
        return {"tipo": tipo, "modo": modo,
                "titulo": f"Variacion de vendedores ({label})",
                "series": ["Periodo actual", "Periodo anterior"], "datos": rows}

    # ── REPORTE VENTAS (multi-panel) ─────────────────────────────────────────
    elif tipo == "reporte_ventas":
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

        # Ticket promedio y total pedidos del periodo (ACUMULADOS)
        fa_rep, _ = _acu_filtros(modo, fi, ff)
        ticket_data = query_acu(f"""
            SELECT SUM(VentaUnidades) AS total_pedidos,
                   ISNULL(SUM(VentaNeta), 0) AS total_importe
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE {fa_rep}
        """)
        t = ticket_data[0] if ticket_data else {}
        total_pedidos  = int(t.get("total_pedidos") or 0)
        ticket_promedio = round(
            float(t.get("total_importe") or 0) / total_pedidos, 2
        ) if total_pedidos else 0

        # Proyeccion del siguiente mes basada en tendencia de 6 meses
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

    # ── REPORTE INVENTARIO ───────────────────────────────────────────────────
    elif tipo == "reporte_inventario":
        stock = _fetch_tipo("inventario_stock", modo)
        out   = _fetch_tipo("stockouts",        modo)

        # Tendencia historica de valor de stock (ultimos 4 meses)
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

        # Proyeccion de valor de stock siguiente mes
        valores_stock = [float(r.get("valor") or 0) for r in tendencia_stock]
        proyeccion_stock = _proyectar(valores_stock) if len(valores_stock) >= 2 else None

        # Rotacion de inventario: ventas 30d / valor_stock_actual (por sucursal)
        # Ventas 30d desde ACUMULADOS para velocidad
        h_inv = f"CAST({hoy()} AS DATE)"
        ventas_30d_acu = query_acu(f"""
            SELECT Cve_Sucursal,
                   ISNULL(SUM(VentaUnidades), 0) AS ventas_30d,
                   ISNULL(SUM(VentaUnidades) / 30.0, 0) AS ventas_diaria
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) >= DATEADD(DAY,-30,{h_inv})
            GROUP BY Cve_Sucursal
        """)
        # Build dict for JOIN in Python
        v30 = {int(r["Cve_Sucursal"]): r for r in ventas_30d_acu}

        stock_rows = query(f"""
            SELECT s.Cve_Sucursal, s.Nombre AS label,
                   ISNULL(SUM(e.Existencia * ISNULL(pg.Costo_Promedio,0)),0) AS valor_stock,
                   ISNULL(SUM(e.Existencia),0) AS total_existencia
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e
              ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status='AC'
            LEFT JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING ISNULL(SUM(e.Existencia),0) > 0
            ORDER BY valor_stock DESC
        """)
        rotacion_rows = []
        for sr in stock_rows:
            cve = int(sr["Cve_Sucursal"])
            v = v30.get(cve, {})
            vstock = float(sr.get("valor_stock") or 0)
            v30d   = float(v.get("ventas_30d") or 0)
            vdia   = float(v.get("ventas_diaria") or 0)
            exist  = float(sr.get("total_existencia") or 0)
            rotacion_rows.append({
                "label": sr["label"],
                "valor_stock": vstock,
                "ventas_30d": v30d,
                "rotacion": round(v30d / vstock, 2) if vstock > 0 else 0,
                "dias_cobertura": round(exist / vdia, 0) if vdia > 0 else None,
            })

        # Productos con stock pero sin ventas en ultimos 30 dias
        # Obtener productos vendidos en 30d desde ACUMULADOS
        vendidos_acu = query_acu(f"""
            SELECT DISTINCT Cve_Producto
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE CAST(Fecha AS DATE) >= DATEADD(DAY,-30,{h_inv})
        """)
        vendidos_set = {r["Cve_Producto"] for r in vendidos_acu}
        # Contar productos con stock que no están en vendidos
        all_stock = query("""
            SELECT DISTINCT e.Cve_Producto
            FROM IN_Existencias_Alm e
            WHERE e.Existencia > 0 AND e.Status='AC' AND e.Cve_Sucursal <> 99
        """)
        sin_mov_count = sum(1 for r in all_stock if r["Cve_Producto"] not in vendidos_set)
        sin_mov = [{"total": sin_mov_count}]
        sin_movimiento = int((sin_mov[0] if sin_mov else {}).get("total") or 0)

        # KPIs de rotacion agregados
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

    # ── INVENTARIO STOCK ─────────────────────────────────────────────────────
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

    # ── STOCKOUTS ────────────────────────────────────────────────────────────
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

    # ── VENTAS PRODUCTO (ACUMULADOS) ────────────────────────────────────────
    elif tipo == "ventas_producto":
        fa, _ = _acu_filtros(modo, fi, ff)
        label = _acu_label(modo)
        like_sql = f"AND Descripcion LIKE '%{producto.upper()}%'" if producto else ""
        rows = query_acu(f"""
            SELECT Nombre AS label,
                   ISNULL(SUM(VentaNeta), 0) AS valor,
                   SUM(VentaUnidades) AS unidades
            FROM ACU_VTA_DEV_DIARIA_FAM_PROD
            WHERE {fa}
              {like_sql}
            GROUP BY Cve_Sucursal, Nombre
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
