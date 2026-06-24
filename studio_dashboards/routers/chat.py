# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/chat.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.2.0
# ============================================================
"""
Router de Chat — Studio Dashboards.

Igual que el chat de la PWA pero con:
  - Modelo superior (STUDIO_CHAT_MODEL, default gpt-4.1)
  - Detección automática de solicitudes de dashboard
  - Cada consulta descuenta 1.75 del límite (IA_RATIO_STUDIO = 1.75)

Endpoints:
  GET    /api/studio/chat/conversaciones           → Lista conversaciones
  POST   /api/studio/chat/conversaciones           → Nueva conversación
  GET    /api/studio/chat/conversaciones/{id}      → Mensajes de una conversación
  DELETE /api/studio/chat/conversaciones/{id}      → Elimina una conversación
  POST   /api/studio/chat/mensaje/async            → Envía mensaje (background)
  GET    /api/studio/chat/job/{id}                 → Estado de un job
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openai import OpenAI as _OpenAI
from shared.auth import get_current_user
from shared.config import (
    OPENAI_API_KEY,
    STUDIO_PRECIO_INPUT, STUDIO_PRECIO_OUTPUT, IA_RATIO_STUDIO,
    STUDIO_CHAT_MODEL,
)

_ai_client = _OpenAI(api_key=OPENAI_API_KEY)
from shared.database_local import execute, fetch_all, fetch_one, verificar_mes_ia
from pwa_asistente.agente import director
from pwa_asistente.agente import grafica as _grafica
from pwa_asistente.agente.especialistas import (
    ventas, inventario, pedidos, medicos, clientes, mixto,
)
try:
    from studio_dashboards.routers.datos import _clasificar, _fetch_tipo, _narrar
    _DASHBOARD_FN_OK = True
except Exception as _e:
    print(f"[studio-chat] No se pudo importar funciones de dashboard: {_e}", flush=True)
    _DASHBOARD_FN_OK = False
    def _clasificar(p): return {}
    def _fetch_tipo(t, m): return {}
    def _narrar(p, t, m, d): return ""

_pool = ThreadPoolExecutor(max_workers=4)

# Regex para detectar solicitudes EXPLÍCITAS de dashboard en el texto del usuario.
_PIDE_DASH = re.compile(
    r"\b(dash\w*|tablero|gr[aá]fic[ao]s?|chart|visualiza[cr]|report[e]?)\b",
    re.IGNORECASE,
)

# Detecta comparativas específicas que requieren gráfica dinámica.
# Estas consultas se saltan los dashboards predefinidos directamente.
_GRAFICA_CUSTOM = re.compile(
    r"\b(vs\.?|versus|comparar|comparativa\s+de|comparaci[oó]n)\b",
    re.IGNORECASE,
)


def _datos_tienen_contenido(datos: dict) -> bool:
    """True si el dict de datos contiene al menos una lista no vacía o un valor numérico >0."""
    if not datos:
        return False
    for v in datos.values():
        if isinstance(v, list) and len(v) > 0:
            return True
        if isinstance(v, (int, float)) and v != 0:
            return True
    return False


_SALUDO = re.compile(
    r"^[\s¡!]*(hola|buenas?|buenos?\s+días?|buenas?\s+tardes?|buenas?\s+noches?|"
    r"hey|qué\s+tal|cómo\s+est[aá]s?|buen\s+día|hi|good\s+morning)[\s!¡.]*$",
    re.IGNORECASE,
)
_RESPUESTA_SALUDO = (
    "¡Hola! ¿En qué puedo ayudarte?\n\n"
    "Puedo responder preguntas sobre ventas, inventario, pedidos, médicos y clientes. "
    "También puedo generar dashboards visuales — solo pídemelos en tu pregunta."
)

_CAPACIDADES = re.compile(
    r"(qu[eé]\s+(haces?|puedes?|sabes?|eres?)|cómo\s+funciona[s]?|"
    r"para\s+qu[eé]\s+sirves?|qu[eé]\s+tipo\s+de|cu[aá]les\s+son\s+tus|"
    r"qu[eé]\s+informaci[oó]n|ayuda[s]?\s+con|qu[eé]\s+consultas)",
    re.IGNORECASE,
)
_DASHBOARDS_DISPONIBLES = (
    "Los dashboards visuales disponibles son:\n"
    "- **Ventas**: reporte completo · ventas de hoy · por sucursal · tendencia · comparativo de meses · diario\n"
    "- **Vendedores**: top vendedores · variación de vendedores\n"
    "- **Médicos**: ranking de médicos por ventas · tendencia mensual · ventas por representante\n"
    "- **Productos**: top productos más vendidos\n"
    "- **Clientes**: clientes frecuentes\n"
    "- **Pedidos**: pedidos activos por sucursal\n"
    "- **Inventario**: stock actual por sucursal · caducidades próximas · productos sin existencia\n\n"
    "Pídeme alguno de esos y lo genero con datos del ERP."
)

_RESPUESTA_CAPACIDADES = (
    "Soy el asistente analítico de Studio.\n\n"
    "Puedo ayudarte con:\n"
    "- **Ventas** — importes, facturas, comparativos por sucursal o vendedor\n"
    "- **Inventario** — existencias, caducidades, productos sin stock\n"
    "- **Pedidos** — pedidos activos y su antigüedad\n"
    "- **Médicos** — directorio y duplicados\n"
    "- **Clientes** — historial de compras y clientes frecuentes\n"
    "- **Dashboards** — genera tableros visuales pidiendo: *\"genera un dashboard de ventas de hoy\"*\n\n"
    "Solo escribe tu pregunta."
)

_ESPECIALISTAS = {
    "ventas":     ventas.responder,
    "inventario": inventario.responder,
    "pedidos":    pedidos.responder,
    "medicos":    medicos.responder,
    "clientes":   clientes.responder,
    "mixto":      mixto.responder,
}

_SISTEMA_ANALISTA = """
Eres un analista de negocio senior de una distribuidora farmacéutica de especialidades.
Tu asistente técnico ya consultó el ERP y obtuvo los datos exactos.

