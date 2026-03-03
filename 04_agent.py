"""
ERP DEMO — Agente Conversacional
Archivo: 04_agent.py

Uso:
    export OPENAI_API_KEY="sk-proj-..."
    python3 04_agent.py
"""

import json
import os
from pathlib import Path
import mysql.connector
from openai import OpenAI

# Cargar .env manualmente (sin dependencia extra)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Configuración ─────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "nentria",
    "database": "erp_demo",
    "charset": "utf8mb4",
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = "gpt-4o"

# ── Conexión MySQL ─────────────────────────────────────────────────────────────

def get_db():
    return mysql.connector.connect(**DB_CONFIG)


def query_view(sql: str, params: tuple = ()) -> list[dict]:
    """Ejecuta una query y devuelve lista de dicts."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    # Convertir tipos no serializables
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = str(v)
            elif v is None:
                row[k] = None
    return rows


# ── Definición de herramientas (tools) ───────────────────────────────────────

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
                    "anio": {
                        "type": "integer",
                        "description": "Filtrar por año específico (ej: 2024). Opcional."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos meses mostrar. Por defecto 12."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ventas_por_region",
            "description": (
                "Devuelve ventas desglosadas por región y sucursal. "
                "Usar para preguntas sobre qué región vende más, comparar sucursales, rendimiento geográfico."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "anio": {
                        "type": "integer",
                        "description": "Filtrar por año. Opcional."
                    },
                    "mes": {
                        "type": "integer",
                        "description": "Filtrar por mes (1-12). Opcional."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cuántas filas mostrar. Por defecto 20."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rendimiento_vendedores",
            "description": (
                "Devuelve el ranking de vendedores con sus ventas totales, ticket promedio, "
                "comisión generada y porcentaje de meta alcanzado. "
                "Usar para preguntas sobre quién vende más, comparar vendedores, top performers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos vendedores mostrar. Por defecto 10."
                    },
                    "zona": {
                        "type": "string",
                        "description": "Filtrar por zona (Norte, Sur, Centro, Oriente, Poniente, Bajío, Sureste). Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clientes_top",
            "description": (
                "Devuelve el ranking de clientes por facturación total. "
                "Incluye total de pedidos, ticket promedio, último pedido y días desde última compra. "
                "Usar para preguntas sobre mejores clientes, clientes inactivos, segmentación."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos clientes mostrar. Por defecto 10."
                    },
                    "segmento": {
                        "type": "string",
                        "description": "Filtrar por segmento: Retail, Corporativo, Gobierno, Mayorista, Startup. Opcional."
                    },
                    "region": {
                        "type": "string",
                        "description": "Filtrar por región. Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "empleados_activos",
            "description": (
                "Devuelve la nómina de empleados activos con cargo, departamento, sucursal, "
                "salario y años en la empresa. "
                "Usar para preguntas sobre cuántos empleados hay, quiénes trabajan en X departamento, salarios."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {
                        "type": "string",
                        "description": "Filtrar por nombre de departamento. Opcional."
                    },
                    "sucursal": {
                        "type": "string",
                        "description": "Filtrar por nombre o ciudad de sucursal. Opcional."
                    },
                    "nivel": {
                        "type": "string",
                        "description": "Filtrar por nivel: junior, mid, senior, lead, gerente, director. Opcional."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos empleados mostrar. Por defecto 20."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "empleados_antiguedad",
            "description": (
                "Devuelve empleados ordenados por antigüedad (años en la empresa). "
                "Usar para preguntas sobre empleados más antiguos, veteranos, tiempo promedio en la empresa."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos empleados mostrar. Por defecto 10."
                    },
                    "minimo_anos": {
                        "type": "integer",
                        "description": "Mostrar solo empleados con al menos N años. Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inventario_estado",
            "description": (
                "Devuelve el estado actual del inventario: stock, estado (normal/crítico/sin_stock/sobrestock), "
                "margen y valor en almacén. "
                "Usar para preguntas sobre productos sin stock, inventario crítico, valor del inventario."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "estado": {
                        "type": "string",
                        "description": "Filtrar por estado: sin_stock, critico, sobrestock, normal. Opcional."
                    },
                    "categoria": {
                        "type": "string",
                        "description": "Filtrar por categoría de producto. Opcional."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos productos mostrar. Por defecto 20."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pedidos_pendientes",
            "description": (
                "Devuelve pedidos que aún no han sido entregados (pendiente, confirmado, en preparación, enviado). "
                "Incluye días en espera. "
                "Usar para preguntas sobre pedidos atrasados, qué está pendiente, seguimiento de órdenes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "estado": {
                        "type": "string",
                        "description": "Filtrar por estado: pendiente, confirmado, en_preparacion, enviado. Opcional."
                    },
                    "min_dias_espera": {
                        "type": "integer",
                        "description": "Mostrar solo pedidos con más de N días de espera. Opcional."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos pedidos mostrar. Por defecto 20."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "productos_mas_vendidos",
            "description": (
                "Devuelve el ranking de productos por unidades vendidas y revenue generado. "
                "Usar para preguntas sobre qué se vende más, productos estrella, top de ventas por producto."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos productos mostrar. Por defecto 10."
                    },
                    "categoria": {
                        "type": "string",
                        "description": "Filtrar por categoría. Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pagos_recientes",
            "description": (
                "Devuelve los pagos más recientes con método de pago, monto, cliente y estado de factura. "
                "Usar para preguntas sobre cobros recientes, flujo de caja, pagos por método."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limite": {
                        "type": "integer",
                        "description": "Cuántos pagos mostrar. Por defecto 15."
                    },
                    "metodo": {
                        "type": "string",
                        "description": "Filtrar por método: transferencia, tarjeta_credito, tarjeta_debito, efectivo, cheque, credito_30, credito_60. Opcional."
                    }
                },
                "required": []
            }
        }
    },
]


# ── Ejecutores de herramientas ────────────────────────────────────────────────

def ejecutar_herramienta(nombre: str, args: dict) -> str:
    """Llama a la función correcta según el nombre del tool y devuelve JSON."""
    try:
        if nombre == "ventas_mensuales":
            sql = "SELECT * FROM vw_ventas_mensuales"
            filtros, params = [], []
            if args.get("anio"):
                filtros.append("anio = %s")
                params.append(args["anio"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 12))
            rows = query_view(sql, tuple(params))

        elif nombre == "ventas_por_region":
            sql = "SELECT * FROM vw_ventas_por_region"
            filtros, params = [], []
            if args.get("anio"):
                filtros.append("anio = %s")
                params.append(args["anio"])
            if args.get("mes"):
                filtros.append("mes = %s")
                params.append(args["mes"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "rendimiento_vendedores":
            sql = "SELECT * FROM vw_rendimiento_vendedores"
            filtros, params = [], []
            if args.get("zona"):
                filtros.append("zona = %s")
                params.append(args["zona"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "clientes_top":
            sql = "SELECT * FROM vw_clientes_top"
            filtros, params = [], []
            if args.get("segmento"):
                filtros.append("segmento = %s")
                params.append(args["segmento"])
            if args.get("region"):
                filtros.append("region LIKE %s")
                params.append(f"%{args['region']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "empleados_activos":
            sql = "SELECT * FROM vw_empleados_activos"
            filtros, params = [], []
            if args.get("departamento"):
                filtros.append("departamento LIKE %s")
                params.append(f"%{args['departamento']}%")
            if args.get("sucursal"):
                filtros.append("(sucursal LIKE %s OR ciudad LIKE %s)")
                params.extend([f"%{args['sucursal']}%", f"%{args['sucursal']}%"])
            if args.get("nivel"):
                filtros.append("nivel = %s")
                params.append(args["nivel"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "empleados_antiguedad":
            sql = "SELECT * FROM vw_empleados_antiguedad"
            filtros, params = [], []
            if args.get("minimo_anos"):
                filtros.append("anos >= %s")
                params.append(args["minimo_anos"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "inventario_estado":
            sql = "SELECT * FROM vw_inventario_estado"
            filtros, params = [], []
            if args.get("estado"):
                filtros.append("estado_stock = %s")
                params.append(args["estado"])
            if args.get("categoria"):
                filtros.append("categoria LIKE %s")
                params.append(f"%{args['categoria']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "pedidos_pendientes":
            sql = "SELECT * FROM vw_pedidos_pendientes"
            filtros, params = [], []
            if args.get("estado"):
                filtros.append("estado = %s")
                params.append(args["estado"])
            if args.get("min_dias_espera"):
                filtros.append("dias_en_espera >= %s")
                params.append(args["min_dias_espera"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 20))
            rows = query_view(sql, tuple(params))

        elif nombre == "productos_mas_vendidos":
            sql = "SELECT * FROM vw_productos_mas_vendidos"
            filtros, params = [], []
            if args.get("categoria"):
                filtros.append("categoria LIKE %s")
                params.append(f"%{args['categoria']}%")
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 10))
            rows = query_view(sql, tuple(params))

        elif nombre == "pagos_recientes":
            sql = "SELECT * FROM vw_pagos_recientes"
            filtros, params = [], []
            if args.get("metodo"):
                filtros.append("metodo_pago = %s")
                params.append(args["metodo"])
            if filtros:
                sql += " WHERE " + " AND ".join(filtros)
            sql += f" LIMIT %s"
            params.append(args.get("limite", 15))
            rows = query_view(sql, tuple(params))

        else:
            return json.dumps({"error": f"Herramienta desconocida: {nombre}"})

        return json.dumps(rows, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Agente principal ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente de análisis empresarial integrado al sistema ERP de la empresa.
Tienes acceso a datos reales de ventas, empleados, inventario, clientes y finanzas.

Instrucciones:
- Responde siempre en español, de forma clara y concisa.
- Cuando el usuario haga una pregunta sobre datos, usa las herramientas disponibles para consultar la información.
- Presenta los datos de forma legible: usa listas, tablas en texto, o resúmenes según corresponda.
- Si los datos tienen números grandes, formatea con separadores de miles.
- Si la pregunta es ambigua, responde con lo más relevante y ofrece profundizar.
- Nunca inventes datos. Si no tienes la información, dilo claramente.
- Puedes hacer varias consultas en la misma respuesta si la pregunta lo requiere.
"""


def chat(api_key: str):
    client = OpenAI(api_key=api_key)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("\n" + "=" * 60)
    print("  AGENTE ERP — Asistente de Análisis Empresarial")
    print("  Escribe 'salir' para terminar")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nHasta luego.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            print("Hasta luego.")
            break

        messages.append({"role": "user", "content": user_input})

        # Loop de tool use
        while True:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            msg = response.choices[0].message

            # Si el modelo quiere usar herramientas
            if msg.tool_calls:
                messages.append(msg)  # guardamos el mensaje del asistente con tool_calls

                for tool_call in msg.tool_calls:
                    nombre = tool_call.function.name
                    args   = json.loads(tool_call.function.arguments)

                    print(f"  [consultando {nombre}...]")
                    resultado = ejecutar_herramienta(nombre, args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": resultado,
                    })

            else:
                # Respuesta final
                respuesta = msg.content
                messages.append({"role": "assistant", "content": respuesta})
                print(f"\nAgente: {respuesta}\n")
                break


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: No se encontró OPENAI_API_KEY en el archivo .env")
        exit(1)

    chat(api_key)
