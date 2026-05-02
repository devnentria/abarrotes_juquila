# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/nombres_cache.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Caché persistente de variantes de nombres del ERP.

Cuando el agente busca "Lorelin" y el ERP devuelve
["LORELIN 11.25 MG", "LORELIN 3.75 MG", "LORELIN 11.25 MG PROMOCION"],
se guarda ese mapeo. En la siguiente llamada el agente ya sabe qué
variantes existen sin tener que buscarlo de nuevo.

Archivo de datos: nombres_cache.json (junto a este módulo, gitignoreado).

Formato interno:
  {
    "producto": {"lorelin": ["LORELIN 11.25 MG", "LORELIN 3.75 MG", ...]},
    "cliente":  {"farmacia xyz": ["FARMACIA XYZ SA DE CV"]},
    "medico":   {"garcia": ["GARCIA LOPEZ JUAN", "GARCIA MARTINEZ ANA"]}
  }
"""
import json
import re
from pathlib import Path

_ARCHIVO = Path(__file__).parent / "nombres_cache.json"
_datos: dict = {"producto": {}, "cliente": {}, "medico": {}}

# Mapeo tabla → (tipo_entidad, columna_nombre_en_resultado)
_TABLA_TIPO = {
    "IM_Productos_Gral": ("producto", "Descripcion"),
    "CM_Clientes":       ("cliente",  "Razon_Social"),
    "GC_Medicos":        ("medico",   "Nombre"),
}


def _cargar() -> None:
    global _datos
    if _ARCHIVO.exists():
        try:
            loaded = json.loads(_ARCHIVO.read_text(encoding="utf-8"))
            # Merge con estructura base para tolerar archivos parciales
            for k in _datos:
                _datos[k] = loaded.get(k, {})
        except Exception:
            pass


def _guardar() -> None:
    _ARCHIVO.write_text(
        json.dumps(_datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_cargar()


# ── API pública ───────────────────────────────────────────────────────────────

def registrar_desde_sql(sql: str, filas: list[dict]) -> None:
    """
    Detecta si el SQL es una búsqueda por nombre (LIKE) y si los resultados
    contienen variantes reconocibles. Si es así, las guarda en caché.

    Args:
        sql   (str):        SQL que se ejecutó exitosamente.
        filas (list[dict]): Resultados devueltos por el ERP.
    """
    if not filas:
        return

    for tabla, (tipo, col_nombre) in _TABLA_TIPO.items():
        if tabla.upper() not in sql.upper():
            continue

        # Buscar patrones LIKE '%termino%' en el SQL
        likes = re.findall(
            rf"(?:{col_nombre}|p\.Descripcion|c\.Razon_Social|m\.Nombre)"
            r"\s+LIKE\s+'%([^%']+)%'",
            sql, re.IGNORECASE,
        )
        if not likes:
            # Fallback: cualquier LIKE '%X%' en el SQL
            likes = re.findall(r"LIKE\s+'%([^%']+)%'", sql, re.IGNORECASE)

        if not likes:
            continue

        # Recoger los valores encontrados en los resultados
        valores = []
        for fila in filas:
            v = fila.get(col_nombre) or fila.get("Descripcion") or fila.get("Razon_Social") or fila.get("Nombre")
            if v and v not in valores:
                valores.append(str(v))

        if not valores:
            continue

        for termino in likes:
            clave = termino.strip().lower()
            if len(clave) < 3:
                continue
            existentes = _datos[tipo].get(clave, [])
            nuevos = [v for v in valores if v not in existentes]
            if nuevos:
                _datos[tipo][clave] = existentes + nuevos
                _guardar()
                print(
                    f"[nombres_cache] {tipo} '{clave}' → {existentes + nuevos}",
                    flush=True,
                )
        break  # Solo procesar la primera tabla que coincida


def como_bloque_prompt() -> str:
    """
    Devuelve texto para inyectar en el system prompt con variantes conocidas.
    Retorna cadena vacía si el caché está vacío.
    """
    lineas = []
    etiquetas = {"producto": "Productos", "cliente": "Clientes", "medico": "Médicos"}

    for tipo, etiqueta in etiquetas.items():
        entradas = _datos.get(tipo, {})
        if not entradas:
            continue
        lineas.append(f"{etiqueta} conocidos y sus variantes en el ERP:")
        for termino, variantes in entradas.items():
            variantes_str = " | ".join(variantes)
            lineas.append(f'  "{termino}" → {variantes_str}')

    if not lineas:
        return ""
    return (
        "\n\nNOMBRES CONOCIDOS DEL ERP (usa estas variantes exactas en tus queries):\n"
        + "\n".join(lineas)
    )
