"""
Agente IA — lógica desacoplada para uso desde Flask.
"""

import json

from openai import OpenAI

from db import query_view

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """Eres un asistente de análisis empresarial integrado al sistema ERP de la empresa.
Tienes acceso a datos reales de ventas, empleados, inventario, clientes y finanzas.

CONTEXTO DE DATOS MUY IMPORTANTE:
- Hoy es 2026. La base de datos contiene registros históricos de 2023, 2024 y 2025.
- El año más reciente con datos completos es 2025.
- "Este año" = 2025. "El año pasado" = 2024. "Hace dos años" = 2023.
- SIEMPRE usa las herramientas para consultar cualquier dato de 2023, 2024 o 2025. Nunca respondas con tu conocimiento de entrenamiento.
- Solo usa proyeccion_ventas o proyeccion_producto cuando el usuario pida datos de 2026 en adelante.

Instrucciones:
- Responde siempre en español, de forma clara y concisa.
- Cuando el usuario haga una pregunta sobre datos, usa las herramientas disponibles para consultar la información.
- Presenta los datos de forma legible: usa listas, resúmenes o comparaciones según corresponda.
- Si los datos tienen números grandes, formatea con separadores de miles y moneda MXN donde aplique.
- Si la pregunta es ambigua, responde con lo más relevante y ofrece profundizar.
- Nunca inventes datos. Si no tienes la información, dilo claramente.
- Puedes hacer varias consultas en la misma respuesta si la pregunta lo requiere.

Proyecciones y predicciones:
- Cuando te pregunten sobre el futuro (2026 en adelante, próximo mes después de diciembre 2025) usa las herramientas `proyeccion_ventas` o `proyeccion_producto`.
- Siempre da un número concreto proyectado, la tendencia (creciente/decreciente) y el porcentaje de cambio estimado.
- Aclara que la proyección se basa en la tendencia histórica y puede variar, pero nunca te niegues a dar un número.
- Si te preguntan por el año completo, suma las proyecciones mensuales.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ventas_mensuales",
            "description": (
                "Devuelve el resumen de ventas agrupado por mes. "
                "Incluye total de pedidos, ventas totales, ticket promedio y clientes únicos. "
                "Usar para preguntas sobre tendencias de ventas, comparar meses, ver evolución."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anio": {"type": "integer", "description": "Filtrar por año específico. Opcional."},
                    "limite": {"type": "integer", "description": "Cuántos meses mostrar. Por defecto 12."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ventas_por_region",
            "description": (
                "Devuelve ventas desglosadas por región y sucursal. "
                "Usar para preguntas sobre qué región vende más, comparar sucursales."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anio": {"type": "integer", "description": "Filtrar por año. Opcional."},
                    "mes": {"type": "integer", "description": "Filtrar por mes (1-12). Opcional."},
                    "limite": {"type": "integer", "description": "Cuántas filas mostrar. Por defecto 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rendimiento_vendedores",
            "description": (
                "Devuelve el ranking de vendedores con ventas totales, ticket promedio, "
                "comisión generada y porcentaje de meta. "
                "Usar para preguntas sobre quién vende más, top performers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cuántos vendedores mostrar. Por defecto 10."},
                    "zona": {"type": "string", "description": "Filtrar por zona. Opcional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clientes_top",
            "description": (
                "Devuelve el ranking de clientes por facturación total. "
                "Incluye pedidos, ticket promedio y último pedido. "
                "Usar para mejores clientes, clientes inactivos, segmentación."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cuántos clientes mostrar. Por defecto 10."},
                    "segmento": {"type": "string", "description": "Retail, Corporativo, Gobierno, Mayorista, Startup. Opcional."},
                    "region": {"type": "string", "description": "Filtrar por región. Opcional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "empleados_activos",
            "description": (
                "Devuelve la nómina de empleados activos con cargo, departamento, sucursal y salario. "
                "Usar para preguntas sobre cuántos empleados hay, quiénes trabajan en X área."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {"type": "string", "description": "Filtrar por departamento. Opcional."},
                    "sucursal": {"type": "string", "description": "Filtrar por sucursal o ciudad. Opcional."},
                    "nivel": {"type": "string", "description": "junior, mid, senior, lead, gerente, director. Opcional."},
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "empleados_antiguedad",
            "description": (
                "Devuelve empleados ordenados por antigüedad. "
                "Usar para empleados más antiguos, veteranos, tiempo promedio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 10."},
                    "minimo_anos": {"type": "integer", "description": "Solo empleados con al menos N años. Opcional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inventario_estado",
            "description": (
                "Devuelve el estado actual del inventario: stock, estado y valor. "
                "Usar para productos sin stock, inventario crítico, valor total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "estado": {"type": "string", "description": "sin_stock, critico, sobrestock, normal. Opcional."},
                    "categoria": {"type": "string", "description": "Filtrar por categoría. Opcional."},
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pedidos_pendientes",
            "description": (
                "Devuelve pedidos aún no entregados. "
                "Usar para pedidos atrasados, seguimiento de órdenes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "estado": {"type": "string", "description": "pendiente, confirmado, en_preparacion, enviado. Opcional."},
                    "min_dias_espera": {"type": "integer", "description": "Solo pedidos con más de N días en espera. Opcional."},
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 20."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_mas_vendidos",
            "description": (
                "Devuelve el ranking de productos por unidades vendidas y revenue. "
                "Usar para qué se vende más, productos estrella."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 10."},
                    "categoria": {"type": "string", "description": "Filtrar por categoría. Opcional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proyeccion_ventas",
            "description": (
                "Calcula la proyección de ventas para los próximos meses basándose en la tendencia histórica real. "
                "Usa regresión lineal sobre los datos mensuales. "
                "Usar para preguntas como: ¿cuánto venderemos el próximo mes?, ¿cómo van las ventas este año?, "
                "¿cuál es la tendencia de ventas?, proyección para el próximo año."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meses_a_proyectar": {
                        "type": "integer",
                        "description": "Cuántos meses hacia adelante proyectar. Por defecto 3. Máximo 12.",
                    },
                    "meses_historicos": {
                        "type": "integer",
                        "description": "Cuántos meses históricos usar como base. Por defecto 12.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proyeccion_producto",
            "description": (
                "Calcula la proyección de ventas de un producto específico para los próximos meses. "
                "También estima cuántos días de stock quedan antes de agotarse. "
                "Usar para: ¿cómo va a vender X producto?, ¿cuándo se agota X?, tendencia de demanda de un producto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Nombre o parte del nombre del producto a analizar.",
                    },
                    "meses_a_proyectar": {
                        "type": "integer",
                        "description": "Cuántos meses proyectar. Por defecto 3.",
                    },
                },
                "required": ["producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pagos_recientes",
            "description": (
                "Devuelve los pagos más recientes con método, monto, cliente y estado. "
                "Usar para cobros recientes, flujo de caja."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {"type": "integer", "description": "Cuántos mostrar. Por defecto 15."},
                    "metodo": {"type": "string", "description": "transferencia, tarjeta_credito, efectivo, etc. Opcional."},
                },
                "required": [],
            },
        },
    },
]


def _regresion_lineal(valores: list[float]) -> dict:
    """Regresión lineal simple sobre una serie temporal. Devuelve slope, intercept y proyección."""
    n = len(valores)
    if n < 2:
        return {"slope": 0, "intercept": valores[0] if valores else 0}
    x = list(range(n))
    sum_x  = sum(x)
    sum_y  = sum(valores)
    sum_xy = sum(xi * yi for xi, yi in zip(x, valores))
    sum_xx = sum(xi * xi for xi in x)
    denom  = n * sum_xx - sum_x ** 2
    if denom == 0:
        return {"slope": 0, "intercept": sum_y / n}
    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    promedio  = sum_y / n
    tasa_pct  = round(slope / promedio * 100, 1) if promedio else 0
    return {"slope": slope, "intercept": intercept, "tasa_mensual_pct": tasa_pct}


def _ejecutar_herramienta(nombre: str, args: dict) -> str:
    try:
        if nombre == "ventas_mensuales":
            sql, params = "SELECT * FROM vw_ventas_mensuales", []
            filtros = []
            if args.get("anio"):
                filtros.append("anio = %s"); params.append(args["anio"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 12))
            rows = query_view(sql, tuple(params))

        elif nombre == "ventas_por_region":
            sql, params, filtros = "SELECT * FROM vw_ventas_por_region", [], []
            if args.get("anio"):
                filtros.append("anio = %s"); params.append(args["anio"])
            if args.get("mes"):
                filtros.append("mes = %s"); params.append(args["mes"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "rendimiento_vendedores":
            sql, params, filtros = "SELECT * FROM vw_rendimiento_vendedores", [], []
            if args.get("zona"):
                filtros.append("zona = %s"); params.append(args["zona"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "clientes_top":
            sql, params, filtros = "SELECT * FROM vw_clientes_top", [], []
            if args.get("segmento"):
                filtros.append("segmento = %s"); params.append(args["segmento"])
            if args.get("region"):
                filtros.append("region LIKE %s"); params.append(f"%{args['region']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "empleados_activos":
            sql, params, filtros = "SELECT * FROM vw_empleados_activos", [], []
            if args.get("departamento"):
                filtros.append("departamento LIKE %s"); params.append(f"%{args['departamento']}%")
            if args.get("sucursal"):
                filtros.append("(sucursal LIKE %s OR ciudad LIKE %s)")
                params.extend([f"%{args['sucursal']}%", f"%{args['sucursal']}%"])
            if args.get("nivel"):
                filtros.append("nivel = %s"); params.append(args["nivel"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "empleados_antiguedad":
            sql, params, filtros = "SELECT * FROM vw_empleados_antiguedad", [], []
            if args.get("minimo_anos"):
                filtros.append("anos >= %s"); params.append(args["minimo_anos"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "inventario_estado":
            sql, params, filtros = "SELECT * FROM vw_inventario_estado", [], []
            if args.get("estado"):
                filtros.append("estado_stock = %s"); params.append(args["estado"])
            if args.get("categoria"):
                filtros.append("categoria LIKE %s"); params.append(f"%{args['categoria']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "pedidos_pendientes":
            sql, params, filtros = "SELECT * FROM vw_pedidos_pendientes", [], []
            if args.get("estado"):
                filtros.append("estado = %s"); params.append(args["estado"])
            if args.get("min_dias_espera"):
                filtros.append("dias_en_espera >= %s"); params.append(args["min_dias_espera"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "productos_mas_vendidos":
            sql, params, filtros = "SELECT * FROM vw_productos_mas_vendidos", [], []
            if args.get("categoria"):
                filtros.append("categoria LIKE %s"); params.append(f"%{args['categoria']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "pagos_recientes":
            sql, params, filtros = "SELECT * FROM vw_pagos_recientes", [], []
            if args.get("metodo"):
                filtros.append("metodo_pago = %s"); params.append(args["metodo"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += " LIMIT %s"; params.append(args.get("limite", 15))
            rows = query_view(sql, tuple(params))

        elif nombre == "proyeccion_ventas":
            meses_hist = min(args.get("meses_historicos", 12), 24)
            meses_proy = min(args.get("meses_a_proyectar", 3), 12)
            historico  = query_view(
                "SELECT periodo, ventas_totales, total_pedidos, ticket_promedio "
                "FROM vw_ventas_mensuales ORDER BY anio, mes LIMIT %s",
                (meses_hist,),
            )
            if not historico:
                return json.dumps({"error": "No hay datos históricos"})

            ventas = [float(r["ventas_totales"] or 0) for r in historico]
            reg    = _regresion_lineal(ventas)
            base   = len(ventas)

            proyecciones = []
            for i in range(1, meses_proy + 1):
                valor = reg["slope"] * (base + i - 1) + reg["intercept"]
                proyecciones.append({"mes_futuro": i, "ventas_proyectadas": round(max(0, valor), 2)})

            rows = {
                "historico_meses": len(historico),
                "ventas_ultimo_mes": ventas[-1],
                "ventas_promedio_mensual": round(sum(ventas) / len(ventas), 2),
                "tasa_crecimiento_mensual_pct": reg["tasa_mensual_pct"],
                "tendencia": "creciente" if reg["slope"] > 0 else "decreciente",
                "proyecciones": proyecciones,
                "total_proyectado_periodo": round(sum(p["ventas_proyectadas"] for p in proyecciones), 2),
            }

        elif nombre == "proyeccion_producto":
            producto_filtro = args.get("producto", "")
            meses_proy      = min(args.get("meses_a_proyectar", 3), 12)

            historico = query_view(
                "SELECT DATE_FORMAT(p.fecha_pedido, '%Y-%m') AS periodo, "
                "SUM(dp.cantidad) AS unidades, SUM(dp.subtotal) AS revenue "
                "FROM detalle_pedidos dp "
                "JOIN pedidos p ON dp.id_pedido = p.id "
                "JOIN estados_pedido ep ON p.id_estado = ep.id "
                "JOIN productos pr ON dp.id_producto = pr.id "
                "WHERE ep.nombre NOT IN ('cancelado','devuelto') "
                "AND pr.nombre LIKE %s "
                "GROUP BY DATE_FORMAT(p.fecha_pedido,'%Y-%m') "
                "ORDER BY periodo LIMIT 24",
                (f"%{producto_filtro}%",),
            )

            stock_info = query_view(
                "SELECT producto, sku, stock_actual, estado_stock "
                "FROM vw_inventario_estado WHERE producto LIKE %s LIMIT 1",
                (f"%{producto_filtro}%",),
            )

            if not historico:
                return json.dumps({"error": f"No se encontraron ventas para '{producto_filtro}'"})

            unidades = [float(r["unidades"] or 0) for r in historico]
            reg      = _regresion_lineal(unidades)
            base     = len(unidades)
            prom_mes = sum(unidades) / len(unidades)

            proyecciones = []
            for i in range(1, meses_proy + 1):
                valor = reg["slope"] * (base + i - 1) + reg["intercept"]
                proyecciones.append({"mes_futuro": i, "unidades_proyectadas": round(max(0, valor), 0)})

            stock_actual = int(stock_info[0]["stock_actual"]) if stock_info else None
            dias_stock   = None
            if stock_actual and prom_mes > 0:
                dias_stock = round(stock_actual / (prom_mes / 30), 0)

            rows = {
                "producto":      stock_info[0]["producto"] if stock_info else producto_filtro,
                "sku":           stock_info[0]["sku"] if stock_info else None,
                "meses_analizados": len(historico),
                "unidades_mes_promedio": round(prom_mes, 1),
                "unidades_ultimo_mes": unidades[-1],
                "tasa_crecimiento_mensual_pct": reg["tasa_mensual_pct"],
                "tendencia": "creciente" if reg["slope"] > 0 else "decreciente",
                "proyecciones_unidades": proyecciones,
                "stock_actual": stock_actual,
                "dias_estimados_de_stock": dias_stock,
            }

        else:
            return json.dumps({"error": f"Herramienta desconocida: {nombre}"})

        return json.dumps(rows, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


def process_message(api_key: str, user_message: str, history: list) -> dict:
    """
    Procesa un mensaje del usuario con el agente IA.

    history: lista de {role, content} (mensajes previos, sin system prompt)
    Devuelve: {response: str, tools_used: list[str]}
    """
    client = OpenAI(api_key=api_key)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    tools_used = []

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tool_call in msg.tool_calls:
                nombre = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                tools_used.append(nombre)
                resultado = _ejecutar_herramienta(nombre, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": resultado,
                })
        else:
            return {
                "response": msg.content,
                "tools_used": tools_used,
            }
