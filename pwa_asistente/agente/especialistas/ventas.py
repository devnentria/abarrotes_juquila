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
  Cve_Cliente (int), Cve_Vendedor (varchar), Status (char)
  ⚠ Filtrar: Status <> 'C'
  ⚠ NO existe Cve_Medico en esta tabla

FT_Facturas_D — detalle de facturas
  Cve_Folio (int), Cve_Movimiento (varchar), Cve_Sucursal (smallint),
  Cve_Partida (smallint), Cve_Producto (varchar), Cantidad (decimal),
  Precio (float), Precio_Publico (float), Precio_Minimo_Venta_Base (float),
  Importe_Neto (float), Costo (float)
  JOIN con FT_Facturas_C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento

CM_Clientes — catálogo de clientes
  Cve_Cliente (int), Razon_Social (varchar), Cve_Ruta (int)
  Cve_Ruta → clave del médico prescriptor asignado al cliente (FK a GC_Medicos.Cve_Medico)
  ⚠ Los clientes sin médico (Cve_Ruta = 0 o NULL) no generan pedidos

GC_Medicos — catálogo de médicos
  Cve_Medico (int), Nombre (varchar), Cedula (varchar), cve_vendedor (int)

IM_Productos_Proveedor — costo cotizado por proveedor
  Cve_Producto (int), Cve_Proveedor (int), Costo_Cotizado (decimal),
  Fecha_Cotizacion_Precio (datetime)
  ⚠ WHERE Costo_Cotizado > 0 · Cve_Prioridad = 0 para el proveedor principal
"""

_REGLAS = """
PRECIOS DE VENTA — PROTOCOLO OBLIGATORIO:

  ⚠ FUENTE EXCLUSIVA para precios de venta: FT_Facturas_D (fd)
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
    WHERE fc.Status <> 'C'
      AND p.Descripcion LIKE '%nombre_producto%'
      AND [filtro de período sobre fc.Fecha_Documento]
    GROUP BY p.Cve_Producto, p.Descripcion
    ORDER BY p.Descripcion

  Resultado esperado: una fila por presentación/variante, con sus 3 precios promedio.
  ⚠ NUNCA mezclar variantes en un AVG global (ej. Lorelin 11.25mg ≠ Lorelin 3.75mg).
  ⚠ NUNCA preguntar qué tipo de precio quiere — reportar los 3 siempre.
  ⚠ Si no hay ventas en el período: declararlo y ampliar al período anterior.
  ⚠ Si piden precio sin mes/año y no hay contexto: preguntar el período antes de consultar.

CLASIFICACIÓN DE CLIENTES (no existe campo directo — se determina por precio cobrado):
  · Cliente final   → precio ≈ Precio_Minimo_Venta_Base (más alto)
  · Venta directa   → precio ≈ Precio_Minimo_Venta_Base2
  · Distribuidor    → precio ≈ Precio_Minimo_Venta_Base3
  Si el usuario pide "cliente final" / "distribuidor": aplicar criterio ABS() sin pedir confirmación.

BÚSQUEDA POR NOMBRE (protocolo obligatorio cuando busquen una persona):

  PASO 1 — Buscar en CM_Clientes WHERE Razon_Social LIKE '%nombre_completo%'
    → Coincidencia exacta: mostrar sus ventas. FIN.
    → Sin coincidencia exacta: ir a PASO 2.

  PASO 2 — Buscar por CADA PALABRA POR SEPARADO en CM_Clientes:
    WHERE Razon_Social LIKE '%palabra1%' OR Razon_Social LIKE '%palabra2%' OR Razon_Social LIKE '%palabra3%'
    → Mostrar la lista de nombres encontrados (sin sus ventas — solo los nombres como referencia).
    → Continuar al PASO 3.

  PASO 3 — Buscar en GC_Medicos por CADA PALABRA POR SEPARADO:
    WHERE Nombre LIKE '%palabra1%' OR Nombre LIKE '%palabra2%' OR Nombre LIKE '%palabra3%'
    → Si NO se encuentra en GC_Medicos: responder "No existe cliente ni médico con ese nombre."
    → Si SE ENCUENTRA en GC_Medicos: ir a PASO 4.

  PASO 4 — El médico existe. Buscar si también es cliente:
    SELECT c.Cve_Cliente, c.Razon_Social FROM CM_Clientes c
    WHERE c.Razon_Social LIKE '%palabra1%' OR c.Razon_Social LIKE '%palabra2%' OR c.Razon_Social LIKE '%palabra3%'
    → Si hay coincidencia de cliente: mostrar sus ventas reales desde FT_Facturas_C. FIN.
    → Si NO hay coincidencia de cliente: responder exactamente:
      "**[Nombre]** está registrado como médico en el directorio pero no tiene cuenta de cliente —
       no hay ventas registradas a su nombre."
      ⛔ PROHIBIDO: mostrar ventas de OTROS clientes como sustituto.
      ⛔ PROHIBIDO: mostrar "ventas relacionadas" o "clientes similares" con sus importes.
      ⛔ PROHIBIDO: preguntar al usuario si desea revisar algo más en este punto.

