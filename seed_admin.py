# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : raíz
# Archivo  : seed_admin.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Script de inicialización — crea el usuario administrador de la Suite.

Uso (correr una sola vez al instalar):
    python seed_admin.py

Lee las credenciales de .env si están definidas:
    ADMIN_NOMBRE   = Geovani Daniel Nolasco
    ADMIN_EMAIL    = admin@nentria.com
    ADMIN_PASSWORD = tu_contraseña_segura

Si no están en .env, las solicita de forma interactiva.
Es seguro correrlo varias veces — no crea duplicados.
"""
import getpass
import sys
from pathlib import Path

# Agregar raíz del proyecto al path para importar shared/
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import hash_password
from shared.config import ADMIN_EMAIL, ADMIN_NOMBRE, ADMIN_PASSWORD
from shared.database_local import execute, fetch_one, init_db


def solicitar_credenciales() -> tuple[str, str, str]:
    """
    Solicita interactivamente nombre, email y contraseña si no están en .env.

    Returns:
        tuple[str, str, str]: (nombre, email, password)
    """
    nombre   = ADMIN_NOMBRE   or input("Nombre del administrador: ").strip()
    email    = ADMIN_EMAIL    or input("Email del administrador: ").strip()
    password = ADMIN_PASSWORD or getpass.getpass("Contraseña: ")
    return nombre, email, password


def main() -> None:
    """Inicializa la BD y crea el usuario admin si no existe."""
    print("\n── Suite Analítica — Inicialización de administrador ──\n")

    init_db()

    nombre, email, password = solicitar_credenciales()

    if not nombre or not email or not password:
        print("ERROR: Nombre, email y contraseña son requeridos.")
        sys.exit(1)

    existente = fetch_one("SELECT id FROM usuarios WHERE email = ?", (email,))
    if existente:
        print(f"El usuario '{email}' ya existe. No se realizaron cambios.")
        sys.exit(0)

    todos_los_permisos = '["ventas", "inventario", "medicos", "clientes", "finanzas"]'
    execute(
        """
        INSERT INTO usuarios (nombre, email, password_hash, rol, modulos, permisos)
        VALUES (?, ?, ?, 'admin', '["pwa", "studio"]', ?)
        """,
        (nombre, email, hash_password(password), todos_los_permisos),
    )

    print(f"\nAdministrador creado exitosamente.")
    print(f"  Nombre : {nombre}")
    print(f"  Email  : {email}")
    print(f"  Rol    : admin")
    print(f"  Módulos: pwa, studio\n")


if __name__ == "__main__":
    main()
