# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos_inventario.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
# ============================================================
"""
Sub-router de datos: Dashboard de Inventario.

Endpoints:
  GET  /inventario           → Dashboard de inventario completo
  GET  /inventario/consulta  → Consulta stock histórico de un producto
  GET  /inventario/historico → Histórico de snapshots de inventario
"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from shared.database import query, hoy
from shared.database_local import fetch_all, fetch_one

router = APIRouter()


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
                SELECT Cve_Producto FROM (
                    SELECT DISTINCT rd.Cve_Producto
                    FROM FT_Remisiones_C rc
                    INNER JOIN FT_Remisiones_D rd
                        ON rd.Cve_Folio=rc.Cve_Folio AND rd.Cve_Sucursal=rc.Cve_Sucursal
                           AND rd.Cve_Movimiento=rc.Cve_Movimiento
                    WHERE rc.Status='AC' AND rc.Cve_Movimiento='VTA'
                      AND rc.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
                    UNION
                    SELECT DISTINCT fd.Cve_Producto
                    FROM FT_Facturas_C fc
                    INNER JOIN FT_Facturas_D fd
                        ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
                           AND fd.Cve_Movimiento=fc.Cve_Movimiento
                    WHERE fc.Status='AC' AND fc.Cve_Movimiento IN ('FM','FP')
                      AND fc.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
                ) vendidos
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
                MIN(ISNULL(pg.PrecioP, 0))                     AS precio2,
                MIN(ISNULL(pg.PrecioF, 0))                     AS precio3
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
                SELECT Cve_Producto,
                       SUM(piezas) AS piezas_90d,
                       SUM(importe) AS importe_90d
                FROM (
                    SELECT rd.Cve_Producto,
                           SUM(rd.Cantidad)        AS piezas,
                           SUM(rd.Importe_Neto)     AS importe
                    FROM FT_Remisiones_C rc
                    INNER JOIN FT_Remisiones_D rd
                        ON rd.Cve_Folio=rc.Cve_Folio AND rd.Cve_Sucursal=rc.Cve_Sucursal
                           AND rd.Cve_Movimiento=rc.Cve_Movimiento
                    WHERE rc.Status='AC' AND rc.Cve_Movimiento='VTA'
                      AND rc.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
                    GROUP BY rd.Cve_Producto
                    UNION ALL
                    SELECT fd.Cve_Producto,
                           SUM(fd.Cantidad)        AS piezas,
                           SUM(fd.Importe_Neto)     AS importe
                    FROM FT_Facturas_C fc
                    INNER JOIN FT_Facturas_D fd
                        ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
                           AND fd.Cve_Movimiento=fc.Cve_Movimiento
                    WHERE fc.Status='AC' AND fc.Cve_Movimiento IN ('FM','FP')
                      AND fc.Fecha_Documento >= DATEADD(DAY, -90, {_hoy})
                    GROUP BY fd.Cve_Producto
                ) ventas_combinadas
                GROUP BY Cve_Producto
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
