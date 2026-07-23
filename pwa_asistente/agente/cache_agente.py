# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/cache_agente.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Caché de respuestas del agente IA.

Solo cachea consultas históricas (mes pasado, enero, 2025…).
Las consultas en tiempo real (hoy, ayer, esta semana…) nunca se cachean.
TTL: 24 horas.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared.database_local import fetch_one, execute

# ── Palabras que indican datos en tiempo real → sin caché ────────────────────
_TIEMPO_REAL = re.compile(
    r"\b(hoy|ayer|ahorita|ahora|actualmente|en este momento"
    r"|esta semana|este mes|este a[ñn]o"
    r"|pedidos activos|en curso|pendientes)\b",
    re.IGNORECASE,
)

# ── Palabras que indican datos históricos → cacheable ───────────────────────
_HISTORICO = re.compile(
    r"\b(mes pasado|semana pasada|a[ñn]o pasado"
    r"|enero|febrero|marzo|abril|mayo|junio"
    r"|julio|agosto|septiembre|octubre|noviembre|diciembre"
    r"|20\d{2})\b",
    re.IGNORECASE,
)

_TTL = timedelta(hours=24)


def es_historico(pregunta: str) -> bool:
    """
    Devuelve True si la pregunta es sobre datos históricos y puede cachearse.
    Una pregunta con palabras de tiempo real nunca se cachea aunque mencione
    términos históricos.
    """
    if _TIEMPO_REAL.search(pregunta):
        return False
    return bool(_HISTORICO.search(pregunta))


def _clave(especialista: str, pregunta: str) -> str:
    """Genera una clave de caché reproducible para la combinación especialista+pregunta."""
    normalizada = " ".join(pregunta.lower().split())
    contenido = f"{especialista}|{normalizada}"
    return hashlib.sha256(contenido.encode()).hexdigest()


def get(especialista: str, pregunta: str) -> Optional[str]:
    """
    Retorna la respuesta cacheada si existe y no ha expirado. None en caso contrario.

    Args:
        especialista (str): Nombre del especialista (ej. 'ventas').
        pregunta     (str): Pregunta del usuario.

    Returns:
        str | None: Respuesta cacheada o None.
    """
    clave = _clave(especialista, pregunta)
    row = fetch_one(
        "SELECT respuesta, creado_en FROM cache_agente WHERE clave = ?",
        (clave,),
    )
    if not row:
        return None
    creado = datetime.fromisoformat(row["creado_en"])
    if datetime.now(timezone.utc).replace(tzinfo=None) - creado > _TTL:
        execute("DELETE FROM cache_agente WHERE clave = ?", (clave,))
        return None
    return row["respuesta"]


def set(especialista: str, pregunta: str, respuesta: str) -> None:
    """
    Guarda (o reemplaza) una respuesta en caché.

    Args:
        especialista (str): Nombre del especialista.
        pregunta     (str): Pregunta del usuario.
        respuesta    (str): Respuesta generada por el modelo.
    """
    clave = _clave(especialista, pregunta)
    execute(
        "INSERT OR REPLACE INTO cache_agente (clave, respuesta, creado_en) "
        "VALUES (?, ?, ?)",
        (clave, respuesta, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
    )
