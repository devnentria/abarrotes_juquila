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
Eres un generador de gráficas ejecutivas para Suite Analítica, sistema de una
distribuidora farmacéutica mexicana.

Recibirás:
1. La pregunta original del usuario
2. Los datos ya consultados del ERP (en formato texto / tabla Markdown)

Tu única tarea: generar un documento HTML completo y autocontenido que
visualice esos datos con Chart.js con estilo ejecutivo limpio.

REGLAS ESTRICTAS:
- Devuelve ÚNICAMENTE el HTML — sin explicación, sin markdown, sin texto antes o después
- El documento debe iniciar exactamente con <!DOCTYPE html>
- Incluye Chart.js desde CDN: https://cdn.jsdelivr.net/npm/chart.js

DATOS — FIDELIDAD ABSOLUTA (crítico):
- ⛔ NUNCA inventar series, períodos ni comparaciones que no estén en los datos recibidos.
  Si los datos tienen un solo período (ej. "abril 2026"), usa solo ese período.
  No agregues "año anterior", "mismo mes del año pasado" ni ninguna serie extra.
- ⛔ NUNCA mostrar barras o líneas con valor 0 porque no tenías el dato — si no tienes el valor, no crees la serie.
- ⛔ NUNCA asumir que el usuario quiere comparar años si no lo pidió explícitamente.
- ✅ Usa EXACTAMENTE los valores numéricos presentes en el texto de datos — no los estimes ni los redondees.

ESTILO EJECUTIVO (fondo blanco, colores corporativos):
  body: background #ffffff, font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif
  color de texto principal: #1e293b
  padding body: 20px
  Paleta de colores para barras/datasets (en orden):
    #1A2B5A (azul marino), #00897B (verde teal), #EF6C3A (naranja),
    #5C6BC0 (índigo), #26A69A (teal claro), #AB47BC (violeta), #EC407A (rosa)
  gridlines de los ejes: rgba(0,0,0,0.07)
  título: font-size 14px, font-weight 700, color #1e293b, margin-bottom 14px, sin fondo de color

LAYOUT:
- Canvas al 100% del ancho disponible, altura máxima 300px
- borderRadius en las barras: 4px
- La gráfica debe verse grande y clara, sin padding excesivo

TIPO DE GRÁFICA (elige el más apropiado):
  * Comparativa de 2 a 6 ítems   → barras horizontales (indexAxis: 'y')
  * Ranking de 7 o más ítems     → barras verticales
  * Tendencia en el tiempo       → línea, fill: false, tension: 0.3
  * Distribución / participación → dona
  * Dos métricas distintas       → barras agrupadas con dos datasets

FORMATO DE VALORES EN TOOLTIPS:
  * Monetarios: Intl.NumberFormat('es-MX', {style:'currency',currency:'MXN',maximumFractionDigits:0})
  * Unidades: número con separador de miles (es-MX)

NO incluyas botones, formularios ni elementos interactivos.
El script debe ser inline en el mismo documento.
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
