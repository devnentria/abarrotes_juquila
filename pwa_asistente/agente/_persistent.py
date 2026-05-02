# ============================================================
# Proyecto : Suite Analítica — Nentria Intelligent Solutions
# Módulo   : pwa_asistente / agente
# Archivo  : agente/_persistent.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 1.0.0
# ============================================================
"""
Base compartida para módulos con persistencia JSON en disco.

Elimina el patrón _cargar/_guardar duplicado en sql_blacklist,
nombres_cache, feedback y candidatas.
"""
import json
from pathlib import Path


class PersistentStore:
    """
    Almacén JSON persistente en el directorio del módulo que lo instancia.

    Uso:
        _store = PersistentStore("mi_archivo.json", {"clave": []})
        _store.datos["clave"].append(x)
        _store.guardar()
    """

    def __init__(self, filename: str, default: object, base_dir: Path) -> None:
        self._archivo = base_dir / filename
        self.datos    = default
        self._cargar()

    def _cargar(self) -> None:
        if self._archivo.exists():
            try:
                self.datos = json.loads(self._archivo.read_text(encoding="utf-8"))
            except Exception:
                pass

    def guardar(self) -> None:
        self._archivo.write_text(
            json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
