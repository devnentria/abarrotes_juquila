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
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"postalcode": cp, "country": "MX", "format": "json", "limit": 1},
            headers={"User-Agent": "SuiteAnaliticaNentria/1.0"},
            timeout=8,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        log.warning(f"  Nominatim error CP {cp}: {e}")
    return None


def _top_cps_mes(anio: int, mes: int) -> list:
    """Devuelve los top 150 CPs con más ventas en el mes dado."""
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


def refresh_geocodificacion() -> None:
    """Geocodifica los CPs sin coordenadas del mes actual y los 6 anteriores."""
    log.info("--- Geocodificación mapa iniciada ---")
    hoy = date.today()

    # Construir lista de (anio, mes): mes actual + 6 anteriores
    meses = []
    a, m = hoy.year, hoy.month
    for _ in range(7):
        meses.append((a, m))
        m -= 1
        if m == 0:
            m = 12
            a -= 1

    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cp_coords (
            cp TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL,
            cached_at TEXT DEFAULT (datetime('now')))
    """)
    conn.commit()

    total_nuevos = 0
    for anio, mes in meses:
        cps = _top_cps_mes(anio, mes)
        if not cps:
            continue
        placeholders = ",".join(["?"] * len(cps))
        ya_cached = {
            r[0] for r in conn.execute(
                f"SELECT cp FROM cp_coords WHERE cp IN ({placeholders})", cps
            ).fetchall()
        }
        faltantes = [cp for cp in cps if cp not in ya_cached]
        if not faltantes:
            log.info(f"  {anio}-{mes:02d}: todos los CPs ya en caché")
            continue
        log.info(f"  {anio}-{mes:02d}: geocodificando {len(faltantes)} CPs nuevos...")
        for cp in faltantes:
            coords = _geocode_cp(cp)
            if coords:
                lat, lng = coords
                conn.execute(
                    "INSERT OR REPLACE INTO cp_coords (cp, lat, lng) VALUES (?, ?, ?)",
                    (cp, lat, lng),
                )
                conn.commit()
                total_nuevos += 1
            time.sleep(1.1)  # Respetar rate limit Nominatim

    conn.close()
    log.info(f"--- Geocodificación terminada — {total_nuevos} CPs nuevos guardados ---")


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
