# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/sql_blacklist.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.1.0
# ============================================================
"""
Registro persistente de identificadores SQL inválidos.

Cada vez que el ERP devuelve un error de columna o tabla inexistente,
se registra aquí para que el agente no vuelva a intentarlo en consultas
futuras. El bloque se inyecta en el system prompt de cada llamada.

Archivo de datos: sql_blacklist.json (gitignoreado).
"""
import re
from pathlib import Path
from pwa_asistente.agente._persistent import PersistentStore

_store = PersistentStore(
    "sql_blacklist.json",
    {"columnas": [], "tablas": []},
    Path(__file__).parent,
)


def _extraer_tabla_columna(columna: str, sql: str) -> str:
    """Intenta determinar 'Tabla.Columna' buscando el alias en el SQL."""
    alias_m = re.search(rf'\b(\w+)\.{re.escape(columna)}\b', sql, re.IGNORECASE)
    if alias_m:
        alias = alias_m.group(1)
        tabla_m = re.search(
            rf'(?:FROM|JOIN)\s+(\w+)\s+(?:AS\s+)?{re.escape(alias)}\b',
            sql, re.IGNORECASE,
        )
        if tabla_m:
            return f"{tabla_m.group(1)}.{columna}"
        return f"{alias}.{columna}"
    return columna


def registrar_columna(columna: str, sql: str = "") -> None:
    entrada = _extraer_tabla_columna(columna, sql) if sql else columna
    if entrada not in _store.datos["columnas"]:
        _store.datos["columnas"].append(entrada)
        _store.guardar()
        print(f"[blacklist] Columna inválida registrada: {entrada}", flush=True)


def registrar_tabla(tabla: str) -> None:
    if tabla not in _store.datos["tablas"]:
        _store.datos["tablas"].append(tabla)
        _store.guardar()
        print(f"[blacklist] Tabla inválida registrada: {tabla}", flush=True)


def como_bloque_prompt() -> str:
    partes = []
    if _store.datos["columnas"]:
        partes.append(
            "Columnas que NO EXISTEN en el ERP — no volver a usarlas NUNCA:\n"
            + "\n".join(f"  ✗ {c}" for c in _store.datos["columnas"])
        )
    if _store.datos["tablas"]:
        partes.append(
            "Tablas que NO EXISTEN en el ERP — no volver a usarlas NUNCA:\n"
            + "\n".join(f"  ✗ {t}" for t in _store.datos["tablas"])
        )
    if not partes:
        return ""
    return "\n\nERRORES SQL CONFIRMADOS — NUNCA REUTILIZAR:\n" + "\n".join(partes)
