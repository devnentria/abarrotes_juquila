# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / funciones
# Archivo  : funciones/matcher.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Detector de funciones predefinidas — sin LLM.

Evalúa si la pregunta del usuario encaja en uno de los patrones
de consulta frecuente (Q1-Q10). Si hay coincidencia devuelve
(func_id, params) para que catalogo.ejecutar() lo resuelva
directamente contra el ERP sin pasar por el agente dinámico.

Regla de diseño:
  - Conservador: si hay duda, devuelve None (→ dinámico)
  - Solo patrones de agregados sin entidad específica
  - Preguntas sobre producto/cliente/médico/vendedor concreto → None
"""
import re
from datetime import date
from typing import Optional

# ── Meses en español ──────────────────────────────────────────────────────────
_MESES_NUM = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}
_RE_MES_NOMBRE = re.compile(
    r'\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto'
    r'|septiembre|octubre|noviembre|diciembre)\b',
    re.IGNORECASE,
)

# ── Palabras que indican referencia a una entidad específica ──────────────────
# Si la pregunta las contiene, el matcher no toca la consulta
# (se necesita extracción de nombre → agente dinámico)
_RE_ENTIDAD = re.compile(
    r'\b(del?\s+(producto|producto|cliente|proveedor|contacto|vendedor|proveedor|laboratorio|farmacia)\b'
    r'|para\s+(el|la)\s+(producto|cliente|proveedor|vendedor))',
    re.IGNORECASE,
)

# Palabras temporales/comunes que pueden seguir a "del" sin ser entidades
_PALABRAS_PERIODO = frozenset({
    'mes', 'año', 'periodo', 'período', 'semana', 'trimestre', 'bimestre',
    'dia', 'día', 'hoy', 'ayer', 'mañana',
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
    'septiembre', 'octubre', 'noviembre', 'diciembre',
    'total', 'equipo', 'sistema', 'mercado', 'primer', 'segundo', 'tercer',
    'mismo', 'pasado', 'anterior', 'actual', 'presente', 'completo', 'entero',
})

# "del X" / "de X" donde X empieza en mayúscula y no es una palabra de período → entidad
_RE_DEL_NOMBRE = re.compile(r'\bdel?\s+([A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]+)', re.UNICODE)

# "el Lorelin", "el Ozempic" (artículo masculino + nombre propio) → entidad específica
_RE_EL_NOMBRE = re.compile(r'\bel\s+([A-ZÁÉÍÓÚÑ][a-zA-ZáéíóúñÁÉÍÓÚÑ]+)', re.UNICODE)

# ── Patrones por función ──────────────────────────────────────────────────────

# Ventas → período global (sin nombre de entidad)
_RE_VENTAS_BASE = re.compile(
    r'\bventas?\b|\bcuánto\s+se\s+vendió\b|\bfacturaci[oó]n\b|\bimporte\s+vendido\b',
    re.IGNORECASE,
)
_RE_PERIODO_ACTUAL = re.compile(
    r'\b(este\s+mes|mes\s+actual|mes\s+en\s+curso)\b', re.IGNORECASE,
)
_RE_PERIODO_PASADO = re.compile(
    r'\b(mes\s+pasado|mes\s+anterior|[úu]ltimo\s+mes)\b', re.IGNORECASE,
)
_RE_ESTE_AÑO = re.compile(r'\beste\s+a[ñn]o\b', re.IGNORECASE)

# Top productos
_RE_TOP_PRODUCTOS = re.compile(
    r'\b(productos?\s+m[aá]s\s+vendidos?|top\s+\d*\s*productos?'
    r'|m[aá]s\s+se\s+vendi[oó]|m[aá]s\s+vendidos?\s+del?\s+mes'
    r'|mejor\s+vendidos?|ranking\s+de\s+productos?)\b',
    re.IGNORECASE,
)

# Pedidos activos / pendientes
_RE_PEDIDOS = re.compile(
    r'\b(pedidos?\s+(activos?|pendientes?|abiertos?|sin\s+surtir)'
    r'|cuántos?\s+pedidos?'
    r'|pedidos?\s+hay'
    r'|pedidos?\s+pendientes?)\b',
    re.IGNORECASE,
)

# Caducidades
_RE_CADUCIDADES = re.compile(
    r'\b(caduc[ao]|venc[eo]|pr[oó]xim[ao][s]?\s+a\s+caducar|caducidades?|lotes?\s+por\s+vencer)\b',
    re.IGNORECASE,
)
_RE_DIAS = re.compile(r'(\d+)\s*d[ií]as?', re.IGNORECASE)

# Proveedores / distribuidores
_RE_PROVEEDORES = re.compile(
    r'\b(cu[aá]les?\s+(son\s+)?los?\s+(proveedores?|distribuidores?)'
    r'|lista\s+de\s+(proveedores?|distribuidores?)'
    r'|qu[eé]\s+distribuidores?\s+(nos?\s+surten?|tenemos?|hay)'
    r'|qu[eé]\s+proveedores?\s+(tenemos?|hay)'
    r'|proveedores?\s+activos?'
    r'|distribuidores?\s+activos?)\b',
    re.IGNORECASE,
)

# Stock sin existencia
_RE_SIN_STOCK = re.compile(
    r'\b(sin\s+existencia[s]?|sin\s+stock|agotados?|existencia\s+(?:en\s+)?cero'
    r'|stock\s+(?:en\s+)?cero|productos?\s+agotados?'
    r'|que\s+no\s+ten(?:emos?|gan?)\s+existencia)\b',
    re.IGNORECASE,
)


# ── Función pública ───────────────────────────────────────────────────────────

def detectar(pregunta: str) -> Optional[tuple]:
    """
    Evalúa si la pregunta encaja en una función predefinida.

    Args:
        pregunta (str): Mensaje del usuario.

    Returns:
        tuple[str, dict] | None: (func_id, params) si hay coincidencia, None si no.
    """
    hoy = date.today()

    # Si la pregunta referencia una entidad específica, no usar predefinida
    if _RE_ENTIDAD.search(pregunta):
        return None
    # "del Ozempic", "del Cliente X", "de Tienda X" → entidad por nombre propio
    for m in _RE_DEL_NOMBRE.finditer(pregunta):
        if m.group(1).lower() not in _PALABRAS_PERIODO:
            return None
    # "el Lorelin", "el Ozempic" → entidad por nombre propio con artículo
    for m in _RE_EL_NOMBRE.finditer(pregunta):
        if m.group(1).lower() not in _PALABRAS_PERIODO:
            return None

    # --- Caducidades (alta precisión, sin necesidad de período) ---------------
    if _RE_CADUCIDADES.search(pregunta):
        dias = _extraer_dias(pregunta) or 90
        return ('caducidades_proximas', {'dias': dias})

    # --- Proveedores / distribuidores -------------------------------------------
    if _RE_PROVEEDORES.search(pregunta):
        return ('proveedores_activos', {})

    # --- Pedidos activos ------------------------------------------------------
    if _RE_PEDIDOS.search(pregunta):
        return ('pedidos_activos', {})

    # --- Stock sin existencia -------------------------------------------------
    if _RE_SIN_STOCK.search(pregunta):
        return ('stock_sin_existencia', {})

    # --- Top productos --------------------------------------------------------
    if _RE_TOP_PRODUCTOS.search(pregunta):
        anio, mes = _extraer_periodo(pregunta, hoy)
        return ('top_productos', {'anio': anio, 'mes': mes})

    # --- Ventas por período ---------------------------------------------------
    if _RE_VENTAS_BASE.search(pregunta):
        # Si hay "ventas de [nombre_específico]" → producto concreto, no función predefinida
        _m_de = re.search(
            r'\bventas?\s+de\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ0-9][a-zA-ZáéíóúñÁÉÍÓÚÑ0-9\s\-]{1,40}?)'
            r'(?:\s+(?:en|entre|del?|al|el|la|\d)|$)',
            pregunta, re.IGNORECASE,
        )
        if _m_de:
            _palabras = [p for p in _m_de.group(1).strip().lower().split()
                         if p not in _PALABRAS_PERIODO]
            if _palabras:
                return None  # Es consulta de producto específico → agente dinámico

        # Mes con nombre explícito ("enero", "febrero", ...)
        mes_m = _RE_MES_NOMBRE.search(pregunta)
        if mes_m:
            mes_num = _MESES_NUM[mes_m.group(1).lower()]
            # Si el mes ya pasó → mismo año; si es futuro → año anterior
            anio = hoy.year if mes_num <= hoy.month else hoy.year - 1
            return ('ventas_mes', {'anio': anio, 'mes': mes_num})

        # Este año completo
        if _RE_ESTE_AÑO.search(pregunta):
            return ('ventas_anio', {'anio': hoy.year})

        # Este mes / mes actual
        if _RE_PERIODO_ACTUAL.search(pregunta):
            return ('ventas_mes', {'anio': hoy.year, 'mes': hoy.month})

        # Mes pasado
        if _RE_PERIODO_PASADO.search(pregunta):
            if hoy.month == 1:
                return ('ventas_mes', {'anio': hoy.year - 1, 'mes': 12})
            return ('ventas_mes', {'anio': hoy.year, 'mes': hoy.month - 1})

    return None


# ── Helpers privados ──────────────────────────────────────────────────────────

def _extraer_dias(pregunta: str) -> Optional[int]:
    m = _RE_DIAS.search(pregunta)
    if m:
        return int(m.group(1))
    if re.search(r'\bmes\b', pregunta, re.IGNORECASE):
        return 30
    if re.search(r'\btrimestre\b', pregunta, re.IGNORECASE):
        return 90
    return None


def _extraer_periodo(pregunta: str, hoy: date) -> tuple[int, int]:
    mes_m = _RE_MES_NOMBRE.search(pregunta)
    if mes_m:
        mes_num = _MESES_NUM[mes_m.group(1).lower()]
        anio = hoy.year if mes_num <= hoy.month else hoy.year - 1
        return (anio, mes_num)
    if _RE_PERIODO_PASADO.search(pregunta):
        if hoy.month == 1:
            return (hoy.year - 1, 12)
        return (hoy.year, hoy.month - 1)
    return (hoy.year, hoy.month)
