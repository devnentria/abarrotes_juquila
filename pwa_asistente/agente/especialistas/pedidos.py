# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/pedidos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 3.0.0
# ============================================================
"""
Agente Especialista — Compras (Órdenes de Compra).

Responde preguntas sobre órdenes de compra activas, pendientes,
antigüedad y distribución por sucursal o proveedor.
Tablas origen: MT_Ordenes_C (encabezado) + MT_Ordenes_D (detalle).
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE ÓRDENES DE COMPRA:

MT_Ordenes_C — encabezado de órdenes de compra
  Cve_Folio (varchar), Cve_Sucursal (int), Cve_Movimiento (varchar, siempre 'OCC'),
  Cve_Proveedor (varchar 10), fecha (datetime), imp_total (decimal),
  Status (char 2), observaciones (varchar)
  Status: 'TR'=transferida(completada) · 'RP'=recibida parcial · 'AU'=autorizada(pendiente) · 'CN'=cancelada

MT_Ordenes_D — detalle de órdenes de compra
  Cve_Folio (varchar), Cve_Sucursal (int), Cve_Movimiento (varchar),
  Cve_Producto (varchar 14), Descripcion (varchar 250),
  cant_ordenada (decimal), cant_recibida (decimal), cant_pendiente (decimal),
  costo_unitario (decimal), imp_total (decimal), Status (char 2)
  JOIN con MT_Ordenes_C por: d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal AND d.Cve_Movimiento=c.Cve_Movimiento

PM_Proveedores — catálogo de proveedores
  Cve_Proveedor (varchar 10), Nombre (varchar)
  JOIN: p.Cve_Proveedor = c.Cve_Proveedor

IM_Productos_Gral — catálogo de productos (solo si se necesita buscar por nombre de producto)
  Cve_Producto (varchar 14), Descripcion (varchar)
  JOIN: ip.Cve_Producto = d.Cve_Producto
  ⚠ MT_Ordenes_D ya tiene Descripcion del producto — usarla directamente cuando sea suficiente.
"""

_REGLAS = """
REGLAS DE ÓRDENES DE COMPRA:

ESTATUS DE ÓRDENES:
  · Status = 'AU'  → autorizada / pendiente (aún no recibida)
  · Status = 'RP'  → recibida parcial (se recibió parte de la mercancía)
  · Status = 'TR'  → transferida / completada (orden recibida y procesada)
  · Status = 'CN'  → cancelada
  · Para órdenes ACTIVAS/PENDIENTES: WHERE c.Status IN ('AU','RP')
  · Para órdenes COMPLETADAS:        WHERE c.Status = 'TR'
  · Para TODAS las órdenes (histórico, conteo): WHERE c.Status <> 'CN'

CONTEO HISTÓRICO DE ÓRDENES DE COMPRA (cuántas órdenes hubo en un período):
  Consulta estándar para contar órdenes de un producto en un período:
    SELECT ISNULL(d.Descripcion,'── TOTAL') AS Producto,
           COUNT(DISTINCT c.Cve_Folio) AS Ordenes,
           SUM(d.cant_ordenada)         AS Piezas_Ordenadas,
           SUM(d.imp_total)             AS Importe_Total
    FROM MT_Ordenes_C c
    JOIN MT_Ordenes_D d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal AND d.Cve_Movimiento=c.Cve_Movimiento
    WHERE c.Status <> 'CN'
      AND c.Cve_Sucursal <> 99
      AND d.Descripcion LIKE '%nombre_producto%'
      AND YEAR(c.fecha)=2026 AND MONTH(c.fecha)=7
    GROUP BY ROLLUP(d.Descripcion)
    ORDER BY GROUPING(d.Descripcion), Ordenes DESC

  ⚠ Para conteo sin filtro de producto: omitir el filtro de Descripcion.

PROVEEDOR:
  · SIEMPRE hacer JOIN a PM_Proveedores para mostrar el nombre del proveedor:
    JOIN PM_Proveedores p ON p.Cve_Proveedor = c.Cve_Proveedor
  · Si preguntan por un proveedor específico: AND p.Nombre LIKE '%nombre_proveedor%'

ANTIGÜEDAD Y ACTIVOS:
  · Antigüedad crítica (+30 días): c.fecha < DATEADD(DAY,-30,GETDATE())
  · Antigüedad media (15-30 días): entre DATEADD(DAY,-30,...) y DATEADD(DAY,-15,...)
  · Órdenes del día: CAST(c.fecha AS DATE) = CAST(GETDATE() AS DATE)
  · Priorizar reportar órdenes de más de 30 días — son las más urgentes.

PENDIENTE DE RECEPCIÓN:
  · Las piezas que faltan por recibir están en d.cant_pendiente.
  · El importe pendiente se puede estimar: d.cant_pendiente * d.costo_unitario

ÓRDENES DE UN PRODUCTO ESPECÍFICO — REGLA CRÍTICA:
  Cuando la pregunta menciona un producto (ej: "órdenes de Aceite", "cuánto se pidió de Arroz"):
  ⛔ NUNCA devolver el total de órdenes sin filtrar por producto.
  ✅ SIEMPRE filtrar: AND d.Descripcion LIKE '%nombre_producto%'

  Consulta estándar para órdenes de compra de un producto específico:
    SELECT d.Descripcion, s.Nombre AS Sucursal, p.Nombre AS Proveedor,
           SUM(d.cant_pendiente)                    AS Piezas_Pendientes,
           SUM(d.cant_pendiente * d.costo_unitario) AS Importe_Pendiente
    FROM MT_Ordenes_C c
    JOIN MT_Ordenes_D d ON d.Cve_Folio=c.Cve_Folio AND d.Cve_Sucursal=c.Cve_Sucursal AND d.Cve_Movimiento=c.Cve_Movimiento
    JOIN PM_Proveedores p ON p.Cve_Proveedor=c.Cve_Proveedor
    JOIN GN_Sucursales s ON s.Cve_Sucursal=c.Cve_Sucursal
    WHERE c.Status IN ('AU','RP') AND c.Cve_Sucursal <> 99
      AND d.cant_pendiente > 0
      AND d.Descripcion LIKE '%nombre_producto%'
    GROUP BY d.Descripcion, s.Nombre, p.Nombre
    ORDER BY Importe_Pendiente DESC

RESUMEN POR PROVEEDOR:
  Consulta estándar para órdenes activas agrupadas por proveedor:
    SELECT p.Nombre AS Proveedor,
           COUNT(DISTINCT c.Cve_Folio) AS Ordenes_Activas,
           SUM(c.imp_total)            AS Importe_Total
    FROM MT_Ordenes_C c
    JOIN PM_Proveedores p ON p.Cve_Proveedor=c.Cve_Proveedor
    WHERE c.Status IN ('AU','RP') AND c.Cve_Sucursal <> 99
    GROUP BY p.Nombre
    ORDER BY Importe_Total DESC

FORMATO ADICIONAL ÓRDENES DE COMPRA:
  · 🔴 para órdenes con más de 30 días · ⚠ para órdenes entre 15 y 30 días
  · Agrupar por sucursal o proveedor cuando aplique
  · Mostrar piezas pendientes (cant_pendiente) cuando se habla de órdenes activas
"""

_SYSTEM = build(
    rol="Eres el agente especialista en ÓRDENES DE COMPRA (compras a proveedores) de Abarrotes Suite.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre órdenes de compra.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "pedidos", **kwargs)
