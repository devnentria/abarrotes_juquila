# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente
# Archivo  : routers/auth.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Endpoints de autenticación de la Suite.

Rutas:
  POST  /auth/login    → Valida credenciales y retorna JWT
  GET   /auth/me       → Retorna datos del usuario autenticado
  PATCH /auth/password → Cambia la contraseña del usuario autenticado
  PATCH /auth/perfil   → Actualiza nombre y/o foto de perfil
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.auth import create_access_token, get_current_user, hash_password, verify_password
from shared.database_local import execute, fetch_one

router = APIRouter(prefix="/auth", tags=["auth"])


class CambiarPassword(BaseModel):
    password_actual: str
    nueva_password:  str


class ActualizarPerfil(BaseModel):
    nombre:      Optional[str] = None
    foto_perfil: Optional[str] = None  # Data URL base64 (redimensionada en frontend)


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    """
    Autentica un usuario con email y contraseña.
    Retorna un JWT Bearer token si las credenciales son correctas.

    Args:
        form.username (str): Email del usuario.
        form.password (str): Contraseña en texto plano.

    Returns:
        JSONResponse: { access_token, token_type, rol, nombre }

    Raises:
        HTTPException 401: Si el email no existe, la contraseña es incorrecta
                           o el usuario está inactivo.
    """
    usuario = fetch_one(
        "SELECT id, nombre, email, password_hash, rol, activo FROM usuarios WHERE email = ?",
        (form.username,),
    )

    if not usuario or not usuario["activo"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
        )

    if not verify_password(form.password, usuario["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
        )

    # Registrar último acceso
    execute(
        "UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), usuario["id"]),
    )

    token = create_access_token(
        user_id=usuario["id"],
        email=usuario["email"],
        rol=usuario["rol"],
    )

    return JSONResponse({
        "access_token": token,
        "token_type":   "bearer",
        "rol":          usuario["rol"],
        "nombre":       usuario["nombre"],
    })


@router.get("/me")
def me(usuario: dict = Depends(get_current_user)):
    """
    Retorna los datos del usuario actualmente autenticado.

    Args:
        usuario (dict): Inyectado por la dependencia get_current_user.

    Returns:
        JSONResponse: { id, nombre, email, rol, modulos }
    """
    perfil = fetch_one(
        "SELECT foto_perfil, debe_cambiar_password, "
        "COALESCE(consultas_ia_r, consultas_ia) AS consultas_ia_r, limite_ia "
        "FROM usuarios WHERE id = ?",
        (usuario["id"],),
    )
    return JSONResponse({
        "id":                    usuario["id"],
        "nombre":                usuario["nombre"],
        "email":                 usuario["email"],
        "rol":                   usuario["rol"],
        "modulos":               usuario["modulos"],
        "foto_perfil":           perfil["foto_perfil"] if perfil else None,
        "debe_cambiar_password": bool(perfil["debe_cambiar_password"]) if perfil else False,
        "consultas_ia":          int(perfil["consultas_ia_r"] or 0) if perfil else 0,
        "limite_ia":             int(perfil["limite_ia"] or 0) if perfil else 700,
    })


@router.patch("/password")
def cambiar_password(datos: CambiarPassword, usuario: dict = Depends(get_current_user)):
    """
    Permite al usuario autenticado cambiar su propia contraseña.

    Args:
        datos.password_actual (str): Contraseña vigente para verificar identidad.
        datos.nueva_password  (str): Nueva contraseña (mínimo 6 caracteres).

    Returns:
        JSONResponse: { mensaje }

    Raises:
        HTTPException 400: Si la contraseña actual es incorrecta o la nueva es inválida.
    """
    if len(datos.nueva_password) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres")

    registro = fetch_one("SELECT password_hash FROM usuarios WHERE id = ?", (usuario["id"],))
    if not verify_password(datos.password_actual, registro["password_hash"]):
        raise HTTPException(status_code=400, detail="La contraseña actual es incorrecta")

    execute(
        "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?",
        (hash_password(datos.nueva_password), usuario["id"]),
    )
    return JSONResponse({"mensaje": "Contraseña actualizada exitosamente"})


@router.patch("/perfil")
def actualizar_perfil(datos: ActualizarPerfil, usuario: dict = Depends(get_current_user)):
    """
    Actualiza el nombre visible y/o la foto de perfil del usuario autenticado.

    Args:
        datos.nombre      (str, opcional): Nuevo nombre a mostrar.
        datos.foto_perfil (str, opcional): Data URL base64 de la imagen (redimensionada en frontend).

    Returns:
        JSONResponse: { mensaje, nombre, foto_perfil }
    """
    campos, valores = [], []

    if datos.nombre is not None:
        if not datos.nombre.strip():
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
        campos.append("nombre = ?");      valores.append(datos.nombre.strip())
    if datos.foto_perfil is not None:
        campos.append("foto_perfil = ?"); valores.append(datos.foto_perfil or None)

    if not campos:
        return JSONResponse({"mensaje": "Sin cambios"})

    valores.append(usuario["id"])
    execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", tuple(valores))

    actualizado = fetch_one("SELECT nombre, foto_perfil FROM usuarios WHERE id = ?", (usuario["id"],))
    return JSONResponse({
        "mensaje":     "Perfil actualizado",
        "nombre":      actualizado["nombre"],
        "foto_perfil": actualizado["foto_perfil"],
    })
