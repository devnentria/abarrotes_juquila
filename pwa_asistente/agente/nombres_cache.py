# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/nombres_cache.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.1.0
# ============================================================
"""
Caché persistente de variantes de nombres del ERP.

Cuando el agente busca "Lorelin" y el ERP devuelve variantes
(LORELIN 11.25 MG, LORELIN 3.75 MG, ...), guarda ese mapeo.
En la siguiente llamada el agente ya sabe qué variantes existen.

Archivo de datos: nombres_cache.json (gitignoreado).

Formato interno:
  {
    "producto": {"lorelin": ["LORELIN 11.25 MG", "LORELIN 3.75 MG"]},
    "cliente":  {"farmacia xyz": ["FARMACIA XYZ SA DE CV"]},
    "medico":   {"garcia": ["GARCIA LOPEZ JUAN"]}
  }
"""
import re
from pathlib import Path
from pwa_asistente.agente._persistent import PersistentStore

_store = PersistentStore(
    "nombres_cache.json",
    {"producto": {}, "cliente": {}, "medico": {}},
    Path(__file__).parent,
)

# Mapeo tabla → (tipo_entidad, columna_nombre_en_resultado)
_TABLA_TIPO = {
    "IM_Productos_Gral": ("producto", "Descripcion"),
    "CM_Clientes":       ("cliente",  "Razon_Social"),
    "PM_Proveedores":    ("medico",   "Nombre"),
}

# Solo procesar queries que contengan LIKE — las agregaciones no tienen nombres
_RE_LIKE = re.compile(r"LIKE\s+'%([^%']+)%'", re.IGNORECASE)


def registrar_desde_sql(sql: str, filas: list[dict]) -> None:
    """
    Detecta si el SQL es una búsqueda por nombre (LIKE) y guarda
    el mapeo término → variantes encontradas.
    Solo se ejecuta si el SQL contiene LIKE — evita regex innecesario
    en queries de agregación o filtros por fecha.
    """
    if not filas or "LIKE" not in sql.upper():
        return

    for tabla, (tipo, col_nombre) in _TABLA_TIPO.items():
        if tabla.upper() not in sql.upper():
            continue

        likes = _RE_LIKE.findall(sql)
        if not likes:
            break

        # Recoger valores únicos del resultado usando la columna correcta
        col_candidates = [col_nombre, "Descripcion", "Razon_Social", "Nombre"]
        valores = []
        for fila in filas:
            v = next((fila[c] for c in col_candidates if c in fila and fila[c]), None)
            if v and str(v) not in valores:
                valores.append(str(v))

        if not valores:
            break

        with _store.lock:
            datos = _store.datos[tipo]
            hubo_cambios = False
            for termino in likes:
                clave = termino.strip().lower()
                if len(clave) < 3:
                    continue
                existentes = datos.get(clave, [])
                nuevos = [v for v in valores if v not in existentes]
                if nuevos:
                    datos[clave] = existentes + nuevos
                    hubo_cambios = True
                    print(f"[nombres_cache] {tipo} '{clave}' → {datos[clave]}", flush=True)

            if hubo_cambios:
                _store.guardar()
        break


# Límite de entradas por tipo para evitar prompt infinito
_MAX_ENTRADAS_PROMPT = 30


def como_bloque_prompt() -> str:
    lineas = []
    etiquetas = {"producto": "Productos", "cliente": "Clientes", "medico": "Médicos"}

    for tipo, etiqueta in etiquetas.items():
        entradas = _store.datos.get(tipo, {})
        if not entradas:
            continue
        lineas.append(f"{etiqueta} conocidos y sus variantes en el ERP:")
        for termino, variantes in list(entradas.items())[:_MAX_ENTRADAS_PROMPT]:
            lineas.append(f'  "{termino}" → {" | ".join(variantes)}')

    if not lineas:
        return ""
    return (
        "\n\nNOMBRES CONOCIDOS DEL ERP (usa estas variantes exactas en tus queries):\n"
        + "\n".join(lineas)
    )
