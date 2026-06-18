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
                consultas_ia  INTEGER NOT NULL DEFAULT 0,   -- legacy, usar consultas_ia_r
                consultas_ia_r REAL   NOT NULL DEFAULT 0.0, -- fuente de verdad (soporta 1.3x)
                limite_ia     INTEGER NOT NULL DEFAULT 700,
                costo_ia_usd  REAL    NOT NULL DEFAULT 0.0,
                mes_consultas TEXT    NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS consumo_ia_mensual (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id   INTEGER NOT NULL,
                mes          TEXT    NOT NULL,
                consultas    INTEGER NOT NULL DEFAULT 0,
                costo_usd    REAL    NOT NULL DEFAULT 0.0,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
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

            CREATE TABLE IF NOT EXISTS cache_agente (
                clave      TEXT    PRIMARY KEY,
                respuesta  TEXT    NOT NULL,
                creado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS cache_dashboard (
                clave        TEXT    PRIMARY KEY,
                payload      TEXT    NOT NULL,
                generado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chat_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL,
                conversacion_id INTEGER,
                pregunta        TEXT    NOT NULL,
                respuesta       TEXT,
                area            TEXT,
                estado          TEXT    NOT NULL DEFAULT 'pending',
                creado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
                terminado_en    TEXT,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            );

            CREATE TABLE IF NOT EXISTS dashboards (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo      TEXT    NOT NULL,
                pregunta    TEXT    NOT NULL DEFAULT '',
                tipo        TEXT    NOT NULL DEFAULT 'texto',
                datos_json  TEXT    NOT NULL DEFAULT '{}',
                guardado    INTEGER NOT NULL DEFAULT 0,
                creado_por  INTEGER NOT NULL,
                creado_en   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (creado_por) REFERENCES usuarios(id)
            );
        """)
        # Migraciones: agregar columnas si la tabla ya existía sin ellas
        migraciones = [
            "ALTER TABLE usuarios ADD COLUMN permisos       TEXT    NOT NULL DEFAULT '[\"ventas\"]'",
            "ALTER TABLE usuarios ADD COLUMN consultas_ia   INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN limite_ia      INTEGER NOT NULL DEFAULT 700",
            "ALTER TABLE usuarios ADD COLUMN costo_ia_usd   REAL    NOT NULL DEFAULT 0.0",
            "ALTER TABLE usuarios ADD COLUMN foto_perfil    TEXT",
            "ALTER TABLE usuarios ADD COLUMN mes_consultas  TEXT    NOT NULL DEFAULT ''",
            """CREATE TABLE IF NOT EXISTS consumo_ia_mensual (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id   INTEGER NOT NULL,
                mes          TEXT    NOT NULL,
                consultas    INTEGER NOT NULL DEFAULT 0,
                costo_usd    REAL    NOT NULL DEFAULT 0.0,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )""",
            """CREATE TABLE IF NOT EXISTS cache_agente (
                clave      TEXT    PRIMARY KEY,
                respuesta  TEXT    NOT NULL,
                creado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS cache_dashboard (
                clave        TEXT    PRIMARY KEY,
                payload      TEXT    NOT NULL,
                generado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
            )""",
            """CREATE TABLE IF NOT EXISTS chat_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL,
                conversacion_id INTEGER,
                pregunta        TEXT    NOT NULL,
                respuesta       TEXT,
                area            TEXT,
                estado          TEXT    NOT NULL DEFAULT 'pending',
                creado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
                terminado_en    TEXT,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )""",
            """CREATE TABLE IF NOT EXISTS dashboards (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo      TEXT    NOT NULL,
                pregunta    TEXT    NOT NULL DEFAULT '',
                tipo        TEXT    NOT NULL DEFAULT 'texto',
                datos_json  TEXT    NOT NULL DEFAULT '{}',
                guardado    INTEGER NOT NULL DEFAULT 0,
                creado_por  INTEGER NOT NULL,
                creado_en   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (creado_por) REFERENCES usuarios(id)
            )""",
            "ALTER TABLE usuarios ADD COLUMN debe_cambiar_password INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE chat_conversaciones ADD COLUMN modulo TEXT NOT NULL DEFAULT 'pwa'",
            "ALTER TABLE chat_jobs ADD COLUMN meta_json TEXT",
            # Migración: columna REAL para acumular consultas con decimales (ratio 1.3)
            # consultas_ia (INTEGER) se mantiene por compatibilidad; consultas_ia_r es la fuente de verdad
            "ALTER TABLE usuarios ADD COLUMN consultas_ia_r REAL NOT NULL DEFAULT 0.0",
            "UPDATE usuarios SET consultas_ia_r = CAST(consultas_ia AS REAL) WHERE consultas_ia_r = 0.0 AND consultas_ia > 0",
            # Dashboard compartido con PWA
            "ALTER TABLE dashboards ADD COLUMN compartido INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE dashboards ADD COLUMN compartido_en TEXT",
            # Histórico diario de inventario (snapshot al cierre del día)
            """CREATE TABLE IF NOT EXISTS inventario_historico (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha           TEXT    NOT NULL,
                valor_stock     REAL    NOT NULL DEFAULT 0,
                unidades        INTEGER NOT NULL DEFAULT 0,
                productos_stock INTEGER NOT NULL DEFAULT 0,
                criticos        INTEGER NOT NULL DEFAULT 0,
                por_sucursal    TEXT    NOT NULL DEFAULT '[]',
                guardado_en     TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(fecha)
            )""",
            # Histórico por producto × sucursal (solo con existencia > 0)
            """CREATE TABLE IF NOT EXISTS inventario_historico_productos (
                fecha           TEXT    NOT NULL,
                cve_producto    TEXT    NOT NULL,
                cve_sucursal    INTEGER NOT NULL,
                sucursal        TEXT    NOT NULL DEFAULT '',
                descripcion     TEXT    NOT NULL DEFAULT '',
                existencia      REAL    NOT NULL DEFAULT 0,
                costo_promedio  REAL    NOT NULL DEFAULT 0,
                precio1         REAL    NOT NULL DEFAULT 0,
                precio2         REAL    NOT NULL DEFAULT 0,
                precio3         REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (fecha, cve_producto, cve_sucursal)
            )""",
            "ALTER TABLE inventario_historico_productos ADD COLUMN sucursal TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE inventario_historico_productos ADD COLUMN precio1 REAL NOT NULL DEFAULT 0",
            "ALTER TABLE inventario_historico_productos ADD COLUMN precio2 REAL NOT NULL DEFAULT 0",
            "ALTER TABLE inventario_historico_productos ADD COLUMN precio3 REAL NOT NULL DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS idx_invhp_prod ON inventario_historico_productos(cve_producto)",
            "CREATE INDEX IF NOT EXISTS idx_invhp_fecha ON inventario_historico_productos(fecha)",
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


def verificar_mes_ia(usuario_id: int, mes_actual: str) -> None:
    """
    Verifica si el mes del contador IA cambió. Si cambió, archiva el consumo
    del mes anterior en consumo_ia_mensual y reinicia los contadores del usuario.

    Args:
        usuario_id (int): ID del usuario.
        mes_actual (str): Mes en formato "YYYY-MM" (ej. "2026-04").
    """
    u = fetch_one(
        "SELECT consultas_ia_r, costo_ia_usd, mes_consultas FROM usuarios WHERE id = ?",
        (usuario_id,),
    )
    if not u:
        return
    if u["mes_consultas"] == mes_actual:
        return

    # Archivar mes anterior si tenía datos
    if u["mes_consultas"] and (u["consultas_ia_r"] > 0 or u["costo_ia_usd"] > 0):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO consumo_ia_mensual (usuario_id, mes, consultas, costo_usd) "
                "VALUES (?, ?, ?, ?)",
                (usuario_id, u["mes_consultas"], u["consultas_ia_r"], u["costo_ia_usd"]),
            )
            conn.commit()

    # Reiniciar contadores y marcar mes actual
    execute(
        "UPDATE usuarios SET consultas_ia = 0, consultas_ia_r = 0.0, costo_ia_usd = 0, mes_consultas = ? WHERE id = ?",
        (mes_actual, usuario_id),
    )


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
