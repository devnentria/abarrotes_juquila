#!/usr/bin/env python3
"""
Script de diagnóstico del mapa — ejecutar en el servidor:
  python3 debug_mapa.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from shared.database_local import fetch_all, fetch_one, get_connection
from shared.database import query

print("=== 1. Registros en cp_coords ===")
total = fetch_one("SELECT COUNT(*) AS n FROM cp_coords")
print(f"  Coordenadas guardadas: {total['n'] if total else 'TABLA NO EXISTE'}")

print("\n=== 2. Top CPs del mes actual en ERP ===")
from datetime import date
hoy = date.today()
try:
    rows = query(f"""
        SELECT TOP 10 con.CP,
               COUNT(DISTINCT p.Cve_Folio) AS pedidos,
               CAST(SUM(ISNULL(d.Cantidad_Ordenada*d.Precio,0)) AS bigint) AS ventas
        FROM FT_Pedidos_C p
        INNER JOIN FT_Pedidos_Dia d ON d.Cve_Folio=p.Cve_Folio AND d.Cve_Sucursal=p.Cve_Sucursal
        INNER JOIN CM_Consignatarios con ON con.Cve_Consignatario=p.Cve_Consignatario
        WHERE p.Estatus<>'CN' AND p.Referencia_Cliente='PAGADO'
          AND p.Cve_Sucursal<>99
          AND con.CP LIKE '[0-9][0-9][0-9][0-9][0-9]'
          AND YEAR(p.Fecha_Documento)={hoy.year} AND MONTH(p.Fecha_Documento)={hoy.month}
        GROUP BY con.CP ORDER BY ventas DESC
    """)
    for r in rows:
        en_cache = fetch_one("SELECT lat, lng FROM cp_coords WHERE cp = ?", (r['CP'],))
        estado = f"✓ lat={en_cache['lat']:.2f}" if en_cache else "✗ SIN COORDS"
        print(f"  CP {r['CP']} — ventas ${r['ventas']:,} — {estado}")
except Exception as e:
    print(f"  ERROR consultando ERP: {e}")

print("\n=== 3. Prueba Nominatim con primer CP ===")
import requests
try:
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"postalcode": "06600", "country": "MX", "format": "json", "limit": 1},
        headers={"User-Agent": "SuiteAnaliticaNentria/1.0"},
        timeout=8,
    )
    data = r.json()
    if data:
        print(f"  Nominatim OK — lat={data[0]['lat']}, lon={data[0]['lon']}")
    else:
        print("  Nominatim respondió pero SIN RESULTADOS para CP 06600")
except Exception as e:
    print(f"  Nominatim ERROR: {e}")
