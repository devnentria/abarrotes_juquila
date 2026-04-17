# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : routers/ia_flash.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router IA Flash — Resúmenes ejecutivos ligeros con IA.

Endpoints:
  GET /api/ia/sucursal/{cve}   → Resumen del día de una sucursal (Inicio)
  GET /api/ia/inventario/{cve} → Alerta inteligente de stock de una sucursal
  GET /api/ia/medicos          → Insight sobre duplicados de médicos

Diseño:
  - Una sola llamada a gpt-4o-mini por petición (sin tool-calls, sin director).
  - Personalizado con el nombre del usuario autenticado.
  - Con ?regenerar=1 incrementa consultas_ia del usuario (segunda generación del día).
"""
from datetime import date

from openai import OpenAI
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from shared.auth import get_current_user
from shared.config import IA_COSTO_POR_CONSULTA, IA_FLASH_MODEL, OPENAI_API_KEY
from shared.database import query, hoy
from shared.database_local import execute as execute_local, verificar_mes_ia
from shared import cache_dashboard as _cache

router = APIRouter(prefix="/api/ia", dependencies=[Depends(get_current_user)])

_client = OpenAI(api_key=OPENAI_API_KEY)
_MODEL  = IA_FLASH_MODEL  # Modelo rápido para flash — independiente del chat principal


def _primer_nombre(nombre_completo: str) -> str:
    """Extrae el primer nombre de un nombre completo."""
    return (nombre_completo or "").split()[0].capitalize()


def _flash(prompt: str) -> str:
    """Llamada mínima a la IA — 1 turno, sin historial, sin tools."""
    resp = _client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


# ── Resumen de sucursal — Inicio ──────────────────────────────────────────────

@router.get("/sucursal/{cve_sucursal}")
def ia_sucursal(
    cve_sucursal: int,
    regenerar: bool = Query(False),
    usuario: dict = Depends(get_current_user),
):
    """
    Genera 2 oraciones de resumen ejecutivo para una sucursal,
    personalizadas con el nombre del usuario.
    """
    _clave = f"ia_sucursal_{cve_sucursal}"
    if not regenerar:
        cached = _cache.get(_clave)
        if cached:
            return JSONResponse(cached)

    nombre = _primer_nombre(usuario["nombre"])

    suc = query(
        "SELECT Nombre FROM GN_Sucursales WHERE Cve_Sucursal = ?",
        (cve_sucursal,),
    )
    if not suc:
        return JSONResponse({"texto": "Sucursal no encontrada."})
    nombre_suc = suc[0]["Nombre"]

    ventas = query(f"""
        SELECT COUNT(*) AS facturas, COALESCE(SUM(Importe_Total), 0) AS importe
        FROM FT_Facturas_C
        WHERE Cve_Sucursal = ?
          AND Status       <> 'C'
          AND CAST(Fecha_Documento AS DATE) = DATEADD(DAY, -1, {hoy()})
    """, (cve_sucursal,))

    top3 = query(f"""
        SELECT TOP 3
            p.Descripcion                  AS producto,
            ROUND(SUM(fd.Importe_Neto), 2) AS importe
        FROM FT_Facturas_D fd
        JOIN FT_Facturas_C fc
          ON fd.Cve_Folio      = fc.Cve_Folio
         AND fd.Cve_Sucursal   = fc.Cve_Sucursal
         AND fd.Cve_Movimiento = fc.Cve_Movimiento
        LEFT JOIN IM_Productos_Gral p ON fd.Cve_Producto = p.Cve_Producto
        WHERE fc.Cve_Sucursal = ?
          AND fc.Status       <> 'C'
          AND YEAR(fc.Fecha_Documento)  = YEAR({hoy()})
          AND MONTH(fc.Fecha_Documento) = MONTH({hoy()})
          AND p.Descripcion IS NOT NULL
        GROUP BY p.Descripcion
        ORDER BY SUM(fd.Importe_Neto) DESC
    """, (cve_sucursal,))

    pedidos = query(
        "SELECT COUNT(*) AS total FROM FT_Pedidos_C WHERE Cve_Sucursal = ? AND Estatus = 'AC'",
        (cve_sucursal,),
    )

    # Pedido activo más antiguo — dato no visible en la tarjeta
    pedido_viejo = query(f"""
        SELECT TOP 1
            DATEDIFF(DAY, Fecha_Documento, {hoy()}) AS dias_antiguo
        FROM FT_Pedidos_C
        WHERE Cve_Sucursal = ? AND Estatus = 'AC'
        ORDER BY Fecha_Documento ASC
    """, (cve_sucursal,))

    # Productos en stock crítico (1–5 unidades) — dato no visible en la tarjeta
    stock_critico = query("""
        SELECT COUNT(*) AS total
        FROM IN_Existencias_Alm
        WHERE Cve_Sucursal = ? AND Status = 'AC'
          AND Existencia > 0 AND Existencia <= 5
    """, (cve_sucursal,))

    v             = ventas[0]       if ventas       else {}
    ped           = pedidos[0]      if pedidos      else {}
    viejo         = pedido_viejo[0] if pedido_viejo else {}
    critico       = stock_critico[0] if stock_critico else {}
    importe       = v.get("importe",      0)
    facturas      = v.get("facturas",     0)
    n_ped         = ped.get("total",      0)
    dias_viejo    = viejo.get("dias_antiguo", 0) or 0
    n_critico     = critico.get("total",  0)
    top_txt       = ", ".join(f"{r['producto']} (${r['importe']:,.0f})" for r in top3) or "sin registros"

    prompt = (
        f"Eres el asistente analítico personal de {nombre}. "
        f"Redacta exactamente 2 oraciones de resumen ejecutivo para la sucursal {nombre_suc}, "
        f"dirigidas a {nombre}. "
        f"Ventas de ayer: ${importe:,.0f} en {facturas} facturas. "
        f"Pedidos activos: {n_ped} (el más antiguo lleva {dias_viejo} días sin surtir). "
        f"Productos con stock crítico (menos de 5 piezas): {n_critico}. "
        f"Top productos del mes: {top_txt}. "
        f"Tono directo y profesional. Empieza con el nombre. "
        f"Menciona el dato más alarmante si lo hay (pedido viejo o stock crítico alto). "
        f"Sin títulos, sin viñetas, sin saludos extensos."
    )

    texto = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    if regenerar:
        verificar_mes_ia(usuario["id"], date.today().strftime("%Y-%m"))
        execute_local(
            "UPDATE usuarios SET consultas_ia = consultas_ia + 1, "
            "    costo_ia_usd = ROUND(costo_ia_usd + ?, 4) WHERE id = ?",
            (IA_COSTO_POR_CONSULTA, usuario["id"]),
        )
    return JSONResponse({"texto": texto})


# ── Alerta de inventario — Inventario ─────────────────────────────────────────

@router.get("/inventario/{cve_sucursal}")
def ia_inventario(
    cve_sucursal: int,
    regenerar: bool = Query(False),
    usuario: dict = Depends(get_current_user),
):
    """
    Genera una alerta inteligente de inventario para una sucursal:
    caducidades próximas y productos sin existencia.
    """
    _clave = f"ia_inventario_{cve_sucursal}"
    if not regenerar:
        cached = _cache.get(_clave)
        if cached:
            return JSONResponse(cached)

    nombre = _primer_nombre(usuario["nombre"])

    suc = query(
        "SELECT Nombre FROM GN_Sucursales WHERE Cve_Sucursal = ?",
        (cve_sucursal,),
    )
    if not suc:
        return JSONResponse({"texto": "Sucursal no encontrada."})
    nombre_suc = suc[0]["Nombre"]

    caducidades = query(f"""
        SELECT TOP 3
            p.Descripcion      AS producto,
            el.Num_Lote        AS lote,
            DATEDIFF(DAY, {hoy()}, el.Fecha_Caducidad) AS dias
        FROM IN_Existencias_Lote el
        LEFT JOIN IM_Productos_Gral p ON el.Cve_Producto = p.Cve_Producto
        WHERE el.Cve_Sucursal    = ?
          AND el.Existencia      > 0
          AND el.Fecha_Caducidad IS NOT NULL
          AND el.Fecha_Caducidad BETWEEN {hoy()} AND DATEADD(DAY, 60, {hoy()})
        ORDER BY el.Fecha_Caducidad ASC
    """, (cve_sucursal,))

    # Mismo criterio que el detalle: sin stock + >= $50/mes promedio en 3 meses
    sin_stock = query(f"""
        SELECT COUNT(*) AS total
        FROM (
            SELECT ea.Cve_Producto
            FROM IN_Existencias_Alm ea
            LEFT JOIN (
                SELECT fd.Cve_Producto, SUM(fd.Importe_Neto) AS importe
                FROM FT_Facturas_D fd
                JOIN FT_Facturas_C fc
                  ON fd.Cve_Folio      = fc.Cve_Folio
                 AND fd.Cve_Sucursal   = fc.Cve_Sucursal
                 AND fd.Cve_Movimiento = fc.Cve_Movimiento
                WHERE fc.Cve_Sucursal = ?
                  AND fc.Status      <> 'C'
                  AND fc.Fecha_Documento >= DATEADD(DAY, -90, {hoy()})
                GROUP BY fd.Cve_Producto
            ) v ON ea.Cve_Producto = v.Cve_Producto
            WHERE ea.Cve_Sucursal = ?
              AND ea.Status       = 'AC'
            GROUP BY ea.Cve_Producto
            HAVING SUM(ea.Existencia) <= 0
               AND ISNULL(SUM(v.importe), 0) / 3.0 >= 50
        ) t
    """, (cve_sucursal, cve_sucursal))

    # TOP 3 más vendidos (90 días) sin existencia — para hacer la alerta específica
    top_sin_stock = query(f"""
        SELECT TOP 3
            p.Descripcion          AS producto,
            SUM(fd.Cantidad)       AS unidades
        FROM FT_Facturas_D fd
        JOIN FT_Facturas_C fc
          ON fd.Cve_Folio      = fc.Cve_Folio
         AND fd.Cve_Sucursal   = fc.Cve_Sucursal
         AND fd.Cve_Movimiento = fc.Cve_Movimiento
        JOIN IM_Productos_Gral p ON fd.Cve_Producto = p.Cve_Producto
        JOIN IN_Existencias_Alm ea
          ON fd.Cve_Producto  = ea.Cve_Producto
         AND ea.Cve_Sucursal  = fc.Cve_Sucursal
        WHERE fc.Cve_Sucursal  = ?
          AND fc.Status       <> 'C'
          AND fc.Fecha_Documento >= DATEADD(DAY, -90, {hoy()})
          AND ea.Status        = 'AC'
          AND ea.Existencia   <= 0
          AND p.Descripcion IS NOT NULL
        GROUP BY p.Descripcion
        HAVING SUM(fd.Importe_Neto) / 3.0 >= 50
        ORDER BY SUM(fd.Importe_Neto) DESC
    """, (cve_sucursal,))

    # Productos sin existencia que ya tienen traspaso pendiente de recibir
    en_camino = query("""
        SELECT COUNT(DISTINCT CAST(t.Cve_Producto AS INT)) AS total
        FROM VW_Temp_Transpaso_Pedidos t
        JOIN IN_Existencias_Alm ea
          ON ea.Cve_Producto = CAST(t.Cve_Producto AS INT)
         AND ea.Cve_Sucursal = ?
        WHERE t.Cve_Sucursal = ?
          AND ea.Status      = 'AC'
          AND ea.Existencia  <= 0
    """, (cve_sucursal, cve_sucursal))

    # Lotes ya caducados con existencia — dato no visible en la tarjeta
    caducados = query(f"""
        SELECT COUNT(*) AS total
        FROM IN_Existencias_Lote
        WHERE Cve_Sucursal    = ?
          AND Existencia      > 0
          AND Fecha_Caducidad < {hoy()}
    """, (cve_sucursal,))

    # Stock crítico solo en productos con ventas recientes
    stock_critico = query(f"""
        SELECT COUNT(DISTINCT ea.Cve_Producto) AS total
        FROM IN_Existencias_Alm ea
        WHERE ea.Cve_Sucursal = ? AND ea.Status = 'AC'
          AND ea.Existencia > 0 AND ea.Existencia <= 5
          AND EXISTS (
              SELECT 1
              FROM FT_Facturas_D fd
              JOIN FT_Facturas_C fc
                ON fd.Cve_Folio      = fc.Cve_Folio
               AND fd.Cve_Sucursal   = fc.Cve_Sucursal
               AND fd.Cve_Movimiento = fc.Cve_Movimiento
              WHERE fd.Cve_Producto  = ea.Cve_Producto
                AND fc.Cve_Sucursal  = ea.Cve_Sucursal
                AND fc.Status       <> 'C'
                AND fc.Fecha_Documento >= DATEADD(DAY, -90, {hoy()})
          )
    """, (cve_sucursal,))

    n_sin        = sin_stock[0]["total"]     if sin_stock     else 0
    n_caducados  = caducados[0]["total"]     if caducados     else 0
    n_critico    = stock_critico[0]["total"] if stock_critico else 0
    n_en_camino  = en_camino[0]["total"]     if en_camino     else 0
    cad_txt      = ", ".join(
        f"{r['producto']} (lote {r['lote']}, {r['dias']} días)"
        for r in caducidades
    ) or "ninguna en los próximos 60 días"
    top_sin_txt  = ", ".join(
        f"{r['producto']} ({r['unidades']:.0f} uds)"
        for r in top_sin_stock
    ) or "ninguno"

    prompt = (
        f"Eres el asistente analítico personal de {nombre}. "
        f"Redacta exactamente 2 oraciones de alerta de inventario para {nombre_suc}, "
        f"dirigidas a {nombre}. "
        f"Datos: {n_sin} productos con demanda reciente (últimos 90 días) sin existencia "
        f"({n_en_camino} de ellos ya tienen piezas en camino por traspaso pendiente), "
        f"{n_critico} en stock crítico (menos de 5 piezas), "
        f"{n_caducados} lotes con producto caducado aún en almacén. "
        f"Productos más vendidos sin existencia: {top_sin_txt}. "
        f"Caducidades próximas (60 días): {cad_txt}. "
        f"Tono profesional y urgente si hay riesgo. Empieza con el nombre. "
        f"Destaca el problema más grave. Sin títulos ni viñetas."
    )

    texto = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    if regenerar:
        verificar_mes_ia(usuario["id"], date.today().strftime("%Y-%m"))
        execute_local(
            "UPDATE usuarios SET consultas_ia = consultas_ia + 1, "
            "    costo_ia_usd = ROUND(costo_ia_usd + ?, 4) WHERE id = ?",
            (IA_COSTO_POR_CONSULTA, usuario["id"]),
        )
    return JSONResponse({"texto": texto})


# ── Alerta de médicos duplicados ──────────────────────────────────────────────

@router.get("/medicos")
def ia_medicos(
    regenerar: bool = Query(False),
    usuario: dict = Depends(get_current_user),
):
    """
    Genera una alerta corta sobre médicos duplicados en el catálogo.
    """
    nombre = _primer_nombre(usuario["nombre"])

    try:
        cedula = query("""
            SELECT COUNT(*) AS grupos
            FROM (
                SELECT LTRIM(RTRIM(cedula)) AS c
                FROM GC_Medicos
                WHERE LTRIM(RTRIM(ISNULL(cedula,''))) <> ''
                GROUP BY LTRIM(RTRIM(cedula))
                HAVING COUNT(*) > 1
            ) t
        """)
        nombre_dup = query("""
            SELECT COUNT(*) AS grupos
            FROM (
                SELECT UPPER(LTRIM(RTRIM(Nombre))) AS n
                FROM GC_Medicos
                WHERE LTRIM(RTRIM(ISNULL(Nombre,''))) <> ''
                GROUP BY UPPER(LTRIM(RTRIM(Nombre)))
                HAVING COUNT(*) > 1
            ) t
        """)
        # Sin vendedor asignado — dato no visible en la pantalla
        sin_vendedor = query("""
            SELECT COUNT(*) AS total
            FROM GC_Medicos
            WHERE ISNULL(LTRIM(RTRIM(cve_vendedor)), '') = ''
        """)
        # Sin cédula registrada — dato no visible en la pantalla
        sin_cedula = query("""
            SELECT COUNT(*) AS total
            FROM GC_Medicos
            WHERE LTRIM(RTRIM(ISNULL(cedula, ''))) = ''
        """)
        # Vendedor con más médicos asignados
        top_vendedor = query("""
            SELECT TOP 1
                v.Nombre                          AS vendedor,
                COUNT(m.Cve_Medico)               AS total
            FROM GC_Medicos m
            JOIN GC_Vendedores v ON m.cve_vendedor = v.Cve_Vendedor
            WHERE ISNULL(LTRIM(RTRIM(m.cve_vendedor)), '') <> ''
            GROUP BY v.Nombre
            ORDER BY COUNT(m.Cve_Medico) DESC
        """)
    except Exception:
        return JSONResponse({"texto": None})

    n_cedula     = cedula[0]["grupos"]        if cedula        else 0
    n_nombre     = nombre_dup[0]["grupos"]    if nombre_dup    else 0
    n_sin_vend   = sin_vendedor[0]["total"]   if sin_vendedor  else 0
    n_sin_ced    = sin_cedula[0]["total"]     if sin_cedula    else 0
    top_vend_txt = (
        f"{top_vendedor[0]['vendedor']} ({top_vendedor[0]['total']} médicos)"
        if top_vendedor else "sin datos"
    )

    if n_cedula == 0 and n_nombre == 0:
        prompt = (
            f"Eres el asistente analítico personal de {nombre}. "
            f"Redacta exactamente 2 oraciones sobre el estado del catálogo de médicos, "
            f"dirigidas a {nombre}. "
            f"Datos: sin duplicados detectados. "
            f"{n_sin_vend} médicos sin vendedor asignado. "
            f"{n_sin_ced} médicos sin cédula registrada. "
            f"El vendedor con más médicos a cargo es {top_vend_txt}. "
            f"Tono positivo pero con oportunidades de mejora. Empieza con el nombre. Sin títulos ni viñetas."
        )
    else:
        prompt = (
            f"Eres el asistente analítico personal de {nombre}. "
            f"Redacta exactamente 2 oraciones sobre el estado del catálogo de médicos, "
            f"dirigidas a {nombre}. "
            f"Duplicados detectados: {n_cedula} médicos con la misma cédula registrada más de una vez, "
            f"{n_nombre} médicos con el mismo nombre registrado más de una vez. "
            f"Médicos sin vendedor asignado: {n_sin_vend}. Sin cédula registrada: {n_sin_ced}. "
            f"El vendedor con más médicos a cargo es {top_vend_txt}. "
            f"Usa la palabra 'médicos', no 'grupos'. "
            f"Tono profesional. Empieza con el nombre. Menciona el impacto en comisiones. "
            f"Sin títulos ni viñetas."
        )

    texto = _flash(prompt)
    if regenerar:
        verificar_mes_ia(usuario["id"], date.today().strftime("%Y-%m"))
        execute_local(
            "UPDATE usuarios SET consultas_ia = consultas_ia + 1, "
            "    costo_ia_usd = ROUND(costo_ia_usd + ?, 4) WHERE id = ?",
            (IA_COSTO_POR_CONSULTA, usuario["id"]),
        )
    return JSONResponse({"texto": texto})
