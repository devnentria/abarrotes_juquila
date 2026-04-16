"""
Serialización de tipos SQL Server → tipos nativos de Python / JSON.

SQL Server devuelve Decimal, datetime y a veces bytes.
JSON no sabe manejarlos, así que los convertimos aquí.
Este módulo no sabe nada de negocio — solo convierte tipos.
"""
import datetime
from decimal import Decimal


def serialize_row(row: dict) -> dict:
    """
    Recibe un dict de una fila SQL y devuelve un dict serializable a JSON.

    Conversiones:
        Decimal  → float   (precios, cantidades, importes)
        date     → "YYYY-MM-DD"
        datetime → "YYYY-MM-DDTHH:MM:SS"
        bytes    → str UTF-8
        None     → None    (se respeta el nulo)
    """
    result = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, datetime.datetime):
            result[key] = value.isoformat()
        elif isinstance(value, datetime.date):
            result[key] = value.isoformat()
        elif isinstance(value, bytes):
            result[key] = value.decode("utf-8", errors="replace")
        else:
            result[key] = value
    return result
