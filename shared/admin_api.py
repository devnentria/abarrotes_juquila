# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : shared/admin_api.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
API de administración de usuarios compartida entre PWA y Studio.

Studio (centro de operaciones) incluye este router y agrega el endpoint
DELETE para eliminar usuarios. La PWA lo incluye sin ese endpoint.

Rutas registradas por build_api_router():
  GET    /api/admin/usuarios                        → Lista usuarios
  POST   /api/admin/usuarios                        → Crea usuario
  PATCH  /api/admin/usuarios/{id}                   → Actualiza usuario
  PATCH  /api/admin/usuarios/{id}/toggle            → Activa/desactiva
  PATCH  /api/admin/usuarios/{id}/reset-consultas   → Reinicia contador IA
  PATCH  /api/admin/usuarios/{id}/password          → Reset de contraseña
  GET    /api/admin/equipo                          → Lista equipo (supervisor)
  GET    /api/admin/usuarios/{id}/historial-ia      → Historial IA del usuario
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.auth import hash_password, require_rol
from shared.config import MODULOS_VALIDOS, PERMISOS_VALIDOS
from shared.database_local import execute, fetch_all, fetch_one


# ── Modelos de entrada ────────────────────────────────────────────────────────

class UsuarioNuevo(BaseModel):
    nombre:    str
    email:     str
    password:  str
    rol:       str
    modulos:   List[str]
    permisos:  List[str]
    limite_ia: Optional[int] = 700


class UsuarioActualizar(BaseModel):
    nombre:    Optional[str] = None
    rol:       Optional[str] = None
    modulos:   Optional[List[str]] = None
    permisos:  Optional[List[str]] = None
    password:  Optional[str] = None
    limite_ia: Optional[int] = None


class ResetPassword(BaseModel):
    nueva_password: str


# ── Factory del router ────────────────────────────────────────────────────────

