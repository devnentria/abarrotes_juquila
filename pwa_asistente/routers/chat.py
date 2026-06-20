# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : routers/chat.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Router de Chat — Asistente IA PWA.

Endpoints:
  GET    /api/chat/conversaciones           → Lista conversaciones del usuario
  POST   /api/chat/conversaciones           → Crea una nueva conversación
  GET    /api/chat/conversaciones/{id}      → Mensajes de una conversación
  DELETE /api/chat/conversaciones/{id}      → Elimina una conversación
  POST   /api/chat/mensaje/async            → Envía mensaje (procesamiento en background)
  GET    /api/chat/job/{id}                 → Consulta estado de un job async
  POST   /api/chat/feedback                 → Registra feedback 👍/👎 de una respuesta
  POST   /api/chat/audio                    → Modo llamada: STT → agente → TTS (base64)
"""
import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from openai import OpenAI
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.config import IA_PRECIO_INPUT, IA_PRECIO_OUTPUT, IA_RATIO_PWA, OPENAI_API_KEY
from shared.database import query as query_erp
from shared.database_local import execute, fetch_all, fetch_one, verificar_mes_ia
from pwa_asistente.agente import director
from pwa_asistente.agente import feedback as _feedback
from pwa_asistente.agente import candidatas as _candidatas
from pwa_asistente.agente.especialistas import (
    ventas, inventario, pedidos, medicos, clientes, mixto
)
from pwa_asistente.agente.funciones import matcher as _matcher, catalogo as _catalogo

_pool = ThreadPoolExecutor(max_workers=4)

# Cache de nombres de productos para el prompt de Whisper (se renueva cada 24h)
_whisper_prompt_cache: dict = {"txt": "", "ts": 0.0}

def _whisper_product_prompt() -> str:
    """
    Devuelve un string con los top 150 nombres de productos del catálogo,
    para pasárselo a Whisper como contexto y mejorar la transcripción
    de nombres farmacéuticos (ej: "Oblitrop" → "Omnitrope").
    Cachea el resultado 24 horas para no consultar la BD en cada llamada.
    """
    import time
    if time.time() - _whisper_prompt_cache["ts"] < 86400 and _whisper_prompt_cache["txt"]:
        return _whisper_prompt_cache["txt"]
    try:
        rows = query_erp("""
            SELECT TOP 150 Descripcion
            FROM IM_Productos_Gral
            WHERE Descripcion IS NOT NULL
            ORDER BY Cve_Producto
        """)
        nombres = ", ".join(r["Descripcion"] for r in rows if r.get("Descripcion"))
        prompt = f"Consulta de ventas e inventario de farmacia. Productos: {nombres}."
        _whisper_prompt_cache["txt"] = prompt
        _whisper_prompt_cache["ts"]  = time.time()
        return prompt
    except Exception:
        return "Consulta de ventas e inventario de farmacia."


def _intentar_funcion_fija(msg: str) -> tuple:
    """
    Intenta resolver la pregunta con una función predefinida (sin LLM para SQL).

    Returns:
        tuple[str | None, float]: (respuesta, costo_usd) — respuesta es None si no hay coincidencia.
    """
    match = _matcher.detectar(msg)
    if not match:
        return None, 0.0
    func_id, params = match
    try:
        texto, costo = _catalogo.ejecutar(func_id, params)
        return texto, costo
    except Exception as e:
        print(f"[funciones] Error en {func_id}: {e}", flush=True)
        return None, 0.0

router = APIRouter(prefix="/api/chat")

_SALUDO = re.compile(
    r"^[\s¡!]*(hola|buenas?|buenos?\s+días?|buenas?\s+tardes?|buenas?\s+noches?|"
    r"hey|qué\s+tal|cómo\s+est[aá]s?|buen\s+día|hi|good\s+morning)[\s!¡.]*$",
    re.IGNORECASE,
)
_RESPUESTA_SALUDO = (
    "¡Hola! ¿En qué puedo ayudarte?\n\n"
    "Puedes preguntarme sobre ventas, inventario, pedidos, médicos o clientes."
)

_CAPACIDADES = re.compile(
    r"(qu[eé]\s+(haces?|puedes?|sabes?|eres?)|cómo\s+funciona[s]?|"
    r"para\s+qu[eé]\s+sirves?|qu[eé]\s+tipo\s+de|cu[aá]les\s+son\s+tus|"
    r"qu[eé]\s+informaci[oó]n|ayuda[s]?\s+con|qu[eé]\s+consultas)",
    re.IGNORECASE,
)
_RESPUESTA_CAPACIDADES = (
    "Soy tu asistente analítico. Puedo ayudarte con:\n\n"
    "- **Ventas** — importes, facturas, comparativos por sucursal o vendedor\n"
    "- **Inventario** — existencias, caducidades, productos sin stock\n"
    "- **Pedidos** — pedidos activos y su antigüedad\n"
    "- **Médicos** — directorio y duplicados\n"
    "- **Clientes** — historial de compras y clientes frecuentes\n\n"
    "Solo escribe tu pregunta y te respondo."
)

# Mapa área → función especialista
_ESPECIALISTAS = {
    "ventas":     ventas.responder,
    "inventario": inventario.responder,
    "pedidos":    pedidos.responder,
    "medicos":    medicos.responder,
    "clientes":   clientes.responder,
    "mixto":      mixto.responder,
}


# ── Modelos ───────────────────────────────────────────────────────────────────

class MensajeBody(BaseModel):
    mensaje:         str
    conversacion_id: Optional[int] = None


# ── Conversaciones ────────────────────────────────────────────────────────────

@router.get("/conversaciones")
def listar_conversaciones(usuario: dict = Depends(get_current_user)):
    """Lista todas las conversaciones del usuario autenticado."""
    filas = fetch_all(
        "SELECT id, titulo, ultimo_msg, creado_en "
        "FROM chat_conversaciones "
        "WHERE usuario_id = ? ORDER BY creado_en DESC",
        (usuario["id"],),
    )
    return JSONResponse({"conversaciones": filas})


@router.post("/conversaciones")
def crear_conversacion(usuario: dict = Depends(get_current_user)):
    """Crea una conversación vacía y devuelve su ID."""
    conv_id = execute(
        "INSERT INTO chat_conversaciones (usuario_id, titulo) VALUES (?, ?)",
        (usuario["id"], "Nueva conversación"),
    )
    return JSONResponse({"id": conv_id, "titulo": "Nueva conversación"})


@router.get("/conversaciones/{conv_id}")
def obtener_conversacion(conv_id: int, usuario: dict = Depends(get_current_user)):
    """Devuelve metadatos + mensajes de una conversación."""
    conv = fetch_one(
        "SELECT id, titulo, creado_en FROM chat_conversaciones "
        "WHERE id = ? AND usuario_id = ?",
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
    """Elimina una conversación y todos sus mensajes."""
    conv = fetch_one(
        "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ?",
        (conv_id, usuario["id"]),
    )
    if not conv:
        raise HTTPException(404, "Conversación no encontrada")

    execute("DELETE FROM chat_mensajes WHERE conversacion_id = ?", (conv_id,))
    execute("DELETE FROM chat_conversaciones WHERE id = ?", (conv_id,))
    return JSONResponse({"mensaje": "Conversación eliminada"})


# ── Helper: guardar mensajes y actualizar contador ────────────────────────────

# ── Procesamiento en background ───────────────────────────────────────────────

def _procesar_job(job_id: int, conv_id: int, msg: str, historial: list, usuario_id: int) -> None:
    """
    Ejecuta la consulta del agente en un thread de background y guarda el resultado.
    Se llama desde el pool de threads — no bloquea el request HTTP.
    """
    costo_usd = 0.0
    try:
        respuesta, costo_usd = _intentar_funcion_fija(msg)
        if respuesta:
            area   = "funcion_fija"
            estado = "done"
        else:
            area, costo_director = director.clasificar(msg, historial)
            fn                   = _ESPECIALISTAS.get(area, mixto.responder)
            resultado            = fn(msg, historial)
            respuesta            = resultado.texto
            costo_usd            = costo_director + (
                resultado.tokens_prompt     * IA_PRECIO_INPUT
                + resultado.tokens_completion * IA_PRECIO_OUTPUT
            )
            estado = "done"
    except Exception:
        respuesta = "Ups, parece que no pudimos procesar esta solicitud. Comunícate con tu proveedor."
        area      = "error"
        estado    = "error"

    execute(
        "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
        (conv_id, "assistant", respuesta),
    )

    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
        "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
        (IA_RATIO_PWA, IA_RATIO_PWA, costo_usd, usuario_id),
    )

    execute(
        "UPDATE chat_jobs SET estado = ?, respuesta = ?, area = ?, "
        "    terminado_en = datetime('now') WHERE id = ?",
        (estado, respuesta, area, job_id),
    )


@router.post("/mensaje/async")
def enviar_mensaje_async(body: MensajeBody, usuario: dict = Depends(get_current_user)):
    """
    Envía el mensaje al agente en background y devuelve un job_id inmediatamente.
    El frontend debe hacer polling a GET /api/chat/job/{job_id} cada 2 s.

    Para saludos y preguntas sobre capacidades responde de forma síncrona
    (estado='done' en la misma respuesta) sin crear un job real.
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
            "INSERT INTO chat_conversaciones (usuario_id, titulo) VALUES (?, ?)",
            (usuario["id"], msg[:80]),
        )
    else:
        conv = fetch_one(
            "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ?",
            (conv_id, usuario["id"]),
        )
        if not conv:
            raise HTTPException(404, "Conversación no encontrada")

    # Historial ANTES de guardar el mensaje del usuario
    historial = fetch_all(
        "SELECT rol, contenido FROM chat_mensajes WHERE conversacion_id = ? ORDER BY id",
        (conv_id,),
    )

    # Respuestas instantáneas — no necesitan thread
    if _SALUDO.match(msg):
        respuesta = _RESPUESTA_SALUDO
        area      = "saludo"
    elif _CAPACIDADES.search(msg) and len(msg) < 120:
        respuesta = _RESPUESTA_CAPACIDADES
        area      = "capacidades"
    else:
        respuesta = None
        area      = None

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

    # Guardar mensaje del usuario inmediatamente
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
    """
    Devuelve el estado de un job async.
    Si el job lleva más de 10 minutos en 'pending' (p. ej. reinicio del servidor),
    lo marca como 'error' automáticamente.
    """
    job = fetch_one(
        "SELECT id, estado, respuesta, area, conversacion_id, creado_en "
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
            job["estado"]   = "error"
            job["respuesta"] = msg_error

    return JSONResponse(job)


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackBody(BaseModel):
    job_id: int
    tipo:   Literal["positivo", "negativo"]


@router.post("/feedback")
def registrar_feedback(body: FeedbackBody, usuario: dict = Depends(get_current_user)):
    """Registra feedback 👍/👎 del usuario sobre una respuesta del agente."""

    job = fetch_one(
        "SELECT pregunta, respuesta FROM chat_jobs WHERE id = ? AND usuario_id = ?",
        (body.job_id, usuario["id"]),
    )
    if not job:
        raise HTTPException(404, "Job no encontrado")

    _feedback.registrar(
        job_id=body.job_id,
        tipo=body.tipo,
        pregunta=job["pregunta"] or "",
        respuesta=job["respuesta"] or "",
    )
    return JSONResponse({"ok": True})


@router.post("/audio")
async def enviar_audio_llamada(
    audio: UploadFile = File(...),
    conversacion_id: Optional[int] = Form(None),
    usuario: dict = Depends(get_current_user),
):
    """
    Modo llamada: recibe audio del usuario, transcribe con Whisper,
    procesa con el agente y devuelve respuesta en audio TTS (base64).

    Créditos: 1 por enviar + 3 por respuesta con audio = 4 por turno.
    """
    # Verificar límite de créditos
    verificar_mes_ia(usuario["id"], date.today().strftime("%Y-%m"))
    u = fetch_one(
        "SELECT COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, limite_ia "
        "FROM usuarios WHERE id = ?",
        (usuario["id"],),
    )
    if u and u["limite_ia"] > 0 and u["consultas_ia_r"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu límite de consultas de IA. "
            "Contacta a tu administrador para ampliar el límite.",
        )

    # Leer bytes del audio
    audio_bytes = await audio.read()

    # ── 1. STT: Whisper ───────────────────────────────────────────────────────
    client = OpenAI(api_key=OPENAI_API_KEY)

    import io
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = audio.filename or "audio.webm"

    # Obtener nombres de productos del catálogo para mejorar la transcripción
    # (Whisper usa el prompt para reconocer nombres farmacéuticos correctamente)
    _whisper_prompt = _whisper_product_prompt()

    transcripcion = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language="es",
        prompt=_whisper_prompt,
    )
    msg = transcripcion.text.strip()
    if not msg:
        raise HTTPException(400, "No se pudo transcribir el audio")

    # Cobrar 1 crédito por enviar (sin costo real)
    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
        "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
        (1.0, 1.0, 0.0, usuario["id"]),
    )

    # ── 2. Crear / verificar conversación ────────────────────────────────────
    conv_id = conversacion_id
    if not conv_id:
        conv_id = execute(
            "INSERT INTO chat_conversaciones (usuario_id, titulo) VALUES (?, ?)",
            (usuario["id"], msg[:80]),
        )
    else:
        conv = fetch_one(
            "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ?",
            (conv_id, usuario["id"]),
        )
        if not conv:
            raise HTTPException(404, "Conversación no encontrada")

    # Historial antes de guardar el mensaje actual
    historial = fetch_all(
        "SELECT rol, contenido FROM chat_mensajes WHERE conversacion_id = ? ORDER BY id",
        (conv_id,),
    )

    # ── 3. Agente ────────────────────────────────────────────────────────────
    costo_usd = 0.0
    if _SALUDO.match(msg):
        respuesta = _RESPUESTA_SALUDO
    elif _CAPACIDADES.search(msg) and len(msg) < 120:
        respuesta = _RESPUESTA_CAPACIDADES
    else:
        respuesta, costo_fija = _intentar_funcion_fija(msg)
        if respuesta:
            costo_usd = costo_fija
        else:
            area, costo_director = director.clasificar(msg, historial)
            fn = _ESPECIALISTAS.get(area, mixto.responder)
            resultado = fn(msg, historial)
            respuesta = resultado.texto
            costo_usd = costo_director + (
                resultado.tokens_prompt * IA_PRECIO_INPUT
                + resultado.tokens_completion * IA_PRECIO_OUTPUT
            )

    # Guardar mensajes en la conversación
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

    # ── 4. TTS: OpenAI TTS ───────────────────────────────────────────────────
    tts_response = client.audio.speech.create(
        model="tts-1-hd",
        voice="onyx",
        input=respuesta,
        response_format="mp3",
    )
    audio_b64 = base64.b64encode(tts_response.content).decode("utf-8")

    # Cobrar 3 créditos por respuesta con audio (+ costo real del agente y TTS)
    execute(
        "UPDATE usuarios SET "
        "consultas_ia   = CAST(ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 0) AS INTEGER), "
        "consultas_ia_r = ROUND(COALESCE(consultas_ia_r, consultas_ia) + ?, 2), "
        "costo_ia_usd   = ROUND(costo_ia_usd + ?, 6) WHERE id = ?",
        (3.0, 3.0, costo_usd, usuario["id"]),
    )

    return JSONResponse({
        "texto_usuario":   msg,
        "texto_ia":        respuesta,
        "audio_b64":       audio_b64,
        "conversacion_id": conv_id,
    })


@router.get("/candidatas")
def listar_candidatas(usuario: dict = Depends(get_current_user)):
    """
    Devuelve los patrones de preguntas más frecuentes que aún no tienen
    función predefinida. Solo accesible para administradores Nentria.
    """
    if usuario.get("username") not in ("admin_nentria",):
        raise HTTPException(403, "Acceso restringido")
    return JSONResponse({"candidatas": _candidatas.top(20)})
