# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/medicos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 4.0.0
# ============================================================
"""
Agente Especialista — Proveedores.

Responde preguntas sobre el directorio de proveedores, productos por proveedor,
costos cotizados, relación producto-proveedor y vendedores asociados.

Nota: la tabla GC_Medicos NO existe en este ERP.
      El catálogo de proveedores es PM_Proveedores (187 registros activos).
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE PROVEEDORES:

PM_Proveedores — catálogo de proveedores / distribuidores (~187 activos)
  Cve_Proveedor (varchar 10), Nombre (varchar), Razon_Social (varchar),
  RFC (varchar), Status (char), EMail (varchar), Contacto (varchar),
  Telefono (varchar), Cve_Moneda (varchar)
  ⚠ Filtrar: Status = 'AC' para proveedores activos
  ⚠ Cve_Proveedor es varchar(10) — usar CAST si se une con tablas numéricas
  ⚠ Usar LTRIM(RTRIM()) al comparar nombres (hay espacios extra en el ERP)

IM_Productos_Proveedor — relación producto-proveedor
  Cve_Producto (varchar), Cve_Proveedor (varchar), Cve_Prioridad (smallint),
  Costo_Cotizado (decimal), Fecha_Cotizacion_Costo (datetime),
  Precio_Venta (decimal), Fecha_Cotizacion_Precio (datetime)
  ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad = 0 para el proveedor principal

IT_Movimientos_C — encabezado de movimientos de inventario (compras reales)
  Cve_Movimiento (varchar), Fecha_Documento (datetime), Cve_Sucursal (smallint),
  Cve_Almacen (smallint), Cve_Folio (int), Cve_Proveedor (varchar)
  ⚠ Cve_Movimiento = 'EC' para Entrada por Compra (filtro principal)

IT_Movimientos_D — detalle de movimientos de inventario
  Cve_Movimiento (varchar), Cve_Folio (int), Cve_Almacen (smallint),
  Cve_Producto (varchar), Cantidad (decimal), Costo_Unitario (decimal),
  Num_Lote (varchar), Fecha_Caducidad (datetime)
  JOIN con _C por: Cve_Folio + Cve_Movimiento + Cve_Almacen
  ⚠ Costo_Unitario = costo REAL de compra al proveedor

MT_Ordenes_C — encabezado de órdenes de compra
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Proveedor (varchar), Fecha_Documento (datetime), Status (char),
  Importe_Total (float)
  ⚠ Filtrar: Status = 'AC'

MT_Ordenes_D — detalle de órdenes de compra
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Producto (varchar), Cantidad (decimal), Costo_Unitario (decimal)
  JOIN con _C por: Cve_Folio + Cve_Movimiento + Cve_Sucursal

GC_Vendedores — catálogo de vendedores
  Cve_Vendedor (varchar), Nombre (varchar), Cve_Sucursal (smallint),
  Status (char), TipoVendedor (varchar), Tipo_Vendedor (char),
  Cve_Supervisor (varchar), Cve_Ruta (varchar),
  Porc_Comision (decimal), email (varchar)
  ⚠ Filtrar Status = 'AC' para activos

IM_Productos_Gral — catálogo de productos
  Cve_Producto (varchar), Descripcion (varchar), Status (varchar),
  Cve_Familia (varchar), Cve_Subfamilia (varchar),
  Costo_Promedio (decimal), Costo_Ultima_Compra (decimal)
"""

