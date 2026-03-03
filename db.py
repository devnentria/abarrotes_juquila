"""
Conexión y utilidades MySQL para el demo ERP.
"""

import os
from decimal import Decimal
from pathlib import Path

import mysql.connector

# Cargar .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "nentria",
    "database": "erp_demo",
    "charset": "utf8mb4",
}


def get_db():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute(
        "SET SESSION sql_mode = (SELECT REPLACE(@@sql_mode, 'ONLY_FULL_GROUP_BY', ''))"
    )
    cursor.close()
    return conn


def query_view(sql: str, params: tuple = ()) -> list:
    """Ejecuta una query y devuelve lista de dicts serializables a JSON."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in rows:
        for k, v in row.items():
            if isinstance(v, Decimal):
                row[k] = float(v)
            elif hasattr(v, "isoformat"):
                row[k] = str(v)
            elif v is None:
                row[k] = None

    return rows