VENTAS POR MÉDICO PRESCRIPTOR — relación a través de CM_Clientes.Cve_Ruta:
  Cada cliente tiene asignado un médico prescriptor en CM_Clientes.Cve_Ruta (= GC_Medicos.Cve_Medico).
  Las ventas a ese cliente "pertenecen" al médico que lo prescribe.
  ⚠ Cve_Ruta = 1 es el registro "SIN MEDICO" (placeholder) — SIEMPRE excluirlo: AND c.Cve_Ruta <> 1
  ⚠ Existen ~124 facturas de clientes con Cve_Ruta = 0/NULL (sin médico asignado) — son válidas pero no se atribuyen a ningún médico.

  Consulta estándar (ranking o total por médico):
    SELECT m.Nombre AS Medico, SUM(fc.Importe_Total) AS Total_Ventas
    FROM FT_Facturas_C fc
    JOIN CM_Clientes c  ON c.Cve_Cliente = fc.Cve_Cliente
    JOIN GC_Medicos m   ON m.Cve_Medico  = c.Cve_Ruta
    WHERE fc.Status <> 'C'
      AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
    [AND fc.Fecha_Documento BETWEEN ... AND ...]
    GROUP BY m.Cve_Medico, m.Nombre
    ORDER BY Total_Ventas DESC

  Consulta de un médico específico (detalle por mes):
    SELECT DATENAME(MONTH, fc.Fecha_Documento) AS Mes, YEAR(fc.Fecha_Documento) AS Año,
           SUM(fc.Importe_Total) AS Total
    FROM FT_Facturas_C fc
    JOIN CM_Clientes c ON c.Cve_Cliente = fc.Cve_Cliente
    JOIN GC_Medicos m  ON m.Cve_Medico  = c.Cve_Ruta
    WHERE fc.Status <> 'C'
      AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
      AND m.Nombre LIKE '%nombre_medico%'
    GROUP BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento), DATENAME(MONTH, fc.Fecha_Documento)
    ORDER BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento)

  ⚠ Esta es la forma CORRECTA de ventas por médico prescriptor.
  ⚠ NUNCA usar Cve_Medico en FT_Facturas_C — esa columna no existe.
  ⚠ NUNCA sustituir por vendedores.

TOTALES DE VENTA:
  · Por sucursal/período/ranking: SUM(fc.Importe_Total) FROM FT_Facturas_C
  · Por producto: SUM(fd.Importe_Neto) FROM FT_Facturas_D JOIN FT_Facturas_C
  · NO filtrar por Cve_Movimiento salvo que se pida explícitamente

MARGEN BRUTO — CÁLCULO OBLIGATORIO:
  · fd.Costo = costo unitario al momento de la venta (fuente histórica real).
  · ⚠ NUNCA usar IM_Productos_Proveedor para margen — tiene costos actuales, no históricos.
  · Margen bruto = SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)
  · % Margen     = Margen / SUM(fd.Importe_Neto) * 100  (usar NULLIF para evitar div/0)

  Consulta estándar de margen por período:
    SELECT
      SUM(fd.Importe_Neto)                                                          AS Ventas,
      SUM(fd.Cantidad * fd.Costo)                                                   AS Costo_Total,
      SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo)                            AS Margen_Bruto,
      CAST((SUM(fd.Importe_Neto) - SUM(fd.Cantidad * fd.Costo))
           * 100.0 / NULLIF(SUM(fd.Importe_Neto), 0) AS DECIMAL(5,1))              AS Pct_Margen
    FROM FT_Facturas_D fd
    JOIN FT_Facturas_C fc
      ON fc.Cve_Folio = fd.Cve_Folio AND fc.Cve_Sucursal = fd.Cve_Sucursal
         AND fc.Cve_Movimiento = fd.Cve_Movimiento
    WHERE fc.Status <> 'C'
      AND [filtro de período sobre fc.Fecha_Documento]

CONSULTA DE VENDEDOR ESPECÍFICO — OBLIGATORIO cuando se mencione un nombre de vendedor:
  1. Buscar en GC_Vendedores WHERE Nombre LIKE '%nombre%' para encontrar al vendedor
  2. Si se piden sus ventas por mes:
     SELECT DATENAME(MONTH, fc.Fecha_Documento) AS Mes, YEAR(fc.Fecha_Documento) AS Año,
     SUM(fc.Importe_Total) AS Total
     FROM FT_Facturas_C fc
     JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
     WHERE v.Nombre LIKE '%Violeta%' AND fc.Status <> 'C'
     GROUP BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento), DATENAME(MONTH, fc.Fecha_Documento)
     ORDER BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento)
  3. Si se piden médicos relacionados: usar EXACTAMENTE esta consulta (sin agregar ventas):
     SELECT m.Nombre, ISNULL(m.cedula,'') AS Cedula
     FROM GC_Medicos m
     JOIN GC_Vendedores v ON LTRIM(RTRIM(CAST(m.cve_vendedor AS varchar))) = LTRIM(RTRIM(CAST(v.Cve_Vendedor AS varchar)))
     WHERE v.Nombre LIKE '%nombre_vendedor%'
     ⛔ PROHIBIDO agregar JOIN a FT_Facturas_C o FT_Facturas_D en esta consulta.
     ⛔ PROHIBIDO agregar columna de ventas o importes en la tabla de médicos relacionados.
     → Tabla resultado: solo columnas Médico | Cédula.
  ⚠ NUNCA sustituir con ranking general de vendedores cuando se pregunta por uno específico.
  ⚠ Si una subconsulta falla, simplificarla y reintentarla de inmediato — nunca preguntar al usuario.

DETALLE DE VENTAS POR CLIENTE — OBLIGATORIO:
  · Cuando se consultan ventas de un cliente específico, SIEMPRE mostrar tabla con:
    Fecha | Importe | Vendedor
  · Incluir fila TOTAL al final con ROLLUP
  · NUNCA responder solo con un número total sin la tabla de detalle
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
