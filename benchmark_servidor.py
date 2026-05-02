#!/usr/bin/env python3
"""
Benchmark de preguntas contra servidor de PRODUCCIÓN — Suite Analítica Supra
Mismas preguntas que benchmark_preguntas.py pero apunta a supra.nentria.com
Resultado: pruebaservidor.md
"""
import time
import datetime
import requests

BASE     = "https://supra.nentria.com"
EMAIL    = "admin_nentria"
PASSWORD = "Nentria01"
SALIDA   = "pruebaservidor.md"

PREGUNTAS = [
    ("Q1  Ventas del período",          "¿Cuánto se vendió el mes pasado?"),
    ("Q2  Precio histórico",            "¿A cuánto se vendió el Lorelin en enero?"),
    ("Q3a Existencias actuales",        "¿Cuántas piezas hay del Ozempic en cada sucursal?"),
    ("Q3b Existencias en fecha",        "¿Cuántas piezas había del Ozempic el 15 de enero?"),
    ("Q4  Pedidos activos",             "¿Cuántos pedidos hay pendientes actualmente?"),
    ("Q5  Médicos sin cédula",          "¿Qué médicos no tienen cédula registrada?"),
    ("Q6a Último costo",                "¿Cuál es el último costo del Lorelin?"),
    ("Q6b Costo en mes específico",     "¿A cuánto se compró el Ozempic en enero?"),
    ("Q7  Clientes frecuentes",         "¿Cuáles son los 10 clientes con más compras del año pasado?"),
    ("Q8  Caducidades",                 "¿Qué productos caducan en los próximos 30 días?"),
    ("Q9  Piezas compradas",            "¿Cuántas piezas del Ozempic se compraron en enero?"),
    ("Q10 Proveedores",                 "¿Qué proveedor surte el Ozempic?"),
    ("EXTRA Margen bruto",              "¿Cuál fue el margen bruto de ventas del mes pasado?"),
    ("EXTRA Stock crítico",             "¿Qué productos tienen 5 piezas o menos en inventario?"),
    ("EXTRA Médicos por prescripción",  "¿Qué médico generó más ventas por prescripción el año pasado?"),
]


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", data={"username": EMAIL, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise ValueError(f"Login fallido: {r.json()}")
    return token


def preguntar(token: str, conv_id: int, texto: str) -> tuple:
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.time()

    job = requests.post(
        f"{BASE}/api/chat/mensaje/async",
        json={"conversacion_id": conv_id, "mensaje": texto},
        headers=headers,
        timeout=20,
    )
    job.raise_for_status()
    data = job.json()

    if data.get("estado") == "done":
        return data.get("respuesta", ""), time.time() - t0, data.get("area", "")

    job_id = data["job_id"]
    while True:
        estado = requests.get(f"{BASE}/api/chat/job/{job_id}", headers=headers, timeout=15)
        estado.raise_for_status()
        data = estado.json()
        if data["estado"] in ("done", "error"):
            break
        time.sleep(0.5)

    return data.get("respuesta", ""), time.time() - t0, data.get("area", "")


def main():
    print(f"Conectando a {BASE}...")
    try:
        token = login()
    except Exception as e:
        print(f"❌ Login fallido: {e}")
        return

    headers = {"Authorization": f"Bearer {token}"}
    print("✅ Login OK\n")

    resultados = []
    tiempos    = []

    for i, (etiqueta, pregunta) in enumerate(PREGUNTAS, 1):
        conv = requests.post(f"{BASE}/api/chat/conversaciones", headers=headers, timeout=10)
        conv_id = conv.json()["id"]

        try:
            respuesta, seg, area = preguntar(token, conv_id, pregunta)
            tiempos.append(seg)
            icono = "🟢" if seg < 10 else "🟡" if seg < 20 else "🔴"
            print(f"{icono} [{i:02d}/{len(PREGUNTAS)}] {etiqueta:<32} {seg:>5.1f}s  ({area})")
            resultados.append((etiqueta, pregunta, respuesta, seg, area, None))
        except Exception as e:
            print(f"❌ [{i:02d}/{len(PREGUNTAS)}] {etiqueta:<32}  ERROR: {e}")
            resultados.append((etiqueta, pregunta, "", 0, "error", str(e)))

        requests.delete(f"{BASE}/api/chat/conversaciones/{conv_id}", headers=headers, timeout=10)

    ahora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [
        f"# Prueba en Servidor de Producción — Suite Analítica Supra",
        f"**Fecha:** {ahora}  |  **Servidor:** {BASE}",
        "",
    ]

    if tiempos:
        promedio = sum(tiempos) / len(tiempos)
        lineas += [
            "## Resumen de tiempos",
            "",
            f"| Métrica | Valor |",
            f"|---------|-------|",
            f"| Promedio | {promedio:.1f}s |",
            f"| Más rápida | {min(tiempos):.1f}s |",
            f"| Más lenta | {max(tiempos):.1f}s |",
            f"| Total preguntas | {len(tiempos)} |",
            "",
        ]

    lineas.append("---")
    lineas.append("")

    for etiqueta, pregunta, respuesta, seg, area, error in resultados:
        icono = "🟢" if seg < 10 else "🟡" if seg < 20 else "🔴" if seg > 0 else "❌"
        lineas += [
            f"## {etiqueta}",
            f"**Tiempo:** {seg:.1f}s  |  **Área:** `{area}`  {icono}",
            "",
            f"**Pregunta:** {pregunta}",
            "",
            "**Respuesta:**",
            "",
            respuesta if respuesta else f"_ERROR: {error}_",
            "",
            "---",
            "",
        ]

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))

    print(f"\n{'─'*60}")
    if tiempos:
        print(f"Promedio: {sum(tiempos)/len(tiempos):.1f}s  |  "
              f"Rápida: {min(tiempos):.1f}s  |  Lenta: {max(tiempos):.1f}s")
    print(f"Resultado guardado en: {SALIDA}")


if __name__ == "__main__":
    main()
