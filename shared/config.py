# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : shared
# Archivo  : shared/config.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Configuración central de la Suite Analítica Nentria.

REGLA: Este es el ÚNICO archivo donde viven los valores de configuración.
       Para cambiar la BD, el modelo de IA o los puertos — solo editar .env
       o las variables de entorno. Nada más en el proyecto cambia.
"""
import os
from pathlib import Path

# ── Cargar .env desde la raíz del proyecto ────────────────────────────────────
# Se busca el .env un nivel arriba de este archivo (raíz del repo)
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())


# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL:      str = os.getenv("OPENAI_MODEL",       "gpt-4o-mini")
# Modelo para resúmenes flash (IA en cards) — no requiere razonamiento, prioriza velocidad
IA_FLASH_MODEL:    str = os.getenv("IA_FLASH_MODEL",    "gpt-4o-mini")


# ── Base de datos (SQL Server) ────────────────────────────────────────────────
# Para cambiar de entorno (Docker local → servidor cliente) solo editar .env
DB_HOST:     str = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT:     int = int(os.getenv("DB_PORT", "1433"))
DB_NAME:     str = os.getenv("DB_NAME", "CreaSoftTest2")
DB_USER:     str = os.getenv("DB_USER", "sa")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")


# ── Puertos de los módulos ────────────────────────────────────────────────────
PWA_PORT:    int = int(os.getenv("PWA_PORT",    "8001"))
STUDIO_PORT: int = int(os.getenv("STUDIO_PORT", "8002"))

# ── Fecha de prueba ───────────────────────────────────────────────────────────
# Para probar con datos históricos, pon una fecha en .env: TEST_DATE=2026-03-28
# En producción dejar vacío — usará la fecha real del servidor.
TEST_DATE: str = os.getenv("TEST_DATE", "")  # "" = fecha real | "YYYY-MM-DD" = fecha fija


# ── Consumo de IA ────────────────────────────────────────────────────────────
# Cada N mensajes del usuario equivale a 1 consulta descontada del límite.
# PWA (chat móvil/desktop): menos complejo → cada 3 mensajes = 1 consulta
# Studio (dashboards, gráficas): más complejo → cada 2 mensajes = 1 consulta
IA_RATIO_PWA:    int = int(os.getenv("IA_RATIO_PWA",    "3"))
IA_RATIO_STUDIO: int = int(os.getenv("IA_RATIO_STUDIO", "2"))

# Costo estimado por consulta (1 incremento del contador).
# Ajustar según el modelo activo en OPENAI_MODEL:
#   gpt-4o-mini → ~$0.005 USD por consulta
#   gpt-4o      → ~$0.025 USD por consulta
#   gpt-o3-mini → ~$0.06  USD por consulta  (studio)
IA_COSTO_POR_CONSULTA: float = float(os.getenv("IA_COSTO_POR_CONSULTA", "0.005"))

# ── JWT ───────────────────────────────────────────────────────────────────────
# Generar un secret seguro: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET:       str = os.getenv("JWT_SECRET", "cambiar-en-produccion")
JWT_ALGORITHM:    str = "HS256"
JWT_EXPIRY_HOURS: int = int(os.getenv("JWT_EXPIRY_HOURS", "720"))  # 30 días


# ── Admin inicial (para seed_admin.py) ───────────────────────────────────────
# Opcional: si están en .env, seed_admin.py no solicita input interactivo.
ADMIN_NOMBRE:   str = os.getenv("ADMIN_NOMBRE",   "")
ADMIN_EMAIL:    str = os.getenv("ADMIN_EMAIL",    "")
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
