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
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.config import IA_FLASH_MODEL, IA_PRECIO_INPUT, IA_PRECIO_OUTPUT, IA_RATIO_PWA, OPENAI_API_KEY
from shared.database import query, hoy
from shared.database_local import execute as execute_local, verificar_mes_ia, periodo_ia_actual
from shared import cache_dashboard as _cache
from pwa_asistente.routers.vistas import stock_detalle

router = APIRouter(prefix="/api/ia", dependencies=[Depends(get_current_user)])

_client = OpenAI(api_key=OPENAI_API_KEY)
_MODEL  = IA_FLASH_MODEL


def _primer_nombre(nombre_completo: str) -> str:
    """Extrae el primer nombre de un nombre completo."""
    return (nombre_completo or "").split()[0].capitalize()


def _flash(prompt: str) -> tuple[str, float]:
    """Llamada mínima a la IA — 1 turno, sin historial, sin tools.

    Returns:
        (texto, costo_usd): respuesta del modelo y costo real calculado con tokens.
    """
    resp = _client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = resp.choices[0].message.content.strip()
    costo = 0.0
    if resp.usage:
        costo = (
            resp.usage.prompt_tokens     * IA_PRECIO_INPUT
            + resp.usage.completion_tokens * IA_PRECIO_OUTPUT
        )
    return texto, costo


