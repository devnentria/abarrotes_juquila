# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/sql_blacklist.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Registro persistente de identificadores SQL inválidos.

Cada vez que el ERP devuelve un error de columna o tabla inexistente,
se registra aquí para que el agente no vuelva a intentarlo en consultas
futuras. El bloque se inyecta en el system prompt de cada llamada.

Archivo de datos: sql_blacklist.json (junto a este módulo, gitignoreado).
"""
import json
import re
from pathlib import Path

_ARCHIVO = Path(__file__).parent / "sql_blacklist.json"
_datos: dict = {"columnas": [], "tablas": []}


def _cargar() -> None:
    global _datos
    if _ARCHIVO.exists():
        try:
            _datos = json.loads(_ARCHIVO.read_text(encoding="utf-8"))
        except Exception:
            pass


def _guardar() -> None:
    _ARCHIVO.write_text(
        json.dumps(_datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_cargar()


# ── Extracción de contexto desde SQL ─────────────────────────────────────────

def _extraer_tabla_columna(columna: str, sql: str) -> str:
    """
    Intenta determinar 'Tabla.Columna' a partir del SQL y el nombre de columna.

    1. Busca el patrón alias.columna en el SQL.
    2. Mapea el alias a su tabla real en las cláusulas FROM/JOIN.
    Si no puede determinarlo, devuelve solo el nombre de columna.
    """
    alias_m = re.search(rf'\b(\w+)\.{re.escape(columna)}\b', sql, re.IGNORECASE)
    if alias_m:
        alias = alias_m.group(1)
        tabla_m = re.search(
            rf'(?:FROM|JOIN)\s+(\w+)\s+(?:AS\s+)?{re.escape(alias)}\b',
            sql, re.IGNORECASE,
        )
        if tabla_m:
            return f"{tabla_m.group(1)}.{columna}"
        # El alias podría ser el nombre directo de la tabla
        return f"{alias}.{columna}"
    return columna


# ── API pública ───────────────────────────────────────────────────────────────

def registrar_columna(columna: str, sql: str = "") -> None:
    """Registra una columna inválida detectada en un error DB."""
    entrada = _extraer_tabla_columna(columna, sql) if sql else columna
    if entrada not in _datos["columnas"]:
        _datos["columnas"].append(entrada)
        _guardar()
        print(f"[blacklist] Columna inválida registrada: {entrada}", flush=True)


def registrar_tabla(tabla: str) -> None:
    """Registra una tabla inexistente detectada en un error DB."""
    if tabla not in _datos["tablas"]:
        _datos["tablas"].append(tabla)
        _guardar()
        print(f"[blacklist] Tabla inválida registrada: {tabla}", flush=True)


def como_bloque_prompt() -> str:
    """
    Devuelve un bloque de texto listo para inyectar en el system prompt.
    Retorna cadena vacía si la lista está vacía.
    """
    partes = []
    if _datos["columnas"]:
        partes.append(
            "Columnas que NO EXISTEN en el ERP — no volver a usarlas NUNCA:\n"
            + "\n".join(f"  ✗ {c}" for c in _datos["columnas"])
        )
    if _datos["tablas"]:
        partes.append(
            "Tablas que NO EXISTEN en el ERP — no volver a usarlas NUNCA:\n"
            + "\n".join(f"  ✗ {t}" for t in _datos["tablas"])
        )
    if not partes:
        return ""
    return "\n\nERRORES SQL CONFIRMADOS — NUNCA REUTILIZAR:\n" + "\n".join(partes)