def build_api_router() -> APIRouter:
    """
    Construye y retorna un APIRouter con todos los endpoints comunes de admin.
    Cada módulo (PWA, Studio) llama a esta función y agrega sus propios
    endpoints específicos sobre el router resultante.
    """
    router = APIRouter()

    @router.get("/api/admin/usuarios", dependencies=[Depends(require_rol("admin"))])
    def listar_usuarios():
        """Retorna la lista completa de usuarios registrados en la Suite."""
        filas = fetch_all(
            "SELECT id, nombre, email, rol, modulos, permisos, activo, creado_en, ultimo_acceso, "
            "       consultas_ia, COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, "
            "       limite_ia, costo_ia_usd, mes_consultas "
            "FROM usuarios ORDER BY id"
        )
        for u in filas:
            u["modulos"]  = json.loads(u["modulos"]  or "[]")
            u["permisos"] = json.loads(u["permisos"] or "[]")
        return JSONResponse({"usuarios": filas})

    @router.post("/api/admin/usuarios", dependencies=[Depends(require_rol("admin"))])
    def crear_usuario(datos: UsuarioNuevo):
        """Crea un nuevo usuario en la Suite."""
        if datos.rol not in ("admin", "supervisor", "usuario"):
            raise HTTPException(status_code=400, detail="Rol inválido")

        modulos_invalidos = set(datos.modulos) - MODULOS_VALIDOS
        if modulos_invalidos:
            raise HTTPException(status_code=400, detail=f"Módulos inválidos: {modulos_invalidos}")

        permisos_invalidos = set(datos.permisos) - PERMISOS_VALIDOS
        if permisos_invalidos:
            raise HTTPException(status_code=400, detail=f"Permisos inválidos: {permisos_invalidos}")

        existente = fetch_one("SELECT id FROM usuarios WHERE email = ?", (datos.email,))
        if existente:
            raise HTTPException(status_code=400, detail="El email ya está registrado")

        nuevo_id = execute(
            "INSERT INTO usuarios (nombre, email, password_hash, rol, modulos, permisos, limite_ia, debe_cambiar_password) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                datos.nombre,
                datos.email,
                hash_password(datos.password),
                datos.rol,
                json.dumps(datos.modulos),
                json.dumps(datos.permisos),
                datos.limite_ia if datos.limite_ia is not None else 700,
            ),
        )
        return JSONResponse({"id": nuevo_id, "mensaje": "Usuario creado exitosamente"}, status_code=201)

    @router.patch("/api/admin/usuarios/{usuario_id}")
    def actualizar_usuario(
        usuario_id: int,
        datos: UsuarioActualizar,
        ejecutor: dict = Depends(require_rol("admin", "supervisor")),
    ):
        """Actualiza los campos enviados de un usuario existente.
        Admin: puede editar cualquier campo de cualquier usuario.
        Supervisor: puede editar nombre, email, módulos, permisos y límite IA
                    de usuarios con rol 'usuario' únicamente (no admins ni supervisores).
        """
        usuario = fetch_one("SELECT id, rol FROM usuarios WHERE id = ?", (usuario_id,))
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        es_supervisor = ejecutor["rol"] == "supervisor"
        if es_supervisor and usuario["rol"] != "usuario":
            raise HTTPException(
                status_code=403,
                detail="El supervisor solo puede editar usuarios finales",
            )

        campos, valores = [], []

        if datos.nombre is not None:
            campos.append("nombre = ?");   valores.append(datos.nombre)
        if datos.rol is not None:
            if es_supervisor:
                raise HTTPException(status_code=403, detail="El supervisor no puede cambiar roles")
            if datos.rol not in ("admin", "supervisor", "usuario"):
                raise HTTPException(status_code=400, detail="Rol inválido")
            campos.append("rol = ?");      valores.append(datos.rol)
        if datos.modulos is not None:
            campos.append("modulos = ?");  valores.append(json.dumps(datos.modulos))
        if datos.permisos is not None:
            campos.append("permisos = ?"); valores.append(json.dumps(datos.permisos))
        if datos.password is not None:
            campos.append("password_hash = ?"); valores.append(hash_password(datos.password))
        if datos.limite_ia is not None:
            if es_supervisor:
                raise HTTPException(status_code=403, detail="El supervisor no puede cambiar el límite de IA")
            if datos.limite_ia < 0:
                raise HTTPException(status_code=400, detail="El límite de IA no puede ser negativo")
            campos.append("limite_ia = ?"); valores.append(datos.limite_ia)

        if not campos:
            return JSONResponse({"mensaje": "Sin cambios"})

        valores.append(usuario_id)
        execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", tuple(valores))
        return JSONResponse({"mensaje": "Usuario actualizado"})

    @router.patch("/api/admin/usuarios/{usuario_id}/toggle", dependencies=[Depends(require_rol("admin"))])
    def toggle_usuario(usuario_id: int):
        """Alterna el estado activo/inactivo de un usuario."""
        usuario = fetch_one("SELECT id, rol, activo FROM usuarios WHERE id = ?", (usuario_id,))
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        if usuario["rol"] == "admin" and usuario["activo"]:
            admins_activos = fetch_one(
                "SELECT COUNT(*) as total FROM usuarios WHERE rol = 'admin' AND activo = 1"
            )
            if admins_activos["total"] <= 1:
                raise HTTPException(status_code=400, detail="No puedes desactivar al único administrador")

        nuevo_estado = 0 if usuario["activo"] else 1
        execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, usuario_id))

        estado_texto = "activado" if nuevo_estado else "desactivado"
        return JSONResponse({"activo": bool(nuevo_estado), "mensaje": f"Usuario {estado_texto}"})

    @router.get("/api/admin/equipo")
    def listar_equipo(ejecutor: dict = Depends(require_rol("admin", "supervisor"))):
        """Retorna la lista de usuarios con rol 'usuario' (equipo del supervisor)."""
        filas = fetch_all(
            "SELECT id, nombre, email, activo, consultas_ia, limite_ia "
            "FROM usuarios WHERE rol = 'usuario' ORDER BY nombre"
        )
        return JSONResponse({"usuarios": filas})

    @router.patch("/api/admin/usuarios/{usuario_id}/reset-consultas",
                  dependencies=[Depends(require_rol("admin", "supervisor"))])
    def resetear_consultas(usuario_id: int):
        """Reinicia el contador de consultas IA y costo acumulado a cero."""
        usuario = fetch_one("SELECT id FROM usuarios WHERE id = ?", (usuario_id,))
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        execute(
            "UPDATE usuarios SET consultas_ia = 0, consultas_ia_r = 0.0, costo_ia_usd = 0, mes_consultas = '' WHERE id = ?",
            (usuario_id,),
        )
        return JSONResponse({"mensaje": "Contador de consultas reiniciado"})

    @router.get("/api/admin/usuarios/{usuario_id}/historial-ia",
                dependencies=[Depends(require_rol("admin", "supervisor"))])
    def historial_ia(usuario_id: int):
        """Retorna el historial mensual de consumo IA de un usuario."""
        usuario = fetch_one("SELECT id FROM usuarios WHERE id = ?", (usuario_id,))
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        filas = fetch_all(
            "SELECT mes, consultas, costo_usd "
            "FROM consumo_ia_mensual WHERE usuario_id = ? ORDER BY mes DESC",
            (usuario_id,),
        )
        return JSONResponse({"historial": filas})

    @router.patch("/api/admin/usuarios/{usuario_id}/password")
    def resetear_password(
        usuario_id: int,
        datos: ResetPassword,
        ejecutor: dict = Depends(require_rol("admin", "supervisor")),
    ):
        """
        Restablece la contraseña de un usuario.
        Admin puede resetear cualquier usuario.
        Supervisor solo puede resetear usuarios con rol 'usuario'.
        """
        if not datos.nueva_password or len(datos.nueva_password) < 6:
            raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

        objetivo = fetch_one("SELECT id, rol FROM usuarios WHERE id = ?", (usuario_id,))
        if not objetivo:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        if ejecutor["rol"] == "supervisor" and objetivo["rol"] != "usuario":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El supervisor solo puede restablecer contraseñas de usuarios finales",
            )

        execute(
            "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ?",
            (hash_password(datos.nueva_password), usuario_id),
        )
        return JSONResponse({"mensaje": "Contraseña restablecida exitosamente"})

    return router
