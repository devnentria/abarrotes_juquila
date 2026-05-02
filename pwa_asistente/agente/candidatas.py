# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/candidatas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Detector de preguntas frecuentes candidatas a función predefinida.

Cada pregunta que pasa por el agente dinámico se normaliza:
  "¿cuánto vendió Violeta este mes?"  →  "cuánto vendió [vendedor] este mes"
  "¿existencias de Ozempic en CDMX?" →  "existencias de [producto] en [sucursal]"

Cuando un patrón llega a UMBRAL_CANDIDATA consultas, se registra en el log
como candidata a convertirse en función predefinida.

Archivo de datos: candidatas_funciones.json (gitignoreado).
"""
import json
import re
from datetime import datetime
from pathlib import Path

_ARCHIVO   = Path(__file__).parent / "candidatas_funciones.json"
_datos: dict = {"patrones": {}}

UMBRAL_CANDIDATA = 10  # veces que debe aparecer para ser candidata


def _cargar() -> None:
    global _datos
    if _ARCHIVO.exists():
        try:
            _datos = json.loads(_ARCHIVO.read_text(encoding="utf-8"))
        except Exception:
            pass


def _guardar() -> None:
    _ARCHIVO.write_text(
        json.dumps(_datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_cargar()

# ── Normalización ─────────────────────────────────────────────────────────────

# Tokens que se reemplazan por su placeholder genérico
_SUSTITUCIONES = [
    # Meses en español → [periodo]
    (re.compile(
        r'\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto'
        r'|septiembre|octubre|noviembre|diciembre)\b', re.IGNORECASE),
     '[periodo]'),
    # Número + días/semanas/meses/años → [periodo]
    (re.compile(r'\b\d+\s*(días?|semanas?|meses?|años?)\b', re.IGNORECASE), '[periodo]'),
    # Referencias temporales comunes → [periodo]
    (re.compile(
        r'\b(este mes|mes actual|mes pasado|mes anterior|último mes'
        r'|este año|año pasado|año anterior|hoy|ayer)\b', re.IGNORECASE),
     '[periodo]'),
    # Nombre propio seguido de mayúscula (productos, clientes, médicos) → placeholder
    # Detecta palabras con mayúscula inicial que no son inicio de pregunta
    (re.compile(r'(?<=[a-záéíóúñ\s])\b([A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,})\b'), '[nombre]'),
    # Números solos → [numero]
    (re.compile(r'\b\d+\b'), '[numero]'),
]

_RE_PUNTUACION = re.compile(r'[¿?¡!.,;:]')


def _normalizar(pregunta: str) -> str:
    """Convierte una pregunta concreta en un patrón genérico."""
    txt = _RE_PUNTUACION.sub('', pregunta.strip().lower())
    for patron, reemplazo in _SUSTITUCIONES:
        txt = patron.sub(reemplazo, txt)
    # Colapsar espacios y placeholders repetidos
    txt = re.sub(r'\[nombre\](\s+\[nombre\])+', '[nombre]', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt


# ── API pública ───────────────────────────────────────────────────────────────

def registrar(pregunta: str) -> None:
    """
    Registra una pregunta que llegó al agente dinámico (no predefinida).
    Incrementa el contador del patrón normalizado y alerta si supera el umbral.

    Args:
        pregunta (str): Mensaje original del usuario.
    """
    patron = _normalizar(pregunta)
    if len(patron) < 10:
        return  # Ignorar preguntas muy cortas o saludos

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    if patron not in _datos["patrones"]:
        _datos["patrones"][patron] = {"count": 0, "ejemplo": pregunta, "ultima_vez": ahora}

    entrada = _datos["patrones"][patron]
    entrada["count"]     += 1
    entrada["ultima_vez"] = ahora
    _guardar()

    count = entrada["count"]
    if count == UMBRAL_CANDIDATA or (count > UMBRAL_CANDIDATA and count % 10 == 0):
        print(
            f"[candidatas] ⚠ Patrón frecuente ({count}x): \"{patron}\"\n"
            f"             Ejemplo real: \"{pregunta}\"",
            flush=True,
        )


def top(n: int = 10) -> list[dict]:
    """
    Devuelve los N patrones más frecuentes, ordenados por count desc.

    Args:
        n (int): Cantidad de patrones a devolver.

    Returns:
        list[dict]: Lista de {patron, count, ejemplo, ultima_vez}.
    """
    ordenados = sorted(
        _datos["patrones"].items(),
        key=lambda x: x[1]["count"],
        reverse=True,
    )
    return [
        {"patron": k, **v}
        for k, v in ordenados[:n]
    ]
