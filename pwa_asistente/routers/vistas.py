# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : routers/vistas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router de datos — PWA Asistente.

Endpoints:
  GET /api/stock                      → Resumen de inventario por sucursal
  GET /api/stock/{cve}                → Detalle de stock de una sucursal
  GET /api/sucursal/{cve}/resumen     → Métricas del día para el inicio
  GET /api/pedidos                    → Pedidos activos

Nota sobre fechas:
  Todas las queries usan hoy() en lugar de GETDATE() directamente.
  Si TEST_DATE está configurado en .env, hoy() devuelve esa fecha fija.
  En producción (TEST_DATE vacío) usa la fecha real del servidor.
"""
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from shared.auth import get_current_user
from shared.config import TEST_DATE
from shared.database import query, hoy

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


# ── Sucursales: ventas del mes (para Inicio) ─────────────────────────────────

@router.get("/sucursales")
def sucursales():
    """
    Ventas del mes en curso vs mes anterior por sucursal.
    Alimenta las cards del Inicio.
    """
    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                  AS cve_sucursal,
            s.Nombre                                                        AS sucursal,
            COALESCE(SUM(CASE
                WHEN YEAR(fc.Fecha_Documento)  = YEAR({hoy()})
                 AND MONTH(fc.Fecha_Documento) = MONTH({hoy()})
                THEN fc.Importe_Total END), 0)                              AS ventas_mes,
            COUNT(CASE
                WHEN YEAR(fc.Fecha_Documento)  = YEAR({hoy()})
                 AND MONTH(fc.Fecha_Documento) = MONTH({hoy()})
                THEN 1 END)                                                 AS facturas_mes,
            COALESCE(SUM(CASE
                WHEN YEAR(fc.Fecha_Documento)  = YEAR(DATEADD(MONTH,-1,{hoy()}))
                 AND MONTH(fc.Fecha_Documento) = MONTH(DATEADD(MONTH,-1,{hoy()}))
                 AND DAY(fc.Fecha_Documento)  <= DAY({hoy()})
                THEN fc.Importe_Total END), 0)                              AS ventas_mes_anterior
        FROM GN_Sucursales s
        LEFT JOIN FT_Facturas_C fc
               ON fc.Cve_Sucursal = s.Cve_Sucursal AND fc.Status <> 'C'
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY ventas_mes DESC
    """)

    for r in rows:
        actual   = float(r.get("ventas_mes") or 0)
        anterior = float(r.get("ventas_mes_anterior") or 0)
        r["variacion_pct"] = (
            round((actual - anterior) / anterior * 100, 1) if anterior > 0 else None
        )

    return JSONResponse({"sucursales": rows})


# ── Stock: resumen por sucursal ───────────────────────────────────────────────

@router.get("/stock")
def stock():
    """Card por sucursal con totales de existencia y lotes por caducar."""
    sucursales = query(f"""
        SELECT
            s.Cve_Sucursal                                                    AS cve_sucursal,
            s.Nombre                                                          AS sucursal,
            COUNT(DISTINCT ea.Cve_Producto)                                   AS total_productos,
            SUM(CASE WHEN ea.Existencia <= 0 THEN 1 ELSE 0 END)               AS sin_stock,
            COUNT(DISTINCT CASE
                WHEN el.Fecha_Caducidad BETWEEN {hoy()} AND DATEADD(DAY, 90, {hoy()})
                     AND el.Existencia > 0
                THEN el.Num_Lote END)                                         AS lotes_por_caducar
        FROM GN_Sucursales s
        LEFT JOIN IN_Existencias_Alm ea
               ON ea.Cve_Sucursal = s.Cve_Sucursal AND ea.Status = 'AC'
        LEFT JOIN IN_Existencias_Lote el
               ON el.Cve_Sucursal = s.Cve_Sucursal
              AND el.Cve_Producto  = ea.Cve_Producto
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY s.Nombre
    """)
    return JSONResponse({"sucursales": sucursales})


# ── Stock: detalle de una sucursal ────────────────────────────────────────────