def _registrar_costo(usuario_id: int, costo_usd: float, ratio: float = 0.0) -> None:
    """
    Acumula costo de tokens y descuenta créditos al usuario.

    Args:
        usuario_id (int):   ID del usuario (0 = cron/sistema, se ignora).
        costo_usd  (float): Costo real en USD.
        ratio      (float): Créditos a descontar. 0 = solo registra costo sin contar consulta.
                            1.0 = primer click (flash sin regenerar).
                            IA_RATIO_PWA = regenerar (doble).
    """
    if usuario_id == 0:
        return  # Llamada del cron — no hay usuario real que actualizar
    verificar_mes_ia(usuario_id, periodo_ia_actual())
    if ratio > 0:
        execute_local(
            "UPDATE usuarios SET "
            "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
            "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
            "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
            (ratio, ratio, costo_usd, usuario_id),
        )
    else:
        execute_local(
            "UPDATE usuarios SET costo_ia_usd = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
            (costo_usd, usuario_id),
        )


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

    # Ventas de hoy (lo que va del día)
    ventas_hoy = query(f"""
        SELECT COUNT(DISTINCT c.Cve_Folio) AS pedidos,
               COALESCE(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
        FROM FT_Pedidos_C c
        JOIN FT_Pedidos_Dia d
          ON d.Cve_Folio     = c.Cve_Folio
         AND d.Cve_Sucursal  = c.Cve_Sucursal
        WHERE c.Cve_Sucursal        = ?
          AND c.Referencia_Cliente  = 'PAGADO'
          AND CAST(c.Fecha_Documento AS DATE) = CAST({hoy()} AS DATE)
    """, (cve_sucursal,))

    # Ventas de ayer (pedidos cobrados)
    ventas_ay = query(f"""
        SELECT COUNT(DISTINCT c.Cve_Folio) AS pedidos,
               COALESCE(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
        FROM FT_Pedidos_C c
        JOIN FT_Pedidos_Dia d
          ON d.Cve_Folio     = c.Cve_Folio
         AND d.Cve_Sucursal  = c.Cve_Sucursal
        WHERE c.Cve_Sucursal        = ?
          AND c.Referencia_Cliente  = 'PAGADO'
          AND CAST(c.Fecha_Documento AS DATE) = DATEADD(DAY, -1, {hoy()})
    """, (cve_sucursal,))

    # Ventas del mes en curso
    ventas_mes = query(f"""
        SELECT COALESCE(SUM(d.Cantidad_Ordenada * d.Precio), 0) AS importe
        FROM FT_Pedidos_C c
        JOIN FT_Pedidos_Dia d
          ON d.Cve_Folio     = c.Cve_Folio
         AND d.Cve_Sucursal  = c.Cve_Sucursal
        WHERE c.Cve_Sucursal        = ?
          AND c.Referencia_Cliente  = 'PAGADO'
          AND YEAR(c.Fecha_Documento)  = YEAR({hoy()})
          AND MONTH(c.Fecha_Documento) = MONTH({hoy()})
    """, (cve_sucursal,))

    # Top 3 productos del mes con importe y unidades vendidas
    top3 = query(f"""
        SELECT TOP 3
            p.Descripcion                              AS producto,
            ROUND(SUM(d.Cantidad_Ordenada * d.Precio), 2) AS importe,
            SUM(d.Cantidad_Ordenada)                   AS uds
        FROM FT_Pedidos_C c
        JOIN FT_Pedidos_Dia d
          ON d.Cve_Folio     = c.Cve_Folio
         AND d.Cve_Sucursal  = c.Cve_Sucursal
        LEFT JOIN IM_Productos_Gral p ON d.Cve_Producto = p.Cve_Producto
        WHERE c.Cve_Sucursal        = ?
          AND c.Referencia_Cliente  = 'PAGADO'
          AND YEAR(c.Fecha_Documento)  = YEAR({hoy()})
          AND MONTH(c.Fecha_Documento) = MONTH({hoy()})
          AND p.Descripcion IS NOT NULL
          AND p.Descripcion NOT LIKE 'ENVIO ESPECIAL%'
        GROUP BY p.Descripcion
        ORDER BY SUM(d.Cantidad_Ordenada * d.Precio) DESC
    """, (cve_sucursal,))

    # Usar los mismos datos que las tarjetas de inventario (stock_detalle cache)
    stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}")
    if stock_cache is None:
        stock_detalle(cve_sucursal)
        stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}") or {}
    sin_stock_list = stock_cache.get("sin_stock", [])

    hoy_row     = ventas_hoy[0] if ventas_hoy else {}
    ay          = ventas_ay[0]  if ventas_ay  else {}
    mes         = ventas_mes[0] if ventas_mes else {}
    importe_hoy = hoy_row.get("importe", 0)
    pedidos_hoy = hoy_row.get("pedidos", 0)
    importe_ay  = ay.get("importe",  0)
    pedidos_ay  = ay.get("pedidos",  0)
    importe_mes = mes.get("importe", 0)
    n_sin       = len(sin_stock_list)
    top_sin     = ", ".join(r["producto"] for r in sin_stock_list[:3]) or "ninguno"
    top_txt     = ", ".join(
        f"{r['producto']} ({int(r['uds'])} uds, ${r['importe']:,.0f})"
        for r in top3
    ) or "sin registros"

    prompt = (
        f"Eres el asistente analítico personal de {nombre}. "
        f"Redacta exactamente 2 oraciones de resumen ejecutivo para la sucursal {nombre_suc}, "
        f"dirigidas a {nombre}. "
        f"Ventas de hoy hasta ahora: ${importe_hoy:,.0f} en {pedidos_hoy} pedidos cobrados. "
        f"Ventas de ayer: ${importe_ay:,.0f} en {pedidos_ay} pedidos. "
        f"Ventas acumuladas este mes: ${importe_mes:,.0f}. "
        f"Top productos del mes: {top_txt}. "
        f"Productos con demanda reciente sin existencia: {n_sin} (los más críticos: {top_sin}). "
        f"Tono directo y profesional. Empieza con el nombre. "
        f"Menciona el dato más relevante o alarmante. Sin títulos, sin viñetas, sin saludos extensos."
    )

    texto, costo = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    _registrar_costo(usuario["id"], costo, ratio=IA_RATIO_PWA if regenerar else 1.0)
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

    # Leer datos del detalle de stock (misma fuente que la tarjeta)
    # Si no hay cache (antes del cron), calcularlo ahora
    stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}")
    if stock_cache is None:
        stock_detalle(cve_sucursal)
        stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}") or {}
    sin_stock_list = stock_cache.get("sin_stock", [])

    # Usar exactamente los mismos datos que las tarjetas de inventario
    n_sin = len(sin_stock_list)
    # Detalle de los primeros 5 sin existencia: nombre + promedio mensual de ventas
    top_sin_txt = "; ".join(
        f"{p['producto']} (~${p.get('prom_importe_mensual', 0):,.0f}/mes)"
        for p in sin_stock_list[:5] if p.get("producto")
    ) or "ninguno"

    if n_sin == 0:
        prompt = (
            f"Eres el asistente analítico personal de {nombre}. "
            f"Redacta exactamente 2 oraciones de situación de inventario para {nombre_suc}, "
            f"dirigidas a {nombre}. "
            f"No hay productos con demanda real sin existencia — el inventario está cubierto. "
            f"Tono tranquilo pero sugiere mantener el monitoreo. Empieza con el nombre. Sin títulos ni viñetas."
        )
    else:
        prompt = (
            f"Eres el asistente analítico personal de {nombre}. "
            f"Redacta exactamente 2 oraciones de alerta de inventario para {nombre_suc}, "
            f"dirigidas a {nombre}. "
            f"Hay {n_sin} producto(s) con ventas reales en los últimos 3 meses pero sin existencia disponible. "
            f"Los más urgentes (por venta promedio mensual): {top_sin_txt}. "
            f"Tono profesional y urgente. Empieza con el nombre. "
            f"Destaca cuántos hay y nombra los más críticos. Sin títulos ni viñetas."
        )

    texto, costo = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    _registrar_costo(usuario["id"], costo, ratio=IA_RATIO_PWA if regenerar else 1.0)
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

    texto, costo = _flash(prompt)
    _registrar_costo(usuario["id"], costo, ratio=IA_RATIO_PWA if regenerar else 1.0)
    return JSONResponse({"texto": texto})


# ── TTS — Texto a voz con OpenAI ─────────────────────────────────────────────

class TTSRequest(BaseModel):
    texto: str


@router.post("/tts")
def tts(datos: TTSRequest, usuario: dict = Depends(get_current_user)):
    """
    Convierte texto a audio usando OpenAI TTS (voz nova, español).
    Retorna el audio como stream mp3 para reproducción directa en el navegador.
    """
    respuesta = _client.audio.speech.create(
        model="tts-1-hd",
        voice="onyx",
        input=datos.texto[:4096],
        response_format="mp3",
    )
    return Response(
        content=respuesta.content,
        media_type="audio/mpeg",
    )
