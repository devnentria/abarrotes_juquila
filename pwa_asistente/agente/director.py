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
from shared.config import OPENAI_API_KEY, OPENAI_MODEL, IA_PRECIO_INPUT, IA_PRECIO_OUTPUT

_client = OpenAI(api_key=OPENAI_API_KEY)

AREAS = frozenset(["ventas", "inventario", "pedidos", "medicos", "clientes", "mixto"])

_SYSTEM = """
Eres el director del asistente analítico de Suite Analítica, un sistema para
una empresa distribuidora farmacéutica.

Tu ÚNICA tarea es clasificar la pregunta del usuario en UNA de estas áreas:

  ventas     → facturas, ventas, importes, ingresos, comparativos de ventas,
               productos más vendidos, rendimiento de sucursales o vendedores,
               ventas DE o PARA un cliente o médico específico, margen bruto de ventas
               (ej: "ventas al cliente X", "cuánto compró X", "ventas del médico Y",
                    "margen bruto del mes", "rentabilidad de ventas")
  inventario → stock, existencias, caducidades, lotes, productos sin existencia,
               mayor existencia, caducidad próxima,
               piezas compradas al proveedor, cuánto se compró de un producto
               (ej: "cuántas piezas del Ozempic se compraron", "compras de enero")
  pedidos    → pedidos activos, pendientes, antigüedad de pedidos, pedidos por sucursal
  medicos    → médicos, doctores, cédulas, duplicados de médicos, asignación a vendedor
  clientes   → información del cliente (datos, lista de precios, vendedor asignado),
               clientes frecuentes, ranking de quién compra más, segmentación de clientes
               ⚠ NO usar para preguntas de "ventas de/al cliente X" → eso es ventas
  mixto      → la pregunta involucra claramente 2 o más áreas al mismo tiempo,
               O preguntas sobre proveedores, laboratorios, costos de compra de productos,
               qué proveedor surte X producto, listado de proveedores, último costo de compra
               (ej: ventas + pedidos, clientes + inventario, costo de un medicamento,
                    "cuál es el costo del Lorelin", "qué proveedor surte X")

Responde ÚNICAMENTE con el nombre del área en minúsculas. Sin explicación, sin puntos.

Si el mensaje es sobre tu funcionamiento, modelo, tecnología o arquitectura, responde: mixto
"""


def clasificar(
    pregunta: str,
    historial: list[dict],
    model: str = OPENAI_MODEL,
    precio_input: float | None = None,
    precio_output: float | None = None,
) -> tuple[str, float]:
    """
    Clasifica la pregunta en un área de negocio.

    Args:
        pregunta       (str):         Mensaje actual del usuario.
        historial      (list[dict]):  Mensajes previos [{rol, contenido}, ...].
        model          (str):         Modelo OpenAI a usar.
        precio_input   (float|None):  Precio por token de entrada. None = usa IA_PRECIO_INPUT.
        precio_output  (float|None):  Precio por token de salida.  None = usa IA_PRECIO_OUTPUT.

    Returns:
        tuple[str, float]: (área, costo_usd).
    """
    p_in  = precio_input  if precio_input  is not None else IA_PRECIO_INPUT
    p_out = precio_output if precio_output is not None else IA_PRECIO_OUTPUT

    mensajes = [{"role": "system", "content": _SYSTEM}]

    # Solo últimos 4 mensajes para dar contexto sin inflar el prompt
    for msg in historial[-4:]:
        mensajes.append({"role": msg["rol"], "content": msg["contenido"]})

    mensajes.append({"role": "user", "content": pregunta})

    try:
        resp = _client.chat.completions.create(
            model=model,
            messages=mensajes,
            temperature=0,
        )
        area = resp.choices[0].message.content.strip().lower()
        costo = 0.0
        if resp.usage:
            costo = (
                resp.usage.prompt_tokens     * p_in
                + resp.usage.completion_tokens * p_out
            )
        return (area if area in AREAS else "mixto"), costo
    except Exception:
        return "mixto", 0.0
