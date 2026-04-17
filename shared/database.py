# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
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

from shared.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, TEST_DATE
from shared.serializers import serialize_row


def _detect_driver() -> str:
    """Usa el driver ODBC 18 si está disponible, si no el 17."""
    drivers = pyodbc.drivers()
    if "ODBC Driver 18 for SQL Server" in drivers:
        return "ODBC Driver 18 for SQL Server"
    return "ODBC Driver 17 for SQL Server"


# La cadena de conexión se construye una sola vez al importar el módulo.
# Si DB_HOST o DB_PASSWORD cambian en .env, reiniciar el servidor es suficiente.
_CONNECTION_STRING = (
    f"DRIVER={{{_detect_driver()}}};"
    f"SERVER={DB_HOST},{DB_PORT};"
    f"DATABASE={DB_NAME};"
    f"UID={DB_USER};"
    f"PWD={DB_PASSWORD};"
    "TrustServerCertificate=yes;"  # Necesario en redes locales y Docker
)


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
    """Abre y devuelve una conexión nueva. Siempre cerrarla después de usar."""
    return pyodbc.connect(_CONNECTION_STRING)


def query(sql: str, params: tuple = ()) -> list[dict]:
    """
    Ejecuta un SELECT y devuelve una lista de dicts listos para JSON.

    Args:
        sql:    Consulta SQL con ? como placeholder (nunca f-strings con datos externos).
        params: Tupla de valores para los placeholders.

    Returns:
        Lista de dicts. Lista vacía si no hay resultados.

    Ejemplo:
        rows = query(
            "SELECT TOP 5 Cve_Producto, Descripcion FROM IM_Productos_Gral WHERE Laboratorio = ?",
            params=("BAYER",)
        )
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
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
