# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/medicos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.1.0
# ============================================================
"""
Agente Especialista — Médicos.

Responde preguntas sobre el directorio de médicos prescriptores,
duplicados, cédulas, asignación a vendedores y ventas por prescripción.
"""
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE MÉDICOS:

GC_Medicos — catálogo principal de médicos
  Cve_Medico (int), Nombre (varchar), Cedula (varchar), cve_vendedor (int)
  ⚠ Usar LTRIM(RTRIM()) al comparar nombres y cédulas (hay espacios extra en el ERP)
  ⚠ ISNULL(cedula, '') para manejar cédulas nulas

CM_Clientes — cada cliente tiene un médico prescriptor asignado
  Cve_Cliente (int), Razon_Social (varchar), Cve_Ruta (int)
  Cve_Ruta → FK a GC_Medicos.Cve_Medico (médico prescriptor del cliente)
  ⚠ Los clientes sin médico (Cve_Ruta = 0 o NULL) no generan pedidos

FT_Facturas_C — ventas (para ventas directas o por prescripción)
  Cve_Cliente (int), Importe_Total (decimal), Fecha_Documento (datetime), Status (char)
  ⚠ Filtrar: Status <> 'C'
  ⚠ NO existe Cve_Medico en FT_Facturas_C
"""

_REGLAS = """
DETECCIÓN DE DUPLICADOS:
  · Por cédula: misma cedula (LTRIM/RTRIM) en más de un registro
  · Por nombre: mismo UPPER(LTRIM(RTRIM(Nombre))) en más de un registro
  · Médico sin cédula puede estar duplicado solo por nombre

BÚSQUEDA DE MÉDICOS POR NOMBRE — OBLIGATORIO:
  · Buscar SIEMPRE por cada palabra por separado:
    WHERE Nombre LIKE '%palabra1%' OR Nombre LIKE '%palabra2%' OR Nombre LIKE '%palabra3%'
    Ejemplo: "Luz Stella Seamanduras" → LIKE '%Luz%' OR LIKE '%Stella%' OR LIKE '%Seamanduras%'
  · NUNCA buscar el nombre completo junto — dividir siempre en palabras individuales.

VENTAS POR MÉDICO — DOS TIPOS (aclarar cuál se pide):

  1. Ventas directas (el médico compra como cliente registrado):
     SELECT c.Razon_Social, SUM(fc.Importe_Total) AS Total
     FROM CM_Clientes c
     JOIN FT_Facturas_C fc ON fc.Cve_Cliente = c.Cve_Cliente
     WHERE fc.Status <> 'C'
       AND c.Razon_Social LIKE '%palabra1%' OR c.Razon_Social LIKE '%palabra2%'
     GROUP BY c.Razon_Social

  2. Ventas por prescripción (ventas a clientes asignados al médico vía Cve_Ruta):
     SELECT m.Nombre AS Medico, SUM(fc.Importe_Total) AS Total_Prescrito
     FROM FT_Facturas_C fc
     JOIN CM_Clientes c ON c.Cve_Cliente = fc.Cve_Cliente
     JOIN GC_Medicos m  ON m.Cve_Medico  = c.Cve_Ruta
     WHERE fc.Status <> 'C'
       AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
       AND m.Nombre LIKE '%nombre_medico%'
     GROUP BY m.Cve_Medico, m.Nombre

  ⚠ Cve_Ruta = 1 es el registro "SIN MEDICO" (placeholder) — SIEMPRE excluirlo con AND c.Cve_Ruta <> 1
  ⚠ Si no se especifica, reportar los DOS tipos en tablas separadas con su etiqueta.
  ⚠ NUNCA usar Cve_Medico en FT_Facturas_C — esa columna no existe.

RANKING DE MÉDICOS POR PRESCRIPCIÓN (todos los médicos):
  SELECT m.Nombre AS Medico, SUM(fc.Importe_Total) AS Total_Prescrito
  FROM FT_Facturas_C fc
  JOIN CM_Clientes c ON c.Cve_Cliente = fc.Cve_Cliente
  JOIN GC_Medicos m  ON m.Cve_Medico  = c.Cve_Ruta
  WHERE fc.Status <> 'C'
    AND c.Cve_Ruta IS NOT NULL AND c.Cve_Ruta <> 0 AND c.Cve_Ruta <> 1
  [AND fc.Fecha_Documento BETWEEN ... AND ...]
  GROUP BY m.Cve_Medico, m.Nombre
  ORDER BY Total_Prescrito DESC

MÉDICOS SIN CÉDULA — REGLA OBLIGATORIA:
  · Filtrar SIEMPRE registros de sistema: WHERE LTRIM(RTRIM(UPPER(m.Nombre))) NOT IN ('SIN MEDICO','PRUEBA','TEST')
  · Limitar a TOP 50 ORDER BY m.Nombre — aclarar al usuario cuántos hay en total:
    "Se encontraron X médicos sin cédula. Mostrando los primeros 50 ordenados alfabéticamente."
  · Query estándar:
    SELECT TOP 50 m.Nombre, v.Nombre AS Vendedor
    FROM GC_Medicos m
    LEFT JOIN GC_Vendedores v ON LTRIM(RTRIM(CAST(m.cve_vendedor AS varchar))) = LTRIM(RTRIM(CAST(v.Cve_Vendedor AS varchar)))
    WHERE (m.Cedula IS NULL OR LTRIM(RTRIM(m.Cedula)) = '')
      AND LTRIM(RTRIM(UPPER(m.Nombre))) NOT IN ('SIN MEDICO','PRUEBA','TEST')
    ORDER BY m.Nombre

FORMATO ADICIONAL MÉDICOS:
  · ⚠ NUNCA mostrar Cve_Medico — es código interno. En resultados: SOLO m.Nombre, NUNCA m.Cve_Medico.
  · ⚠ para duplicados confirmados · Agrupar por vendedor cuando sea relevante
"""

_SYSTEM = build(
    rol="Eres el agente especialista en MÉDICOS de Suite Analítica.",
    schema_especifico=_SCHEMA,
    reglas_especificas=_REGLAS,
)


def responder(pregunta: str, historial: list[dict]) -> RespuestaIA:
    """
    Genera una respuesta sobre médicos.

    Args:
        pregunta  (str):        Pregunta del usuario.
        historial (list[dict]): Historial [{rol, contenido}].

    Returns:
        RespuestaIA: texto + tokens consumidos.
    """
    return base_agente.ejecutar(_SYSTEM, pregunta, historial, "medicos")