REGLAS ABSOLUTAS:
1. Reproduce TODOS los datos exactamente — cifras, totales, filas de tabla, sin omitir ninguna.
2. NUNCA recalcules totales ni inventes números que no estén en los datos originales.
3. NUNCA omitas sucursales, productos o registros aunque su valor sea cero.

Tu tarea (después de reproducir los datos):
- Interpreta qué significan los números en términos de operación del negocio
- Identifica el hallazgo más relevante (líder, caída, anomalía, oportunidad)
- Cierra con una conclusión ejecutiva breve y accionable (1-2 líneas)

Formato: markdown con secciones claras. Responde en español.
"""


def _enriquecer(pregunta: str, respuesta_agente: str) -> str:
    """Segunda pasada de Studio: análisis ejecutivo con STUDIO_CHAT_MODEL (gpt-4.1-mini)."""
    try:
        resp = _ai_client.chat.completions.create(
            model=STUDIO_CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SISTEMA_ANALISTA},
                {"role": "user", "content": (
                    f"**Pregunta del usuario:** {pregunta}\n\n"
                    f"**Datos del ERP:**\n{respuesta_agente}"
                )},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[studio-chat] _enriquecer error: {e}", flush=True)
        return respuesta_agente


_RATIO = float(IA_RATIO_STUDIO)  # 1.75 — Studio usa doble gpt-4.1-mini: agente + analista

router = APIRouter(prefix="/api/studio/chat")


class MensajeBody(BaseModel):
    mensaje:         str
    conversacion_id: Optional[int] = None


# ── Conversaciones ────────────────────────────────────────────────────────────

@router.get("/conversaciones")
def listar_conversaciones(usuario: dict = Depends(get_current_user)):
    """Lista todas las conversaciones de Studio del usuario."""
    filas = fetch_all(
        "SELECT id, titulo, ultimo_msg, creado_en "
        "FROM chat_conversaciones "
        "WHERE usuario_id = ? AND modulo = 'studio' ORDER BY creado_en DESC",
        (usuario["id"],),
    )
    return JSONResponse({"conversaciones": filas})


@router.post("/conversaciones")
def crear_conversacion(usuario: dict = Depends(get_current_user)):
    """Crea una conversación vacía de Studio."""
    conv_id = execute(
        "INSERT INTO chat_conversaciones (usuario_id, titulo, modulo) VALUES (?, ?, 'studio')",
        (usuario["id"], "Nueva conversación"),
    )
    return JSONResponse({"id": conv_id, "titulo": "Nueva conversación"})


@router.get("/conversaciones/{conv_id}")
def obtener_conversacion(conv_id: int, usuario: dict = Depends(get_current_user)):
    """Devuelve mensajes de una conversación de Studio."""
    conv = fetch_one(
        "SELECT id, titulo, creado_en FROM chat_conversaciones "
        "WHERE id = ? AND usuario_id = ? AND modulo = 'studio'",
        (conv_id, usuario["id"]),
    )
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")

    mensajes = fetch_all(
        "SELECT rol, contenido, creado_en "
        "FROM chat_mensajes WHERE conversacion_id = ? ORDER BY id",
        (conv_id,),
    )
    return JSONResponse({"conversacion": conv, "mensajes": mensajes})


@router.delete("/conversaciones/{conv_id}")
def eliminar_conversacion(conv_id: int, usuario: dict = Depends(get_current_user)):
    """Elimina una conversación de Studio."""
    conv = fetch_one(
        "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ? AND modulo = 'studio'",
        (conv_id, usuario["id"]),
    )
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")

    execute("DELETE FROM chat_mensajes WHERE conversacion_id = ?", (conv_id,))
    execute("DELETE FROM chat_conversaciones WHERE id = ?", (conv_id,))
    return JSONResponse({"mensaje": "Conversación eliminada"})


# ── Procesamiento en background ───────────────────────────────────────────────

def _procesar_job(job_id: int, conv_id: int, msg: str, historial: list, usuario_id: int) -> None:
    """
    Ejecuta la consulta con STUDIO_CHAT_MODEL y, si se detecta solicitud de dashboard,
    también genera el spec del dashboard.
    Cada bloque de DB opera de forma independiente para que un fallo parcial
    no deje el job colgado en 'pending'.
    """
    import json as _json

    costo_usd = 0.0
    dashboard = None
    respuesta = "Ups, parece que no pudimos procesar esta solicitud. Comunícate con tu proveedor."
    estado    = "error"

    # ── 1. Clasificar si se requiere dashboard PRIMERO ───────────────────────
    # Solo intentar dashboard cuando el usuario lo pide explícitamente
    # ("dashboard", "gráfica", "tablero"…). Preguntas sobre ventas o inventario
    # sin esa palabra van directamente al agente de texto.
    spec_dash = {}
    tipo_dash = "ninguno"
    es_grafica_custom = bool(_GRAFICA_CUSTOM.search(msg))  # "vs", "comparar", etc.
    pide_dash = _DASHBOARD_FN_OK and bool(_PIDE_DASH.search(msg)) and not es_grafica_custom
    if pide_dash:
        try:
            spec_dash = _clasificar(msg)
            tipo_dash = spec_dash.get("funcion", "ninguno")
        except Exception as e:
            print(f"[studio-chat] Clasificar error job={job_id}: {e}", flush=True)

    # ── 2. Generar dashboard predefinido si aplica ────────────────────────────
    # Tipos con tab dedicado en el Studio — redirigir al tab en lugar de generar chart
    # Solo redirigir al tab cuando el usuario dice explícitamente "dashboard" o "tablero".
    # Si dice "gráfica" → generar chart dinámico aunque el tipo tenga tab dedicado.
    _es_dashboard = bool(re.search(r'\b(dash\w*|tablero)\b', msg, re.IGNORECASE))
    _TIPOS_CON_TAB = {
        "top_vendedores", "variacion_vendedores",              # → tab Vendedores
        "medicos_dashboard",                                   # → tab Médicos
        "ventas_sucursal", "comparativo_meses",                # → tab Ventas (resumen)
        "ventas_diario",   "tendencia_anual", "reporte_ventas",# → tab Ventas (resumen)
        "top_productos",                                       # → tab Productos
        "reporte_inventario", "inventario_stock", "stockouts", # → tab Inventario
    }
    if tipo_dash in _TIPOS_CON_TAB and _es_dashboard:
        modo = spec_dash.get("modo", "30d")
        fi   = spec_dash.get("fecha_inicio")
        ff   = spec_dash.get("fecha_fin")
        if tipo_dash == "medicos_dashboard":
            dashboard = {"tipo": "switch_tab", "tab": "medicos", "modo": modo, "fecha_inicio": fi, "fecha_fin": ff}
            respuesta = "Te muestro el dashboard de **Médicos** — ranking, tendencia mensual y ventas por representante."
        elif tipo_dash in ("top_vendedores", "variacion_vendedores"):
            dashboard = {"tipo": "switch_tab", "tab": "vendedores", "modo": modo, "fecha_inicio": fi, "fecha_fin": ff}
            respuesta = "Te muestro el dashboard de **Vendedores** — ranking, tendencia mensual y líder por sucursal."
        elif tipo_dash == "top_productos":
            dashboard = {"tipo": "switch_tab", "tab": "productos", "modo": modo, "fecha_inicio": fi, "fecha_fin": ff}
            respuesta = "Te muestro el dashboard de **Productos** — top ventas, variación y tendencia mensual."
        elif tipo_dash in ("reporte_inventario", "inventario_stock", "stockouts"):
            dashboard = {"tipo": "switch_tab", "tab": "inventario", "modo": modo, "fecha_inicio": fi, "fecha_fin": ff}
            respuesta = "Te muestro el dashboard de **Inventario** — stock actual, críticos y valor por sucursal."
        else:
            # Ventas generales → tab Ventas (resumen)
            dashboard = {"tipo": "switch_tab", "tab": "resumen", "modo": modo, "fecha_inicio": fi, "fecha_fin": ff}
            respuesta = "Análisis generado con datos del ERP en tiempo real."
        estado    = "done"
        tipo_dash = "con_tab"   # evitar que caiga al bloque siguiente

    if tipo_dash not in ("ninguno", "con_tab"):
        try:
            modo      = spec_dash.get("modo", "30d")
            fi        = spec_dash.get("fecha_inicio")
            ff        = spec_dash.get("fecha_fin")
            producto  = spec_dash.get("producto")
            datos     = _fetch_tipo(tipo_dash, modo, fi, ff, producto=producto)
            if not _datos_tienen_contenido(datos):
                # Datos vacíos — caer al generador dinámico
                tipo_dash = "ninguno"
            else:
                narrativa, _ = _narrar(msg, tipo_dash, modo, datos)
                dashboard = {
                    "tipo":         tipo_dash,
                    "layout":       spec_dash.get("layout", "kpi_bar"),
                    "chart_type":   spec_dash.get("chart_type", "bar"),
                    "titulo":       spec_dash.get("titulo", "Dashboard"),
                    "modo":         modo,
                    "fecha_inicio": fi,
                    "fecha_fin":    ff,
                    "narrativa":    narrativa,
                    "datos":        datos,
                }
                respuesta = narrativa
                estado    = "done"
                costo_usd = 0.0
                print(f"[studio-chat] Dashboard predefinido tipo={tipo_dash} job={job_id}", flush=True)
        except Exception as e:
            print(f"[studio-chat] Dashboard error job={job_id}: {e}", flush=True)
            tipo_dash = "ninguno"

    # ── 3. Respuesta de texto o gráfica dinámica (si no hay dashboard predefinido) ────
    if tipo_dash == "ninguno" and estado != "done":
        try:
            area, costo_dir = director.clasificar(
                msg, historial, model=STUDIO_CHAT_MODEL,
                precio_input=STUDIO_PRECIO_INPUT, precio_output=STUDIO_PRECIO_OUTPUT,
            )
            fn        = _ESPECIALISTAS.get(area, mixto.responder)
            resultado = fn(msg, historial, model=STUDIO_CHAT_MODEL)
            costo_usd = costo_dir + (
                resultado.tokens_prompt     * STUDIO_PRECIO_INPUT
                + resultado.tokens_completion * STUDIO_PRECIO_OUTPUT
            )

            # Si el usuario pidió gráfica pero no hay dashboard predefinido (o datos vacíos),
            # generar Chart.js dinámico — se muestra en el área de dashboards, no en el chat
            if bool(_PIDE_DASH.search(msg)) or es_grafica_custom:
                html_chart, tp_g, tc_g = _grafica.generar(resultado.texto, msg)
                costo_usd += tp_g * STUDIO_PRECIO_INPUT + tc_g * STUDIO_PRECIO_OUTPUT
                dashboard = {
                    "tipo":    "chart_dinamico",
                    "titulo":  "Gráfica generada con IA",
                    "html":    html_chart,
                }
                respuesta = "Análisis generado con datos del ERP en tiempo real."
            else:
                respuesta = _enriquecer(msg, resultado.texto)

            estado = "done"
        except Exception as e:
            print(f"[studio-chat] Agente error job={job_id}: {e}", flush=True)

    # ── 4. Guardar mensaje en el chat ─────────────────────────────────────────
    try:
        execute(
            "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
            (conv_id, "assistant", respuesta),
        )
    except Exception as e:
        print(f"[studio-chat] INSERT mensaje error job={job_id}: {e}", flush=True)

    # ── 5. Descontar consultas ────────────────────────────────────────────────
    try:
        execute(
            "UPDATE usuarios SET "
            "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
            "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
            "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
            (_RATIO, _RATIO, costo_usd, usuario_id),
        )
    except Exception as e:
        print(f"[studio-chat] UPDATE usuarios error job={job_id}: {e}", flush=True)

    # ── 6. Marcar job como terminado — SIEMPRE se ejecuta ────────────────────
    meta = _json.dumps({"dashboard": dashboard}, ensure_ascii=False) if dashboard else None
    try:
        execute(
            "UPDATE chat_jobs SET estado = ?, respuesta = ?, area = 'studio', "
            "    meta_json = ?, terminado_en = datetime('now') WHERE id = ?",
            (estado, respuesta, meta, job_id),
        )
    except Exception:
        # meta_json puede no existir en instancias viejas — reintentar sin ella
        try:
            execute(
                "UPDATE chat_jobs SET estado = ?, respuesta = ?, area = 'studio', "
                "    terminado_en = datetime('now') WHERE id = ?",
                (estado, respuesta, job_id),
            )
        except Exception as e:
            print(f"[studio-chat] UPDATE job FATAL error job={job_id}: {e}", flush=True)


@router.post("/mensaje/async")
def enviar_mensaje_async(body: MensajeBody, usuario: dict = Depends(get_current_user)):
    """
    Envía mensaje al agente Studio en background.
    Respuestas instantáneas (saludo/capacidades) se resuelven sin thread.
    """
    msg = body.mensaje.strip()
    if not msg:
        raise HTTPException(400, "El mensaje no puede estar vacío")

    verificar_mes_ia(usuario["id"], date.today().strftime("%Y-%m"))
    u = fetch_one("SELECT COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, limite_ia FROM usuarios WHERE id = ?", (usuario["id"],))
    if u and u["limite_ia"] > 0 and u["consultas_ia_r"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu límite de consultas de IA. "
            "Contacta a tu administrador para ampliar el límite.",
        )

    # Crear o verificar conversación
    conv_id = body.conversacion_id
    if not conv_id:
        conv_id = execute(
            "INSERT INTO chat_conversaciones (usuario_id, titulo, modulo) VALUES (?, ?, 'studio')",
            (usuario["id"], msg[:80]),
        )
    else:
        conv = fetch_one(
            "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ? AND modulo = 'studio'",
            (conv_id, usuario["id"]),
        )
        if not conv:
            raise HTTPException(404, "Conversación no encontrada")

    historial = fetch_all(
        "SELECT rol, contenido FROM chat_mensajes WHERE conversacion_id = ? ORDER BY id",
        (conv_id,),
    )

    # Respuestas instantáneas — sin thread ni costo
    if _SALUDO.match(msg):
        respuesta, area = _RESPUESTA_SALUDO, "saludo"
    elif _CAPACIDADES.search(msg) and len(msg) < 120:
        respuesta, area = _RESPUESTA_CAPACIDADES, "capacidades"
    else:
        respuesta, area = None, None

    if respuesta is not None:
        execute(
            "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
            (conv_id, "user", msg),
        )
        execute(
            "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
            (conv_id, "assistant", respuesta),
        )
        execute(
            "UPDATE chat_conversaciones SET ultimo_msg = ? WHERE id = ?",
            (msg[:80], conv_id),
        )
        job_id = execute(
            "INSERT INTO chat_jobs (usuario_id, conversacion_id, pregunta, respuesta, area, estado, terminado_en) "
            "VALUES (?, ?, ?, ?, ?, 'done', datetime('now'))",
            (usuario["id"], conv_id, msg, respuesta, area),
        )
        return JSONResponse({
            "job_id": job_id, "conversacion_id": conv_id,
            "estado": "done", "respuesta": respuesta, "area": area,
        })

    # Guardar mensaje del usuario
    execute(
        "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
        (conv_id, "user", msg),
    )
    execute(
        "UPDATE chat_conversaciones SET ultimo_msg = ? WHERE id = ?",
        (msg[:80], conv_id),
    )

    job_id = execute(
        "INSERT INTO chat_jobs (usuario_id, conversacion_id, pregunta) VALUES (?, ?, ?)",
        (usuario["id"], conv_id, msg),
    )

    _pool.submit(_procesar_job, job_id, conv_id, msg, historial, usuario["id"])

    return JSONResponse({"job_id": job_id, "conversacion_id": conv_id, "estado": "pending"})


@router.get("/job/{job_id}")
def obtener_job(job_id: int, usuario: dict = Depends(get_current_user)):
    """Estado de un job de Studio. Auto-expira a error si lleva >10 min."""
    job = fetch_one(
        "SELECT id, estado, respuesta, area, conversacion_id, creado_en, meta_json "
        "FROM chat_jobs WHERE id = ? AND usuario_id = ?",
        (job_id, usuario["id"]),
    )
    if not job:
        raise HTTPException(404, "Job no encontrado")

    if job["estado"] == "pending":
        stale = fetch_one(
            "SELECT id FROM chat_jobs WHERE id = ? AND creado_en < datetime('now', '-10 minutes')",
            (job_id,),
        )
        if stale:
            msg_error = "La consulta fue interrumpida. Intenta de nuevo."
            execute(
                "UPDATE chat_jobs SET estado = 'error', respuesta = ?, terminado_en = datetime('now') WHERE id = ?",
                (msg_error, job_id),
            )
            job["estado"]    = "error"
            job["respuesta"] = msg_error

    # Parsear dashboard del meta_json si existe
    if job.get("meta_json"):
        import json as _json
        try:
            meta = _json.loads(job["meta_json"])
            job["dashboard"] = meta.get("dashboard")
        except Exception:
            pass
    job.pop("meta_json", None)

    return JSONResponse(job)