@router.get("/stock/{cve_sucursal}")
def stock_detalle(cve_sucursal: int):
    """Top stock, caducidades próximas y sin existencia por sucursal."""
    top_stock = query("""
        SELECT TOP 10
            p.Descripcion            AS producto,
            p.Laboratorio            AS laboratorio,
            SUM(ea.Existencia)       AS existencia_total
        FROM IN_Existencias_Alm ea
        JOIN IM_Productos_Gral p ON ea.Cve_Producto = p.Cve_Producto
        WHERE ea.Cve_Sucursal = ?
          AND ea.Status       = 'AC'
          AND ea.Existencia   > 0
          AND p.Descripcion IS NOT NULL
        GROUP BY p.Descripcion, p.Laboratorio
        ORDER BY SUM(ea.Existencia) DESC
    """, (cve_sucursal,))

    caducidades = query(f"""
        SELECT TOP 15
            p.Descripcion      AS producto,
            el.Num_Lote        AS lote,
            el.Fecha_Caducidad AS fecha_caducidad,
            el.Existencia      AS existencia_lote,
            DATEDIFF(DAY, {hoy()}, el.Fecha_Caducidad) AS dias_para_caducar
        FROM IN_Existencias_Lote el
        LEFT JOIN IM_Productos_Gral p ON el.Cve_Producto = p.Cve_Producto
        WHERE el.Cve_Sucursal    = ?
          AND el.Existencia      > 0
          AND el.Fecha_Caducidad IS NOT NULL
          AND el.Fecha_Caducidad BETWEEN {hoy()} AND DATEADD(DAY, 90, {hoy()})
        ORDER BY el.Fecha_Caducidad ASC
    """, (cve_sucursal,))

    sin_stock = query("""
        SELECT TOP 10
            p.Descripcion    AS producto,
            p.Laboratorio    AS laboratorio
        FROM IN_Existencias_Alm ea
        LEFT JOIN IM_Productos_Gral p ON ea.Cve_Producto = p.Cve_Producto
        WHERE ea.Cve_Sucursal = ?
          AND ea.Status       = 'AC'
          AND ea.Existencia   <= 0
          AND p.Descripcion IS NOT NULL
        ORDER BY p.Descripcion
    """, (cve_sucursal,))

    return JSONResponse({
        "top_stock":   top_stock,
        "caducidades": caducidades,
        "sin_stock":   sin_stock,
    })


# ── Resumen del día por sucursal (para Inicio) ────────────────────────────────

@router.get("/sucursal/{cve_sucursal}/resumen")
def sucursal_resumen(cve_sucursal: int):
    """
    Ventas de ayer, top 3 productos (últimos 30 días) y pedidos activos.
    Se carga solo cuando el usuario expande una sucursal en Inicio.
    """
    ventas_ayer = query(f"""
        SELECT
            COUNT(*)                        AS total_facturas,
            COALESCE(SUM(Importe_Total), 0) AS importe_total
        FROM FT_Facturas_C
        WHERE Cve_Sucursal = ?
          AND Status      <> 'C'
          AND CAST(Fecha_Documento AS DATE) = DATEADD(DAY, -1, {hoy()})
    """, (cve_sucursal,))

    top_productos = query(f"""
        SELECT TOP 3
            p.Descripcion                  AS producto,
            SUM(fd.Cantidad)               AS unidades,
            ROUND(SUM(fd.Importe_Neto), 2) AS importe
        FROM FT_Facturas_D fd
        JOIN FT_Facturas_C fc
          ON fd.Cve_Folio      = fc.Cve_Folio
         AND fd.Cve_Sucursal   = fc.Cve_Sucursal
         AND fd.Cve_Movimiento = fc.Cve_Movimiento
        LEFT JOIN IM_Productos_Gral p ON fd.Cve_Producto = p.Cve_Producto
        WHERE fc.Cve_Sucursal = ?
          AND fc.Status      <> 'C'
          AND YEAR(fc.Fecha_Documento)  = YEAR({hoy()})
          AND MONTH(fc.Fecha_Documento) = MONTH({hoy()})
          AND p.Descripcion IS NOT NULL
        GROUP BY p.Descripcion
        ORDER BY SUM(fd.Importe_Neto) DESC
    """, (cve_sucursal,))

    pedidos_pendientes = query("""
        SELECT COUNT(*) AS total
        FROM FT_Pedidos_C
        WHERE Cve_Sucursal = ? AND Estatus = 'AC'
    """, (cve_sucursal,))

    fecha_hoy = TEST_DATE if TEST_DATE else date.today().strftime("%Y-%m-%d")

    return JSONResponse({
        "ventas_ayer":        ventas_ayer[0] if ventas_ayer else {},
        "top_productos":      top_productos,
        "pedidos_pendientes": int((pedidos_pendientes[0] or {}).get("total") or 0),
        "fecha_hoy":          fecha_hoy,
    })


# ── Pedidos: resumen por sucursal ─────────────────────────────────────────────

@router.get("/pedidos/sucursales")
def pedidos_sucursales():
    """Conteo de pedidos activos e historial 30d por sucursal."""
    rows = query(f"""
        SELECT
            s.Cve_Sucursal                                                    AS cve_sucursal,
            s.Nombre                                                          AS sucursal,
            COUNT(CASE WHEN p.Estatus = 'AC' THEN 1 END)                     AS activos,
            COUNT(CASE WHEN p.Estatus IN ('TR','CN')
                        AND p.Fecha_Documento >= DATEADD(DAY,-30,{hoy()})
                  THEN 1 END)                                                 AS historial_30d
        FROM GN_Sucursales s
        LEFT JOIN FT_Pedidos_C p ON p.Cve_Sucursal = s.Cve_Sucursal
        WHERE s.Cve_Sucursal <> 99
        GROUP BY s.Cve_Sucursal, s.Nombre
        ORDER BY activos DESC
    """)
    return JSONResponse({"sucursales": rows})


# ── Pedidos: detalle por sucursal ─────────────────────────────────────────────

