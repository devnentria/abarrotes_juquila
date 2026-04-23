"""
Benchmark de tiempos de respuesta — Suite Analítica
Compara gpt-4o-mini, gpt-5-nano y gpt-5-mini con una pregunta típica del agente.
"""
import time
from openai import OpenAI
from shared.config import OPENAI_API_KEY
from pwa_asistente.agente import ejecutor

client = OpenAI(api_key=OPENAI_API_KEY)

MODELOS = ["gpt-4o-mini", "gpt-5-nano", "gpt-5-mini"]

SYSTEM = """Eres un agente analítico de ventas. Tienes acceso a una herramienta SQL para consultar el ERP."""

PREGUNTA = "¿Cuánto se vendió en CDMX el mes pasado?"

TOOL = ejecutor.TOOL


def medir(modelo: str, n: int = 3) -> list[float]:
    tiempos = []
    for i in range(n):
        mensajes = [
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": PREGUNTA},
        ]
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=modelo,
                messages=mensajes,
                tools=[TOOL],
                tool_choice="auto",
                timeout=60,
            )
            elapsed = time.time() - t0
            msg = resp.choices[0].message
            # Si hizo tool call, ejecutar y hacer segunda llamada
            if msg.tool_calls:
                import json
                mensajes.append(msg)
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                        filas = ejecutor.run(args["sql"])
                        import json as _j
                        contenido = _j.dumps(filas, ensure_ascii=False, default=str) if filas else "Sin resultados."
                    except Exception as e:
                        contenido = f"Error: {e}"
                    mensajes.append({"role": "tool", "tool_call_id": tc.id, "content": contenido})
                t1 = time.time()
                client.chat.completions.create(model=modelo, messages=mensajes, timeout=60)
                elapsed = time.time() - t0
            tiempos.append(elapsed)
            print(f"  [{modelo}] intento {i+1}: {elapsed:.2f}s")
        except Exception as e:
            print(f"  [{modelo}] ERROR: {e}")
            tiempos.append(None)
    return tiempos


if __name__ == "__main__":
    print(f"\nPregunta de prueba: '{PREGUNTA}'\n")
    resultados = {}
    for modelo in MODELOS:
        print(f"▶ {modelo}")
        tiempos = medir(modelo)
        validos = [t for t in tiempos if t is not None]
        if validos:
            promedio = sum(validos) / len(validos)
            resultados[modelo] = promedio
            print(f"  → Promedio: {promedio:.2f}s\n")
        else:
            resultados[modelo] = None
            print(f"  → Sin resultados (modelo no disponible?)\n")

    print("=" * 45)
    print(f"{'Modelo':<20} {'Promedio':>10}")
    print("-" * 45)
    for m, t in sorted(resultados.items(), key=lambda x: x[1] or 999):
        val = f"{t:.2f}s" if t else "ERROR"
        print(f"{m:<20} {val:>10}")
    print("=" * 45)
