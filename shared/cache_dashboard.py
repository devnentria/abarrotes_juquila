# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : cache_dashboard.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Cache de dashboard en SQLite.

Guarda snapshots diarios del stock y resúmenes de IA para que
los endpoints sirvan resultados instantáneos sin tocar SQL Server
ni llamar a OpenAI en cada visita.

El cron job (cron_refresh.py) regenera el cache a las 2am.
El botón de reload en el frontend (regenerar=1) lo invalida y
recalcula en tiempo real.
"""
import json
from datetime import date
from typing import Optional

from shared.database_local import get_connection


def get(clave: str) -> Optional[dict]:
    """
    Retorna el payload cacheado si fue generado hoy.

    Args:
        clave (str): Identificador del cache (ej. "stock_detalle_1").

    Returns:
        Optional[dict]: Payload como dict, o None si no existe o es de otro día.
    """
    hoy = date.today().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM cache_dashboard WHERE clave = ? AND DATE(generado_en) = ?",
            (clave, hoy),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def set(clave: str, payload: dict) -> None:
    """
    Guarda o reemplaza el payload en cache con timestamp actual.

    Args:
        clave   (str):  Identificador del cache.
        payload (dict): Datos a guardar (se serializa a JSON).
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache_dashboard (clave, payload, generado_en) "
            "VALUES (?, ?, datetime('now'))",
            (clave, json.dumps(payload, ensure_ascii=False, default=str)),
        )


def invalidate(clave: str) -> None:
    """
    Elimina una entrada del cache para forzar regeneración.

    Args:
        clave (str): Identificador del cache a eliminar.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM cache_dashboard WHERE clave = ?", (clave,))
