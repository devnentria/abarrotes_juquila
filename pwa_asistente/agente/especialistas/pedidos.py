# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/pedidos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Agente Especialista — Pedidos.

Responde preguntas sobre pedidos activos, su antigüedad
y distribución por sucursal o vendedor.
"""
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
  · Pedidos activos = Estatus = 'AC'
  · Antigüedad crítica (+30 días): Fecha_Documento < DATEADD(DAY,-30,GETDATE())
  · Antigüedad media (15-30 días): entre DATEADD(DAY,-30,...) y DATEADD(DAY,-15,...)
  · Pedidos del día: CAST(Fecha_Documento AS DATE) = CAST(GETDATE() AS DATE)
  · Priorizar reportar pedidos de más de 30 días — son los más urgentes.

FORMATO ADICIONAL PEDIDOS:
  · 🔴 para pedidos con más de 30 días · ⚠ para pedidos entre 15 y 30 días
  · Agrupar por sucursal cuando aplique
"""

_SYSTEM = build(
    rol="Eres el agente especialista en PEDIDOS de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list[dict]) -> RespuestaIA:
    """
    Genera una respuesta sobre pedidos.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "pedidos")
