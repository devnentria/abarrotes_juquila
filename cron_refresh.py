#!/usr/bin/env python3
# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Archivo  : cron_refresh.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Cron job nocturno — precarga el cache del dashboard.

Ejecutar a las 2:00am en el servidor:
    cd /ruta/del/proyecto && python3 cron_refresh.py

Registrar en crontab del servidor:
    0 2 * * * cd /home/ubuntu/suite && python3 cron_refresh.py >> logs/cron.log 2>&1

O con pm2 (recomendado):
    pm2 start cron_refresh.py --name cron-suite --interpreter python3 --cron "0 2 * * *" --no-autorestart

Qué hace:
  1. Invalida el cache del día anterior para todas las sucursales
  2. Regenera stock_detalle (SQL Server → SQLite)
  3. Regenera resúmenes de IA (SQL Server + OpenAI → SQLite)
  Al terminar, los endpoints sirven resultados instantáneos todo el día.
"""
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

# Asegurar que el proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent))

from shared import cache_dashboard as _cache
from shared.database import query as db_query
from shared.database_local import get_connection
from pwa_asistente.routers.vistas import stock_detalle
from pwa_asistente.routers.ia_flash import ia_sucursal, ia_inventario
import pwa_asistente.routers.chat as _chat_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Usuario ficticio para el cron — no cuenta consultas_ia
_CRON_USER = {"id": 0, "nombre": "Sistema Automático", "rol": "admin"}


def _get_sucursales() -> list[int]:
    """Obtiene todas las sucursales activas (excepto Corporativa)."""
    rows = db_query(
        "SELECT Cve_Sucursal FROM GN_Sucursales WHERE Cve_Sucursal <> 99 ORDER BY Cve_Sucursal"
    )
    return [r["Cve_Sucursal"] for r in (rows or [])]


def refresh_sucursal(cve: int) -> None:
    """Invalida y regenera el cache completo de una sucursal."""
    # Invalidar cache del día anterior
    _cache.invalidate(f"stock_detalle_{cve}")
    _cache.invalidate(f"ia_sucursal_{cve}")
    _cache.invalidate(f"ia_inventario_{cve}")

    # stock_detalle: no necesita usuario, llama directo
    stock_detalle(cve)
    log.info(f"  stock_detalle OK")

    # ia_sucursal: pasar regenerar=False para que NO cuente consultas
    ia_sucursal(cve_sucursal=cve, regenerar=False, usuario=_CRON_USER)
    log.info(f"  ia_sucursal OK")

    # ia_inventario: igual
    ia_inventario(cve_sucursal=cve, regenerar=False, usuario=_CRON_USER)
    log.info(f"  ia_inventario OK")


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
        with _urllib.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.warning(f"  Nominatim error CP {cp}: {e}")
    return None


def _top_cps_mes(anio: int, mes: int) -> list:
    """Devuelve los top 150 CPs con más ventas en el mes dado (backfill meses pasados)."""
    try:
        rows = db_query(f"""
            SELECT TOP 150 con.CP
            FROM FT_Pedidos_C p
            INNER JOIN FT_Pedidos_Dia d
              ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
            INNER JOIN CM_Consignatarios con
              ON con.Cve_Consignatario=p.Cve_Consignatario
            WHERE p.Estatus<>'CN'
              AND p.Referencia_Cliente='PAGADO'
              AND p.Cve_Sucursal<>99
              AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
              AND YEAR(p.Fecha_Documento)={anio}
              AND MONTH(p.Fecha_Documento)={mes}
            GROUP BY con.CP
            ORDER BY SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) DESC
        """)
        return [r["CP"] for r in (rows or []) if r.get("CP")]
    except Exception as e:
        log.error(f"  Error consultando CPs {anio}-{mes:02d}: {e}")
        return []


def _cps_del_dia(fecha: date) -> list:
    """Devuelve TODOS los CPs con pedidos en la fecha dada (sin límite)."""
    try:
        rows = db_query(f"""
            SELECT DISTINCT con.CP
            FROM FT_Pedidos_C p
            INNER JOIN CM_Consignatarios con
              ON con.Cve_Consignatario=p.Cve_Consignatario
            WHERE p.Estatus<>'CN'
              AND p.Referencia_Cliente='PAGADO'
              AND p.Cve_Sucursal<>99
              AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
              AND CAST(p.Fecha_Documento AS DATE) = '{fecha.isoformat()}'
        """)
        return [r["CP"] for r in (rows or []) if r.get("CP")]
    except Exception as e:
        log.error(f"  Error consultando CPs del día {fecha}: {e}")
        return []


def refresh_geocodificacion() -> None:
    """
    Geocodifica CPs sin coordenadas.
    - Día actual: TODOS los CPs de los pedidos de hoy (acumulativo diario)
    - Meses pasados: top 150 por mes (backfill)
    - Limpia coordenadas de CPs sin actividad en más de 6 meses
    """
    log.info("--- Geocodificación mapa iniciada ---")
    hoy = date.today()

    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cp_coords (
            cp TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL,
            cached_at TEXT DEFAULT (datetime('now')))
    """)
    conn.commit()

    def _geocodificar_lista(cps: list, etiqueta: str) -> int:
        """Geocodifica los CPs de la lista que aún no estén en caché. Retorna nuevos guardados."""
        if not cps:
            return 0
        placeholders = ",".join(["?"] * len(cps))
        ya_cached = {
            r[0] for r in conn.execute(
                f"SELECT cp FROM cp_coords WHERE cp IN ({placeholders})", cps
            ).fetchall()
        }
        faltantes = [cp for cp in cps if cp not in ya_cached]
        if not faltantes:
            log.info(f"  {etiqueta}: todos los CPs ya en caché ({len(cps)})")
            return 0
        log.info(f"  {etiqueta}: geocodificando {len(faltantes)} CPs nuevos...")
        nuevos = 0
        for cp in faltantes:
            coords = _geocode_cp(cp)
            if coords:
                lat, lng = coords
                conn.execute(
                    "INSERT OR REPLACE INTO cp_coords (cp, lat, lng) VALUES (?, ?, ?)",
                    (cp, lat, lng),
                )
                conn.commit()
                nuevos += 1
            time.sleep(1.1)  # Respetar rate limit Nominatim
        return nuevos

    total_nuevos = 0

    # 1. CPs del día actual — todos sin límite
    cps_hoy = _cps_del_dia(hoy)
    log.info(f"  Hoy ({hoy}): {len(cps_hoy)} CPs en pedidos del día")
    total_nuevos += _geocodificar_lista(cps_hoy, f"hoy {hoy}")

    # 2. Meses pasados — top 150 como backfill
    a, m = hoy.year, hoy.month
    m -= 1
    if m == 0:
        m = 12; a -= 1
    for _ in range(6):
        cps = _top_cps_mes(a, m)
        total_nuevos += _geocodificar_lista(cps, f"{a}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12; a -= 1

    # 3. Limpiar CPs sin actividad en más de 6 meses
    try:
        cur = conn.execute(
            "DELETE FROM cp_coords WHERE cached_at < date('now', '-180 days')"
        )
        if cur.rowcount:
            conn.commit()
            log.info(f"  Limpieza: {cur.rowcount} CPs eliminados (>6 meses sin uso)")
    except Exception as e:
        log.warning(f"  Error en limpieza de CPs: {e}")

    conn.close()
    log.info(f"--- Geocodificación terminada — {total_nuevos} CPs nuevos guardados ---")


def guardar_snapshot_inventario() -> None:
    """
    Guarda un snapshot del inventario actual en SQLite.
    Se ejecuta al cierre del día para tener histórico consultable.
    """
    import json
    log.info("--- Snapshot inventario iniciado ---")
    hoy_str = date.today().isoformat()

    try:
        rows_kpi = db_query("""
            SELECT
                ISNULL(SUM(e.Existencia * ISNULL(e.Costo_Promedio, 0)), 0) AS valor_stock,
                ISNULL(SUM(e.Existencia), 0)                               AS unidades,
                COUNT(DISTINCT CASE WHEN e.Existencia > 0 THEN e.Cve_Producto END) AS productos_stock
            FROM IN_Existencias_Alm e
            WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99
        """)
        kpi = rows_kpi[0] if rows_kpi else {}

        rows_crit = db_query(f"""
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
                  AND c.Fecha_Documento >= DATEADD(DAY, -90, CAST(GETDATE() AS DATE))
            )
        """)
        criticos = int((rows_crit[0] if rows_crit else {}).get("total") or 0)

        rows_suc = db_query("""
            SELECT s.Nombre AS sucursal,
                   ISNULL(SUM(e.Existencia * ISNULL(e.Costo_Promedio, 0)), 0) AS valor,
                   ISNULL(SUM(e.Existencia), 0) AS unidades
            FROM GN_Sucursales s
            LEFT JOIN IN_Existencias_Alm e ON e.Cve_Sucursal = s.Cve_Sucursal AND e.Status = 'AC'
            WHERE s.Cve_Sucursal <> 99
            GROUP BY s.Cve_Sucursal, s.Nombre
            HAVING ISNULL(SUM(e.Existencia), 0) > 0
            ORDER BY SUM(e.Existencia * ISNULL(e.Costo_Promedio, 0)) DESC
        """)
        por_sucursal = [
            {"sucursal": (r["sucursal"] or "").strip(),
             "valor": round(float(r["valor"] or 0), 2),
             "unidades": int(r["unidades"] or 0)}
            for r in (rows_suc or [])
        ]

        # Snapshot por producto × sucursal (todos los productos, sin importar existencia)
        rows_prod = db_query("""
            SELECT e.Cve_Sucursal,
                   MIN(ISNULL(s.Nombre, CAST(e.Cve_Sucursal AS VARCHAR))) AS sucursal,
                   CAST(e.Cve_Producto AS VARCHAR)  AS cve_producto,
                   MIN(pg.Descripcion)              AS descripcion,
                   SUM(e.Existencia)                AS existencia,
                   MIN(ISNULL(e.Costo_Promedio, 0)) AS costo_promedio,
                   MIN(ISNULL(pg.Precio_Minimo_Venta_Base, 0))       AS precio1,
                   MIN(ISNULL(pg.Precio_Minimo_Venta_Base2, 0))       AS precio2,
                   MIN(ISNULL(pg.Precio_Minimo_Venta_Base3, 0))       AS precio3
            FROM IN_Existencias_Alm e
            INNER JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
            LEFT JOIN GN_Sucursales s ON s.Cve_Sucursal = e.Cve_Sucursal
            WHERE e.Status = 'AC' AND e.Cve_Sucursal <> 99
            GROUP BY e.Cve_Sucursal, e.Cve_Producto
        """)

        conn = get_connection()
        # Snapshot global
        conn.execute("""
            INSERT OR REPLACE INTO inventario_historico
                (fecha, valor_stock, unidades, productos_stock, criticos, por_sucursal)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            hoy_str,
            round(float(kpi.get("valor_stock") or 0), 2),
            int(kpi.get("unidades") or 0),
            int(kpi.get("productos_stock") or 0),
            criticos,
            json.dumps(por_sucursal),
        ))
        # Snapshot por producto (upsert en lote)
        conn.executemany("""
            INSERT OR REPLACE INTO inventario_historico_productos
                (fecha, cve_producto, cve_sucursal, sucursal, descripcion,
                 existencia, costo_promedio, precio1, precio2, precio3)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (hoy_str,
             str(r["cve_producto"]),
             int(r["Cve_Sucursal"]),
             (r["sucursal"] or "").strip(),
             (r["descripcion"] or "").strip(),
             round(float(r["existencia"] or 0), 2),
             round(float(r["costo_promedio"] or 0), 4),
             round(float(r["precio1"] or 0), 4),
             round(float(r["precio2"] or 0), 4),
             round(float(r["precio3"] or 0), 4))
            for r in (rows_prod or [])
        ])
        conn.commit()
        conn.close()
        log.info(f"--- Snapshot inventario guardado para {hoy_str} ({len(rows_prod or [])} filas de producto) ---")

    except Exception as e:
        log.error(f"Error en snapshot inventario: {e}")


