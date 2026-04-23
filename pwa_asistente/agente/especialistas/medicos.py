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

FT_Facturas_C — para calcular ventas de médicos como clientes directos
  Cve_Cliente (int), Importe_Total (decimal), Fecha_Documento (datetime), Status (char)
  ⚠ Filtrar: Status <> 'C'
  ⚠ NO existe Cve_Medico en FT_Facturas_C — buscar médicos como clientes via CM_Clientes.Razon_Social
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

VENTAS POR MÉDICO:
  · Si el médico compra como cliente: buscar en CM_Clientes WHERE Razon_Social LIKE '%palabra1%' OR ...
    luego: JOIN FT_Facturas_C fc ON fc.Cve_Cliente = c.Cve_Cliente
  · NUNCA usar Cve_Medico en FT_Facturas_C — esa columna no existe en esta base de datos.

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
