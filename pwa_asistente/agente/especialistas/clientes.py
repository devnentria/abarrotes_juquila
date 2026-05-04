# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/clientes.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.1.0
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

TOTALES DE VENTA — REGLA CRÍTICA:
  · SIEMPRE usar SUM(fd.Importe_Neto) de FT_Facturas_D para totales de venta.
  · NUNCA usar fc.Importe_Total de FT_Facturas_C — incluye IVA y no coincide con los reportes.
  · JOIN obligatorio: FT_Facturas_C fc + FT_Facturas_D fd ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal AND fd.Cve_Movimiento=fc.Cve_Movimiento

BÚSQUEDA DE CLIENTE POR NOMBRE — PROTOCOLO DE PARADA:
  1. Buscar exacto: CM_Clientes WHERE Razon_Social LIKE '%nombre_completo%'
  2. Si no hay resultado: buscar por palabras individuales LIKE '%palabra1%' OR LIKE '%palabra2%'
  3. Mostrar la lista de nombres similares encontrados.
  ⛔ PARAR AQUÍ — NO buscar ventas de los clientes similares si no se pidió.
  ⛔ NUNCA hacer queries adicionales después de mostrar la lista de similares.
  ⛔ NUNCA buscar ventas de clientes que el usuario no confirmó como el correcto.
  La respuesta correcta es: "No existe [nombre]. Clientes similares: [lista]."

ANÁLISIS ÚTILES DE CLIENTES:
  · Top clientes por monto: SUM(fd.Importe_Neto) con JOIN a FT_Facturas_D
  · Clientes inactivos: LEFT JOIN con FT_Facturas_C buscando última fecha lejana o NULL
  · Productos más vendidos a un cliente: JOIN FT_Facturas_D GROUP BY Cve_Producto ORDER BY SUM(fd.Cantidad) DESC
  · Clientes por vendedor: JOIN CM_Clientes con GC_Vendedores
  · Frecuencia: COUNT(DISTINCT fc.Cve_Folio) por cliente en el período
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
