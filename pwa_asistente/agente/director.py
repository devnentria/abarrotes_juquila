# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/director.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Agente Director — enruta la pregunta al especialista correcto.

No sabe de SQL ni de negocio. Su único trabajo es leer
la pregunta y decidir a qué área pertenece.
"""
from openai import OpenAI
from shared.config import OPENAI_API_KEY, OPENAI_MODEL

_client = OpenAI(api_key=OPENAI_API_KEY)

AREAS = frozenset(["ventas", "inventario", "pedidos", "medicos", "clientes", "mixto"])

_SYSTEM = """
Eres el director del asistente analítico de Suite Analítica, un sistema para
una empresa distribuidora farmacéutica.

Tu ÚNICA tarea es clasificar la pregunta del usuario en UNA de estas áreas:

  ventas     → facturas, ventas, importes, ingresos, comparativos de ventas,
               productos más vendidos, rendimiento de sucursales o vendedores
  inventario → stock, existencias, caducidades, lotes, productos sin existencia,
               mayor existencia, caducidad próxima
  pedidos    → pedidos activos, pendientes, antigüedad de pedidos, pedidos por sucursal
  medicos    → médicos, doctores, cédulas, duplicados de médicos, asignación a vendedor
  clientes   → clientes, compradores, historial de compras de un cliente,
               clientes frecuentes, quién compra más
  mixto      → la pregunta involucra claramente 2 o más áreas al mismo tiempo,
               O preguntas sobre proveedores, laboratorios, costos de productos,
               qué proveedor surte X producto, listado de proveedores
               (ej: ventas + pedidos, clientes + inventario, costo de un medicamento)

Responde ÚNICAMENTE con el nombre del área en minúsculas. Sin explicación, sin puntos.

Si el mensaje es sobre tu funcionamiento, modelo, tecnología o arquitectura, responde: mixto
"""


def clasificar(pregunta: str, historial: list[dict]) -> str:
    """
    Clasifica la pregunta en un área de negocio.

    Args:
        pregunta  (str):        Mensaje actual del usuario.
        historial (list[dict]): Mensajes previos de la conversación
                                [{rol, contenido}, ...].

    Returns:
        str: Una de: ventas | inventario | pedidos | medicos | clientes | mixto
    """
    mensajes = [{"role": "system", "content": _SYSTEM}]

    # Solo últimos 4 mensajes para dar contexto sin inflar el prompt
    for msg in historial[-4:]:
        mensajes.append({"role": msg["rol"], "content": msg["contenido"]})

    mensajes.append({"role": "user", "content": pregunta})

    try:
        resp = _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=mensajes,
            temperature=0,
        )
        area = resp.choices[0].message.content.strip().lower()
        return area if area in AREAS else "mixto"
    except Exception:
        return "mixto"
