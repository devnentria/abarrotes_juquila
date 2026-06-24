# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente / funciones
# Archivo  : funciones/catalogo.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.0.0
# ============================================================
"""
Catálogo de funciones predefinidas — SQL validado + interpretación LLM.

Flujo por función:
  1. SQL fijo (sin generación LLM) → datos reales del ERP
  2. Queries adicionales en paralelo: período anterior, top N, alertas
  3. LLM recibe todos los datos ya procesados → escribe análisis ejecutivo

Ventaja vs agente dinámico:
  - Sin loop de tool calls → 3-5× más rápido
  - Sin generación de SQL → sin errores de columnas inexistentes
  - LLM enfocado 100% en interpretar, no en construir queries
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from openai import OpenAI

from shared.config import OPENAI_API_KEY, OPENAI_MODEL
from shared.database import query as _q

_client  = OpenAI(api_key=OPENAI_API_KEY)
_db_pool = ThreadPoolExecutor(max_workers=6)

_MESES_ES = [
    '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]

# ── System prompt del analista ────────────────────────────────────────────────

_SYSTEM = """
Eres el analista de negocio de una distribuidora farmacéutica con varias sucursales en México.
Tu trabajo es interpretar datos reales del ERP y producir un reporte ejecutivo de alto valor.

INSTRUCCIONES OBLIGATORIAS:
- Sé creativo y perspicaz — destaca lo que el director realmente necesita saber
- Incluye siempre comparativas cuando haya datos de períodos anteriores
- Detecta anomalías aunque no se hayan pedido: caídas bruscas, concentración de riesgo, outliers
- Usa Markdown: tablas con | col |, **negritas** para cifras clave, ▲ incremento ▼ decremento ⚠ alerta
- Números: $1,234,567 MXN · Porcentajes con 1 decimal
- Después de cada tabla añade 3-5 observaciones analíticas reales — no trivialidades
- La última observación debe ser una recomendación accionable concreta para el negocio

TERMINOLOGÍA (regla absoluta):
- VENTA / VENTAS → lo que la empresa factura a clientes
- COMPRA / COMPRAS → lo que la empresa paga a proveedores
- Nunca confundir: "el cliente realizó una compra" es incorrecto → "se registró una venta al cliente"

