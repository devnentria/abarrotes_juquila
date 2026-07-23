# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/ventas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Agente Especialista — Ventas.

Responde preguntas sobre ventas (remisiones autoservicio + facturas
mayoreo), importes, comparativos, productos más vendidos y rendimiento
por sucursal o vendedor.

Fuentes de venta:
  • Autoservicio → FT_Remisiones_C / FT_Remisiones_D
  • Mayoreo      → FT_Facturas_C  / FT_Facturas_D
"""
import re
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

# Palabras genéricas que NO son productos
_NO_PRODUCTO = frozenset([
    "enero","febrero","marzo","abril","mayo","junio","julio","agosto",
    "septiembre","octubre","noviembre","diciembre","mes","año","trimestre",
    "hoy","ayer","semana","quincena","periodo","período","fecha","dia","día",
    "ventas","venta","importe","total","piezas","monto","factura","facturas",
    "remision","remisiones","sucursal","sucursales","todas","nacional",
    "juquila","super","matriz","bodega",
    "cuantas","cuántas","cuanto","cuánto","fueron","fue","dame","dime","muestra",
    "de","en","el","la","los","las","por","del","al","entre","y","a","que","cual",
    "cuales","fueron","fueron","fue","han","sido","es","son",
])

_PATRON_PRODUCTO = re.compile(
    r'(?:ventas?\s+de\s+|cuánto[s]?\s+(?:vendimos\s+)?de\s+|cuanto[s]?\s+(?:vendimos\s+)?de\s+)'
    r'([A-Za-záéíóúñÁÉÍÓÚÑ0-9][A-Za-záéíóúñÁÉÍÓÚÑ0-9\s\-\.\/]{1,40}?)'
    r'(?:\s+(?:en|entre|del|al|de|por|el)\b|$)',
    re.IGNORECASE,
)


def _detectar_producto(pregunta: str) -> str:
    """Retorna el nombre de producto detectado, o '' si la pregunta es general."""
    match = _PATRON_PRODUCTO.search(pregunta)
    if not match:
        return ""
    candidato = match.group(1).strip()
    palabras_significativas = [p for p in candidato.lower().split() if p not in _NO_PRODUCTO]
    return candidato if palabras_significativas else ""

_SCHEMA = """
TABLAS DE VENTAS:

Super Juquila registra sus ventas en DOS fuentes separadas que se
consultan con UNION ALL:

══════════════════════════════════════════════════════════════
1) AUTOSERVICIO — FT_Remisiones_C + FT_Remisiones_D
══════════════════════════════════════════════════════════════
FT_Remisiones_C — encabezado de remisiones (autoservicio / punto de venta)
  Cve_Folio (int), Cve_Sucursal (smallint), Cve_Movimiento (varchar),
  Cve_Cliente (varchar),
  Fecha_Documento (datetime),
  Status (char) → 'AC'=activa, 'CA'=cancelada,
  Importe_Neto (float) → total neto de la remisión (usar para agregados)
  ⚠ Filtrar: Status = 'AC' AND Cve_Movimiento = 'VTA'
  ⚠ El Cve_Cliente suele ser '/' (venta anónima de mostrador)
  ⚠ NO tiene Cve_Vendedor
  ⚠ SIEMPRE filtrar por Fecha_Documento — hay 23M+ filas en detalle

FT_Remisiones_D — detalle de remisiones (autoservicio)
  Cve_Folio (int), Cve_Sucursal (smallint), Cve_Movimiento (varchar),
  Cve_Partida (smallint), Cve_Producto (varchar), Cve_Presentacion (varchar),
  Cantidad (decimal),
  Precio (float), Importe_Neto (float),
  Costo (float), Costo_Promedio (float)
  JOIN con FT_Remisiones_C por:
    d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
    AND d.Cve_Movimiento = c.Cve_Movimiento

