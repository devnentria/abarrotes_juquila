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
  ⚠ IT_Movimientos_D.Costo_Unitario = costo de COMPRA al proveedor — NUNCA usar para precio de venta al cliente
  ⚠ NO existe columna Precio en IT_Movimientos_D ni IT_Movimientos_C
  ⚠ Para precios de venta usar EXCLUSIVAMENTE: FT_Facturas_D (Precio, Precio_Publico, Precio_Minimo_Venta_Base)

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

ÚLTIMO COSTO DE COMPRA — PROTOCOLO OBLIGATORIO:
  ⚠ FUENTE CORRECTA: IT_Movimientos_D + IT_Movimientos_C (compras reales al proveedor)
  ⚠ NO usar IM_Productos_Proveedor.Costo_Cotizado para "último costo" — es precio cotizado, no el pagado

  Consulta estándar (último costo real por presentación):
    SELECT TOP 1 p.Descripcion, imd.Costo_Unitario, imc.Fecha_Documento AS Fecha_Compra
    FROM IT_Movimientos_D imd
    JOIN IT_Movimientos_C imc ON imc.Cve_Folio = imd.Cve_Folio
                              AND imc.Cve_Movimiento = imd.Cve_Movimiento
                              AND imc.Cve_Almacen = imd.Cve_Almacen
    JOIN IM_Productos_Gral p  ON p.Cve_Producto = imd.Cve_Producto
    WHERE imc.Cve_Movimiento = 'EC'
      AND p.Descripcion LIKE '%nombre_producto%'
    ORDER BY imc.Fecha_Documento DESC

  Si el producto tiene varias presentaciones — mostrar el último costo de CADA UNA:
    SELECT p.Descripcion,
           MAX(imc.Fecha_Documento) AS Ultima_Compra,
           MAX(imd.Costo_Unitario)  AS Ultimo_Costo
    FROM IT_Movimientos_D imd
    JOIN IT_Movimientos_C imc ON imc.Cve_Folio = imd.Cve_Folio
                              AND imc.Cve_Movimiento = imd.Cve_Movimiento
                              AND imc.Cve_Almacen = imd.Cve_Almacen
    JOIN IM_Productos_Gral p  ON p.Cve_Producto = imd.Cve_Producto
    WHERE imc.Cve_Movimiento = 'EC'
      AND p.Descripcion LIKE '%nombre_producto%'
    GROUP BY p.Cve_Producto, p.Descripcion
    ORDER BY p.Descripcion

MARGEN BRUTO — PROTOCOLO OBLIGATORIO (cuando llegue a mixto):
  · fd.Costo = costo unitario al momento de la venta (fuente histórica real)
  · Margen bruto = SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)
  · % Margen     = Margen / SUM(fd.Importe_Neto) * 100

  Consulta estándar:
    SELECT
      SUM(fd.Importe_Neto)                                                         AS Ventas,
      SUM(fd.Cantidad * fd.Costo)                                                  AS Costo_Total,
      SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)                           AS Margen_Bruto,
      CAST((SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo))
           * 100.0 / NULLIF(SUM(fd.Importe_Neto), 0) AS DECIMAL(5,1))             AS Pct_Margen
    FROM FT_Facturas_D fd
    JOIN FT_Facturas_C fc ON fc.Cve_Folio = fd.Cve_Folio
                          AND fc.Cve_Sucursal = fd.Cve_Sucursal
                          AND fc.Cve_Movimiento = fd.Cve_Movimiento
    WHERE fc.Status <> 'C'
      AND [filtro de período sobre fc.Fecha_Documento]

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
