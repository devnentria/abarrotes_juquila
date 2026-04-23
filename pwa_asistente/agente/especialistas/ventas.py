# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/ventas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Agente Especialista — Ventas.

Responde preguntas sobre facturas, importes, comparativos,
productos más vendidos y rendimiento por sucursal o vendedor.
"""
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE VENTAS:

FT_Facturas_C — encabezado de facturas
  Cve_Folio (int), Cve_Movimiento (int), Cve_Sucursal (int),
  Fecha_Documento (datetime), Importe_Total (decimal),
  Cve_Cliente (int), Cve_Vendedor (varchar), Cve_Medico (int), Status (char)
  Cve_Medico → médico prescriptor (JOIN con GC_Medicos)
  ⚠ Filtrar: Status <> 'C'

FT_Facturas_D — detalle de facturas
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Partida (smallint), Cve_Producto (varchar), Cantidad (decimal),
  Precio (float), Precio_Publico (float), Precio_Minimo_Venta_Base (float),
  Importe_Neto (float), Costo (float)
  JOIN con FT_Facturas_C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

IM_Productos_Proveedor — costo cotizado por proveedor
  Cve_Producto (int), Cve_Proveedor (int), Costo_Cotizado (decimal),
  Fecha_Cotizacion_Precio (datetime)
  ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad = 0 para el proveedor principal
"""

_REGLAS = """
PRECIOS DE VENTA (3 tipos — reportar los 3 o aclarar cuál se pide):
  · Precio_Publico           → público general
  · Precio_Minimo_Venta_Base → venta directa / base
  · Precio                   → precio pactado (puede incluir descuento)
  Para precio en período específico: AVG de cada tipo en FT_Facturas_D por rango de fecha.
  ⚠ Nunca preguntar qué tipo de precio quiere — reportar los 3 en tabla.
  ⚠ Si piden precio en fecha pasada sin especificar mes/año: pedir el período antes de consultar.

CLASIFICACIÓN DE CLIENTES (no existe campo directo — se determina por precio cobrado):
  · Cliente final   → precio ≈ Precio_Minimo_Venta_Base (más alto)
  · Venta directa   → precio ≈ Precio_Minimo_Venta_Base2
  · Distribuidor    → precio ≈ Precio_Minimo_Venta_Base3
  Si el usuario pide "cliente final" / "distribuidor": aplicar criterio ABS() sin pedir confirmación.

BÚSQUEDA POR NOMBRE (protocolo obligatorio cuando busquen una persona):
  1. Buscar en CM_Clientes WHERE Razon_Social LIKE '%nombre%'
     → Resultado exacto: mostrar sus ventas y terminar.
     → Sin resultado exacto: continuar pasos 2 y 3 (ambos obligatorios).
  2. Buscar difuso en CM_Clientes por palabras individuales → listar similares en la respuesta.
  3. SIEMPRE buscar también en GC_Medicos WHERE Nombre LIKE '%nombre%'
     → Si se encuentra: verificar ventas via FT_Facturas_C.Cve_Medico.
  Respuesta final: clientes similares + si es médico + ventas del médico (si las hay).

RANKING DE MÉDICOS POR VENTAS:
  · Como prescriptores: JOIN FT_Facturas_C fc con GC_Medicos m ON fc.Cve_Medico = m.Cve_Medico
                        WHERE fc.Cve_Medico > 0 → SUM(Importe_Total)
  · Como clientes directos: CM_Clientes WHERE Razon_Social IN (SELECT Nombre FROM GC_Medicos)
  Presentar ambas fuentes separadas. NUNCA sustituir por vendedores.

TOTALES DE VENTA:
  · Por sucursal/período/ranking: SUM(fc.Importe_Total) FROM FT_Facturas_C
  · Por producto: SUM(fd.Importe_Neto) FROM FT_Facturas_D JOIN FT_Facturas_C
  · NO filtrar por Cve_Movimiento salvo que se pida explícitamente
"""

_SYSTEM = build(
    rol="Eres el agente especialista en VENTAS de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list[dict]) -> RespuestaIA:
    """
    Genera una respuesta sobre ventas.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "ventas")
