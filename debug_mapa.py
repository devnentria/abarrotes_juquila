#!/usr/bin/env python3
"""
Script de diagnóstico del mapa — ejecutar en el servidor:
  python debug_mapa.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "suite.db"

print("=== 1. Registros en cp_coords ===")
try:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT COUNT(*) FROM cp_coords").fetchone()
    print(f"  Coordenadas guardadas: {row[0]}")
    if row[0] > 0:
        muestras = conn.execute("SELECT cp, lat, lng FROM cp_coords LIMIT 5").fetchall()
        for m in muestras:
            print(f"    CP {m[0]} -> lat={m[1]:.4f}, lng={m[2]:.4f}")
    conn.close()
except Exception as e:
    print(f"  ERROR: {e}")

print("\n=== 2. Prueba conexion a Nominatim ===")
try:
    import urllib.request, json
    url = "https://nominatim.openstreetmap.org/search?postalcode=06600&country=MX&format=json&limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "SuiteAnaliticaNentria/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read())
    if data:
        print(f"  Nominatim OK — lat={data[0]['lat']}, lon={data[0]['lon']}")
    else:
        print("  Nominatim respondio pero SIN RESULTADOS para CP 06600")
except Exception as e:
    print(f"  Nominatim ERROR: {e}")