PROHIBIDO:
- Mencionar SQL, tablas de BD, columnas, modelos, tokens o arquitectura interna
- Respuestas genéricas sin cifras concretas del contexto
- Frases vacías como "hay que mejorar" sin especificar qué y cómo
"""


# ── Dispatcher ────────────────────────────────────────────────────────────────

def ejecutar(func_id: str, params: dict) -> tuple:
    """
    Ejecuta la función predefinida: SQL → datos → LLM interpretación.

    Args:
        func_id (str): Identificador de función.
        params  (dict): Parámetros extraídos por el matcher.

    Returns:
        tuple[str, float]: (texto_markdown, costo_usd)
    """
    _handlers = {
        'ventas_mes':           _ventas_mes,
        'ventas_anio':          _ventas_anio,
        'top_productos':        _top_productos,
        'pedidos_activos':      _pedidos_activos,
        'caducidades_proximas': _caducidades_proximas,
        'proveedores_activos':  _proveedores_activos,
        'stock_sin_existencia': _stock_sin_existencia,
    }
    handler = _handlers.get(func_id)
    if not handler:
        raise ValueError(f"Función no encontrada: {func_id}")
    return handler(**params)


# ── Helper: llamada LLM de interpretación ────────────────────────────────────

def _interpretar(titulo: str, datos: str) -> tuple:
    """
    Llama al LLM con los datos ya recolectados para producir el análisis.
    Sin tool calls — una sola llamada enfocada en redactar.

    Returns:
        tuple[str, float]: (texto, costo_usd)
    """
    from shared.config import IA_PRECIO_INPUT, IA_PRECIO_OUTPUT
    resp = _client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": f"Genera el reporte para: {titulo}\n\n{datos}"},
        ],
        temperature=0.4,
        max_tokens=600,
    )
    costo = 0.0
    if resp.usage:
        costo = (
            resp.usage.prompt_tokens     * IA_PRECIO_INPUT
            + resp.usage.completion_tokens * IA_PRECIO_OUTPUT
        )
    return resp.choices[0].message.content.strip(), costo


def _fmt(filas: list) -> str:
    if not filas:
        return "(sin registros)"
    return json.dumps(filas, ensure_ascii=False, default=str, indent=2)


def _mes_anterior(anio: int, mes: int) -> tuple:
    if mes == 1:
        return (anio - 1, 12)
    return (anio, mes - 1)


def _variacion(actual: float, anterior: Optional[float]) -> Optional[float]:
    """Variación porcentual entre dos períodos. None si no hay dato anterior."""
    if anterior is None or anterior == 0:
        return None
    return round((actual - anterior) / anterior * 100, 1)


def _parallel(*fns) -> list:
    """Ejecuta múltiples callables en paralelo y devuelve resultados en el orden original."""
    results = [None] * len(fns)
    futures = {_db_pool.submit(fn): i for i, fn in enumerate(fns)}
    for future in as_completed(futures):
        idx = futures[future]
        try:
            results[idx] = future.result()
        except Exception as e:
            print(f"[catalogo] Query {idx} falló: {e}", flush=True)
            results[idx] = []
    return results


# ── Q1 — Ventas por mes ───────────────────────────────────────────────────────

def _ventas_mes(anio: int, mes: int) -> str:
    mes_txt  = _MESES_ES[mes]
    ant_anio, ant_mes = _mes_anterior(anio, mes)
    ant_txt  = _MESES_ES[ant_mes]

    def q_actual():
        return _q("""
            SELECT
                s.Nombre                                               AS sucursal,
                COUNT(DISTINCT fc.Cve_Folio)                           AS facturas,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe,
                CAST(SUM(fd.Importe_Neto)*100.0
                    / NULLIF(SUM(SUM(fd.Importe_Neto)) OVER(),0)
                    AS DECIMAL(5,2))                                   AS pct
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN GN_Sucursales s ON s.Cve_Sucursal=fc.Cve_Sucursal
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
            GROUP BY s.Nombre
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio, mes))

    def q_anterior():
        return _q("""
            SELECT
                s.Nombre                                               AS sucursal,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN GN_Sucursales s ON s.Cve_Sucursal=fc.Cve_Sucursal
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
            GROUP BY s.Nombre
        """, (ant_anio, ant_mes))

    def q_top_productos():
        return _q("""
            SELECT TOP 5
                p.Descripcion                                          AS producto,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe,
                SUM(fd.Cantidad)                                       AS piezas
            FROM FT_Facturas_D fd
            JOIN FT_Facturas_C fc
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN IM_Productos_Gral p ON p.Cve_Producto=fd.Cve_Producto
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
              AND p.Descripcion IS NOT NULL
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Descripcion
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio, mes))

    def q_top_vendedores():
        return _q("""
            SELECT TOP 5
                v.Nombre                                               AS vendedor,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe,
                COUNT(DISTINCT fc.Cve_Folio)                           AS facturas
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN GC_Vendedores v ON v.Cve_Vendedor=fc.Cve_Vendedor
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
            GROUP BY v.Nombre
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio, mes))

    actual, anterior, top_prod, top_vend = _parallel(q_actual, q_anterior, q_top_productos, q_top_vendedores)

    if not actual:
        return f"No se encontraron ventas en {mes_txt} {anio}."

    total_actual   = sum(float(r['importe']) for r in actual)
    total_anterior = sum(float(r['importe']) for r in anterior) if anterior else 0
    variacion      = ((total_actual - total_anterior) / total_anterior * 100) if total_anterior else None

    # Mapa anterior por sucursal para comparar
    ant_map = {r['sucursal']: float(r['importe']) for r in anterior} if anterior else {}

    datos = f"""
PERÍODO: {mes_txt} {anio}
TOTAL ACTUAL: ${total_actual:,.0f} MXN | {sum(r['facturas'] for r in actual):,} facturas
TOTAL PERÍODO ANTERIOR ({ant_txt} {ant_anio}): ${total_anterior:,.0f} MXN
VARIACIÓN GLOBAL: {f"▲ +{variacion:.1f}%" if variacion and variacion > 0 else f"▼ {variacion:.1f}%" if variacion else "sin dato anterior"}

VENTAS POR SUCURSAL (actual vs anterior):
{_fmt([{**r,
        'importe_anterior': ant_map.get(r['sucursal']),
        'variacion_pct': _variacion(float(r['importe']), ant_map.get(r['sucursal']))}
       for r in actual])}

TOP 5 PRODUCTOS DEL MES:
{_fmt(top_prod)}

TOP 5 VENDEDORES DEL MES:
{_fmt(top_vend)}
"""
    return _interpretar(f"Ventas de {mes_txt} {anio}", datos)