_REGLAS = """
PROVEEDORES — CONSULTAS PRINCIPALES:

  1. Listado de proveedores activos:
     SELECT Cve_Proveedor, Nombre, RFC, Contacto, Telefono, EMail
     FROM PM_Proveedores WHERE Status = 'AC' ORDER BY Nombre

  2. Qué proveedor surte un producto (por catálogo cotizado):
     SELECT pv.Nombre AS Proveedor, pp.Costo_Cotizado, pp.Fecha_Cotizacion_Costo
     FROM IM_Productos_Proveedor pp
     JOIN PM_Proveedores pv ON pv.Cve_Proveedor = pp.Cve_Proveedor
     JOIN IM_Productos_Gral p ON p.Cve_Producto = pp.Cve_Producto
     WHERE p.Descripcion LIKE '%nombre_producto%'
       AND pv.Status = 'AC' AND pp.Costo_Cotizado > 0
     ORDER BY pp.Cve_Prioridad

  3. Productos de un proveedor:
     SELECT p.Descripcion, pp.Costo_Cotizado
     FROM IM_Productos_Proveedor pp
     JOIN PM_Proveedores pv ON pv.Cve_Proveedor = pp.Cve_Proveedor
     JOIN IM_Productos_Gral p ON p.Cve_Producto = pp.Cve_Producto
     WHERE pv.Nombre LIKE '%nombre_proveedor%'
       AND pp.Costo_Cotizado > 0
     ORDER BY p.Descripcion

  4. Último costo real de compra a un proveedor (no cotizado, sino pagado):
     SELECT TOP 1 p.Descripcion, imd.Costo_Unitario, imc.Fecha_Documento AS Fecha_Compra
     FROM IT_Movimientos_D imd
     JOIN IT_Movimientos_C imc ON imc.Cve_Folio = imd.Cve_Folio
                               AND imc.Cve_Movimiento = imd.Cve_Movimiento
                               AND imc.Cve_Almacen = imd.Cve_Almacen
     JOIN IM_Productos_Gral p  ON p.Cve_Producto = imd.Cve_Producto
     WHERE imc.Cve_Movimiento = 'EC'
       AND p.Descripcion LIKE '%nombre_producto%'
     ORDER BY imc.Fecha_Documento DESC

  5. Órdenes de compra a un proveedor:
     SELECT oc.Cve_Folio, oc.Fecha_Documento, oc.Importe_Total, oc.Status
     FROM MT_Ordenes_C oc
     JOIN PM_Proveedores pv ON pv.Cve_Proveedor = oc.Cve_Proveedor
     WHERE pv.Nombre LIKE '%nombre_proveedor%'
       AND oc.Status = 'AC'
     ORDER BY oc.Fecha_Documento DESC

BÚSQUEDA DE PROVEEDORES POR NOMBRE — OBLIGATORIO:
  · Buscar SIEMPRE por cada palabra por separado:
    WHERE Nombre LIKE '%palabra1%' OR Nombre LIKE '%palabra2%'
  · NUNCA buscar el nombre completo junto — dividir siempre en palabras individuales.

  FALLBACK FONÉTICO — si LIKE no devuelve resultados:
  ⛔ NUNCA responder "no encontré" sin antes intentar DIFFERENCE (SOUNDEX):
    SELECT Cve_Proveedor, Nombre FROM PM_Proveedores
    WHERE Status = 'AC' AND DIFFERENCE(Nombre, 'palabra_buscada') >= 3
    ORDER BY DIFFERENCE(Nombre, 'palabra_buscada') DESC

COSTO COTIZADO vs COSTO REAL — DISTINCIÓN CRÍTICA:
  · IM_Productos_Proveedor.Costo_Cotizado = precio cotizado/negociado (no siempre el pagado)
  · IT_Movimientos_D.Costo_Unitario = costo REAL de la última compra efectiva
  · Para "último costo" o "cuánto se pagó" → usar IT_Movimientos_D + IT_Movimientos_C
  · Para "precio de catálogo" o "cotización" → usar IM_Productos_Proveedor

FORMATO ADICIONAL:
  · ⚠ NUNCA mostrar Cve_Proveedor en resultados — es código interno, mostrar Nombre.
  · Agrupar por proveedor cuando sea relevante.
  · Incluir siempre Contacto y Telefono cuando se listen datos de un proveedor.
"""

_SYSTEM = build(
    rol="Eres el agente especialista en PROVEEDORES de Abarrotes Suite (Super Juquila).",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre proveedores.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "medicos", **kwargs)
