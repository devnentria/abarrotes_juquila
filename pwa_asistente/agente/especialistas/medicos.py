# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / especialistas
# Archivo  : especialistas/medicos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Agente Especialista — Médicos.

Responde preguntas sobre el directorio de médicos prescriptores,
duplicados, cédulas y asignación a vendedores.
"""
from pwa_asistente.agente import base_agente
from pwa_asistente.agente.base_agente import RespuestaIA
from pwa_asistente.agente.especialistas.base_prompt import build

_SCHEMA = """
TABLAS DE MÉDICOS:

GC_Medicos — catálogo principal de médicos (ya en tablas maestras, ampliado aquí)
  ⚠ Usar LTRIM(RTRIM()) al comparar nombres y cédulas (hay espacios extra en el ERP)
  ⚠ ISNULL(cedula, '') para manejar cédulas nulas

FT_Facturas_C — para calcular ventas generadas por médico prescriptor
  Cve_Medico (int), Importe_Total (decimal), Fecha_Documento (datetime), Status (char)
  ⚠ Filtrar: Status <> 'C' · Cve_Medico > 0
"""

_REGLAS = """
DETECCIÓN DE DUPLICADOS:
  · Por cédula: misma cedula (LTRIM/RTRIM) en más de un registro
  · Por nombre: mismo UPPER(LTRIM(RTRIM(Nombre))) en más de un registro
  · Médico sin cédula puede estar duplicado solo por nombre

VENTAS POR MÉDICO:
  · Como prescriptor: JOIN FT_Facturas_C ON Cve_Medico → importe total generado por sus recetas
  · Como cliente: JOIN CM_Clientes WHERE Razon_Social IN (SELECT Nombre FROM GC_Medicos)

FORMATO ADICIONAL MÉDICOS:
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