def main() -> None:
    inicio = datetime.now()
    log.info(f"=== Cron refresh iniciado: {inicio.strftime('%Y-%m-%d %H:%M:%S')} ===")

    sucursales = _get_sucursales()
    log.info(f"Sucursales a procesar: {sucursales}")

    errores = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futuros = {pool.submit(refresh_sucursal, cve): cve for cve in sucursales}
        for futuro in as_completed(futuros):
            cve = futuros[futuro]
            try:
                futuro.result()
                log.info(f"Sucursal {cve} completada")
            except Exception as e:
                log.error(f"Error en sucursal {cve}: {e}")
                errores.append((cve, str(e)))

    # Snapshot de inventario al cierre del día
    try:
        guardar_snapshot_inventario()
    except Exception as e:
        log.error(f"Error en snapshot inventario: {e}")

    # Precargar prompt de productos para Whisper (reconocimiento de nombres farmacéuticos)
    try:
        _chat_router._whisper_product_prompt()
        log.info("Whisper product prompt precargado OK")
    except Exception as e:
        log.error(f"Error precargando Whisper prompt: {e}")

    # Limpiar conversaciones e historial de IA con más de 60 días
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM mensajes WHERE conversacion_id IN "
                "(SELECT id FROM conversaciones WHERE creado_at < date('now', '-60 days'))"
            )
            cur.execute(
                "DELETE FROM conversaciones WHERE creado_at < date('now', '-60 days')"
            )
            conn.commit()
            log.info(f"Limpieza historial: {cur.rowcount} conversaciones eliminadas (>60 días)")
    except Exception as e:
        log.error(f"Error en limpieza historial: {e}")

    # Limpiar dashboards guardados con más de 90 días
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM dashboards WHERE creado_en < date('now', '-90 days')"
            )
            conn.commit()
            log.info(f"Limpieza dashboards: {cur.rowcount} dashboards eliminados (>90 días)")
    except Exception as e:
        log.error(f"Error en limpieza dashboards: {e}")

    # Geocodificación del mapa — al final para no interferir con el refresh principal
    try:
        refresh_geocodificacion()
    except Exception as e:
        log.error(f"Error en geocodificación mapa: {e}")

    duracion = (datetime.now() - inicio).total_seconds()
    log.info(f"=== Cron refresh terminado en {duracion:.1f}s — errores: {len(errores)} ===")
    if errores:
        for cve, err in errores:
            log.error(f"  Sucursal {cve}: {err}")


if __name__ == "__main__":
    main()
