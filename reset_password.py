# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : raíz
# Archivo  : reset_password.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Script de recuperación de contraseña — acceso directo a la BD local.

Úsalo cuando un usuario (incluyendo el administrador) no puede recuperar
su contraseña desde la interfaz web. No requiere que los servidores estén
corriendo — opera directamente sobre data/suite.db.

Uso:
    python reset_password.py
    python reset_password.py --email admin@ejemplo.com

El script:
  1. Lista los usuarios disponibles (si no se pasa --email)
  2. Solicita el nuevo password de forma segura (sin eco en pantalla)
  3. Actualiza el hash en la BD y activa la bandera debe_cambiar_password = 0
     (la contraseña reseteada por este script se considera definitiva, no temporal)
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import hash_password
from shared.database_local import execute, fetch_all, fetch_one, init_db


def listar_usuarios() -> list[dict]:
    """Retorna todos los usuarios activos con id, nombre, email y rol."""
    return fetch_all(
        "SELECT id, nombre, email, rol, activo FROM usuarios ORDER BY rol, nombre"
    )


def elegir_usuario(email: str | None) -> dict:
    """
    Devuelve el registro del usuario a resetear.
    Si email es None muestra la lista interactiva.
    """
    if email:
        usuario = fetch_one("SELECT id, nombre, email, rol FROM usuarios WHERE email = ?", (email,))
        if not usuario:
            print(f"ERROR: No se encontró ningún usuario con email '{email}'.")
            sys.exit(1)
        return usuario

    usuarios = listar_usuarios()
    if not usuarios:
        print("ERROR: No hay usuarios registrados en la base de datos.")
        sys.exit(1)

    print("\n  # │ Nombre                        │ Email                          │ Rol")
    print("────┼───────────────────────────────┼────────────────────────────────┼──────────")
    for i, u in enumerate(usuarios, 1):
        estado = "" if u["activo"] else " [inactivo]"
        print(f"  {i:<2}│ {u['nombre']:<30}│ {u['email']:<31}│ {u['rol']}{estado}")
    print()

    while True:
        opcion = input("Número de usuario a resetear (o 'q' para salir): ").strip()
        if opcion.lower() == 'q':
            print("Cancelado.")
            sys.exit(0)
        if opcion.isdigit() and 1 <= int(opcion) <= len(usuarios):
            return usuarios[int(opcion) - 1]
        print("  Opción inválida. Intenta de nuevo.")


def solicitar_nueva_password() -> str:
    """Solicita y confirma la nueva contraseña de forma segura."""
    while True:
        pwd1 = getpass.getpass("  Nueva contraseña (mín. 6 caracteres): ")
        if len(pwd1) < 6:
            print("  ERROR: La contraseña debe tener al menos 6 caracteres.\n")
            continue
        pwd2 = getpass.getpass("  Confirmar nueva contraseña            : ")
        if pwd1 != pwd2:
            print("  ERROR: Las contraseñas no coinciden.\n")
            continue
        return pwd1


def main() -> None:
    parser = argparse.ArgumentParser(description="Resetea la contraseña de un usuario de la Suite.")
    parser.add_argument("--email", help="Email del usuario a resetear (opcional)")
    args = parser.parse_args()

    print("\n── Suite Analítica — Reset de contraseña ──\n")

    init_db()

    usuario = elegir_usuario(args.email)
    print(f"\n  Usuario : {usuario['nombre']}  <{usuario['email']}>  [{usuario['rol']}]")
    print()

    nueva_password = solicitar_nueva_password()

    execute(
        "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?",
        (hash_password(nueva_password), usuario["id"]),
    )

    print(f"\n  Contraseña actualizada exitosamente para '{usuario['nombre']}'.")
    print("  El usuario puede ingresar de inmediato con la nueva contraseña.\n")


if __name__ == "__main__":
    main()
