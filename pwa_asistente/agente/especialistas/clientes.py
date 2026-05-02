# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/clientes.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Agente Especialista — Clientes.

Responde preguntas sobre historial de compras, clientes frecuentes,
clientes inactivos y segmentación por vendedor.
"""
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE CLIENTES Y VENTAS:

FT_Facturas_C — historial de ventas al cliente
  Cve_Folio (int), Cve_Movimiento (int), Cve_Sucursal (int),
  Cve_Cliente (int), Fecha_Documento (datetime),
  Importe_Total (decimal), Cve_Vendedor (varchar), Status (varchar)
  ⚠ Filtrar: Status <> 'C'

FT_Facturas_D — detalle de ventas al cliente
  Cve_Folio (int), Cve_Movimiento (int), Cve_Sucursal (int),
  Cve_Producto (int), Cantidad (decimal), Importe_Neto (decimal)
  JOIN con FT_Facturas_C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento
"""

_REGLAS = """
TERMINOLOGÍA OBLIGATORIA:
  · FT_Facturas_C registra VENTAS de la empresa a clientes — NUNCA llamarlas "compras del cliente".
  · "Clientes con más compras" = "clientes con más ventas registradas" → usar siempre "ventas".
  · NUNCA escribir "el cliente realizó X compras" → escribir "se registraron X ventas al cliente".
  · NUNCA "historial de compras" → "historial de ventas" o "facturas al cliente".

ANÁLISIS ÚTILES DE CLIENTES:
  · Top clientes por monto de ventas en un período
  · Clientes inactivos: LEFT JOIN con FT_Facturas_C buscando última fecha lejana o NULL
  · Productos más vendidos a un cliente: GROUP BY Cve_Producto ORDER BY SUM(Cantidad) DESC
  · Clientes por vendedor asignado: JOIN CM_Clientes con GC_Vendedores
  · Frecuencia de visita/venta: COUNT(DISTINCT Cve_Folio) por cliente en el período

BÚSQUEDA DE CLIENTE POR NOMBRE:
  Usar CM_Clientes.Razon_Social LIKE '%nombre%'
  Si no hay resultado exacto: buscar por palabras individuales y listar similares.
"""

_SYSTEM = build(
    rol="Eres el agente especialista en CLIENTES de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list[dict]) -> RespuestaIA:
    """
    Genera una respuesta sobre clientes.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "clientes")
