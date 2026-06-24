# Script temporal — buscar productos "Envío Especial" en ERP
# Correr en el servidor: python buscar_envio.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from shared.database import query

rows = query("""
    SELECT TOP 30
        p.Cve_Producto,
        p.Descripcion,
        p.Status
    FROM IM_Productos_Gral p
    WHERE p.Descripcion LIKE '%ENVIO%'
       OR p.Descripcion LIKE '%ENVÍO%'
       OR p.Descripcion LIKE '%FLETE%'
       OR p.Descripcion LIKE '%ESPECIAL%'
    ORDER BY p.Descripcion
""")

if not rows:
    print("No se encontraron productos con esas palabras.")
else:
    print(f"{'Cve_Producto':<15} {'Status':<8} {'Descripcion'}")
    print("-" * 70)
    for r in rows:
        print(f"{r['Cve_Producto']:<15} {r['Status']:<8} {r['Descripcion']}")
