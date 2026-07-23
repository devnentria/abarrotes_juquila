# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/base_agente.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Loop agentico compartido por todos los especialistas.

Ejecuta el ciclo OpenAI → tool calls → respuesta y captura
el uso real de tokens para calcular el costo exacto de la consulta.
"""
import json
from typing import NamedTuple

from openai import OpenAI
from shared.config import OPENAI_API_KEY, OPENAI_MODEL, TEST_DATE
from datetime import date
from pwa_asistente.agente import ejecutor
from pwa_asistente.agente import cache_agente
from pwa_asistente.agente import sql_blacklist
from pwa_asistente.agente import nombres_cache
from pwa_asistente.agente import feedback as _feedback
from pwa_asistente.agente import candidatas

_client = OpenAI(api_key=OPENAI_API_KEY)

_MAX_ITER = 10


class RespuestaIA(NamedTuple):
    texto:             str
    tokens_prompt:     int
    tokens_completion: int


def ejecutar(system: str, pregunta: str, historial: list[dict], area: str, model: str = OPENAI_MODEL, prefijo: str = "") -> RespuestaIA:
    """
    Ejecuta el loop agentico OpenAI para un especialista.

    Retorna la respuesta en texto y el uso real de tokens acumulado
    en todas las iteraciones del loop (útil para calcular costo exacto).

    Args:
        system    (str):        System prompt del especialista.
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Mensajes previos [{rol, contenido}].
        area      (str):        Nombre del especialista (para caché).

    Returns:
        RespuestaIA: texto + tokens_prompt + tokens_completion
    """
    # Caché solo para consultas históricas
    if cache_agente.es_historico(pregunta):
        cached = cache_agente.get(area, pregunta)
        if cached:
            return RespuestaIA(texto=cached, tokens_prompt=0, tokens_completion=0)

    # Registrar candidata solo si no fue cache hit
    candidatas.registrar(pregunta)

    _fecha = TEST_DATE if TEST_DATE else date.today().strftime("%Y-%m-%d")
    mensajes = [
        {"role": "system", "content": (
            system
            + f"\n\nFECHA ACTUAL: {_fecha}."
            + sql_blacklist.como_bloque_prompt()
            + nombres_cache.como_bloque_prompt()
            + _feedback.como_bloque_prompt()
        )}
    ]
    # Limitar historial a los últimos 20 mensajes para no exceder el contexto del modelo
    for msg in historial[-20:]:
        mensajes.append({"role": msg["rol"], "content": msg["contenido"]})
    contenido_usuario = f"{prefijo}\n\n{pregunta}" if prefijo else pregunta
    mensajes.append({"role": "user", "content": contenido_usuario})

    total_prompt     = 0
    total_completion = 0

    for _ in range(_MAX_ITER):
        resp = _client.chat.completions.create(
            model=model,
            messages=mensajes,
            tools=[ejecutor.TOOL],
            tool_choice="auto",
        )

        # Acumular tokens de esta iteración
        if resp.usage:
            total_prompt     += resp.usage.prompt_tokens
            total_completion += resp.usage.completion_tokens

        msg = resp.choices[0].message

        if not msg.tool_calls:
            resultado = msg.content or "No pude generar una respuesta."
            if cache_agente.es_historico(pregunta):
                cache_agente.set(area, pregunta, resultado)
            return RespuestaIA(
                texto=resultado,
                tokens_prompt=total_prompt,
                tokens_completion=total_completion,
            )

        mensajes.append(msg)

        for tc in msg.tool_calls:
            try:
                args  = json.loads(tc.function.arguments)
                print(f"[agente-sql] {args['sql']}", flush=True)
                filas = ejecutor.run(args["sql"])
                contenido = (
                    json.dumps(filas, ensure_ascii=False, default=str)
                    if filas else "La consulta no devolvió resultados."
                )
            except Exception as e:
                contenido = f"Error al ejecutar la consulta: {e}"

            mensajes.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": contenido,
            })

    return RespuestaIA(
        texto="Ups, parece que no pudimos procesar esta solicitud. Comunícate con tu proveedor.",
        tokens_prompt=total_prompt,
        tokens_completion=total_completion,
    )
