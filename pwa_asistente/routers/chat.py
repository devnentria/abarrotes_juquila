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
  POST   /api/chat/mensaje                  → Envía mensaje y obtiene respuesta del agente
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.auth import get_current_user
from shared.config import IA_COSTO_POR_CONSULTA, IA_RATIO_PWA
from shared.database_local import execute, fetch_all, fetch_one
from pwa_asistente.agente import director
from pwa_asistente.agente.especialistas import (
    ventas, inventario, pedidos, medicos, clientes, mixto
)

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


# ── Mensaje ───────────────────────────────────────────────────────────────────

@router.post("/mensaje")
def enviar_mensaje(body: MensajeBody, usuario: dict = Depends(get_current_user)):
    """
    Procesa un mensaje del usuario:
      1. Verifica límite de IA
      2. Crea o reutiliza conversación
      3. Director clasifica el área
      4. Especialista responde consultando el ERP
      5. Guarda ambos mensajes en la BD
      6. Actualiza contador de consultas_ia según ratio PWA
    """
    if not body.mensaje.strip():
        raise HTTPException(400, "El mensaje no puede estar vacío")

    # 1. Verificar límite de consultas IA
    u = fetch_one(
        "SELECT consultas_ia, limite_ia FROM usuarios WHERE id = ?",
        (usuario["id"],),
    )
    if u and u["limite_ia"] > 0 and u["consultas_ia"] >= u["limite_ia"]:
        raise HTTPException(
            429,
            "Has alcanzado tu límite de consultas de IA. "
            "Contacta a tu administrador para ampliar el límite.",
        )

    # 2. Obtener o crear conversación
    conv_id = body.conversacion_id
    if not conv_id:
        titulo  = body.mensaje.strip()[:80]
        conv_id = execute(
            "INSERT INTO chat_conversaciones (usuario_id, titulo) VALUES (?, ?)",
            (usuario["id"], titulo),
        )
    else:
        # Verificar que la conversación pertenece al usuario
        conv = fetch_one(
            "SELECT id FROM chat_conversaciones WHERE id = ? AND usuario_id = ?",
            (conv_id, usuario["id"]),
        )
        if not conv:
            raise HTTPException(404, "Conversación no encontrada")

    # 3. Obtener historial de esta conversación
    historial = fetch_all(
        "SELECT rol, contenido FROM chat_mensajes "
        "WHERE conversacion_id = ? ORDER BY id",
        (conv_id,),
    )

    # 4. Saludo o pregunta sobre capacidades — respuesta instantánea sin OpenAI
    msg = body.mensaje.strip()
    if _SALUDO.match(msg):
        area = "saludo"
        respuesta = _RESPUESTA_SALUDO
    elif _CAPACIDADES.search(msg) and len(msg) < 120:
        area = "capacidades"
        respuesta = _RESPUESTA_CAPACIDADES
    else:
        # Director clasifica y especialista responde
        area = director.clasificar(body.mensaje, historial)
        fn = _ESPECIALISTAS.get(area, mixto.responder)
        try:
            respuesta = fn(body.mensaje, historial)
        except Exception as e:
            respuesta = (
                "Ocurrió un error al procesar tu consulta. "
                "Intenta de nuevo o reformula la pregunta."
            )

    # 5. Guardar mensajes en la BD
    execute(
        "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
        (conv_id, "user", body.mensaje),
    )
    execute(
        "INSERT INTO chat_mensajes (conversacion_id, rol, contenido) VALUES (?, ?, ?)",
        (conv_id, "assistant", respuesta),
    )

    # Actualizar último mensaje visible en la lista
    execute(
        "UPDATE chat_conversaciones SET ultimo_msg = ? WHERE id = ?",
        (body.mensaje.strip()[:80], conv_id),
    )

    # 6. Contar mensajes globales del usuario (todas las conversaciones) y actualizar contadores
    total_global = fetch_one(
        "SELECT COUNT(*) AS total FROM chat_mensajes cm "
        "JOIN chat_conversaciones cc ON cm.conversacion_id = cc.id "
        "WHERE cc.usuario_id = ? AND cm.rol = 'user'",
        (usuario["id"],),
    )["total"]
    if total_global % IA_RATIO_PWA == 0:
        execute(
            "UPDATE usuarios "
            "SET consultas_ia = consultas_ia + 1, "
            "    costo_ia_usd = ROUND(costo_ia_usd + ?, 4) "
            "WHERE id = ?",
            (IA_COSTO_POR_CONSULTA, usuario["id"]),
        )

    return JSONResponse({
        "respuesta":      respuesta,
        "conversacion_id": conv_id,
        "area":           area,
    })
