# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/pedidos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.3.0
# ============================================================
"""
Agente Especialista — Pedidos.

Responde preguntas sobre pedidos activos, su antigüedad
y distribución por sucursal o vendedor.
"""
from typing import Optional
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE PEDIDOS:

FT_Pedidos_C — encabezado de pedidos
  Cve_Folio (int), Cve_Movimiento (int), Cve_Sucursal (int),
  Fecha_Documento (datetime), Cve_Cliente (int), Cve_Vendedor (varchar), Estatus (varchar)
  Estatus: 'AC'=activo/pendiente · 'TR'=transferido · 'CN'=cancelado
  ⚠ NO existe Importe_Total en esta tabla — para importe usar FT_Pedidos_CN_D.PrecioNeto

FT_Pedidos_CN_D — detalle de pedidos (única tabla de detalle disponible)
  Cve_Folio (int), Cve_Movimiento (int), Cve_Sucursal (int),
  Cve_Producto (int), Cantidad_Ordenada (decimal), Cantidad_Surtida (decimal),
  Cantidad_Pendiente (decimal), Precio (decimal), PrecioNeto (decimal)
  JOIN con FT_Pedidos_C por: Cve_Folio + Cve_Sucursal + Cve_Movimiento
  ⚠ NUNCA usar FT_Pedidos_D — esa tabla NO EXISTE en esta base de datos

VW_Pedidos_Total — vista con importe total por pedido
  Cve_Sucursal, Cve_Folio, Cve_Movimiento_Pedido, Neto (decimal), Status
  JOIN: vt.Cve_Folio=pc.Cve_Folio AND vt.Cve_Sucursal=pc.Cve_Sucursal AND vt.Cve_Movimiento_Pedido=pc.Cve_Movimiento
"""

_REGLAS = """
REGLAS DE PEDIDOS:

ESTATUS DE PEDIDOS:
  · Estatus = 'AC'  → activo/pendiente (aún no surtido/cobrado)
  · Estatus = 'TR'  → transferido/procesado (surtido) — NO significa envío físico entre sucursales
  · Estatus = 'CN'  → cancelado
  · Para pedidos ACTIVOS:     WHERE Estatus = 'AC'
  · Para TODOS los pedidos (histórico, conteo): WHERE Estatus <> 'CN'
  ⚠ NUNCA filtrar por Referencia_Cliente = 'PAGADO' en pedidos — ese campo aplica en ventas.
    Un pedido activo ES un pedido aunque no esté cobrado.

CONTEO HISTÓRICO DE PEDIDOS (cuántos pedidos hubo en un período):
  Consulta estándar para contar pedidos de un producto en un período:
    SELECT ISNULL(p.Descripcion,'── TOTAL') AS Producto,
           COUNT(DISTINCT pc.Cve_Folio) AS Pedidos,
           SUM(pd.Cantidad_Ordenada)    AS Piezas_Ordenadas
    FROM FT_Pedidos_C pc
    JOIN FT_Pedidos_CN_D pd ON pd.Cve_Folio=pc.Cve_Folio AND pd.Cve_Sucursal=pc.Cve_Sucursal AND pd.Cve_Movimiento=pc.Cve_Movimiento
    JOIN IM_Productos_Gral p ON p.Cve_Producto=pd.Cve_Producto
    WHERE pc.Estatus <> 'CN'
      AND pc.Cve_Sucursal <> 99
      AND p.Descripcion LIKE '%nombre_producto%'
      AND YEAR(pc.Fecha_Documento)=2026 AND MONTH(pc.Fecha_Documento)=4
    GROUP BY ROLLUP(p.Descripcion)
    ORDER BY GROUPING(p.Descripcion), Pedidos DESC

  ⚠ Para conteo sin filtro de producto: omitir JOIN a IM_Productos_Gral y el filtro de Descripcion.

ANTIGÜEDAD Y ACTIVOS:
  · Antigüedad crítica (+30 días): Fecha_Documento < DATEADD(DAY,-30,GETDATE())
  · Antigüedad media (15-30 días): entre DATEADD(DAY,-30,...) y DATEADD(DAY,-15,...)
  · Pedidos del día: CAST(Fecha_Documento AS DATE) = CAST(GETDATE() AS DATE)
  · Priorizar reportar pedidos de más de 30 días — son los más urgentes.

PEDIDOS DE UN PRODUCTO ESPECÍFICO — REGLA CRÍTICA:
  Cuando la pregunta menciona un producto (ej: "pedidos de Omnitrope", "cuánto hay pedido de Saizen"):
  ⛔ NUNCA devolver el total de pedidos sin filtrar por producto.
  ✅ SIEMPRE hacer JOIN a IM_Productos_Gral p y filtrar: AND p.Descripcion LIKE '%nombre_producto%'

  Consulta estándar para pedidos de un producto específico:
    SELECT p.Descripcion, s.Nombre AS Sucursal,
           SUM(pd.Cantidad_Pendiente) AS Piezas_Pendientes,
           SUM(pd.PrecioNeto * pd.Cantidad_Pendiente) AS Importe_Pendiente
    FROM FT_Pedidos_C pc
    JOIN FT_Pedidos_CN_D pd ON pd.Cve_Folio=pc.Cve_Folio AND pd.Cve_Sucursal=pc.Cve_Sucursal AND pd.Cve_Movimiento=pc.Cve_Movimiento
    JOIN IM_Productos_Gral p ON p.Cve_Producto=pd.Cve_Producto
    JOIN GN_Sucursales s ON s.Cve_Sucursal=pc.Cve_Sucursal
    WHERE pc.Estatus='AC' AND pc.Cve_Sucursal <> 99
      AND pd.Cantidad_Pendiente > 0
      AND p.Descripcion LIKE '%nombre_producto%'
    GROUP BY p.Descripcion, s.Nombre
    ORDER BY Importe_Pendiente DESC

FORMATO ADICIONAL PEDIDOS:
  · 🔴 para pedidos con más de 30 días · ⚠ para pedidos entre 15 y 30 días
  · Agrupar por sucursal cuando aplique
"""

_SYSTEM = build(
    rol="Eres el agente especialista en PEDIDOS de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list, model: Optional[str] = None) -> RespuestaIA:
    """
    Genera una respuesta sobre pedidos.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].
        model     (str|None):   Modelo OpenAI a usar (None = default del sistema).

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    kwargs = {"model": model} if model else {}
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "pedidos", **kwargs)
