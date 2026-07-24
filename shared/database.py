# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : shared/database.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Capa de acceso a datos — SQL Server vía pyodbc.

REGLA: El resto del proyecto NUNCA importa pyodbc directamente.
       Solo llama a query() desde aquí. Si en el futuro cambia el driver
       o el ORM, solo se modifica este archivo.

Uso:
    from shared.database import query

    clientes = query(
        "SELECT TOP 10 Cve_Cliente, Razon_Social FROM CM_Clientes WHERE Cve_Sucursal = ?",
        params=(1,)
    )
    # → [{"Cve_Cliente": 1, "Razon_Social": "Farmacia San Juan"}, ...]
"""
import pyodbc

from shared.config import DB_HOST, DB_PORT, DB_NAME, DB_NAME_ACU, DB_USER, DB_PASSWORD, TEST_DATE
from shared.serializers import serialize_row


def _detect_driver() -> str:
    """Usa el driver ODBC 18 si está disponible, si no el 17."""
    drivers = pyodbc.drivers()
    if "ODBC Driver 18 for SQL Server" in drivers:
        return "ODBC Driver 18 for SQL Server"
    return "ODBC Driver 17 for SQL Server"


# La cadena de conexión se construye una sola vez al importar el módulo.
# Si DB_HOST o DB_PASSWORD cambian en .env, reiniciar el servidor es suficiente.
_driver = _detect_driver()
_base = (
    f"DRIVER={{{_driver}}};"
    f"SERVER={DB_HOST},{DB_PORT};"
    f"DATABASE={DB_NAME};"
    "TrustServerCertificate=yes;"
)
# Si no hay usuario en .env, usar Windows Authentication (Trusted_Connection)
if DB_USER:
    _CONNECTION_STRING = _base + f"UID={DB_USER};PWD={DB_PASSWORD};"
else:
    _CONNECTION_STRING = _base + "Trusted_Connection=yes;"


def hoy() -> str:
    """
    Devuelve la expresión SQL que representa 'hoy'.
    Si TEST_DATE está configurado en .env, usa esa fecha fija.
    En producción (TEST_DATE vacío) usa GETDATE() — la fecha real del servidor.

    Uso en queries:
        sql = f"WHERE CAST(Fecha_Documento AS DATE) = {hoy()}"
    """
    if TEST_DATE:
        return f"CAST('{TEST_DATE}' AS DATE)"
    return "CAST(GETDATE() AS DATE)"


def get_connection() -> pyodbc.Connection:
    """Abre y devuelve una conexión nueva a la BD ERP. Siempre cerrarla después de usar."""
    return pyodbc.connect(_CONNECTION_STRING)


# ── Conexión a ACUMULADOS (datos pre-agregados, consultas rápidas) ───────────
_base_acu = (
    f"DRIVER={{{_driver}}};"
    f"SERVER={DB_HOST},{DB_PORT};"
    f"DATABASE={DB_NAME_ACU};"
    "TrustServerCertificate=yes;"
)
if DB_USER:
    _CONNECTION_STRING_ACU = _base_acu + f"UID={DB_USER};PWD={DB_PASSWORD};"
else:
    _CONNECTION_STRING_ACU = _base_acu + "Trusted_Connection=yes;"


def get_connection_acu() -> pyodbc.Connection:
    """Abre y devuelve una conexión nueva a la BD ACUMULADOS."""
    return pyodbc.connect(_CONNECTION_STRING_ACU)


def _run_query(conn_string: str, sql: str, params: tuple, timeout: int) -> list[dict]:
    """Ejecuta un SELECT genérico y devuelve lista de dicts."""
    conn = pyodbc.connect(conn_string)
    cursor = conn.cursor()
    try:
        if timeout > 0:
            cursor.execute(f"SET LOCK_TIMEOUT {timeout * 1000}")
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        rows = [serialize_row(dict(zip(columns, row))) for row in cursor.fetchall()]
    except Exception as e:
        print(f"\n[DB ERROR] {e}\nSQL: {sql[:300]}\n")
        raise
    finally:
        cursor.close()
        conn.close()
    return rows


def query(sql: str, params: tuple = (), timeout: int = 60) -> list[dict]:
    """
    Ejecuta un SELECT en la BD ERP y devuelve una lista de dicts listos para JSON.

    Args:
        sql:     Consulta SQL con ? como placeholder (nunca f-strings con datos externos).
        params:  Tupla de valores para los placeholders.
        timeout: Segundos máximos de ejecución (default 60). 0 = sin límite.

    Returns:
        Lista de dicts. Lista vacía si no hay resultados.
    """
    return _run_query(_CONNECTION_STRING, sql, params, timeout)


def query_acu(sql: str, params: tuple = (), timeout: int = 60) -> list[dict]:
    """
    Ejecuta un SELECT en la BD ACUMULADOS (datos pre-agregados).
    Misma interfaz que query() pero conecta a ACUMULADOS.
    """
    return _run_query(_CONNECTION_STRING_ACU, sql, params, timeout)