══════════════════════════════════════════════════════════════
2) MAYOREO — FT_Facturas_C + FT_Facturas_D
══════════════════════════════════════════════════════════════
FT_Facturas_C — encabezado de facturas (mayoreo)
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Cliente (varchar), Cve_Vendedor (varchar),
  Fecha_Documento (datetime), Fecha_Vencimiento (datetime),
  Importe_bruto (float), Importe_Descuento (decimal),
  Importe_IVA (float), Importe_Total (float) → total de la factura (usar para agregados),
  Costo_Neto (float),
  Status (char) → 'AC'=activa, 'CA'=cancelada,
  Pagada (varchar), Cve_Lista_precios (smallint),
  Referencia_Cliente (varchar), Cve_Consignatario (int)
  ⚠ Filtrar: Status = 'AC' AND Cve_Movimiento IN ('FM','FP')
  ⚠ Cve_Cliente es varchar(10) aunque FK a CM_Clientes.Cve_Cliente (int) — usar CAST al JOIN

FT_Facturas_D — detalle de facturas (mayoreo)
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Partida (smallint), Cve_Producto (varchar), Cve_Presentacion (varchar),
  Cantidad (decimal), Cantidad_Ordenada (decimal), Cantidad_Devuelta (decimal),
  Precio (float)              → precio real cobrado al cliente,
  Precio_Publico (float)      → precio público de lista,
  Precio_Sugerido_Cte (float) → precio sugerido según lista del cliente,
  Precio_Minimo_Venta_Base (float) → precio lista cliente final,
  Importe_Bruto (float), Importe_Descuentos (float), Importe_Subtotal (float),
  Importe_Neto (float), Importe_Iva (float),
  Costo (float), Costo_Promedio (float), Costo_Oper (float)
  JOIN con FT_Facturas_C por:
    d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
    AND d.Cve_Movimiento = c.Cve_Movimiento

══════════════════════════════════════════════════════════════

CM_Clientes — catálogo de clientes
  Cve_Cliente (int), Razon_Social (varchar), Cve_Ruta (int)
  ⚠ Usar Razon_Social (NO "Nombre_Cliente" — no existe)
  ⚠ Clientes solo son relevantes en FT_Facturas_C (mayoreo)
  ⚠ En autoservicio Cve_Cliente='/' → venta anónima de mostrador

PM_Proveedores — catálogo de proveedores
  Cve_Proveedor (varchar), Razon_Social (varchar)

IM_Productos_Proveedor — costo cotizado por proveedor
  Cve_Producto (varchar), Cve_Proveedor (varchar), Cve_Prioridad (smallint),
  Costo_Cotizado (decimal), Fecha_Cotizacion_Costo (datetime),
  Precio_Venta (decimal), Fecha_Cotizacion_Precio (datetime)
  ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad = 0 para el proveedor principal
"""

_REGLAS = """
══════════════════════════════════════════════════════════════
⚠⚠⚠ REGLA ABSOLUTA — SE EVALÚA PRIMERO ⚠⚠⚠
══════════════════════════════════════════════════════════════
DOS FUENTES DE VENTA — SIEMPRE COMBINAR CON UNION ALL:
  Super Juquila tiene dos canales: autoservicio (remisiones) y mayoreo (facturas).
  Para obtener la venta TOTAL de la empresa se deben sumar ambos con UNION ALL.

  ⚠ SIEMPRE filtrar por fecha — las tablas de detalle tienen millones de filas.
  ⚠ SIEMPRE filtrar Cve_Sucursal <> 99.

