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
from shared.config import IA_FLASH_MODEL, IA_PRECIO_INPUT, IA_PRECIO_OUTPUT, OPENAI_API_KEY
from shared.database import query, hoy
from shared.database_local import execute as execute_local, verificar_mes_ia
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


def _registrar_costo(usuario_id: int, costo_usd: float, sumar_consulta: bool = False) -> None:
    """
    Acumula el costo real de tokens en el usuario.

    Args:
        usuario_id     (int):   ID del usuario (0 = cron/sistema, se ignora).
        costo_usd      (float): Costo real calculado con tokens del modelo.
        sumar_consulta (bool):  Si True, también incrementa consultas_ia (+1 cuota del cliente).
    """
    if usuario_id == 0:
        return  # Llamada del cron — no hay usuario real que actualizar
    verificar_mes_ia(usuario_id, date.today().strftime("%Y-%m"))
    if sumar_consulta:
        execute_local(
            "UPDATE usuarios SET consultas_ia = consultas_ia + 1, "
            "    costo_ia_usd = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
            (costo_usd, usuario_id),
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

    # Pedido activo más antiguo
    pedido_viejo = query(f"""
        SELECT TOP 1
            DATEDIFF(DAY, Fecha_Documento, {hoy()}) AS dias_antiguo
        FROM FT_Pedidos_C
        WHERE Cve_Sucursal = ? AND Estatus = 'AC'
        ORDER BY Fecha_Documento ASC
    """, (cve_sucursal,))

    # Usar los mismos datos que las tarjetas de inventario (stock_detalle cache)
    stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}")
    if stock_cache is None:
        stock_detalle(cve_sucursal)
        stock_cache = _cache.get(f"stock_detalle_{cve_sucursal}") or {}
    sin_stock_list = stock_cache.get("sin_stock",   [])
    caducidades    = stock_cache.get("caducidades", [])

    v          = ventas[0]       if ventas       else {}
    ped        = pedidos[0]      if pedidos      else {}
    viejo      = pedido_viejo[0] if pedido_viejo else {}
    importe    = v.get("importe",      0)
    facturas   = v.get("facturas",     0)
    n_ped      = ped.get("total",      0)
    dias_viejo = viejo.get("dias_antiguo", 0) or 0
    n_sin      = len(sin_stock_list)
    n_cad      = len(caducidades)
    top_sin    = ", ".join(r["producto"] for r in sin_stock_list[:3]) or "ninguno"
    top_txt    = ", ".join(f"{r['producto']} (${r['importe']:,.0f})" for r in top3) or "sin registros"

    prompt = (
        f"Eres el asistente analítico personal de {nombre}. "
        f"Redacta exactamente 2 oraciones de resumen ejecutivo para la sucursal {nombre_suc}, "
        f"dirigidas a {nombre}. "
        f"Ventas de ayer: ${importe:,.0f} en {facturas} facturas. "
        f"Pedidos activos: {n_ped} (el más antiguo lleva {dias_viejo} días sin surtir). "
        f"Productos con demanda reciente sin existencia: {n_sin} (los más críticos: {top_sin}). "
        f"Productos próximos a caducar (90 días): {n_cad}. "
        f"Top productos del mes: {top_txt}. "
        f"Tono directo y profesional. Empieza con el nombre. "
        f"Menciona el dato más alarmante. Sin títulos, sin viñetas, sin saludos extensos."
    )

    texto, costo = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    _registrar_costo(usuario["id"], costo, sumar_consulta=regenerar)
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
    sin_stock_list = stock_cache.get("sin_stock",   [])
    caducidades    = stock_cache.get("caducidades", [])

    n_sin       = len(sin_stock_list)
    n_en_camino = sum(1 for p in sin_stock_list if p.get("en_camino", 0) > 0)
    top_sin_txt = ", ".join(
        p["producto"] for p in sin_stock_list[:3] if p.get("producto")
    ) or "ninguno"
    cad_txt = ", ".join(
        f"{r['producto']} (lote {r['lote']}, {r['dias_para_caducar']} días)"
        for r in caducidades[:3]
    ) or "ninguna en los próximos 60 días"

    # Lotes ya caducados con existencia — no está en cache del detalle
    caducados = query(f"""
        SELECT COUNT(*) AS total
        FROM IN_Existencias_Lote
        WHERE Cve_Sucursal    = ?
          AND Existencia      > 0
          AND Fecha_Caducidad < {hoy()}
    """, (cve_sucursal,))

    # Stock crítico solo en productos con ventas recientes — no está en cache del detalle
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

    n_caducados = caducados[0]["total"]     if caducados     else 0
    n_critico   = stock_critico[0]["total"] if stock_critico else 0

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

    texto, costo = _flash(prompt)
    _cache.set(_clave, {"texto": texto})
    _registrar_costo(usuario["id"], costo, sumar_consulta=regenerar)
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
    _registrar_costo(usuario["id"], costo, sumar_consulta=regenerar)
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
        model="tts-1",
        voice="nova",
        input=datos.texto[:4096],
        response_format="mp3",
    )
    return Response(
        content=respuesta.content,
        media_type="audio/mpeg",
    )
