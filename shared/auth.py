# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : auth.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Utilidades de autenticación: hashing de contraseñas y JWT.

Centraliza toda la lógica de seguridad para que los routers
solo llamen funciones claras sin implementar crypto directamente.
"""
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from shared.config import JWT_ALGORITHM, JWT_EXPIRY_HOURS, JWT_SECRET
from shared.database_local import fetch_one, execute, modulos_de_usuario

# ── OAuth2 scheme ─────────────────────────────────────────────────────────────
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── Contraseñas ───────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """
    Genera el hash bcrypt de una contraseña en texto plano.

    Args:
        password (str): Contraseña en texto plano.

    Returns:
        str: Hash bcrypt listo para guardar en BD.
    """
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verifica si una contraseña en texto plano coincide con su hash bcrypt.

    Args:
        plain  (str): Contraseña ingresada por el usuario.
        hashed (str): Hash guardado en BD.

    Returns:
        bool: True si coinciden, False si no.
    """
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(user_id: int, email: str, rol: str) -> str:
    """
    Genera un JWT firmado con los datos del usuario.

    Args:
        user_id (int): ID del usuario en la BD local.
        email   (str): Email del usuario.
        rol     (str): Rol del usuario (admin, supervisor, usuario).

    Returns:
        str: Token JWT codificado.
    """
    expira = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    payload = {
        "sub": str(user_id),
        "email": email,
        "rol": rol,
        "exp": expira,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decodifica y valida un JWT. Lanza HTTPException si es inválido o expirado.

    Args:
        token (str): Token JWT recibido en el header.

    Returns:
        dict: Payload del token (sub, email, rol, exp).

    Raises:
        HTTPException 401: Si el token es inválido o ha expirado.
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Dependencia FastAPI ───────────────────────────────────────────────────────

def get_current_user(token: str = Depends(_oauth2_scheme)) -> dict:
    """
    Dependencia de FastAPI que extrae y valida el usuario del token JWT.
    Inyectar en cualquier endpoint que requiera autenticación.

    Args:
        token (str): Token extraído automáticamente del header Authorization.

    Returns:
        dict: Datos del usuario activo (id, nombre, email, rol, modulos).

    Raises:
        HTTPException 401: Si el token es inválido o el usuario está inactivo.
    """
    payload = decode_access_token(token)
    user_id = payload.get("sub")

    usuario = fetch_one(
        "SELECT id, nombre, email, rol, modulos, activo FROM usuarios WHERE id = ?",
        (user_id,),
    )

    if not usuario or not usuario["activo"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
        )

    execute(
        "UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )

    usuario["modulos"] = modulos_de_usuario(usuario["modulos"])
    return usuario


def require_rol(*roles: str):
    """
    Fábrica de dependencias para restringir endpoints por rol.
    Uso: Depends(require_rol("admin")) o Depends(require_rol("admin", "supervisor"))

    Args:
        *roles (str): Roles permitidos para acceder al endpoint.

    Returns:
        Callable: Dependencia de FastAPI lista para inyectar.

    Raises:
        HTTPException 403: Si el usuario no tiene el rol requerido.
    """
    def _check(usuario: dict = Depends(get_current_user)) -> dict:
        if usuario["rol"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para esta acción",
            )
        return usuario
    return _check