══════════════════════════════════════════════════════════════
CONSULTA ESTÁNDAR — TOTALES DE VENTA (sin detalle de producto)
══════════════════════════════════════════════════════════════
  Para TOTALES se usan los importes del encabezado (sin JOIN a detalle):

    SELECT SUM(Total) AS Venta_Total, SUM(Docs) AS Documentos
    FROM (
        -- Autoservicio
        SELECT SUM(c.Importe_Neto) AS Total, COUNT(*) AS Docs
        FROM FT_Remisiones_C c
        WHERE c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
          AND c.Cve_Sucursal <> 99
          AND [filtro de fecha sobre c.Fecha_Documento]
        UNION ALL
        -- Mayoreo
        SELECT SUM(c.Importe_Total) AS Total, COUNT(*) AS Docs
        FROM FT_Facturas_C c
        WHERE c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
          AND c.Cve_Sucursal <> 99
          AND [filtro de fecha sobre c.Fecha_Documento]
    ) t

  • Para HOY:  CAST(c.Fecha_Documento AS DATE) = CAST(GETDATE() AS DATE)
  • Para AYER: CAST(c.Fecha_Documento AS DATE) = CAST(DATEADD(DAY,-1,GETDATE()) AS DATE)
  • Para un MES: MONTH(c.Fecha_Documento) = ? AND YEAR(c.Fecha_Documento) = ?

══════════════════════════════════════════════════════════════
CONSULTA ESTÁNDAR — TOTALES POR SUCURSAL
══════════════════════════════════════════════════════════════

    SELECT s.Nombre AS Sucursal,
           SUM(t.Total) AS Venta_Total,
           SUM(t.Docs) AS Documentos
    FROM (
        SELECT c.Cve_Sucursal, c.Importe_Neto AS Total, 1 AS Docs
        FROM FT_Remisiones_C c
        WHERE c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
          AND c.Cve_Sucursal <> 99
          AND [filtro de fecha]
        UNION ALL
        SELECT c.Cve_Sucursal, c.Importe_Total AS Total, 1 AS Docs
        FROM FT_Facturas_C c
        WHERE c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
          AND c.Cve_Sucursal <> 99
          AND [filtro de fecha]
    ) t
    JOIN GN_Sucursales s ON s.Cve_Sucursal = t.Cve_Sucursal
    GROUP BY s.Nombre
    ORDER BY Venta_Total DESC

══════════════════════════════════════════════════════════════
VENTAS DE UN PRODUCTO ESPECÍFICO
══════════════════════════════════════════════════════════════
  Si la pregunta menciona un producto (ej: "ventas de Coca-Cola", "cuánto de Atún",
  "Aceite en enero") — esto es una consulta de PRODUCTO ESPECÍFICO.

  ⛔⛔ NUNCA devolver ventas totales del período sin filtrar por producto.
  ⛔⛔ NUNCA generar "Reporte Ejecutivo" ni panorama general cuando se pidió un producto concreto.
  ✅ SIEMPRE hacer JOIN a detalle + IM_Productos_Gral p y filtrar por p.Descripcion LIKE '%nombre%'.

  ⚠ ENVÍO ESPECIAL — excluir SIEMPRE de listas y rankings de productos:
    "ENVIO ESPECIAL" es un cargo por flete, no un producto real. Sí se suma al total de ventas
    pero NUNCA debe aparecer en tops, rankings ni detalles de productos.
    ✅ FILTRO OBLIGATORIO en cualquier SELECT que liste productos:
       AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'

  ⚠ TOTAL OBLIGATORIO — siempre calcular en SQL con ROLLUP, NUNCA manualmente:
    GROUP BY ROLLUP(p.Descripcion) con ISNULL(p.Descripcion,'── TOTAL')

  Consulta estándar para ventas de un producto en un período:
    SELECT ISNULL(p.Descripcion, '── TOTAL') AS Descripcion,
           SUM(Piezas) AS Piezas,
           SUM(Importe) AS Total,
           SUM(Docs) AS Documentos
    FROM (
        -- Autoservicio
        SELECT p.Descripcion,
               d.Cantidad AS Piezas,
               d.Importe_Neto AS Importe,
               1 AS Docs
        FROM FT_Remisiones_C c
        JOIN FT_Remisiones_D d
          ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
             AND d.Cve_Movimiento = c.Cve_Movimiento
        JOIN IM_Productos_Gral p ON p.Cve_Producto = d.Cve_Producto
        WHERE c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
          AND c.Cve_Sucursal <> 99
          AND p.Descripcion LIKE '%NOMBRE%'
          AND p.Descripcion NOT LIKE '%GRATIS%'
          AND d.Precio > 1
          AND [filtro de fecha sobre c.Fecha_Documento]
        UNION ALL
        -- Mayoreo
        SELECT p.Descripcion,
               d.Cantidad AS Piezas,
               d.Importe_Neto AS Importe,
               1 AS Docs
        FROM FT_Facturas_C c
        JOIN FT_Facturas_D d
          ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
             AND d.Cve_Movimiento = c.Cve_Movimiento
        JOIN IM_Productos_Gral p ON p.Cve_Producto = d.Cve_Producto
        WHERE c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
          AND c.Cve_Sucursal <> 99
          AND p.Descripcion LIKE '%NOMBRE%'
          AND p.Descripcion NOT LIKE '%GRATIS%'
          AND d.Precio > 1
          AND [filtro de fecha sobre c.Fecha_Documento]
    ) t
    JOIN IM_Productos_Gral p2 ON 1=0  -- solo para ROLLUP label
    GROUP BY ROLLUP(Descripcion)
    ORDER BY GROUPING(Descripcion), Piezas DESC

  ⚠ SIMPLIFICACIÓN: como ambas ramas del UNION ALL ya traen p.Descripcion,
    se puede hacer GROUP BY ROLLUP(Descripcion) directamente sobre la subquery.

  Forma más limpia:
    SELECT ISNULL(Descripcion, '── TOTAL') AS Descripcion,
           SUM(Piezas) AS Piezas,
           SUM(Importe) AS Total
    FROM ( ...UNION ALL... ) t
    GROUP BY ROLLUP(Descripcion)
    ORDER BY GROUPING(Descripcion), Piezas DESC

  Si el usuario pregunta explícitamente por "piezas gratis" o "regalías":
    ✅ Quitar AND d.Precio > 1 y filtrar WHERE d.Precio <= 1.

