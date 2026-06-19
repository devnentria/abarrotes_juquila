# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/grafica.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Generador de gráficas dinámicas con IA.

Toma el texto de respuesta de un especialista (que ya contiene los datos
en markdown) y la pregunta original, y genera un bloque HTML autocontenido
con Chart.js que el frontend puede renderizar en un iframe sandbox.

Uso:
    from pwa_asistente.agente.grafica import generar
    html, tp, tc = generar(texto_datos, pregunta_original)
"""
from __future__ import annotations

from openai import OpenAI
from shared.config import OPENAI_API_KEY, OPENAI_MODEL

_client = OpenAI(api_key=OPENAI_API_KEY)

# Prefijo especial para que el frontend identifique respuestas con gráfica
CHART_PREFIX = "CHART_HTML::"

_SYSTEM = """
Eres un generador de gráficas para el asistente analítico de Suite Analítica,
sistema de una distribuidora farmacéutica mexicana.

Recibirás:
1. La pregunta original del usuario
2. Los datos ya consultados del ERP (en formato texto / tabla Markdown)

Tu única tarea: generar un documento HTML completo y autocontenido que
visualice esos datos con Chart.js de forma clara y profesional.

REGLAS ESTRICTAS:
- Devuelve ÚNICAMENTE el HTML — sin explicación, sin markdown, sin texto extra antes o después
- El documento debe iniciar exactamente con <!DOCTYPE html>
- Incluye Chart.js desde CDN: https://cdn.jsdelivr.net/npm/chart.js
- Tema oscuro coherente con el dashboard:
    fondo body: #0f1117
    color de texto: #e2e8f0
    color principal: #2dd4bf  (teal)
    color secundario: #6366f1 (índigo)
    color terciario: #f59e0b  (ámbar)
    gridlines: rgba(255,255,255,0.08)
- Tamaño: canvas al 100% del ancho, máximo 320px de alto, padding 16px en body
- Elige el tipo de gráfica más apropiado según los datos:
    * Comparativa de 2 a 5 ítems          → barras horizontales
    * Ranking de 6 o más ítems            → barras verticales
    * Tendencia en el tiempo (meses/días) → línea con fill suave
    * Distribución o participación %      → dona
- Formatea los valores monetarios con $ y separadores de miles en los tooltips
- El título de la gráfica debe describir lo que se muestra (corto, claro)
- NO incluyas botones, formularios ni elementos interactivos
- El script debe estar inline en el mismo documento
"""


def generar(texto_datos: str, pregunta: str, model: str = OPENAI_MODEL) -> tuple[str, int, int]:
    """
    Genera un HTML autocontenido con Chart.js a partir de los datos del especialista.

    Args:
        texto_datos (str): Respuesta del especialista con datos en markdown.
        pregunta    (str): Pregunta original del usuario.
        model       (str): Modelo OpenAI a usar.

    Returns:
        tuple[str, int, int]: (html_completo, tokens_prompt, tokens_completion)
    """
    prompt_usuario = (
        f"Pregunta del usuario: {pregunta}\n\n"
        f"Datos del ERP:\n{texto_datos}"
    )

    resp = _client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt_usuario},
        ],
        temperature=0.2,
    )

    html = resp.choices[0].message.content.strip()

    # Limpiar por si el modelo envolvió el HTML en markdown
    if html.startswith("```"):
        lines = html.split("\n")
        html  = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    if not html.startswith("<!DOCTYPE"):
        # Intentar extraer el HTML si hay texto antes
        idx = html.find("<!DOCTYPE")
        html = html[idx:] if idx != -1 else html

    tp = resp.usage.prompt_tokens     if resp.usage else 0
    tc = resp.usage.completion_tokens if resp.usage else 0

    return html, tp, tc
