"""
ERP Demo — Servidor Flask
Rutas: / (UI) + /api/* (datos) + /api/chat (agente IA)
"""

import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import agent
from db import query_view

# Cargar .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

app = Flask(__name__)


# ── UI ────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    ventas_kpi = query_view(
        "SELECT COALESCE(SUM(ventas_totales),0) AS total_ventas, "
        "COALESCE(SUM(total_pedidos),0) AS total_pedidos "
        "FROM vw_ventas_mensuales"
    )
    empleados_kpi    = query_view("SELECT COUNT(*) AS total FROM vw_empleados_activos")
    clientes_kpi     = query_view("SELECT COUNT(*) AS total FROM vw_clientes_top")
    ventas_mensuales = query_view("SELECT * FROM vw_ventas_mensuales LIMIT 12")
    top_vendedores   = query_view(
        "SELECT vendedor, ventas_totales, pct_meta_mensual "
        "FROM vw_rendimiento_vendedores LIMIT 5"
    )
    ventas_region = query_view(
        "SELECT region, SUM(ventas_totales) AS total_ventas "
        "FROM vw_ventas_por_region "
        "WHERE anio = (SELECT MAX(anio) FROM vw_ventas_por_region) "
        "GROUP BY region ORDER BY SUM(ventas_totales) DESC"
    )
    metas_raw = query_view(
        "SELECT "
        "SUM(CASE WHEN pct_meta_mensual >= 100 THEN 1 ELSE 0 END) AS sobre_meta, "
        "SUM(CASE WHEN pct_meta_mensual BETWEEN 80 AND 99.9 THEN 1 ELSE 0 END) AS en_meta, "
        "SUM(CASE WHEN pct_meta_mensual < 80 THEN 1 ELSE 0 END) AS bajo_meta "
        "FROM vw_rendimiento_vendedores"
    )

    row   = ventas_kpi[0] if ventas_kpi else {}
    metas = metas_raw[0]  if metas_raw  else {}
    return jsonify({
        "kpis": {
            "ventas_totales":    float(row.get("total_ventas") or 0),
            "total_pedidos":     int(row.get("total_pedidos") or 0),
            "empleados_activos": int((empleados_kpi[0] if empleados_kpi else {}).get("total") or 0),
            "clientes_activos":  int((clientes_kpi[0]  if clientes_kpi  else {}).get("total") or 0),
        },
        "ventas_mensuales": ventas_mensuales,
        "top_vendedores":   top_vendedores,
        "ventas_region":    ventas_region,
        "metas_dist": {
            "sobre_meta": int(metas.get("sobre_meta") or 0),
            "en_meta":    int(metas.get("en_meta")    or 0),
            "bajo_meta":  int(metas.get("bajo_meta")  or 0),
        },
    })


# ── Ventas ────────────────────────────────────────────────────────────────────

@app.route("/api/ventas")
def api_ventas():
    vendedores = query_view("SELECT * FROM vw_rendimiento_vendedores LIMIT 15")
    clientes_top = query_view("SELECT * FROM vw_clientes_top LIMIT 10")
    return jsonify({"vendedores": vendedores, "clientes_top": clientes_top})


# ── Empleados ─────────────────────────────────────────────────────────────────

@app.route("/api/empleados")
def api_empleados():
    empleados = query_view("SELECT * FROM vw_empleados_activos LIMIT 20")
    antiguedad = query_view("SELECT * FROM vw_empleados_antiguedad LIMIT 10")
    kpis_data = query_view(
        "SELECT COUNT(*) AS total_activos, "
        "ROUND(AVG(salario_mensual), 2) AS salario_promedio, "
        "COUNT(DISTINCT departamento) AS total_departamentos, "
        "COUNT(DISTINCT sucursal) AS total_sucursales "
        "FROM vw_empleados_activos"
    )
    return jsonify({
        "kpis": kpis_data[0] if kpis_data else {},
        "empleados": empleados,
        "antiguedad": antiguedad,
    })


# ── Inventario ────────────────────────────────────────────────────────────────

@app.route("/api/inventario")
def api_inventario():
    inventario = query_view("SELECT * FROM vw_inventario_estado LIMIT 20")
    mas_vendidos = query_view("SELECT * FROM vw_productos_mas_vendidos LIMIT 10")
    kpis_data = query_view(
        "SELECT "
        "SUM(CASE WHEN estado_stock = 'sin_stock' THEN 1 ELSE 0 END) AS sin_stock, "
        "SUM(CASE WHEN estado_stock = 'critico'   THEN 1 ELSE 0 END) AS critico, "
        "ROUND(SUM(valor_inventario), 2) AS valor_inventario, "
        "COUNT(*) AS total_productos "
        "FROM vw_inventario_estado"
    )
    return jsonify({
        "kpis": kpis_data[0] if kpis_data else {},
        "inventario": inventario,
        "mas_vendidos": mas_vendidos,
    })


# ── Finanzas ──────────────────────────────────────────────────────────────────

@app.route("/api/finanzas")
def api_finanzas():
    pagos = query_view("SELECT * FROM vw_pagos_recientes LIMIT 30")
    kpis_data = query_view(
        "SELECT "
        "COALESCE(SUM(CASE WHEN MONTH(fecha_pago)=MONTH(CURDATE()) "
        "  AND YEAR(fecha_pago)=YEAR(CURDATE()) THEN monto ELSE 0 END), 0) AS pagos_mes, "
        "COUNT(*) AS total_pagos, "
        "SUM(CASE WHEN factura_pagada = 0 OR factura_pagada IS NULL THEN 1 ELSE 0 END) AS facturas_pendientes, "
        "SUM(CASE WHEN confirmado = 1 THEN 1 ELSE 0 END) AS pagos_confirmados "
        "FROM vw_pagos_recientes"
    )
    return jsonify({
        "kpis": kpis_data[0] if kpis_data else {},
        "pagos": pagos,
    })


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    if not OPENAI_API_KEY:
        return jsonify({"error": "OPENAI_API_KEY no configurada en .env"}), 500

    try:
        result = agent.process_message(OPENAI_API_KEY, message, history)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  ERP Demo corriendo en http://localhost:5000\n")
    app.run(debug=True, port=5000)
