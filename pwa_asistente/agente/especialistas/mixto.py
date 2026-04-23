# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/mixto.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Agente Analítico General — Mixto.

Maneja preguntas que cruzan múltiples áreas de negocio
o que no encajan claramente en un solo especialista.
"""
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS TRANSACCIONALES (además de las maestras):

── VENTAS ──
FT_Facturas_C  → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Cliente, Cve_Medico,
                  Fecha_Documento, Importe_Total, Cve_Vendedor, Status ('C'=cancelada)
FT_Facturas_D  → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Producto,
                  Cantidad, Importe_Neto, Precio, Precio_Publico, Precio_Minimo_Venta_Base
                  JOIN con _C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

── PEDIDOS ──
FT_Pedidos_C   → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Cliente,
                  Fecha_Documento, Importe_Total, Estatus ('AC'=activo,'TR'=transferido,'CN'=cancelado)
FT_Pedidos_D   → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Producto, Cantidad, Precio

── INVENTARIO ──
IN_Existencias_Alm        → Cve_Sucursal, Cve_Producto, Existencia (Status='AC')
IN_Existencias_Lote       → Cve_Sucursal, Cve_Producto, Num_Lote, Fecha_Caducidad, Existencia
IN_Existencias_Alm_Diario → Cve_Sucursal, Cve_Producto (VARCHAR), Fecha, Existencia,
                             Costo_Ultima_Compra, Costo_Promedio

── COMPRAS / COSTOS ──
IT_Movimientos_C → Cve_Movimiento, Fecha_Documento, Cve_Sucursal, Cve_Almacen, Cve_Folio
                   EC=Entrada Compra (filtro principal para costos)
IT_Movimientos_D → Cve_Movimiento, Cve_Folio, Cve_Almacen, Cve_Producto, Cantidad,
                   Costo_Unitario, Num_Lote, Fecha_Caducidad
                   JOIN con _C por: Cve_Folio + Cve_Movimiento + Cve_Almacen

── PROVEEDORES ──
IM_Productos_Proveedor → Cve_Producto, Cve_Proveedor, Costo_Cotizado,
                          Fecha_Cotizacion_Precio, Cve_Prioridad
                          ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad=0 = proveedor principal
"""

_REGLAS = """
ESTRATEGIA PARA PREGUNTAS MIXTAS:
  1. Descompón la pregunta en sub-preguntas por área
  2. Ejecuta una consulta por área
  3. Sintetiza los resultados en una respuesta cohesiva con conclusión clara
  Ejemplo: "¿Qué sucursal vende más pero tiene más pedidos pendientes?"
  → Consulta 1: TOP sucursales por ventas · Consulta 2: Pedidos activos por sucursal · Síntesis: cruzar ambos

FORMATO ADICIONAL MIXTO:
  · Secciones separadas si la respuesta cubre múltiples áreas
  · Conclusión ejecutiva al final que integre todos los hallazgos
"""

_SYSTEM = build(
    rol="Eres el agente analítico general de Suite Analítica. Manejas preguntas complejas que involucran múltiples áreas de negocio.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list[dict]) -> RespuestaIA:
    """
    Genera una respuesta para preguntas mixtas o multi-área.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "mixto")
