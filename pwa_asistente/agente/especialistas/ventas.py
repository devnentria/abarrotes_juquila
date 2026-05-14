# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/ventas.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
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

══════════════════════════════════════════════════════════════
VENTAS REALIZADAS/PAGADAS DEL DÍA — FUENTE CORRECTA
══════════════════════════════════════════════════════════════
FT_Pedidos_C — encabezado de pedidos (también es el encabezado de ventas diarias)
  Cve_Folio (int), Cve_Sucursal (int), Cve_Vendedor (varchar),
  Fecha_Documento (datetime), Estatus (varchar), Referencia_Cliente (varchar)
  ⚠ Para ventas realizadas: Estatus <> 'CN' AND Referencia_Cliente = 'PAGADO'

FT_Pedidos_Dia — detalle de ventas diarias (tabla de venta real, no de cartera)
  Cve_Folio (int), Cve_Sucursal (int),
  Cantidad_Ordenada (decimal), Precio (decimal)
  JOIN con FT_Pedidos_C por: d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
  ⚠ Importe de venta = Cantidad_Ordenada * Precio

CONSULTA ESTÁNDAR para ventas del día/hoy/ayer/período específico (PAGADAS):
  SELECT COUNT(cve_folio) AS Pedidos, ISNULL(SUM(Monto),0) AS Monto
  FROM (
      SELECT c.Cve_Folio, ISNULL(SUM(d.Cantidad_Ordenada * d.Precio),0) AS Monto
      FROM FT_Pedidos_C c
      INNER JOIN FT_Pedidos_Dia d
        ON d.Cve_Folio = c.Cve_Folio AND d.Cve_Sucursal = c.Cve_Sucursal
      WHERE c.Estatus <> 'CN'
        AND c.Referencia_Cliente = 'PAGADO'
        AND [filtro de fecha sobre c.Fecha_Documento]
        -- Opcional: AND c.Cve_Sucursal = ? / AND c.Cve_Vendedor = '?'
      GROUP BY c.Cve_Folio
  ) AS t

  • Para HOY:  CAST(c.Fecha_Documento AS DATE) = CAST(GETDATE() AS DATE)
  • Para AYER: CAST(c.Fecha_Documento AS DATE) = CAST(DATEADD(DAY,-1,GETDATE()) AS DATE)
  • Para un MES: MONTH(c.Fecha_Documento) = ? AND YEAR(c.Fecha_Documento) = ?

  ⚠ SIEMPRE usar esta consulta cuando se pregunte por ventas del día, hoy, ayer o una fecha puntual.
  ⚠ NUNCA usar FT_Facturas_C/D para ventas diarias — las facturas pueden no reflejar el día real.
  ⚠ Por sucursal: agregar AND c.Cve_Sucursal = [cve] dentro del WHERE interior.
  ⚠ Por vendedor: agregar AND c.Cve_Vendedor = '[cve_vendedor]'.
══════════════════════════════════════════════════════════════

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
  ⚠ NUNCA usar MIN() ni MAX() para precios — producen valores atípicos sin contexto.
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
    SELECT m.Nombre AS Medico, SUM(fd.Importe_Neto) AS Total_Ventas
    FROM FT_Facturas_C fc
    JOIN FT_Facturas_D fd ON fd.Cve_Folio = fc.Cve_Folio AND fd.Cve_Sucursal = fc.Cve_Sucursal AND fd.Cve_Movimiento = fc.Cve_Movimiento
    JOIN CM_Clientes c  ON c.Cve_Cliente = fc.Cve_Cliente
    JOIN GC_Medicos m   ON m.Cve_Medico  = c.Cve_Ruta
    WHERE fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
      AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
    [AND fc.Fecha_Documento BETWEEN ... AND ...]
    GROUP BY m.Cve_Medico, m.Nombre
    ORDER BY Total_Ventas DESC

  Consulta de un médico específico (detalle por mes):
    SELECT DATENAME(MONTH, fc.Fecha_Documento) AS Mes, YEAR(fc.Fecha_Documento) AS Año,
           SUM(fd.Importe_Neto) AS Total
    FROM FT_Facturas_C fc
    JOIN FT_Facturas_D fd ON fd.Cve_Folio = fc.Cve_Folio AND fd.Cve_Sucursal = fc.Cve_Sucursal AND fd.Cve_Movimiento = fc.Cve_Movimiento
    JOIN CM_Clientes c ON c.Cve_Cliente = fc.Cve_Cliente
    JOIN GC_Medicos m  ON m.Cve_Medico  = c.Cve_Ruta
    WHERE fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
      AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
      AND m.Nombre LIKE '%nombre_medico%'
    GROUP BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento), DATENAME(MONTH, fc.Fecha_Documento)
    ORDER BY YEAR(fc.Fecha_Documento), MONTH(fc.Fecha_Documento)

  ⚠ Esta es la forma CORRECTA de ventas por médico prescriptor.
  ⚠ NUNCA usar Cve_Medico en FT_Facturas_C — esa columna no existe.
  ⚠ NUNCA sustituir por vendedores.