══════════════════════════════════════════════════════════════

PRECIOS DE VENTA — PROTOCOLO OBLIGATORIO:

  ⚠ FUENTE EXCLUSIVA para precios de venta: FT_Facturas_D (fd) — solo mayoreo tiene precios de lista
  ⚠ NUNCA usar IT_Movimientos_D ni IT_Movimientos_C para precios de venta — son tablas de inventario.
  ⚠ NUNCA devolver filas individuales sin GROUP BY — agrega SIEMPRE con AVG y GROUP BY.

  Consulta estándar (una sola query, cubre todas las variantes del producto):
    SELECT
      p.Descripcion                    AS Presentacion,
      AVG(fd.Precio_Publico)           AS Precio_Publico_Prom,
      AVG(fd.Precio_Minimo_Venta_Base) AS Precio_Base_Prom,
      AVG(fd.Precio)                   AS Precio_Pactado_Prom,
      COUNT(DISTINCT fc.Cve_Folio)     AS Num_Ventas
    FROM FT_Facturas_D fd
    JOIN FT_Facturas_C fc
      ON fc.Cve_Folio = fd.Cve_Folio AND fc.Cve_Sucursal = fd.Cve_Sucursal
         AND fc.Cve_Movimiento = fd.Cve_Movimiento
    JOIN IM_Productos_Gral p ON p.Cve_Producto = fd.Cve_Producto
    WHERE fc.Status = 'AC' AND fc.Cve_Movimiento IN ('FM','FP')
      AND p.Descripcion LIKE '%nombre_producto%'
      AND [filtro de período sobre fc.Fecha_Documento]
    GROUP BY p.Cve_Producto, p.Descripcion
    ORDER BY p.Descripcion

  Resultado esperado: una fila por presentación/variante, con sus 3 precios promedio.
  ⚠ NUNCA usar MIN() ni MAX() para precios — producen valores atípicos sin contexto.
  ⚠ NUNCA mezclar variantes en un AVG global.
  ⚠ NUNCA preguntar qué tipo de precio quiere — reportar los 3 siempre.
  · fd.Precio_Minimo_Venta_Base existe en FT_Facturas_D (precio capturado al momento de la venta).
    Para precios de lista actuales (Base2, Base3): usar IM_Productos_Gral.
  ⚠ Si no hay ventas en el período: declararlo y ampliar al período anterior.
  ⚠ Si piden precio sin mes/año y no hay contexto: preguntar el período antes de consultar.

