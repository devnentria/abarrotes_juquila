# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/_persistent.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.1.0
# ============================================================
"""
Base compartida para módulos con persistencia JSON en disco.

Elimina el patrón _cargar/_guardar duplicado en sql_blacklist,
nombres_cache, feedback y candidatas.

Thread-safe: usa un Lock por instancia para proteger lecturas y
escrituras concurrentes desde el ThreadPoolExecutor del servidor.
"""
import json
import threading
from pathlib import Path


class PersistentStore:
    """
    Almacén JSON persistente, thread-safe.

    Uso:
        _store = PersistentStore("mi_archivo.json", {"clave": []})
        with _store.lock:
            _store.datos["clave"].append(x)
            _store.guardar()
    """

    def __init__(self, filename: str, default: object, base_dir: Path) -> None:
        self._archivo = base_dir / filename
        self.datos    = default
        self.lock     = threading.Lock()
        self._cargar()

    def _cargar(self) -> None:
        if self._archivo.exists():
            try:
                self.datos = json.loads(self._archivo.read_text(encoding="utf-8"))
            except Exception:
                pass

    def guardar(self) -> None:
        """Debe llamarse dentro de `with _store.lock`."""
        self._archivo.write_text(
            json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
