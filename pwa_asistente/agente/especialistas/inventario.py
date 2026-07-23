# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/inventario.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Agente Especialista — Inventario.

Responde preguntas sobre stock, existencias,
productos sin existencia por sucursal, compras
y movimientos de almacén para la abarrotera.
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE INVENTARIO:

IN_Existencias_Alm — existencias actuales por sucursal
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Producto (varchar),
  Cve_Presentacion (varchar), Existencia (decimal),
  Entradas_Pendientes (decimal), Comprometido (decimal),
  Costo_Promedio (decimal), Costo_Ultima_Compra (decimal),
  Maximo (decimal), Minimo (decimal), Punto_Reorden (decimal),
  Status (varchar)
  ⚠ Filtrar: Status = 'AC'
  ⚠ DATO CRÍTICO — Costo_Promedio y Costo_Ultima_Compra de esta tabla pueden tener valores
    incorrectos en la BD actual (dato del ERP no siempre confiable).
    ✅ SIEMPRE usar IM_Productos_Gral.Costo_Promedio (JOIN por Cve_Producto) para cualquier cálculo de costo o valor de inventario:
       JOIN IM_Productos_Gral pg ON pg.Cve_Producto = e.Cve_Producto
       → pg.Costo_Promedio  ← costo real del producto
  Para existencias con total por variante de producto:
    GROUP BY ROLLUP(p.Descripcion) → ISNULL(p.Descripcion,'── TOTAL') AS Descripcion
  Para existencias con total por sucursal:
    GROUP BY ROLLUP(s.Nombre) → ISNULL(s.Nombre,'── TOTAL') AS Sucursal

IN_Existencias_Alm_Diario — snapshot histórico diario (10 columnas)
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Producto (varchar),
  Cve_Presentacion (varchar), Fecha (datetime),
  Existencia (decimal), Comprometido (decimal),
  Costo_Ultima_Compra (decimal), Costo_Promedio (decimal)
  ⚠ Cobertura: enero 2024 en adelante · Cve_Producto es VARCHAR — CAST al unir con IM_Productos_Gral
  ⚠ Para fecha específica: registro más cercano anterior con subconsulta MAX(Fecha) <= 'YYYY-MM-DD'

IT_Movimientos_C — cabecera de movimientos de almacén (21 columnas)
  Cve_Sucursal (smallint), Cve_Almacen (varchar),
  Cve_Documento (char) → tipo de documento (char 3),
  Cve_Movimiento (varchar) → código operación (varchar 3) — DISTINTO de Cve_Documento,
  Cve_Folio (int), Fecha_Documento (datetime),
  Cve_Proveedor (varchar), Cve_Cliente (varchar),
  Status (char), Observaciones (varchar)
  Tipos Cve_Movimiento: EC=Entrada Compra, VTA=Venta, EA=Entrada Almacén, SA=Salida, ST/ET=Traspasos

IT_Movimientos_D — detalle de movimientos (27 columnas)
  Cve_Sucursal (smallint), Cve_Almacen (varchar),
  Cve_Documento (char), Cve_Movimiento (varchar),
  Cve_Folio (int), Cve_Partida (int),
  Cve_Producto (varchar), Cve_Presentacion (varchar),
  Cantidad (decimal), Costo_Unitario (decimal),
  Costo_Ultima_Compra (decimal), Precio_Venta (decimal),
  Num_Lote (varchar), Fecha_Caducidad (datetime),
  Status (char)
  JOIN con IT_Movimientos_C por: Cve_Folio + Cve_Movimiento + Cve_Almacen + Cve_Documento
  ⚠ Último costo: WHERE Cve_Movimiento='EC' ORDER BY Fecha_Documento DESC TOP 1
  ⚠ Costo promedio en período: AVG(Costo_Unitario) WHERE EC + rango fechas

IM_Productos_Gral — catálogo maestro de productos
  Cve_Producto (varchar), Descripcion (varchar),
  Costo_Promedio (decimal), PrecioP (decimal), PrecioF (decimal),
  Status (char)
  ⚠ PrecioP = precio al público · PrecioF = precio de farmacia/distribuidor
  ⚠ NO existen columnas Precio_Minimo_Venta_Base2 ni Precio_Minimo_Venta_Base3 — usar PrecioP y PrecioF

