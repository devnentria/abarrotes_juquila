# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/inventario.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.1.0
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
  Cve_Sucursal (int), Cve_Producto (int), Existencia (decimal), Status (varchar)
  ⚠ Filtrar: Status = 'AC'
  Para existencias actuales con total: GROUP BY ROLLUP usando ISNULL(s.Nombre,'── TOTAL')

IN_Existencias_Lote — existencias por lote (para caducidades)
  Cve_Sucursal (int), Cve_Producto (int), Num_Lote (varchar),
  Fecha_Caducidad (date), Existencia (decimal)

IN_Existencias_Alm_Diario — snapshot histórico diario
  Cve_Sucursal (smallint), Cve_Almacen (varchar), Cve_Producto (varchar),
  Fecha (datetime), Existencia (decimal), Costo_Ultima_Compra (decimal), Costo_Promedio (decimal)
  ⚠ Cobertura: enero 2024 en adelante · Cve_Producto es VARCHAR — CAST al unir con IM_Productos_Gral
  ⚠ Para fecha específica: registro más cercano anterior con subconsulta MAX(Fecha) <= 'YYYY-MM-DD'
  ⚠ Incluir SIEMPRE todas las variantes (normales + promos) con LIKE '%nombre%'

IT_Movimientos_C — cabecera de movimientos de almacén
  Cve_Movimiento (varchar), Fecha_Documento (datetime), Cve_Sucursal (smallint),
  Cve_Almacen (varchar), Cve_Folio (int), Cve_Proveedor (varchar)
  Tipos: EC=Entrada Compra, VTA=Venta, EA=Entrada Almacén, SA=Salida, ST/ET=Traspasos

IT_Movimientos_D — detalle de movimientos
  Cve_Movimiento (varchar), Cve_Folio (int), Cve_Almacen (varchar),
  Cve_Producto (varchar), Cantidad (decimal), Costo_Unitario (decimal),
  Precio_Venta (decimal), Num_Lote (varchar), Fecha_Caducidad (datetime)
  JOIN con IT_Movimientos_C por: Cve_Folio + Cve_Movimiento + Cve_Almacen
  ⚠ Último costo: WHERE Cve_Movimiento='EC' ORDER BY Fecha_Documento DESC TOP 1
  ⚠ Costo promedio en período: AVG(Costo_Unitario) WHERE EC + rango fechas
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
