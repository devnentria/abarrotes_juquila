"""
Agente IA — lógica desacoplada para uso desde Flask.
"""

import json

from openai import OpenAI

from db import query_view

MODEL = "gpt-4o"

SYSTEM_PROMPT = """Eres un asistente de análisis empresarial integrado al sistema ERP de la empresa.
Tienes acceso a datos reales de ventas, empleados, inventario, clientes y finanzas.

Instrucciones:
- Responde siempre en español, de forma clara y concisa.
- Cuando el usuario haga una pregunta sobre datos, usa las herramientas disponibles para consultar la información.
- Presenta los datos de forma legible: usa listas, resúmenes o comparaciones según corresponda.
- Si los datos tienen números grandes, formatea con separadores de miles y moneda MXN donde aplique.
- Si la pregunta es ambigua, responde con lo más relevante y ofrece profundizar.
- Nunca inventes datos. Si no tienes la información, dilo claramente.
- Puedes hacer varias consultas en la misma respuesta si la pregunta lo requiere.
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