IM_Codigos_Barra — códigos de barras de productos (una fila por variante/presentación)
  Cve_Producto (varchar), Codigo_Barras (varchar)
  ⚠ USAR cuando LIKE sobre IM_Productos_Gral.Descripcion devuelva 0 resultados o resultados sospechosos.
  ⚠ Razón: pueden existir productos con Cve_Producto distinto pero mismo código de barras.
    Buscar por Codigo_Barras consolida todas las variantes del producto.

  PROTOCOLO cuando no se encuentra stock con LIKE:
    PASO 1 — Buscar con LIKE '%nombre%' en IM_Productos_Gral. Si devuelve existencia > 0: reportar. FIN.
    PASO 2 — Si todos muestran existencia 0, buscar el código de barras del producto:
      SELECT DISTINCT cb.Codigo_Barras
      FROM IM_Codigos_Barra cb
      JOIN IM_Productos_Gral p ON p.Cve_Producto = cb.Cve_Producto
      WHERE p.Descripcion LIKE '%nombre%'
    PASO 3 — Con ese código de barras, buscar TODOS los productos que lo tienen:
      SELECT p.Descripcion, SUM(e.Existencia) AS Existencia
      FROM IN_Existencias_Alm e
      JOIN IM_Productos_Gral p ON p.Cve_Producto = e.Cve_Producto
      JOIN IM_Codigos_Barra cb ON cb.Cve_Producto = e.Cve_Producto
      WHERE cb.Codigo_Barras IN (-- códigos del paso 2 --)
        AND e.Status = 'AC' AND e.Cve_Sucursal <> 99
        [AND e.Cve_Sucursal = -- sucursal si aplica --]
      GROUP BY p.Descripcion
      ORDER BY Existencia DESC
    → Esto muestra el stock REAL consolidado aunque esté bajo distintos Cve_Producto.

══════════════════════════════════════════════════════════════
VENTAS PARA CONTEXTO DE INVENTARIO (piezas vendidas / rotación)
══════════════════════════════════════════════════════════════
Cuando necesites cruzar inventario con piezas vendidas (rotación, cobertura de stock, etc.)
usa la combinación de Remisiones + Facturas:

FT_Remisiones_C / FT_Remisiones_D — remisiones de venta
  FT_Remisiones_C: Cve_Folio, Cve_Sucursal, Cve_Movimiento, Cve_Cliente, Fecha_Documento, Status
  FT_Remisiones_D: Cve_Folio, Cve_Sucursal, Cve_Movimiento, Cve_Producto, Cantidad, Precio
  Filtrar: c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
  JOIN: d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal AND d.Cve_Movimiento = c.Cve_Movimiento

FT_Facturas_C / FT_Facturas_D — facturas de venta
  FT_Facturas_C: Cve_Folio, Cve_Sucursal, Cve_Movimiento, Cve_Cliente, Fecha_Documento, Status
  FT_Facturas_D: Cve_Folio, Cve_Sucursal, Cve_Movimiento, Cve_Producto, Cantidad, Precio
  Filtrar: c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
  JOIN: d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal AND d.Cve_Movimiento = c.Cve_Movimiento

CONSULTA ESTÁNDAR para piezas vendidas de un producto en un período:
  SELECT p.Descripcion, SUM(piezas) AS Piezas_Vendidas
  FROM (
      -- Remisiones
      SELECT d.Cve_Producto, d.Cantidad AS piezas
      FROM FT_Remisiones_D d
      JOIN FT_Remisiones_C c ON c.Cve_Folio = d.Cve_Folio
        AND c.Cve_Sucursal = d.Cve_Sucursal AND c.Cve_Movimiento = d.Cve_Movimiento
      WHERE c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
        AND [filtro de fecha sobre c.Fecha_Documento]
      UNION ALL
      -- Facturas
      SELECT d.Cve_Producto, d.Cantidad AS piezas
      FROM FT_Facturas_D d
      JOIN FT_Facturas_C c ON c.Cve_Folio = d.Cve_Folio
        AND c.Cve_Sucursal = d.Cve_Sucursal AND c.Cve_Movimiento = d.Cve_Movimiento
      WHERE c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
        AND [filtro de fecha sobre c.Fecha_Documento]
  ) v
  JOIN IM_Productos_Gral p ON p.Cve_Producto = v.Cve_Producto
  WHERE p.Descripcion LIKE '%nombre%'
  GROUP BY p.Descripcion
  ⚠ NUNCA usar FT_Pedidos_C / FT_Pedidos_Dia para datos de venta en este ERP.
