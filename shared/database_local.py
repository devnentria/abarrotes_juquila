# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : database_local.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Conexión y operaciones sobre la base de datos local SQLite.

Esta BD es exclusiva de la Suite (usuarios, sesiones, historial).
Es independiente del SQL Server del cliente (ERP CreaSoft).
El archivo suite.db se crea automáticamente en data/ al iniciar.
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

# ── Ruta del archivo de BD ────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "data" / "suite.db"


def get_connection() -> sqlite3.Connection:
    """
    Retorna una conexión SQLite con row_factory configurado para
    devolver filas como diccionarios.

    Returns:
        sqlite3.Connection: Conexión lista para usar.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Crea las tablas de la Suite si no existen y aplica migraciones pendientes.
    Es seguro llamarla varias veces — no destruye datos existentes.
    """
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre        TEXT    NOT NULL,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                rol           TEXT    NOT NULL DEFAULT 'usuario',
                modulos       TEXT    NOT NULL DEFAULT '["pwa"]',
                permisos      TEXT    NOT NULL DEFAULT '["ventas"]',
                activo        INTEGER NOT NULL DEFAULT 1,
                creado_en     TEXT    NOT NULL DEFAULT (datetime('now')),
                ultimo_acceso TEXT,
                consultas_ia  INTEGER NOT NULL DEFAULT 0,
                limite_ia     INTEGER NOT NULL DEFAULT 700,
                costo_ia_usd  REAL    NOT NULL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS chat_conversaciones (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id  INTEGER NOT NULL,
                titulo      TEXT    NOT NULL DEFAULT 'Nueva conversación',
                ultimo_msg  TEXT,
                creado_en   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            );

            CREATE TABLE IF NOT EXISTS chat_mensajes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                conversacion_id  INTEGER NOT NULL,
                rol              TEXT    NOT NULL,
                contenido        TEXT    NOT NULL,
                creado_en        TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (conversacion_id) REFERENCES chat_conversaciones(id)
            );
        """)
        # Migraciones: agregar columnas si la tabla ya existía sin ellas
        migraciones = [
            "ALTER TABLE usuarios ADD COLUMN permisos       TEXT    NOT NULL DEFAULT '[\"ventas\"]'",
            "ALTER TABLE usuarios ADD COLUMN consultas_ia   INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN limite_ia      INTEGER NOT NULL DEFAULT 700",
            "ALTER TABLE usuarios ADD COLUMN costo_ia_usd   REAL    NOT NULL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN foto_perfil    TEXT",
        ]
        for sql in migraciones:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass  # La columna ya existe


def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """
    Ejecuta una consulta y retorna la primera fila como diccionario.

    Args:
        sql    (str):   Consulta SQL con placeholders ?.
        params (tuple): Valores para los placeholders.

    Returns:
        Optional[dict]: Primera fila como dict, o None si no hay resultados.
    """
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    """
    Ejecuta una consulta y retorna todas las filas como lista de diccionarios.

    Args:
        sql    (str):   Consulta SQL con placeholders ?.
        params (tuple): Valores para los placeholders.

    Returns:
        list[dict]: Lista de filas como dicts (vacía si no hay resultados).
    """
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def execute(sql: str, params: tuple = ()) -> int:
    """
    Ejecuta una instrucción de escritura (INSERT, UPDATE, DELETE) y hace commit.

    Args:
        sql    (str):   Instrucción SQL con placeholders ?.
        params (tuple): Valores para los placeholders.

    Returns:
        int: ID del último registro insertado (útil para INSERT).
    """
    with get_connection() as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid


def modulos_de_usuario(modulos_json: str) -> list[str]:
    """
    Deserializa el campo modulos (JSON string) a lista de strings.

    Args:
        modulos_json (str): JSON guardado en la BD, ej. '["pwa", "studio"]'.

    Returns:
        list[str]: Lista de módulos activos del usuario.
    """
    try:
        return json.loads(modulos_json)
    except (json.JSONDecodeError, TypeError):
        return []
