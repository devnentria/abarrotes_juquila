# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/clientes.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.3.0
# ============================================================
"""
Agente Especialista — Clientes.

Responde preguntas sobre historial de compras, clientes frecuentes,
clientes inactivos y segmentación por vendedor.
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE CLIENTES Y VENTAS:

FT_Facturas_C — historial de ventas al cliente
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Cliente (varchar), Cve_Vendedor (varchar),
  Fecha_Documento (datetime), Fecha_Vencimiento (datetime),
  Importe_bruto (float), Importe_Descuento (decimal),
  Importe_Total (float), Costo_Neto (float),
  Status (char), Pagada (varchar), Cve_Lista_precios (smallint),
  Referencia_Cliente (varchar)
  ⚠ Filtrar: Status <> 'C'

FT_Facturas_D — detalle de ventas al cliente
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Producto (varchar), Cve_Presentacion (varchar), Cve_Partida (smallint),
  Cantidad (decimal), Cantidad_Devuelta (decimal),
  Precio (float), Precio_Publico (float), Precio_Sugerido_Cte (float),
  Precio_Minimo_Venta_Base (float),
  Importe_Bruto (float), Importe_Descuentos (float), Importe_Subtotal (float),
  Importe_Neto (float), Costo (float), Costo_Promedio (float)
  JOIN con FT_Facturas_C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

CM_Clientes — catálogo de clientes
  Cve_Cliente (varchar), Razon_Social (varchar), Cve_Lista_Precios (smallint),
  Status (char)
  ⚠ Filtrar Status = 'AC' para clientes activos
  JOIN con FT_Facturas_C por Cve_Cliente

CM_Consignatarios — direcciones de entrega registradas por cliente
  Cve_Cliente (varchar), Cve_Consignatario (int), Nombre (varchar),
  Calle_No (varchar), Colonia (varchar), Del_Municipio (varchar),
  CP (char), Poblacion (varchar),
  Telefono (varchar), Telefono_2 (varchar),
  EMail (varchar), Status (char)
  JOIN con CM_Clientes por Cve_Cliente
  ⚠ Un cliente puede tener múltiples direcciones — filtrar Status = 'AC' para activas
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

EXCLUSIÓN OBLIGATORIA — VENTA DE MOSTRADOR:
  · "VENTA DE MOSTRADOR" (Cve_Cliente='20000') es un cliente genérico para ventas de caja/contado anónimas — NO es un cliente real.
  · En TODA consulta de top clientes, ranking o mayor comprador:
    - SIEMPRE agregar: AND fc.Cve_Cliente <> '20000'
    - Y si hay JOIN a CM_Clientes: AND cl.Razon_Social NOT LIKE '%MOSTRADOR%'
  ⛔ NUNCA reportar "VENTA DE MOSTRADOR" como cliente — aunque tenga el mayor importe.

CLASIFICACIÓN DE CLIENTES — por CM_Clientes.Cve_Lista_Precios:
  · 0 = Cliente final / Mostrador  (15,033 clientes — mayoría)
  · 1 = Venta directa / Ruta       (1,355 clientes)
  · 2 = Distribuidor / Mayoreo     (1 cliente)
  Usar este campo para segmentar o filtrar por tipo de cliente.

ANÁLISIS ÚTILES DE CLIENTES:
  · Top clientes por monto: SUM(fd.Importe_Neto) con JOIN a FT_Facturas_D
  · Clientes inactivos: LEFT JOIN con FT_Facturas_C buscando última fecha lejana o NULL
  · Productos más vendidos a un cliente: JOIN FT_Facturas_D GROUP BY Cve_Producto ORDER BY SUM(fd.Cantidad) DESC
  · Clientes por vendedor: JOIN CM_Clientes con GC_Vendedores
  · Frecuencia: COUNT(DISTINCT fc.Cve_Folio) por cliente en el período
  · Clientes por tipo: filtrar c.Cve_Lista_Precios = 0/1/2
  · Direcciones de entrega: JOIN CM_Consignatarios ON Cve_Cliente
"""

_SYSTEM = build(
    rol="Eres el agente especialista en CLIENTES de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre clientes.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "clientes", **kwargs)