@router.get("/pedidos/{cve_sucursal}")
def pedidos_sucursal(cve_sucursal: int):
    """Pedidos activos e historial 30d de una sucursal específica."""
    try:
        return _pedidos_sucursal(cve_sucursal)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=200)


def _pedidos_sucursal(cve_sucursal: int):
    """Antigüedad de pedidos activos de la sucursal (vista para director)."""
    antiguedad = query(f"""
        SELECT
            CASE
                WHEN CAST(Fecha_Documento AS DATE) = {hoy()}
                    THEN 'Hoy'
                WHEN CAST(Fecha_Documento AS DATE) >= DATEADD(DAY, -7, {hoy()})
                    THEN 'Esta semana'
                WHEN CAST(Fecha_Documento AS DATE) >= DATEADD(DAY, -30, {hoy()})
                    THEN 'Últimos 30 días'
                ELSE 'Más de 30 días'
            END                  AS rango,
            COUNT(*)             AS num_pedidos,
            MIN(Fecha_Documento) AS mas_antiguo
        FROM FT_Pedidos_C
        WHERE Cve_Sucursal = ? AND Estatus = 'AC'
        GROUP BY
            CASE
                WHEN CAST(Fecha_Documento AS DATE) = {hoy()}
                    THEN 'Hoy'
                WHEN CAST(Fecha_Documento AS DATE) >= DATEADD(DAY, -7, {hoy()})
                    THEN 'Esta semana'
                WHEN CAST(Fecha_Documento AS DATE) >= DATEADD(DAY, -30, {hoy()})
                    THEN 'Últimos 30 días'
                ELSE 'Más de 30 días'
            END
    """, (cve_sucursal,))

    # Orden lógico de más reciente a más antiguo
    orden = ['Hoy', 'Esta semana', 'Últimos 30 días', 'Más de 30 días']
    antiguedad.sort(key=lambda r: orden.index(r['rango']) if r['rango'] in orden else 99)

    return JSONResponse({"antiguedad": antiguedad})


# ── Médicos: detección de duplicados ─────────────────────────────────────────

@router.get("/medicos/duplicados")
def medicos_duplicados():
    """Detecta médicos registrados más de una vez por cédula o nombre idéntico."""

    # Duplicados confirmados: misma cédula, distinto registro
    raw_cedula = query("""
        SELECT
            m.Cve_Medico                        AS cve_medico,
            LTRIM(RTRIM(m.Nombre))              AS nombre,
            LTRIM(RTRIM(m.cedula))              AS cedula,
            m.cve_vendedor                      AS cve_vendedor,
            ISNULL(v.Nombre, CASE WHEN LTRIM(RTRIM(ISNULL(m.cve_vendedor,''))) = '' THEN 'Sin asignar' ELSE m.cve_vendedor END) AS vendedor
        FROM GC_Medicos m
        LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = m.cve_vendedor
        WHERE LTRIM(RTRIM(ISNULL(m.cedula, ''))) <> ''
          AND LTRIM(RTRIM(m.cedula)) IN (
              SELECT LTRIM(RTRIM(cedula))
              FROM GC_Medicos
              WHERE LTRIM(RTRIM(ISNULL(cedula, ''))) <> ''
              GROUP BY LTRIM(RTRIM(cedula))
              HAVING COUNT(*) > 1
          )
        ORDER BY LTRIM(RTRIM(m.cedula)), m.Cve_Medico
    """)

    # Posibles duplicados: mismo nombre exacto (con o sin cédula)
    raw_nombre = query("""
        SELECT
            m.Cve_Medico                        AS cve_medico,
            LTRIM(RTRIM(m.Nombre))              AS nombre,
            LTRIM(RTRIM(ISNULL(m.cedula, ''))) AS cedula,
            m.cve_vendedor                      AS cve_vendedor,
            ISNULL(v.Nombre, CASE WHEN LTRIM(RTRIM(ISNULL(m.cve_vendedor,''))) = '' THEN 'Sin asignar' ELSE m.cve_vendedor END) AS vendedor
        FROM GC_Medicos m
        LEFT JOIN GC_Vendedores v ON v.Cve_Vendedor = m.cve_vendedor
        WHERE UPPER(LTRIM(RTRIM(m.Nombre))) IN (
              SELECT UPPER(LTRIM(RTRIM(Nombre)))
              FROM GC_Medicos
              GROUP BY UPPER(LTRIM(RTRIM(Nombre)))
              HAVING COUNT(*) > 1
          )
        ORDER BY UPPER(LTRIM(RTRIM(m.Nombre))), m.Cve_Medico
    """)

    # Agrupar por cédula
    grupos_cedula: dict = defaultdict(list)
    for r in raw_cedula:
        grupos_cedula[r['cedula']].append(r)

    # Agrupar por nombre normalizado
    grupos_nombre: dict = defaultdict(list)
    for r in raw_nombre:
        grupos_nombre[r['nombre'].upper()].append(r)

    return JSONResponse({
        "por_cedula": list(grupos_cedula.values()),
        "por_nombre": list(grupos_nombre.values()),
    })

