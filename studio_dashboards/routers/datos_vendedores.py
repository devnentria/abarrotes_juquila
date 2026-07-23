# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_vendedores.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Sub-router de datos: Dashboard de Vendedores y Contactos (Proveedores).

Endpoints:
  GET  /vendedores → Dashboard Vendedores: ranking, por sucursal, tendencia mensual
  GET  /medicos    → Dashboard de Contactos: ranking por proveedor, tendencia mensual

Tablas ERP:
  - Vendedores se extraen de FT_Facturas_C (mayoreo) + FT_Facturas_D
  - GC_Medicos NO existe en esta BD; se usa PM_Proveedores como reemplazo
"""
from collections import Counter as _Counter, defaultdict as _dd

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from shared.auth import get_current_user
from shared.database import query, hoy

router = APIRouter()


# ── Dashboard Vendedores ──────────────────────────────────────────────────────
@router.get("/vendedores")
def vendedores_dashboard(modo: str = "30d", mes: str = None, fi: str = None, ff: str = None):
    """
    Dashboard de Vendedores (mayoreo — FT_Facturas_C / FT_Facturas_D).

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
        fecha_ini = f"CAST('{fi}' AS DATE)"
        fecha_fin = f"CAST('{ff}' AS DATE)"
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

    where_periodo   = f"fc.Fecha_Documento >= {fecha_ini} AND fc.Fecha_Documento <= {fecha_fin}"
    where_anterior  = f"fc.Fecha_Documento >= {fecha_ini_a} AND fc.Fecha_Documento <= {fecha_fin_a}"
    filtro_base     = "fc.Status = 'AC' AND fc.Cve_Movimiento IN ('FM','FP') AND fc.Cve_Sucursal <> 99"

    # ── 1. Ventas del período por vendedor ────────────────────────────────────
    try:
        vend_rows = query(f"""
            SELECT
                v.Nombre                                    AS nombre,
                ISNULL(SUM(fd.Importe_Neto), 0)            AS importe,
                COUNT(DISTINCT fc.Cve_Folio)                AS pedidos
            FROM FT_Facturas_C fc
            INNER JOIN FT_Facturas_D fd
                ON fd.Cve_Folio = fc.Cve_Folio
               AND fd.Cve_Sucursal = fc.Cve_Sucursal
               AND fd.Cve_Movimiento = fc.Cve_Movimiento
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
            WHERE {filtro_base} AND {where_periodo}
            GROUP BY v.Nombre
            ORDER BY importe DESC
        """)
    except Exception as e:
        raise HTTPException(500, f"vendedores-ranking: {e}")

    # ── 2. Ventas período anterior por vendedor ──────────────────────────────
    try:
        ant_rows = query(f"""
            SELECT
                v.Nombre                         AS nombre,
                ISNULL(SUM(fd.Importe_Neto), 0) AS importe
            FROM FT_Facturas_C fc
            INNER JOIN FT_Facturas_D fd
                ON fd.Cve_Folio = fc.Cve_Folio
               AND fd.Cve_Sucursal = fc.Cve_Sucursal
               AND fd.Cve_Movimiento = fc.Cve_Movimiento
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
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
                s.Nombre                         AS sucursal,
                v.Nombre                         AS vendedor,
                ISNULL(SUM(fd.Importe_Neto), 0) AS importe
            FROM FT_Facturas_C fc
            INNER JOIN FT_Facturas_D fd
                ON fd.Cve_Folio = fc.Cve_Folio
               AND fd.Cve_Sucursal = fc.Cve_Sucursal
               AND fd.Cve_Movimiento = fc.Cve_Movimiento
            INNER JOIN GN_Sucursales s ON s.Cve_Sucursal = fc.Cve_Sucursal
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
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
                    FORMAT(fc.Fecha_Documento, 'yyyy-MM') AS mes,
                    v.Nombre                               AS vendedor,
                    ISNULL(SUM(fd.Importe_Neto), 0)       AS importe
                FROM FT_Facturas_C fc
                INNER JOIN FT_Facturas_D fd
                    ON fd.Cve_Folio = fc.Cve_Folio
                   AND fd.Cve_Sucursal = fc.Cve_Sucursal
                   AND fd.Cve_Movimiento = fc.Cve_Movimiento
                INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
                WHERE {filtro_base}
                  AND fc.Fecha_Documento >= DATEADD(MONTH, -6, {_hoy})
                  AND v.Nombre IN ({placeholders})
                GROUP BY FORMAT(fc.Fecha_Documento, 'yyyy-MM'), v.Nombre
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
                v.Nombre                         AS vendedor,
                s.Nombre                         AS sucursal,
                ISNULL(SUM(fd.Importe_Neto), 0) AS importe
            FROM FT_Facturas_C fc
            INNER JOIN FT_Facturas_D fd
                ON fd.Cve_Folio = fc.Cve_Folio
               AND fd.Cve_Sucursal = fc.Cve_Sucursal
               AND fd.Cve_Movimiento = fc.Cve_Movimiento
            INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
            INNER JOIN GN_Sucursales s ON s.Cve_Sucursal = fc.Cve_Sucursal
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
                    v.Nombre                         AS vendedor,
                    pg.Descripcion                   AS producto,
                    ISNULL(SUM(fd.Importe_Neto), 0) AS importe
                FROM FT_Facturas_C fc
                INNER JOIN FT_Facturas_D fd
                    ON fd.Cve_Folio = fc.Cve_Folio
                   AND fd.Cve_Sucursal = fc.Cve_Sucursal
                   AND fd.Cve_Movimiento = fc.Cve_Movimiento
                INNER JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
                INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = fd.Cve_Producto
                WHERE {filtro_base} AND {where_periodo}
                  AND v.Nombre IN ({placeholders})
                GROUP BY v.Nombre, pg.Descripcion
                ORDER BY v.Nombre, importe DESC
            """, params=top8_nombres)
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
    Dashboard de Contactos (Proveedores).

    GC_Medicos NO existe en esta BD. Se usa PM_Proveedores como fuente de
    contactos/proveedores, vinculados a facturas vía CM_Clientes.Cve_Ruta.

    Si la relación CM_Clientes.Cve_Ruta -> PM_Proveedores no aplica,
    se devuelve data vacía de placeholder para el demo.
    """
    _hoy = hoy()

    # ── Filtros de período — anterior = período inmediatamente previo ────────
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

    filtro_base    = "fc.Status = 'AC' AND fc.Cve_Movimiento IN ('FM','FP') AND fc.Cve_Sucursal <> 99"
    filtro_prov    = "cl.Cve_Ruta IS NOT NULL AND cl.Cve_Ruta <> 0 AND cl.Cve_Ruta <> 1"
    where_periodo  = f"fc.Fecha_Documento >= {fecha_ini} AND fc.Fecha_Documento <= {fecha_fin}"
    where_anterior = f"fc.Fecha_Documento >= {fecha_ini_a} AND fc.Fecha_Documento <= {fecha_fin_a}"
    joins_base     = """
        INNER JOIN FT_Facturas_D fd
            ON fd.Cve_Folio = fc.Cve_Folio
           AND fd.Cve_Sucursal = fc.Cve_Sucursal
           AND fd.Cve_Movimiento = fc.Cve_Movimiento
        INNER JOIN CM_Clientes cl ON CAST(fc.Cve_Cliente AS INT) = cl.Cve_Cliente
        INNER JOIN PM_Proveedores p ON p.Cve_Proveedor = cl.Cve_Ruta
        LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
    """

    # ── 1. Ventas del período por contacto (proveedor) ─────────────────────────
    try:
        med_rows = query(f"""
            SELECT
                p.Cve_Proveedor                             AS cve_contacto,
                p.Nombre                                    AS nombre,
                ISNULL(v.Nombre, 'Sin rep')                 AS vendedor,
                ISNULL(SUM(fd.Importe_Neto), 0)            AS importe,
                COUNT(DISTINCT fc.Cve_Folio)                AS pedidos,
                COUNT(DISTINCT fc.Cve_Cliente)              AS clientes
            FROM FT_Facturas_C fc {joins_base}
            WHERE {filtro_base} AND {filtro_prov} AND {where_periodo}
            GROUP BY p.Cve_Proveedor, p.Nombre, v.Nombre
            ORDER BY importe DESC
        """)
    except Exception as e:
        # Si PM_Proveedores no tiene relación vía Cve_Ruta, devolvemos vacío
        return JSONResponse({
            "total_ventas":     0,
            "lider_nombre":     "—",
            "lider_importe":    0,
            "medicos_activos":  0,
            "top_rep":          "—",
            "ranking":          [],
            "por_rep":          [],
            "por_mes":          [],
            "_nota":            f"PM_Proveedores sin datos vinculados o error: {e}",
        })

    # ── 2. Período anterior ───────────────────────────────────────────────────
    try:
        ant_rows = query(f"""
            SELECT
                p.Cve_Proveedor                      AS cve_contacto,
                ISNULL(SUM(fd.Importe_Neto), 0)     AS importe
            FROM FT_Facturas_C fc {joins_base}
            WHERE {filtro_base} AND {filtro_prov} AND {where_anterior}
            GROUP BY p.Cve_Proveedor
        """)
        ant_map = {int(r["cve_contacto"]): round(float(r["importe"] or 0), 2) for r in ant_rows}
    except Exception:
        ant_map = {}

    # ── 3. Construir ranking con variación ────────────────────────────────────
    ranking = []
    for r in med_rows:
        importe  = round(float(r["importe"] or 0), 2)
        pedidos  = int(r["pedidos"] or 0)
        clientes = int(r["clientes"] or 0)
        ant      = ant_map.get(int(r["cve_contacto"]), 0.0)
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
    total_ventas    = round(sum(r["importe"] for r in ranking), 2)
    medicos_activos = len(ranking)
    lider           = ranking[0] if ranking else {}
    lider_nombre    = lider.get("nombre", "—")
    lider_importe   = lider.get("importe", 0.0)

    # Rep con más contactos activos
    rep_count = _Counter(r["vendedor"] for r in ranking if r["vendedor"] != "Sin rep")
    top_rep   = rep_count.most_common(1)[0][0] if rep_count else "—"

    # ── 5. Ventas por representante (agrupa contactos de cada rep) ────────────
    try:
        rep_rows = query(f"""
            SELECT
                ISNULL(v.Nombre, 'Sin rep')              AS rep,
                ISNULL(SUM(fd.Importe_Neto), 0)         AS importe,
                COUNT(DISTINCT p.Cve_Proveedor)          AS medicos
            FROM FT_Facturas_C fc {joins_base}
            WHERE {filtro_base} AND {filtro_prov} AND {where_periodo}
            GROUP BY v.Nombre
            ORDER BY importe DESC
        """)
        por_rep = [
            {"rep": (r["rep"] or "").strip(), "importe": round(float(r["importe"] or 0), 2), "medicos": int(r["medicos"] or 0)}
            for r in rep_rows
        ]
    except Exception:
        por_rep = []

    # ── 6. Tendencia mensual top 5 contactos (últimos 6 meses) ────────────────
    top5_nombres = [r["nombre"] for r in ranking[:5]]
    por_mes = []
    if top5_nombres:
        try:
            placeholders = ", ".join(["?" for _ in top5_nombres])
            mes_rows = query(f"""
                SELECT
                    FORMAT(fc.Fecha_Documento, 'yyyy-MM') AS mes,
                    p.Nombre                               AS medico,
                    ISNULL(SUM(fd.Importe_Neto), 0)       AS importe
                FROM FT_Facturas_C fc {joins_base}
                WHERE {filtro_base} AND {filtro_prov}
                  AND fc.Fecha_Documento >= DATEADD(MONTH, -6, {_hoy})
                  AND p.Nombre IN ({placeholders})
                GROUP BY FORMAT(fc.Fecha_Documento, 'yyyy-MM'), p.Nombre
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
