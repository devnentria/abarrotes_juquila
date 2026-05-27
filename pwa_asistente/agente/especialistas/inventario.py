# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/inventario.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.2.0
# ============================================================
"""
Agente Especialista — Inventario.

Responde preguntas sobre stock, existencias, caducidades
y productos sin existencia por sucursal.
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
  Precio_Minimo_Venta_Base (decimal),
  Maximo (decimal), Minimo (decimal), Punto_Reorden (decimal),
  Status (varchar)
  ⚠ Filtrar: Status = 'AC'
  Para existencias actuales con total: GROUP BY ROLLUP usando ISNULL(s.Nombre,'── TOTAL')

IN_Existencias_Lote — existencias por lote (para caducidades)
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Producto (varchar),
  Cve_Presentacion (varchar), Num_Lote (varchar),
  Fecha_Caducidad (datetime), Existencia (decimal), Entrada (decimal)

IN_Existencias_Alm_Diario — snapshot histórico diario
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Producto (varchar),
  Cve_Presentacion (varchar), Fecha (datetime),
  Existencia (decimal), Comprometido (decimal),
  Costo_Ultima_Compra (decimal), Costo_Promedio (decimal)
  ⚠ Cobertura: enero 2024 en adelante · Cve_Producto es VARCHAR — CAST al unir con IM_Productos_Gral
  ⚠ Para fecha específica: registro más cercano anterior con subconsulta MAX(Fecha) <= 'YYYY-MM-DD'
  ⚠ Incluir todas las variantes con LIKE '%nombre%'; mostrar filas separadas distinguiendo promos de productos reales

It_Traspasos_C — encabezado de traspasos entre sucursales
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Movimiento (varchar),
  Cve_Folio (int), Fecha_Documento (datetime),
  Cve_Sucursal_Destino (smallint), Cve_Almacen_Destino (varchar),
  Cve_Movimiento_Salida (varchar), Cve_Folio_Salida (int),
  Cve_Movimiento_Entrada (varchar), Cve_Folio_Entrada (int),
  Status (char), Cve_Usuario (varchar)
  ⚠ Filtrar: Status <> 'C' (cancelados)

It_Traspasos_D — detalle de traspasos
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Movimiento (varchar),
  Cve_Folio (int), Cve_Partida (smallint),
  Cve_Producto (varchar), Cve_Presentacion (varchar),
  Cantidad (float), Cantidad_Recibida (float),
  Costo_Unitario (float), Costo_Ultima_Compra (float),
  Status (char)
  JOIN con It_Traspasos_C por: Cve_Folio + Cve_Sucursal + Cve_Almacen + Cve_Movimiento
  ⚠ Cantidad_Recibida puede diferir de Cantidad (traspasos parciales)

IT_Movimientos_C — cabecera de movimientos de almacén
  Cve_Sucursal (smallint), Cve_Almacen (varchar),
  Cve_Documento (char) → tipo de documento (char 3),
  Cve_Movimiento (varchar) → código operación (varchar 3) — DISTINTO de Cve_Documento,
  Cve_Folio (int), Fecha_Documento (datetime),
  Cve_Proveedor (varchar), Cve_Cliente (varchar),
  Status (char), Observaciones (varchar)
  Tipos Cve_Movimiento: EC=Entrada Compra, VTA=Venta, EA=Entrada Almacén, SA=Salida, ST/ET=Traspasos

IT_Movimientos_D — detalle de movimientos
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

IM_Codigos_Barra — códigos de barras de productos (una fila por variante/presentación)
  Cve_Producto (varchar), Codigo_Barras (varchar)
  ⚠ USAR cuando LIKE sobre IM_Productos_Gral.Descripcion devuelva 0 resultados o resultados sospechosos.
  ⚠ Razón: las promociones crean productos NUEVOS en IM_Productos_Gral con Cve_Producto distinto,
    pero el mismo código de barras. Buscar por Codigo_Barras consolida todas las variantes del producto.

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
"""

_REGLAS = """
REGLAS DE INVENTARIO:
  · Sin existencia:  Existencia <= 0
  · Stock crítico:   Existencia > 0 AND Existencia <= 5
  · Caducidad urgente: Fecha_Caducidad entre hoy y +30 días
  · Caducidad a revisar: Fecha_Caducidad entre hoy y +90 días
  · Caducados: Fecha_Caducidad < CAST(GETDATE() AS DATE)
  · Existencias históricas (en fecha pasada): consultar TODAS las sucursales por default — NUNCA pedir sucursal al usuario.
    Usar IN_Existencias_Alm_Diario con MAX(Fecha) <= 'YYYY-MM-DD' agrupado por sucursal.
  · Si piden existencias en fecha pasada sin especificar la fecha exacta: pedir SOLO la fecha, nunca la sucursal.
  · Para costo de compra: usar TOP 1 ORDER BY Fecha_Documento DESC por defecto. Solo AVG si el usuario lo pide.

PRODUCTOS PROMOCIONALES — REGLA CRÍTICA:
  El ERP crea productos nuevos en IM_Productos_Gral para cada promoción:
    "SAIZEN 20MG/60UI PIEZA PROMOCION GRATIS", "NORDITROPIN PROMO", etc.
  Estos productos tienen existencia propia (separada del producto real).

  ⚠ Cuando preguntan por existencias de un producto real (ej: "Saizen 20mg disponibles"):
    ✅ INCLUIR tanto el producto real como sus variantes promo — el usuario quiere saber todo el stock.
    ✅ Mostrar filas separadas: producto real + piezas promo, con su existencia individual.
    ✅ Aclarar en la respuesta cuáles son piezas de promoción gratuita.
    ⛔ NUNCA colapsar en un solo número sin distinguir los tipos.

  ⚠ Si el resultado muestra existencia 0 en TODAS las variantes, reportarlo claramente:
    "No hay existencia disponible de [producto] en [sucursal/general]."
    No confundir existencia 0 del producto promo con que no hay stock del producto real.

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
  · Incluir SIEMPRE todas las variantes/presentaciones del producto (normales + promos)
  · Si el producto tiene una sola variante: mostrar también el costo unitario promedio del período

TRASPASOS ENTRE SUCURSALES:
  · Consultar It_Traspasos_C + It_Traspasos_D para movimientos entre almacenes.
  · Cantidad vs Cantidad_Recibida: diferencia indica traspaso parcial o pendiente de confirmar.
  · JOIN GN_Sucursales por Cve_Sucursal (origen) y Cve_Sucursal_Destino para nombres.
  · JOIN IM_Productos_Gral p ON p.Cve_Producto = td.Cve_Producto para descripción del producto.

FORMATO ADICIONAL INVENTARIO:
  · ⚠ para alertas de caducidad próxima · 🔴 para sin existencia o caducado
  · Existencias históricas: mostrar desglose por sucursal/presentación + total general en negritas
"""

_SYSTEM = build(
    rol="Eres el agente especialista en INVENTARIO de Suite Analítica.",
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
