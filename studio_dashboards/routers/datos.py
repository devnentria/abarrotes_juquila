# ============================================================
# Proyecto : Abarrotes Suite — Nentria Intelligent Solutions
# Módulo   : studio_dashboards
# Archivo  : routers/datos.py
# Autor    : Geovani Daniel Nolasco
# Versión  : 2.5.0
# ============================================================
"""
Router de datos del ERP para el Studio Dashboards.

Orquestador que incluye los sub-routers divididos por dominio:
  - datos_ventas:      /ventas, /pedidos, /kpis, /ventas-hoy, /plantilla/{tipo}
  - datos_zonas:       /mapa, /zonas
  - datos_productos:   /productos, /productos/prediccion
  - datos_inventario:  /inventario, /inventario/consulta, /inventario/historico
  - datos_ia:          /generar (POST)
  - datos_dashboards:  /dashboards (GET/POST/DELETE/PATCH), /uso-selector
  - datos_vendedores:  /vendedores, /medicos

Todos los endpoints quedan bajo el prefijo /api/datos con autenticación requerida.
"""
from fastapi import APIRouter, Depends

from shared.auth import get_current_user

from .datos_ventas import router as ventas_router
from .datos_zonas import router as zonas_router
from .datos_productos import router as productos_router
from .datos_inventario import router as inventario_router
from .datos_ia import router as ia_router
from .datos_dashboards import router as dashboards_router
from .datos_vendedores import router as vendedores_router

router = APIRouter(prefix="/api/datos", dependencies=[Depends(get_current_user)])

router.include_router(ventas_router)
router.include_router(zonas_router)
router.include_router(productos_router)
router.include_router(inventario_router)
router.include_router(ia_router)
router.include_router(dashboards_router)
router.include_router(vendedores_router)
