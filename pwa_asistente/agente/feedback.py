# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/feedback.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Registro persistente de feedback del usuario (👍 / 👎).

Cuando el usuario marca una respuesta como incorrecta se guarda
la pregunta y la respuesta para inyectarlos en el system prompt
como ejemplos negativos — el agente aprende qué evitar.

Archivo de datos: feedback_respuestas.json (gitignoreado).
"""
import json
from datetime import datetime
from pathlib import Path

_ARCHIVO = Path(__file__).parent / "feedback_respuestas.json"
_datos: list = []

# Cuántos negativos recientes inyectar en el prompt (los más recientes)
_MAX_NEGATIVOS_PROMPT = 5


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


# ── API pública ───────────────────────────────────────────────────────────────

def registrar(job_id: int, tipo: str, pregunta: str, respuesta: str) -> None:
    """
    Guarda el feedback del usuario.

    Args:
        job_id   (int): ID del job asociado a la respuesta.
        tipo     (str): 'positivo' o 'negativo'.
        pregunta (str): Pregunta original del usuario.
        respuesta(str): Respuesta que generó el agente.
    """
    entrada = {
        "job_id":    job_id,
        "tipo":      tipo,
        "pregunta":  pregunta,
        "respuesta": respuesta[:500],  # Truncar para no inflar el prompt
        "fecha":     datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _datos.append(entrada)
    _guardar()
    print(f"[feedback] {tipo.upper()} registrado — job {job_id}", flush=True)


def como_bloque_prompt() -> str:
    """
    Devuelve texto con los últimos negativos para inyectar en el system prompt.
    Retorna cadena vacía si no hay negativos.
    """
    negativos = [e for e in _datos if e.get("tipo") == "negativo"]
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
