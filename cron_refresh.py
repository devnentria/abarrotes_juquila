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
import logging
from datetime import datetime
from pathlib import Path

# Asegurar que el proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent))

from shared import cache_dashboard as _cache
from shared.database import query as db_query
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


def main() -> None:
    inicio = datetime.now()
    log.info(f"=== Cron refresh iniciado: {inicio.strftime('%Y-%m-%d %H:%M:%S')} ===")

    sucursales = _get_sucursales()
    log.info(f"Sucursales a procesar: {sucursales}")

    errores = []
    for cve in sucursales:
        try:
            log.info(f"Procesando sucursal {cve}...")
            refresh_sucursal(cve)
            log.info(f"Sucursal {cve} completada")
        except Exception as e:
            log.error(f"Error en sucursal {cve}: {e}")
            errores.append((cve, str(e)))

    duracion = (datetime.now() - inicio).total_seconds()
    log.info(f"=== Cron refresh terminado en {duracion:.1f}s — errores: {len(errores)} ===")
    if errores:
        for cve, err in errores:
            log.error(f"  Sucursal {cve}: {err}")


if __name__ == "__main__":
    main()