TOTALES DE VENTA — REGLA CRÍTICA (para que los números coincidan con el dashboard, sin IVA):
  ⚠ SIEMPRE usar SUM(fd.Importe_Neto) de FT_Facturas_D para cualquier total de ventas — período, sucursal, ranking o producto.
  ⚠ NUNCA usar fc.Importe_Total de FT_Facturas_C — ese campo incluye IVA y no coincide con el reporte de ventas.
  ⚠ SIEMPRE filtrar fc.Cve_Sucursal <> 99 en la query aunque no haya JOIN a GN_Sucursales.
  · Para todo total de ventas: JOIN FT_Facturas_C fc + FT_Facturas_D fd, luego SUM(fd.Importe_Neto)
    WHERE fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
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
     SUM(fd.Importe_Neto) AS Total
     FROM FT_Facturas_C fc
     JOIN FT_Facturas_D fd ON fd.Cve_Folio = fc.Cve_Folio AND fd.Cve_Sucursal = fc.Cve_Sucursal AND fd.Cve_Movimiento = fc.Cve_Movimiento
     JOIN GC_Vendedores v ON v.Cve_Vendedor = fc.Cve_Vendedor
     WHERE v.Nombre LIKE '%Violeta%' AND fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
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

VENTAS DE UN PRODUCTO ESPECÍFICO — REGLA CRÍTICA:
  Cuando la pregunta menciona un producto (ej: "ventas de Omnitrope 10 mg enero 2026",
  "cuánto vendimos de Norditropin", "Saizen en marzo"):
  ⛔ NUNCA devolver ventas totales del período sin filtrar por producto.
  ⛔ NUNCA omitir el JOIN a IM_Productos_Gral ni el filtro AND p.Descripcion LIKE '%nombre%'.
  ✅ SIEMPRE hacer JOIN a FT_Facturas_D fd → IM_Productos_Gral p y filtrar por p.Descripcion.

  Consulta estándar para ventas de un producto en un período:
    SELECT p.Descripcion, SUM(fd.Importe_Neto) AS Total, SUM(fd.Cantidad) AS Piezas,
           COUNT(DISTINCT fc.Cve_Folio) AS Facturas
    FROM FT_Facturas_C fc
    JOIN FT_Facturas_D fd ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal AND fd.Cve_Movimiento=fc.Cve_Movimiento
    JOIN IM_Productos_Gral p ON p.Cve_Producto=fd.Cve_Producto
    WHERE fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
      AND p.Descripcion LIKE '%Omnitrope 10%'
      AND YEAR(fc.Fecha_Documento)=2026 AND MONTH(fc.Fecha_Documento)=1
    GROUP BY p.Descripcion
    ORDER BY Total DESC

VENTAS POR SUCURSAL ESPECÍFICA — REGLA CRÍTICA:
  Cuando la pregunta menciona una sucursal (ej: "ventas en Puebla", "cómo va CDMX", "Monterrey abril"):
  ⛔ NUNCA devolver ventas de todas las sucursales — el resultado sería incorrecto y confuso.
  ⛔ NUNCA omitir AND s.Nombre LIKE '%nombre_sucursal%' en el WHERE.
  ✅ SIEMPRE JOIN a GN_Sucursales s ON s.Cve_Sucursal=fc.Cve_Sucursal + filtrar por s.Nombre.
  ✅ El número que reportes debe coincidir SOLO con esa sucursal, no con el total de la empresa.

  Consulta estándar para ventas de una sucursal en un período:
    SELECT s.Nombre AS Sucursal, SUM(fd.Importe_Neto) AS Total,
           COUNT(DISTINCT fc.Cve_Folio) AS Facturas
    FROM FT_Facturas_C fc
    JOIN FT_Facturas_D fd ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal AND fd.Cve_Movimiento=fc.Cve_Movimiento
    JOIN GN_Sucursales s ON s.Cve_Sucursal=fc.Cve_Sucursal
    WHERE fc.Status <> 'C' AND fc.Cve_Sucursal <> 99
      AND s.Nombre LIKE '%nombre_sucursal%'
      AND [filtro de período sobre fc.Fecha_Documento]
    GROUP BY s.Nombre

  PROTOCOLO SI NO ENCUENTRA LA SUCURSAL:
    1. Buscar con LIKE '%nombre%' — si hay coincidencia, usar esa sucursal.
    2. Si no hay coincidencia: consultar SELECT Nombre FROM GN_Sucursales WHERE Cve_Sucursal <> 99
       y mostrar la lista al usuario para que elija la correcta.
    ⛔ NUNCA responder "no hay ventas" si la sucursal no existe — primero mostrar la lista disponible.
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