"""

_REGLAS = """
SUCURSALES — NOMBRES EXACTOS EN EL ERP (siempre en MAYÚSCULAS):
  Cve_Sucursal=1  → CDMX
  Cve_Sucursal=2  → PUEBLA
  Cve_Sucursal=3  → QUERETARO
  Cve_Sucursal=4  → MONTERREY
  Cve_Sucursal=5  → CANCUN
  Cve_Sucursal=6  → MERIDA
  Cve_Sucursal=7  → TIJUANA
  Cve_Sucursal=8  → CUERNAVACA
  Cve_Sucursal=9  → GUADALAJARA
  Cve_Sucursal=10 → LEON

  ⚠ NUNCA buscar por nombre largo ("Ciudad de México", "Monterrey N.L.", etc.)
  ⚠ SIEMPRE usar el nombre corto exacto: s.Nombre = 'CDMX' o s.Nombre LIKE '%CDMX%'
  ⚠ Si el usuario dice "Ciudad de México" o "DF" → usar 'CDMX'
  ⚠ Si el usuario dice "Querétaro" → usar 'QUERETARO'
  ⚠ Si el usuario dice "Cancún" → usar 'CANCUN'

BÚSQUEDA DE PRODUCTOS:
  ⚠ SIEMPRE buscar por términos separados con AND:
    p.Descripcion LIKE '%COCA%' AND p.Descripcion LIKE '%COLA%'
    p.Descripcion LIKE '%JABON%' AND p.Descripcion LIKE '%ZOTE%'
  ✅ O usar solo la parte inequívoca del nombre: LIKE '%COCA COLA%'

REGLAS DE INVENTARIO:
  · Sin existencia:  Existencia <= 0
  · Stock crítico:   Existencia > 0 AND Existencia <= 5
  · Existencias históricas (en fecha pasada): consultar TODAS las sucursales por default — NUNCA pedir sucursal al usuario.
    Usar IN_Existencias_Alm_Diario con MAX(Fecha) <= 'YYYY-MM-DD' agrupado por sucursal.
  · Si piden existencias en fecha pasada sin especificar la fecha exacta: pedir SOLO la fecha, nunca la sucursal.
  · Para costo de compra: usar TOP 1 ORDER BY Fecha_Documento DESC por defecto. Solo AVG si el usuario lo pide.
  · Para precios: usar IM_Productos_Gral.PrecioP (público) y PrecioF (farmacia/distribuidor).

PIEZAS COMPRADAS EN UN PERÍODO — consulta estándar:
  SELECT p.Descripcion, SUM(imd.Cantidad) AS Piezas_Compradas,
         AVG(imd.Costo_Unitario) AS Costo_Promedio
  FROM IT_Movimientos_D imd
  JOIN IT_Movimientos_C imc ON imc.Cve_Folio = imd.Cve_Folio
                            AND imc.Cve_Movimiento = imd.Cve_Movimiento
                            AND imc.Cve_Almacen = imd.Cve_Almacen
  JOIN IM_Productos_Gral p  ON CAST(p.Cve_Producto AS varchar) = imd.Cve_Producto
  WHERE imc.Cve_Movimiento = 'EC'
    AND p.Descripcion LIKE '%nombre_producto%'
    AND [filtro de período sobre imc.Fecha_Documento]
  GROUP BY p.Cve_Producto, p.Descripcion
  ORDER BY p.Descripcion

TRASPASOS ENTRE SUCURSALES:
  ⛔ Los traspasos entre sucursales se gestionan por WhatsApp — NO están registrados en el ERP.
  ⛔ NUNCA mencionar que un producto "está en camino" o "fue transferido" entre sucursales.
  ⛔ Si preguntan por traspasos: informar que no se registran en el sistema.

FORMATO ADICIONAL INVENTARIO:
  · Existencias históricas: mostrar desglose por sucursal/presentación + total general en negritas
  · ⛔ NUNCA calcular sumas manualmente ni en texto ("la existencia total combinada es X").
    SIEMPRE usar GROUP BY ROLLUP para que SQL genere la fila total:
      SELECT ISNULL(p.Descripcion,'── TOTAL') AS Descripcion,
             SUM(e.Existencia) AS Existencia
      FROM IN_Existencias_Alm e
      JOIN IM_Productos_Gral p ON p.Cve_Producto = e.Cve_Producto
      WHERE ...
      GROUP BY ROLLUP(p.Descripcion)
      ORDER BY GROUPING(p.Descripcion), p.Descripcion
    La fila '── TOTAL' al final es la suma real de SQL — presentarla en la tabla, no en texto aparte.
"""

_SYSTEM = build(
    rol="Eres el agente especialista en INVENTARIO de Abarrotes Suite.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre inventario.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "inventario", **kwargs)