CLASIFICACIÓN DE CLIENTES — FUENTE CORRECTA:
  El tipo de cliente se determina por CM_Clientes.Cve_Lista_Precios:
  · 0 = Cliente final / Mostrador  → usa Precio_Minimo_Venta_Base  (precio más alto)
  · 1 = Venta directa / Ruta       → usa Precio_Minimo_Venta_Base2 (precio intermedio)
  · 2 = Distribuidor / Mayoreo     → usa Precio_Minimo_Venta_Base3 (precio más bajo)

  Los precios de lista (Base, Base2, Base3) están en IM_Productos_Gral — NUNCA en FT_Facturas_D.
  Para comparar precio cobrado (fd.Precio) vs precio de lista: JOIN IM_Productos_Gral p ON p.Cve_Producto = fd.Cve_Producto.

  CONSULTA TIPO para ventas por tipo de cliente (solo mayoreo — autoservicio es anónimo):
    JOIN CM_Clientes cl ON cl.Cve_Cliente = CAST(fc.Cve_Cliente AS INT)
    WHERE cl.Cve_Lista_Precios = 0  -- 0=final, 1=directa, 2=distribuidor

EXCLUSIÓN OBLIGATORIA — TOP CLIENTES:
  "VENTA DE MOSTRADOR" es un cliente genérico para ventas de caja/contado anónimas — NO es un cliente real.
  En TODA consulta de ranking, top o mejores clientes por ventas:
    ✅ SIEMPRE hacer JOIN a CM_Clientes cl ON cl.Cve_Cliente = CAST(fc.Cve_Cliente AS INT)
    ✅ SIEMPRE filtrar: AND cl.Razon_Social NOT LIKE '%MOSTRADOR%'
  ⛔ NUNCA reportar "VENTA DE MOSTRADOR" en rankings de clientes — aunque tenga el mayor importe.
  ⛔ El JOIN a CM_Clientes con ese filtro es OBLIGATORIO en rankings — no es opcional.
  ⚠ Rankings de clientes SOLO aplican a mayoreo (FT_Facturas_C) — autoservicio es anónimo.

BÚSQUEDA POR NOMBRE (protocolo obligatorio cuando busquen una persona):

  PASO 1 — Buscar en CM_Clientes WHERE Razon_Social LIKE '%nombre_completo%'
    → Coincidencia exacta: mostrar sus ventas. FIN.
    → Sin coincidencia exacta: ir a PASO 2.

  PASO 2 — Buscar por CADA PALABRA POR SEPARADO en CM_Clientes:
    WHERE Razon_Social LIKE '%palabra1%' OR Razon_Social LIKE '%palabra2%' OR Razon_Social LIKE '%palabra3%'
    → Mostrar la lista de nombres encontrados (sin sus ventas — solo los nombres como referencia).
    → Continuar al PASO 3.

  PASO 3 — Buscar en PM_Proveedores (proveedores) por CADA PALABRA POR SEPARADO:
    WHERE Razon_Social LIKE '%palabra1%' OR Razon_Social LIKE '%palabra2%' OR Razon_Social LIKE '%palabra3%'
    → Si NO se encuentra en PM_Proveedores: responder "No existe cliente ni proveedor con ese nombre."
    → Si SE ENCUENTRA en PM_Proveedores: ir a PASO 4.

  PASO 4 — El proveedor existe. Buscar si también es cliente:
    SELECT c.Cve_Cliente, c.Razon_Social FROM CM_Clientes c
    WHERE c.Razon_Social LIKE '%palabra1%' OR c.Razon_Social LIKE '%palabra2%' OR c.Razon_Social LIKE '%palabra3%'
    → Si hay coincidencia de cliente: mostrar sus ventas reales desde FT_Facturas_C. FIN.
    → Si NO hay coincidencia de cliente: responder exactamente:
      "**[Nombre]** está registrado como proveedor pero no tiene cuenta de cliente —
       no hay ventas registradas a su nombre."
      ⛔ PROHIBIDO: mostrar ventas de OTROS clientes como sustituto.
      ⛔ PROHIBIDO: mostrar "ventas relacionadas" o "clientes similares" con sus importes.
      ⛔ PROHIBIDO: preguntar al usuario si desea revisar algo más en este punto.

