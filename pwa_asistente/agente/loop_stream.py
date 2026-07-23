# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/loop_stream.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Loop agenético con streaming de la respuesta final.

Ejecuta el ciclo de tool calls normalmente (sin streaming),
y transmite la respuesta textual final chunk por chunk vía
el generador `responder_stream()`.
"""
import json
from openai import OpenAI
from shared.config import OPENAI_API_KEY, OPENAI_MODEL
from pwa_asistente.agente import ejecutor

_client = OpenAI(api_key=OPENAI_API_KEY)


def responder_stream(sistema: str, pregunta: str, historial: list[dict]):
    """
    Generador que ejecuta el loop de tool calls y hace yield de los
    chunks de texto de la respuesta final.

    Yields:
        str: fragmentos de texto de la respuesta del modelo.
    """
    mensajes = [{"role": "system", "content": sistema}]
    for msg in historial:
        mensajes.append({"role": msg["rol"], "content": msg["contenido"]})
    mensajes.append({"role": "user", "content": pregunta})

    for _ in range(5):
        # Llamada de sondeo (no streaming) para detectar tool calls
        resp = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=mensajes,
            tools=[ejecutor.TOOL],
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            # Es la respuesta final — repetir con stream=True para transmitir
            mensajes.append({"role": "assistant", "content": msg.content or ""})
            # Re-hacer la última llamada en modo stream para enviar los chunks
            stream = _client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=mensajes[:-1],  # sin el último assistant ya agregado
                tools=[ejecutor.TOOL],
                tool_choice="none",      # ya sabemos que no hay más tool calls
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            return

        # Hay tool calls — ejecutarlos y continuar
        mensajes.append(msg)
        for tc in msg.tool_calls:
            try:
                args     = json.loads(tc.function.arguments)
                filas    = ejecutor.run(args["sql"])
                contenido = (
                    json.dumps(filas, ensure_ascii=False, default=str)
                    if filas else "La consulta no devolvió resultados."
                )
            except Exception as e:
                contenido = f"Error al ejecutar la consulta: {e}"

            mensajes.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      contenido,
            })
