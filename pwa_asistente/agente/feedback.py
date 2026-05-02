# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/feedback.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.1.0
# ============================================================
"""
Registro persistente de feedback del usuario (👍 / 👎).

Los negativos se inyectan en el system prompt como ejemplos a evitar.
Se mantiene un máximo de MAX_REGISTROS entradas para evitar crecimiento infinito.

Archivo de datos: feedback_respuestas.json (gitignoreado).
"""
from datetime import datetime
from pathlib import Path
from pwa_asistente.agente._persistent import PersistentStore

_store = PersistentStore(
    "feedback_respuestas.json",
    [],
    Path(__file__).parent,
)

_MAX_NEGATIVOS_PROMPT = 5
_MAX_REGISTROS        = 200  # Evita crecimiento infinito en memoria y disco


def registrar(job_id: int, tipo: str, pregunta: str, respuesta: str) -> None:
    entrada = {
        "job_id":    job_id,
        "tipo":      tipo,
        "pregunta":  pregunta,
        "respuesta": respuesta[:500],
        "fecha":     datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _store.datos.append(entrada)
    # Mantener solo los últimos MAX_REGISTROS
    if len(_store.datos) > _MAX_REGISTROS:
        _store.datos[:] = _store.datos[-_MAX_REGISTROS:]
    _store.guardar()
    print(f"[feedback] {tipo.upper()} registrado — job {job_id}", flush=True)


def como_bloque_prompt() -> str:
    negativos = [e for e in _store.datos if e.get("tipo") == "negativo"]
    if not negativos:
        return ""

    recientes = negativos[-_MAX_NEGATIVOS_PROMPT:]
    lineas = [
        "\n\nRESPUESTAS MARCADAS COMO INCORRECTAS POR EL USUARIO — EVITAR ESTOS PATRONES:"
    ]
    for e in recientes:
        lineas.append(f'  Pregunta: "{e["pregunta"]}"')
        lineas.append(f'  Respuesta incorrecta dada: "{e["respuesta"][:200]}…"')
        lineas.append("")

    return "\n".join(lineas)