TOTALES DE VENTA — FUENTE COMBINADA OBLIGATORIA:
  ⚠ SIEMPRE combinar FT_Remisiones_C (autoservicio) + FT_Facturas_C (mayoreo) con UNION ALL.
  ⚠ Para totales agregados usar Importe_Neto (remisiones) e Importe_Total (facturas) del encabezado.
  ⚠ Para detalle por producto hacer JOIN a las tablas _D respectivas y usar d.Importe_Neto / d.Cantidad.
  ⚠ SIEMPRE filtrar Cve_Sucursal <> 99.
  ⚠ SIEMPRE incluir filtro de fecha — las tablas tienen millones de filas.

MARGEN BRUTO — CÁLCULO OBLIGATORIO:
  · fd.Costo = costo unitario al momento de la venta (fuente histórica real).
  · ⚠ NUNCA usar IM_Productos_Proveedor para margen — tiene costos actuales, no históricos.
  · Margen bruto = SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)
  · % Margen     = Margen / SUM(fd.Importe_Neto) * 100  (usar NULLIF para evitar div/0)

  Consulta estándar de margen por período (combinar ambas fuentes):
    SELECT SUM(Ventas) AS Ventas, SUM(Costo_Total) AS Costo_Total,
           SUM(Ventas) - SUM(Costo_Total) AS Margen_Bruto,
           CAST((SUM(Ventas) - SUM(Costo_Total)) * 100.0
                / NULLIF(SUM(Ventas), 0) AS DECIMAL(5,1)) AS Pct_Margen
    FROM (
        -- Autoservicio
        SELECT SUM(d.Importe_Neto) AS Ventas,
               SUM(d.Cantidad * d.Costo) AS Costo_Total
        FROM FT_Remisiones_D d
        JOIN FT_Remisiones_C c
          ON c.Cve_Folio = d.Cve_Folio AND c.Cve_Sucursal = d.Cve_Sucursal
             AND c.Cve_Movimiento = d.Cve_Movimiento
        WHERE c.Status = 'AC' AND c.Cve_Movimiento = 'VTA'
          AND c.Cve_Sucursal <> 99
          AND [filtro de período sobre c.Fecha_Documento]
        UNION ALL
        -- Mayoreo
        SELECT SUM(d.Importe_Neto) AS Ventas,
               SUM(d.Cantidad * d.Costo) AS Costo_Total
        FROM FT_Facturas_D d
        JOIN FT_Facturas_C c
          ON c.Cve_Folio = d.Cve_Folio AND c.Cve_Sucursal = d.Cve_Sucursal
             AND c.Cve_Movimiento = d.Cve_Movimiento
        WHERE c.Status = 'AC' AND c.Cve_Movimiento IN ('FM','FP')
          AND c.Cve_Sucursal <> 99
          AND [filtro de período sobre c.Fecha_Documento]
    ) t

