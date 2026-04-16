# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/ejecutor.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Ejecutor de SQL — capa de seguridad del agente IA.

Regla absoluta: solo permite instrucciones SELECT.
Cualquier intento de escribir, modificar o eliminar datos
lanza ValueError antes de llegar al ERP.
"""
import re
from shared.database import query as _query_erp

# Palabras clave que NUNCA deben llegar al ERP
_PROHIBIDAS = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE'
    r'|MERGE|REPLACE|GRANT|REVOKE|BULK|OPENROWSET|OPENQUERY)\b',
    re.IGNORECASE,
)


# Definición del tool OpenAI compartida por todos los especialistas
TOOL = {
    "type": "function",
    "function": {
        "name": "ejecutar_sql",
        "description": "Ejecuta una consulta SELECT en el ERP para obtener datos.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "Consulta SELECT válida para SQL Server."}
            },
            "required": ["sql"],
        },
    },
}


def run(sql: str) -> list[dict]:
    """
    Valida y ejecuta una consulta SELECT contra el ERP (SQL Server).

    Args:
        sql (str): Consulta generada por el agente especialista.

    Returns:
        list[dict]: Filas del resultado como lista de diccionarios.

    Raises:
        ValueError: Si la consulta contiene instrucciones no permitidas
                    o no comienza con SELECT.
    """
    limpio = sql.strip()

    if _PROHIBIDAS.search(limpio):
        raise ValueError(
            "La consulta contiene instrucciones no permitidas. "
            "Solo se aceptan consultas SELECT."
        )

    if not limpio.upper().lstrip("(").startswith("SELECT"):
        raise ValueError("La consulta debe comenzar con SELECT.")

    return _query_erp(limpio)
