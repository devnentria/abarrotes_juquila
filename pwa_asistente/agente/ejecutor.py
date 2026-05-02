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
    # Correcciones de errores conocidos generados por el modelo
    # 1. GC_Clientes no existe — la tabla correcta es CM_Clientes
    sql = re.sub(r'\bGC_Clientes\b', 'CM_Clientes', sql, flags=re.IGNORECASE)

    # 2. CM_Clientes usa Razon_Social, no Nombre — corregir el alias que apunte a CM_Clientes
    alias_match = re.search(
        r'\bCM_Clientes\b(?:\s+(?:AS\s+)?(\w+))?', sql, re.IGNORECASE
    )
    if alias_match:
        alias = alias_match.group(1)
        if alias:
            sql = re.sub(
                rf'\b{re.escape(alias)}\.Nombre\b', f'{alias}.Razon_Social',
                sql, flags=re.IGNORECASE
            )
        sql = re.sub(r'\bCM_Clientes\.Nombre\b', 'CM_Clientes.Razon_Social',
                     sql, flags=re.IGNORECASE)

    limpio = sql.strip()

    print(f"\n── SQL ejecutado ──────────────────────────────\n{limpio}\n──────────────────────────────────────────────\n", flush=True)

    if _PROHIBIDAS.search(limpio):
        raise ValueError(
            "La consulta contiene instrucciones no permitidas. "
            "Solo se aceptan consultas SELECT."
        )

    if not limpio.upper().lstrip("(").startswith("SELECT"):
        raise ValueError("La consulta debe comenzar con SELECT.")

    try:
        return _query_erp(limpio)
    except Exception as e:
        # Si falla por columna inexistente, reconstruir la consulta sin ella y reintentar
        col_match = re.search(r"Invalid column name '(\w+)'", str(e))
        if col_match:
            col      = col_match.group(1)
            sql_fix  = limpio

            # 1. Si la columna aparece en un JOIN ON → eliminar todo ese JOIN
            #    y las referencias al alias de esa tabla en SELECT/WHERE
            join_m = re.search(
                rf'(?:LEFT\s+|RIGHT\s+|INNER\s+)?JOIN\s+(\w+)\s+(\w+)\s+ON\s+[^\n]*\b{re.escape(col)}\b[^\n]*',
                sql_fix, re.IGNORECASE,
            )
            if join_m:
                alias = join_m.group(2)
                # Quitar el JOIN completo
                sql_fix = re.sub(
                    rf'(?:LEFT\s+|RIGHT\s+|INNER\s+)?JOIN\s+\w+\s+{re.escape(alias)}\s+ON\s+[^\n]+',
                    ' ', sql_fix, flags=re.IGNORECASE,
                )
                # Quitar columnas del alias en SELECT: , alias.campo AS x
                sql_fix = re.sub(
                    rf',?\s*\b{re.escape(alias)}\.\w+(?:\s+AS\s+\w+)?',
                    '', sql_fix, flags=re.IGNORECASE,
                )
                # Neutralizar condiciones WHERE/AND que usen ese alias
                sql_fix = re.sub(
                    rf'\b{re.escape(alias)}\.\w+\s*(?:=|LIKE|IN|IS\s+NULL|IS\s+NOT\s+NULL)[^\n,)]*',
                    '1=1', sql_fix, flags=re.IGNORECASE,
                )
            else:
                col_pat = rf'\b(?:\w+\.)?{re.escape(col)}\b'
                # BETWEEN col AND val → neutralizar
                sql_fix = re.sub(
                    col_pat + r'\s+BETWEEN\s+.+?\s+AND\s+[\w\(\)\'\-:\.]+',
                    '1=1', sql_fix, flags=re.IGNORECASE,
                )
                # col >= / <= / <> / > / < val → neutralizar
                sql_fix = re.sub(
                    col_pat + r'\s*(?:>=|<=|<>|>|<)\s*(?:\'[^\']*\'|[\w\(\)\-\.]+)',
                    '1=1', sql_fix, flags=re.IGNORECASE,
                )
                # Columna en SELECT directo — eliminarla
                sql_fix = re.sub(
                    rf'(?:,\s*)?\b\w+\.{re.escape(col)}\b(?:\s+AS\s+\w+)?'
                    rf'|(?:,\s*)?\b{re.escape(col)}\b(?:\s+AS\s+\w+)?',
                    '', sql_fix, flags=re.IGNORECASE,
                )

            if sql_fix.strip() != limpio.strip():
                print(f"\n── SQL corregido (retry) ──────────────────────\n{sql_fix.strip()}\n──────────────────────────────────────────────\n", flush=True)
                return _query_erp(sql_fix)
        raise
