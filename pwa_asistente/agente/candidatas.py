# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/candidatas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.1.0
# ============================================================
"""
Detector de preguntas frecuentes candidatas a función predefinida.

Cada pregunta que pasa por el agente dinámico se normaliza eliminando
nombres propios y períodos, dejando solo la estructura:
  "¿cuánto vendió Violeta este mes?"  →  "cuánto vendió [nombre] [periodo]"

Cuando un patrón supera UMBRAL_CANDIDATA, se alerta en el log.

Archivo de datos: candidatas_funciones.json (gitignoreado).
"""
import re
from datetime import datetime
from pathlib import Path
from pwa_asistente.agente._persistent import PersistentStore

_store = PersistentStore(
    "candidatas_funciones.json",
    {"patrones": {}},
    Path(__file__).parent,
)

UMBRAL_CANDIDATA = 10
_MAX_PATRONES    = 500  # Evita crecimiento infinito del dict en memoria

# ── Normalización ─────────────────────────────────────────────────────────────

_SUSTITUCIONES = [
    # Meses → [periodo]  (antes del .lower())
    (re.compile(
        r'\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto'
        r'|septiembre|octubre|noviembre|diciembre)\b', re.IGNORECASE),
     '[periodo]'),
    # Número + unidad temporal → [periodo]
    (re.compile(r'\b\d+\s*(días?|semanas?|meses?|años?)\b', re.IGNORECASE), '[periodo]'),
    # Referencias temporales comunes → [periodo]
    (re.compile(
        r'\b(este\s+mes|mes\s+actual|mes\s+pasado|mes\s+anterior|[úu]ltimo\s+mes'
        r'|este\s+a[ñn]o|a[ñn]o\s+pasado|a[ñn]o\s+anterior|hoy|ayer)\b',
        re.IGNORECASE),
     '[periodo]'),
]

_RE_PUNTUACION = re.compile(r'[¿?¡!.,;:]')
# Detectar nombre propio: palabra con mayúscula inicial de 3+ chars
# Se aplica ANTES de .lower() para que funcione correctamente
_RE_NOMBRE_PROPIO = re.compile(r'\b[A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]{2,}\b')
# Palabras comunes en mayúscula al inicio de oración que NO son nombres propios
_PALABRAS_COMUNES = frozenset({
    'cuánto', 'cuántos', 'cuántas', 'cuál', 'cuáles', 'qué', 'quién',
    'quiénes', 'cómo', 'dónde', 'cuándo', 'hay', 'son', 'está', 'están',
    'tiene', 'tienen', 'hay', 'dime', 'dame', 'muéstrame', 'lista',
})


def _normalizar(pregunta: str) -> str:
    txt = _RE_PUNTUACION.sub('', pregunta.strip())
    # Sustituir períodos (antes de bajar a minúsculas)
    for patron, reemplazo in _SUSTITUCIONES:
        txt = patron.sub(reemplazo, txt)
    # Sustituir nombres propios (antes de bajar a minúsculas)
    def _reemplazar_nombre(m: re.Match) -> str:
        palabra = m.group(0)
        if palabra.lower() in _PALABRAS_COMUNES:
            return palabra
        return '[nombre]'
    txt = _RE_NOMBRE_PROPIO.sub(_reemplazar_nombre, txt)
    txt = txt.lower()
    # Colapsar placeholders repetidos y espacios
    txt = re.sub(r'\[nombre\](\s+\[nombre\])+', '[nombre]', txt)
    txt = re.sub(r'\[periodo\](\s+\[periodo\])+', '[periodo]', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt


# ── API pública ───────────────────────────────────────────────────────────────

def registrar(pregunta: str) -> None:
    """
    Registra una pregunta que llegó al agente dinámico.
    Debe llamarse DESPUÉS del cache check para no desperdiciar trabajo.
    """
    patron = _normalizar(pregunta)
    if len(patron) < 10:
        return

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    with _store.lock:
        patrones = _store.datos["patrones"]
        if patron not in patrones:
            if len(patrones) >= _MAX_PATRONES:
                min_key = min(patrones, key=lambda k: patrones[k]["count"])
                del patrones[min_key]
            patrones[patron] = {"count": 0, "ejemplo": pregunta, "ultima_vez": ahora}
        entrada = patrones[patron]
        entrada["count"]     += 1
        entrada["ultima_vez"] = ahora
        _store.guardar()

    count = entrada["count"]
    if count == UMBRAL_CANDIDATA or (count > UMBRAL_CANDIDATA and count % 10 == 0):
        print(
            f"[candidatas] ⚠ Patrón frecuente ({count}x): \"{patron}\"\n"
            f"             Ejemplo real: \"{pregunta}\"",
            flush=True,
        )


def top(n: int = 10) -> list[dict]:
    ordenados = sorted(
        _store.datos["patrones"].items(),
        key=lambda x: x[1]["count"],
        reverse=True,
    )
    return [{"patron": k, **v} for k, v in ordenados[:n]]