# ── Q1 variante — Ventas anuales ──────────────────────────────────────────────

def _ventas_anio(anio: int) -> str:
    def q_mensual():
        return _q("""
            SELECT
                MONTH(fc.Fecha_Documento)                              AS mes_num,
                DATENAME(MONTH, fc.Fecha_Documento)                    AS mes,
                COUNT(DISTINCT fc.Cve_Folio)                           AS facturas,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=?
            GROUP BY MONTH(fc.Fecha_Documento), DATENAME(MONTH, fc.Fecha_Documento)
            ORDER BY MONTH(fc.Fecha_Documento)
        """, (anio,))

    def q_anio_anterior():
        return _q("""
            SELECT
                MONTH(fc.Fecha_Documento)                              AS mes_num,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=?
            GROUP BY MONTH(fc.Fecha_Documento)
        """, (anio - 1,))

    def q_por_sucursal():
        return _q("""
            SELECT
                s.Nombre                                               AS sucursal,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe_anio
            FROM FT_Facturas_C fc
            JOIN FT_Facturas_D fd
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN GN_Sucursales s ON s.Cve_Sucursal=fc.Cve_Sucursal
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=?
            GROUP BY s.Nombre
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio,))

    mensual, anterior, por_sucursal = _parallel(q_mensual, q_anio_anterior, q_por_sucursal)

    if not mensual:
        return f"No se encontraron ventas registradas en **{anio}**."

    ant_map     = {r['mes_num']: float(r['importe']) for r in anterior} if anterior else {}
    total_anio  = sum(float(r['importe']) for r in mensual)
    total_ant   = sum(float(r['importe']) for r in anterior) if anterior else 0

    datos = f"""
AÑO: {anio}
TOTAL: ${total_anio:,.0f} MXN vs ${total_ant:,.0f} MXN en {anio - 1}
VARIACIÓN ANUAL: {f"▲ +{(total_anio-total_ant)/total_ant*100:.1f}%" if total_ant else "sin comparativa"}

VENTAS MENSUALES (actual vs año anterior):
{_fmt([{**r,
        'importe_anterior': ant_map.get(r['mes_num']),
        'variacion_pct': _variacion(float(r['importe']), ant_map.get(r['mes_num']))}
       for r in mensual])}

VENTAS POR SUCURSAL (acumulado del año):
{_fmt(por_sucursal)}
"""
    return _interpretar(f"Ventas del año {anio}", datos)


# ── Top productos ─────────────────────────────────────────────────────────────

def _top_productos(anio: int, mes: int) -> str:
    mes_txt            = _MESES_ES[mes]
    ant_anio, ant_mes  = _mes_anterior(anio, mes)
    ant_txt            = _MESES_ES[ant_mes]

    def q_actual():
        return _q("""
            SELECT TOP 10
                p.Descripcion                                          AS producto,
                SUM(fd.Cantidad)                                       AS piezas,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe,
                CAST(SUM(fd.Importe_Neto)*100.0
                    /NULLIF(SUM(SUM(fd.Importe_Neto)) OVER(),0)
                    AS DECIMAL(5,2))                                   AS pct
            FROM FT_Facturas_D fd
            JOIN FT_Facturas_C fc
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN IM_Productos_Gral p ON p.Cve_Producto=fd.Cve_Producto
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
              AND p.Descripcion IS NOT NULL
            GROUP BY p.Descripcion
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio, mes))

    def q_anterior():
        return _q("""
            SELECT TOP 10
                p.Descripcion                                          AS producto,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe,
                SUM(fd.Cantidad)                                       AS piezas
            FROM FT_Facturas_D fd
            JOIN FT_Facturas_C fc
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN IM_Productos_Gral p ON p.Cve_Producto=fd.Cve_Producto
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
              AND p.Descripcion IS NOT NULL
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Descripcion
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (ant_anio, ant_mes))

    def q_por_sucursal():
        """Qué sucursal vende más de cada producto top."""
        return _q("""
            SELECT TOP 5
                p.Descripcion                                          AS producto,
                s.Nombre                                               AS sucursal_lider,
                CAST(SUM(fd.Importe_Neto) AS DECIMAL(18,2))            AS importe
            FROM FT_Facturas_D fd
            JOIN FT_Facturas_C fc
              ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
             AND fd.Cve_Movimiento=fc.Cve_Movimiento
            JOIN IM_Productos_Gral p ON p.Cve_Producto=fd.Cve_Producto
            JOIN GN_Sucursales     s ON s.Cve_Sucursal=fc.Cve_Sucursal
            WHERE fc.Status<>'C' AND fc.Cve_Sucursal<>99
              AND YEAR(fc.Fecha_Documento)=? AND MONTH(fc.Fecha_Documento)=?
              AND p.Descripcion IS NOT NULL
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Descripcion, s.Nombre
            ORDER BY SUM(fd.Importe_Neto) DESC
        """, (anio, mes))

    actual, anterior, por_sucursal = _parallel(q_actual, q_anterior, q_por_sucursal)

    if not actual:
        return f"No se encontraron ventas de productos en {mes_txt} {anio}."

    ant_map = {r['producto']: {'importe': float(r['importe']), 'piezas': r['piezas']}
               for r in anterior} if anterior else {}
    ant_ranking = {r['producto']: i+1 for i, r in enumerate(anterior)} if anterior else {}

    datos = f"""
PERÍODO: {mes_txt} {anio} vs {ant_txt} {ant_anio}

TOP 10 PRODUCTOS (con movimiento en ranking vs mes anterior):
{_fmt([{**r,
        'importe_mes_anterior': ant_map.get(r['producto'], {}).get('importe'),
        'variacion_importe_pct': _variacion(float(r['importe']),
                                            ant_map.get(r['producto'], {}).get('importe')),
        'posicion_mes_anterior': ant_ranking.get(r['producto'], 'nuevo en top')}
       for r in actual])}

SUCURSALES LÍDERES POR PRODUCTO (top 5):
{_fmt(por_sucursal)}

PRODUCTOS NUEVOS EN EL TOP 10 (no estaban el mes anterior):
{[r['producto'] for r in actual if r['producto'] not in ant_map] or ['ninguno']}
"""
    return _interpretar(f"Top productos de {mes_txt} {anio}", datos)


# ── Q4 — Pedidos activos ──────────────────────────────────────────────────────

def _pedidos_activos() -> str:
    def q_por_sucursal():
        return _q("""
            SELECT
                s.Nombre                                              AS sucursal,
                COUNT(pc.Cve_Folio)                                   AS total_pedidos,
                MAX(DATEDIFF(DAY, pc.Fecha_Documento, GETDATE()))     AS dias_mas_antiguo,
                MIN(DATEDIFF(DAY, pc.Fecha_Documento, GETDATE()))     AS dias_mas_reciente
            FROM FT_Pedidos_C pc
            JOIN GN_Sucursales s ON s.Cve_Sucursal=pc.Cve_Sucursal
            WHERE pc.Estatus='AC' AND pc.Cve_Sucursal<>99
            GROUP BY s.Nombre
            ORDER BY COUNT(pc.Cve_Folio) DESC
        """)

    def q_mas_antiguos():
        return _q("""
            SELECT TOP 5
                s.Nombre                                              AS sucursal,
                DATEDIFF(DAY, pc.Fecha_Documento, GETDATE())          AS dias_activo,
                CONVERT(varchar(10), pc.Fecha_Documento, 23)          AS fecha_pedido,
                pc.Cve_Folio                                          AS folio
            FROM FT_Pedidos_C pc
            JOIN GN_Sucursales s ON s.Cve_Sucursal=pc.Cve_Sucursal
            WHERE pc.Estatus='AC' AND pc.Cve_Sucursal<>99
            ORDER BY pc.Fecha_Documento ASC
        """)

    def q_semana_pasada():
        """Pedidos que entraron en los últimos 7 días."""
        return _q("""
            SELECT COUNT(*) AS nuevos_7d
            FROM FT_Pedidos_C
            WHERE Estatus='AC'
              AND Cve_Sucursal<>99
              AND Fecha_Documento >= DATEADD(DAY,-7,GETDATE())
        """)

    por_sucursal, mas_antiguos, semana = _parallel(q_por_sucursal, q_mas_antiguos, q_semana_pasada)

    if not por_sucursal:
        return "No hay pedidos activos en este momento."

    total = sum(r['total_pedidos'] for r in por_sucursal)
    nuevos_7d = semana[0]['nuevos_7d'] if semana else 0

    datos = f"""
PEDIDOS ACTIVOS TOTALES: {total}
NUEVOS EN LOS ÚLTIMOS 7 DÍAS: {nuevos_7d}

POR SUCURSAL:
{_fmt(por_sucursal)}

LOS 5 PEDIDOS MÁS ANTIGUOS SIN SURTIR:
{_fmt(mas_antiguos)}
"""
    return _interpretar("Pedidos activos y pendientes", datos)


# ── Q8 — Caducidades próximas ─────────────────────────────────────────────────

def _caducidades_proximas(dias: int = 90) -> str:
    def q_proximos():
        return _q("""
            SELECT TOP 20
                p.Descripcion                                         AS producto,
                s.Nombre                                              AS sucursal,
                il.Lote                                               AS lote,
                il.Existencia                                         AS existencia,
                CONVERT(varchar(10), il.Fecha_Caducidad, 23)          AS fecha_caducidad,
                DATEDIFF(DAY, GETDATE(), il.Fecha_Caducidad)          AS dias_para_caducar
            FROM IN_Existencias_Lote il
            JOIN GN_Sucursales     s ON s.Cve_Sucursal  = il.Cve_Sucursal
            JOIN IM_Productos_Gral p ON p.Cve_Producto  = il.Cve_Producto
            WHERE il.Existencia      > 0
              AND il.Fecha_Caducidad >= GETDATE()
              AND il.Fecha_Caducidad <= DATEADD(DAY,?,GETDATE())
              AND il.Cve_Sucursal   <> 99
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            ORDER BY il.Fecha_Caducidad ASC
        """, (dias,))

    def q_ya_caducados():
        return _q("""
            SELECT
                COUNT(*)          AS lotes_caducados,
                SUM(il.Existencia) AS piezas_caducadas
            FROM IN_Existencias_Lote il
            WHERE il.Existencia      > 0
              AND il.Fecha_Caducidad < GETDATE()
              AND il.Cve_Sucursal   <> 99
        """)

    def q_valor_en_riesgo():
        """Valor estimado de producto próximo a caducar (existencia × costo promedio)."""
        return _q("""
            SELECT TOP 10
                p.Descripcion                                         AS producto,
                SUM(il.Existencia)                                    AS total_piezas,
                CAST(AVG(ea.Costo_Promedio) AS DECIMAL(18,2))         AS costo_promedio,
                CAST(SUM(il.Existencia) * AVG(ea.Costo_Promedio)
                    AS DECIMAL(18,2))                                 AS valor_estimado
            FROM IN_Existencias_Lote il
            JOIN IM_Productos_Gral p  ON p.Cve_Producto  = il.Cve_Producto
            JOIN IN_Existencias_Alm ea
              ON ea.Cve_Producto  = il.Cve_Producto
             AND ea.Cve_Sucursal  = il.Cve_Sucursal
             AND ea.Status = 'AC'
            WHERE il.Existencia      > 0
              AND il.Fecha_Caducidad >= GETDATE()
              AND il.Fecha_Caducidad <= DATEADD(DAY,?,GETDATE())
              AND il.Cve_Sucursal   <> 99
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Descripcion
            ORDER BY SUM(il.Existencia)*AVG(ea.Costo_Promedio) DESC
        """, (dias,))

    proximos, caducados, valor = _parallel(q_proximos, q_ya_caducados, q_valor_en_riesgo)

    if not proximos and not caducados:
        return f"No se encontraron caducidades en los próximos {dias} días."

    lotes_cad   = caducados[0]['lotes_caducados']  if caducados else 0
    piezas_cad  = caducados[0]['piezas_caducadas'] if caducados else 0
    valor_total = sum(float(r['valor_estimado'] or 0) for r in valor) if valor else 0

    datos = f"""
VENTANA DE ANÁLISIS: próximos {dias} días
LOTES YA CADUCADOS CON EXISTENCIA: {lotes_cad} lotes / {piezas_cad} piezas (¡requieren baja inmediata!)
VALOR ESTIMADO EN RIESGO (próximos {dias} días): ${valor_total:,.0f} MXN

LOTES PRÓXIMOS A CADUCAR:
{_fmt(proximos)}

VALOR EN RIESGO POR PRODUCTO (top 10):
{_fmt(valor)}
"""
    return _interpretar(f"Análisis de caducidades — próximos {dias} días", datos)


# ── Q10 — Proveedores / Laboratorios ─────────────────────────────────────────

def _proveedores_activos() -> str:
    def q_lista():
        return _q("""
            SELECT
                p.Nombre           AS proveedor,
                p.RFC              AS rfc
            FROM PM_Proveedores p
            WHERE p.Status='AC' AND p.Cve_Proveedor<>0
            ORDER BY p.Nombre
        """)

    def q_productos_por_proveedor():
        return _q("""
            SELECT TOP 10
                pr.Nombre                                             AS proveedor,
                COUNT(DISTINCT ip.Cve_Producto)                       AS productos_en_catalogo
            FROM PM_Proveedores pr
            JOIN IM_Productos_Proveedor ip ON ip.Cve_Proveedor=pr.Cve_Proveedor
            WHERE pr.Status='AC' AND pr.Cve_Proveedor<>0
            GROUP BY pr.Nombre
            ORDER BY COUNT(DISTINCT ip.Cve_Producto) DESC
        """)

    def q_compras_recientes():
        return _q("""
            SELECT TOP 5
                pr.Nombre                                             AS proveedor,
                COUNT(mc.Cve_Folio)                                   AS entradas_90d,
                CAST(SUM(md.Costo_Unitario * md.Cantidad)
                    AS DECIMAL(18,2))                                 AS importe_comprado
            FROM IT_Movimientos_C mc
            JOIN IT_Movimientos_D md
              ON md.Cve_Folio=mc.Cve_Folio AND md.Cve_Sucursal=mc.Cve_Sucursal
             AND md.Cve_Movimiento=mc.Cve_Movimiento
            JOIN PM_Proveedores pr ON pr.Cve_Proveedor=mc.Cve_Proveedor
            WHERE mc.Cve_Movimiento='EC'
              AND mc.Fecha_Documento >= DATEADD(DAY,-90,GETDATE())
              AND pr.Status='AC' AND pr.Cve_Proveedor<>0
            GROUP BY pr.Nombre
            ORDER BY SUM(md.Costo_Unitario * md.Cantidad) DESC
        """)

    lista, por_catalogo, recientes = _parallel(q_lista, q_productos_por_proveedor, q_compras_recientes)

    if not lista:
        return "No se encontraron proveedores activos."

    datos = f"""
TOTAL PROVEEDORES ACTIVOS: {len(lista)}

DIRECTORIO COMPLETO:
{_fmt(lista)}

PROVEEDORES CON MÁS PRODUCTOS EN CATÁLOGO:
{_fmt(por_catalogo)}

PROVEEDORES MÁS ACTIVOS (últimos 90 días — por importe de compra):
{_fmt(recientes)}
"""
    return _interpretar("Proveedores y laboratorios activos", datos)


# ── Inventario — Stock sin existencia ─────────────────────────────────────────

def _stock_sin_existencia() -> str:
    def q_sin_stock():
        return _q("""
            SELECT TOP 20
                s.Nombre           AS sucursal,
                p.Descripcion      AS producto,
                ea.Comprometido    AS en_pedidos_pendientes
            FROM IN_Existencias_Alm ea
            JOIN GN_Sucursales     s ON s.Cve_Sucursal  = ea.Cve_Sucursal
            JOIN IM_Productos_Gral p ON p.Cve_Producto  = ea.Cve_Producto
            WHERE ea.Existencia=0 AND ea.Status='AC' AND ea.Cve_Sucursal<>99
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
              AND EXISTS (
                  SELECT 1 FROM FT_Facturas_D fd
                  JOIN FT_Facturas_C fc
                    ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
                   AND fd.Cve_Movimiento=fc.Cve_Movimiento
                  WHERE fd.Cve_Producto=ea.Cve_Producto
                    AND fc.Cve_Sucursal=ea.Cve_Sucursal
                    AND fc.Status<>'C'
                    AND fc.Fecha_Documento >= DATEADD(DAY,-90,GETDATE())
              )
            ORDER BY ea.Comprometido DESC, s.Nombre, p.Descripcion
        """)

    def q_otras_sucursales():
        """Productos sin stock que sí tienen existencia en otras sucursales (posibles traspasos)."""
        return _q("""
            SELECT TOP 10
                p.Descripcion                                         AS producto,
                s.Nombre                                              AS sucursal_con_stock,
                ea.Existencia                                         AS disponible
            FROM IN_Existencias_Alm ea
            JOIN GN_Sucursales     s ON s.Cve_Sucursal  = ea.Cve_Sucursal
            JOIN IM_Productos_Gral p ON p.Cve_Producto  = ea.Cve_Producto
            WHERE ea.Existencia > 0 AND ea.Status='AC' AND ea.Cve_Sucursal<>99
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
              AND ea.Cve_Producto IN (
                  SELECT DISTINCT ea2.Cve_Producto
                  FROM IN_Existencias_Alm ea2
                  WHERE ea2.Existencia=0 AND ea2.Status='AC' AND ea2.Cve_Sucursal<>99
                    AND EXISTS (
                        SELECT 1 FROM FT_Facturas_D fd
                        JOIN FT_Facturas_C fc
                          ON fd.Cve_Folio=fc.Cve_Folio AND fd.Cve_Sucursal=fc.Cve_Sucursal
                         AND fd.Cve_Movimiento=fc.Cve_Movimiento
                        WHERE fd.Cve_Producto=ea2.Cve_Producto
                          AND fc.Status<>'C'
                          AND fc.Fecha_Documento >= DATEADD(DAY,-90,GETDATE())
                    )
              )
            ORDER BY ea.Existencia DESC
        """)

    def q_pedidos_sin_stock():
        """Pedidos activos de productos sin existencia → clientes esperando."""
        return _q("""
            SELECT TOP 5
                p.Descripcion                                         AS producto,
                COUNT(DISTINCT pd.Cve_Folio)                          AS pedidos_activos,
                SUM(pd.Cantidad)                                       AS piezas_pendientes
            FROM FT_Pedidos_D pd
            JOIN FT_Pedidos_C pc
              ON pd.Cve_Folio=pc.Cve_Folio AND pd.Cve_Sucursal=pc.Cve_Sucursal
             AND pd.Cve_Movimiento=pc.Cve_Movimiento
            JOIN IM_Productos_Gral p ON p.Cve_Producto=pd.Cve_Producto
            JOIN IN_Existencias_Alm ea
              ON ea.Cve_Producto=pd.Cve_Producto AND ea.Cve_Sucursal=pc.Cve_Sucursal
              AND ea.Status='AC'
            WHERE pc.Estatus='AC' AND pc.Cve_Sucursal<>99
              AND ea.Existencia=0
              AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
            GROUP BY p.Descripcion
            ORDER BY SUM(pd.Cantidad) DESC
        """)

    sin_stock, otras_suc, pedidos = _parallel(q_sin_stock, q_otras_sucursales, q_pedidos_sin_stock)

    if not sin_stock:
        return "No se encontraron productos sin existencia con demanda reciente."

    comprometidos = sum(int(r.get('en_pedidos_pendientes') or 0) for r in sin_stock)

    datos = f"""
PRODUCTOS SIN EXISTENCIA CON DEMANDA RECIENTE (90 días): {len(sin_stock)} (mostrando top 20)
TOTAL PIEZAS COMPROMETIDAS EN PEDIDOS ACTIVOS: {comprometidos}

DETALLE (ordenado por piezas comprometidas):
{_fmt(sin_stock)}

OPORTUNIDAD DE TRASPASO — mismos productos con stock en otras sucursales:
{_fmt(otras_suc)}

PRODUCTOS SIN STOCK CON PEDIDOS ACTIVOS DE CLIENTES ESPERANDO:
{_fmt(pedidos)}
"""
    return _interpretar("Productos sin existencia con demanda activa", datos)