CONSULTA DE VENDEDOR ESPECÍFICO — OBLIGATORIO cuando se mencione un nombre de vendedor:
  ⚠ Solo FT_Facturas_C (mayoreo) tiene Cve_Vendedor — autoservicio NO registra vendedor.
  1. Buscar en GC_Vendedores WHERE Nombre LIKE '%nombre%' para encontrar al vendedor
  2. Si se piden sus ventas por mes:
     SELECT DATENAME(MONTH, fc.Fecha_Documento) AS Mes, YEAR(fc.Fecha_Documento) AS Año,
     SUM(fd.Importe_Neto) AS Total
     FROM FT_Facturas_C fc
     JOIN FT_Facturas_D fd ON fd.Cve_Folio = fc.Cve_Folio AND fd.Cve_Sucursal = fc.Cve_Sucursal AND fd.Cve_Movimiento = fc.Cve_Movimiento
     JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
     WHERE v.Nombre LIKE '%nombre%' AND fc.Status = 'AC'
       AND fc.Cve_Movimiento IN ('FM','FP') AND fc.Cve_Sucursal <> 99
       AND [filtro de fecha]
     GROUP BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento), DATENAME(MONTH, fc.Fecha_Documento)
     ORDER BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento)
  ⚠ NUNCA sustituir con ranking general de vendedores cuando se pregunta por uno específico.
  ⚠ Si una subconsulta falla, simplificarla y reintentarla de inmediato — nunca preguntar al usuario.

DETALLE DE VENTAS POR CLIENTE — OBLIGATORIO:
  · Solo aplica a mayoreo (FT_Facturas_C) — autoservicio es anónimo.
  · Cuando se consultan ventas de un cliente específico, SIEMPRE mostrar tabla con:
    Fecha | Importe | Vendedor
  · Incluir fila TOTAL al final con ROLLUP
  · NUNCA responder solo con un número total sin la tabla de detalle

SUCURSALES — NOMBRES EXACTOS EN EL ERP:
  Las sucursales de Super Juquila se obtienen de GN_Sucursales.
  ⚠ SIEMPRE JOIN a GN_Sucursales s ON s.Cve_Sucursal = c.Cve_Sucursal para obtener el nombre.
  ⚠ NUNCA asumir claves de sucursal — buscar por s.Nombre LIKE '%nombre%'.
  ⚠ SIEMPRE filtrar Cve_Sucursal <> 99.

VENTAS POR SUCURSAL ESPECÍFICA — REGLA CRÍTICA:
  Cuando la pregunta menciona una sucursal (ej: "ventas en Juquila centro", "cómo va la bodega"):
  ⛔ NUNCA devolver ventas de todas las sucursales — el resultado sería incorrecto y confuso.
  ⛔ NUNCA omitir AND s.Nombre LIKE '%nombre_sucursal%' en el WHERE.
  ✅ SIEMPRE JOIN a GN_Sucursales s ON s.Cve_Sucursal=c.Cve_Sucursal + filtrar por s.Nombre.
  ✅ El número que reportes debe coincidir SOLO con esa sucursal, no con el total de la empresa.

  PROTOCOLO SI NO ENCUENTRA LA SUCURSAL:
    1. Buscar con LIKE '%nombre%' — si hay coincidencia, usar esa sucursal.
    2. Si no hay coincidencia: consultar SELECT Nombre FROM GN_Sucursales WHERE Cve_Sucursal <> 99
       y mostrar la lista al usuario para que elija la correcta.
    ⛔ NUNCA responder "no hay ventas" si la sucursal no existe — primero mostrar la lista disponible.

BÚSQUEDA DE PRODUCTOS — REGLA CRÍTICA:
  Las descripciones pueden tener espacios entre número y unidad.
  ⚠ Buscar por términos separados con múltiples LIKE:
    p.Descripcion LIKE '%TERMINO1%' AND p.Descripcion LIKE '%TERMINO2%'
"""

_SYSTEM = build(
    rol="Eres el agente especialista en VENTAS de Abarrotes Suite para Super Juquila (cadena de supermercados con 18 sucursales).",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre ventas.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "ventas", **kwargs)
