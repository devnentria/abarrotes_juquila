# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/mixto.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Agente Analítico General — Mixto.

Maneja preguntas que cruzan múltiples áreas de negocio
o que no encajan claramente en un solo especialista.
Adaptado para Super Juquila: Remisiones (autoservicio) + Facturas (mayoreo).
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS TRANSACCIONALES (además de las maestras):

── VENTAS AUTOSERVICIO (remisiones) ──
FT_Remisiones_C → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Cliente,
                   Fecha_Documento, Importe_Total, Cve_Vendedor, Status ('AC'=activa)
                   ⚠ Autoservicio = venta anónima en caja, cliente genérico
                   ⚠ Filtrar: Status = 'AC'
FT_Remisiones_D → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Producto,
                   Cantidad, Importe_Neto, Precio, Costo, Costo_Promedio
                   JOIN con _C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

── VENTAS MAYOREO (facturas) ──
FT_Facturas_C  → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Cliente,
                  Fecha_Documento, Importe_Total, Cve_Vendedor, Status ('AC'=activa)
                  ⚠ Filtrar: Status = 'AC'
FT_Facturas_D  → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Producto,
                  Cantidad, Importe_Neto, Precio, Precio_Publico, Precio_Minimo_Venta_Base,
                  Costo, Costo_Promedio
                  JOIN con _C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

── INVENTARIO ──
IN_Existencias_Alm        → Cve_Sucursal, Cve_Producto, Existencia (Status='AC')
IN_Existencias_Alm_Diario → Cve_Sucursal, Cve_Producto (VARCHAR), Fecha, Existencia,
                             Costo_Ultima_Compra, Costo_Promedio

── COMPRAS / COSTOS ──
IT_Movimientos_C → Cve_Movimiento, Fecha_Documento, Cve_Sucursal, Cve_Almacen,
                   Cve_Folio, Cve_Proveedor
                   ⚠ Cve_Movimiento = 'EC' para Entrada por Compra (filtro principal para costos)
IT_Movimientos_D → Cve_Movimiento, Cve_Folio, Cve_Almacen, Cve_Producto, Cantidad,
                   Costo_Unitario, Num_Lote, Fecha_Caducidad
                   JOIN con _C por: Cve_Folio + Cve_Movimiento + Cve_Almacen
  ⚠ IT_Movimientos_D.Costo_Unitario = costo de COMPRA al proveedor — NUNCA usar para precio de venta
  ⚠ NO existe columna Precio en IT_Movimientos_D ni IT_Movimientos_C
  ⚠ Para precios de venta usar EXCLUSIVAMENTE: FT_Facturas_D o FT_Remisiones_D

── ÓRDENES DE COMPRA ──
MT_Ordenes_C → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Proveedor,
               Fecha_Documento, Status, Importe_Total
               ⚠ Filtrar: Status = 'AC'
MT_Ordenes_D → Cve_Folio, Cve_Movimiento, Cve_Sucursal, Cve_Producto,
               Cantidad, Costo_Unitario
               JOIN con _C por: Cve_Folio + Cve_Movimiento + Cve_Sucursal

── PROVEEDORES ──
PM_Proveedores → Cve_Proveedor (varchar 10), Nombre, Razon_Social, RFC,
                  Status, EMail, Contacto, Telefono
                  ⚠ Filtrar: Status = 'AC'
IM_Productos_Proveedor → Cve_Producto, Cve_Proveedor, Costo_Cotizado,
                          Fecha_Cotizacion_Precio, Cve_Prioridad
                          ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad=0 = proveedor principal
"""

_REGLAS = """
ESTRATEGIA PARA PREGUNTAS MIXTAS:
  1. Descompón la pregunta en sub-preguntas por área
  2. Ejecuta una consulta por área
  3. Sintetiza los resultados en una respuesta cohesiva con conclusión clara
  Ejemplo: "¿Qué sucursal vende más pero tiene menor existencia?"
  → Consulta 1: TOP sucursales por ventas (remisiones+facturas) · Consulta 2: Existencias por sucursal · Síntesis: cruzar ambos

VENTAS TOTALES — COMBINAR REMISIONES + FACTURAS:
  Las ventas totales de Super Juquila son la suma de:
  · FT_Remisiones_C/D (autoservicio — venta anónima en tienda)
  · FT_Facturas_C/D (mayoreo — venta a clientes registrados)

  Para ventas totales por sucursal o período:
    SELECT 'Remisiones' AS Canal,
           SUM(rd.Importe_Neto) AS Total
    FROM FT_Remisiones_D rd
    JOIN FT_Remisiones_C rc ON rc.Cve_Folio = rd.Cve_Folio
                            AND rc.Cve_Sucursal = rd.Cve_Sucursal
                            AND rc.Cve_Movimiento = rd.Cve_Movimiento
    WHERE rc.Status = 'AC'
      AND [filtro de período sobre rc.Fecha_Documento]
    UNION ALL
    SELECT 'Facturas' AS Canal,
           SUM(fd.Importe_Neto) AS Total
    FROM FT_Facturas_D fd
    JOIN FT_Facturas_C fc ON fc.Cve_Folio = fd.Cve_Folio
                          AND fc.Cve_Sucursal = fd.Cve_Sucursal
                          AND fc.Cve_Movimiento = fd.Cve_Movimiento
    WHERE fc.Status = 'AC'
      AND [filtro de período sobre fc.Fecha_Documento]

  ⚠ NO existen FT_Pedidos_C ni FT_Pedidos_Dia en este ERP — NUNCA referenciar esas tablas.

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
  ⚠ Resultado esperado: UNA fila por presentación. MAX(Fecha) y MAX(Costo) son suficientemente precisos.
  ⚠ NO usar subconsultas correlacionadas — son lentas con muchos productos.

MARGEN BRUTO — PROTOCOLO OBLIGATORIO (cuando llegue a mixto):
  · fd.Costo = costo unitario al momento de la venta (fuente histórica real)
  · Margen bruto = SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)
  · % Margen     = Margen / SUM(fd.Importe_Neto) * 100

  Consulta estándar (facturas mayoreo):
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
    WHERE fc.Status = 'AC'
      AND [filtro de período sobre fc.Fecha_Documento]

  Para margen de autoservicio: usar FT_Remisiones_D/C con la misma estructura.

FORMATO ADICIONAL MIXTO:
  · Secciones separadas si la respuesta cubre múltiples áreas
  · Separar siempre Autoservicio (Remisiones) vs Mayoreo (Facturas) cuando ambos canales apliquen
  · Conclusión ejecutiva al final que integre todos los hallazgos
"""

_SYSTEM = build(
    rol="Eres el agente analítico general de Abarrotes Suite (Super Juquila). Manejas preguntas complejas que involucran múltiples áreas de negocio.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta para preguntas mixtas o multi-área.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "mixto", **kwargs)
