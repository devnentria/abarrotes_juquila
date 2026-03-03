"""
ERP DEMO — Generador de Datos Dummy
Archivo: 02_seed_data.py

Requisitos:
    pip install faker mysql-connector-python tqdm

Uso:
    python 02_seed_data.py

Volumen aproximado generado:
    - 6 sucursales
    - 18 departamentos
    - 2,000 empleados
    - 150 vendedores
    - 1,200 clientes
    - 60 categorías (3 niveles)
    - 80 proveedores
    - 600 productos
    - 50,000 pedidos
    - ~150,000 líneas de detalle
    - ~120,000 movimientos de inventario
    - ~42,000 pagos
    - ~40,000 facturas
"""

import random
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import mysql.connector
from faker import Faker
from faker.providers import company, person, address, internet, phone_number
from tqdm import tqdm

# ── Configuración de conexión ─────────────────────────────────────────────────
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "nentria",
    "database": "erp_demo",
    "charset": "utf8mb4",
    "autocommit": False,
}

# ── Faker en español (México) ─────────────────────────────────────────────────
fake = Faker("es_MX")
fake.seed_instance(42)
random.seed(42)

# ── Constantes de volumen ─────────────────────────────────────────────────────
N_SUCURSALES    = 6
N_DEPARTAMENTOS = 18
N_EMPLEADOS     = 2_000
N_VENDEDORES    = 150
N_CLIENTES      = 1_200
N_PROVEEDORES   = 80
N_PRODUCTOS     = 600
N_PEDIDOS       = 50_000

DATE_START = date(2021, 1, 1)
DATE_END   = date(2025, 12, 31)


# ── Utilidades ────────────────────────────────────────────────────────────────

def rand_date(start: date = DATE_START, end: date = DATE_END) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def rand_datetime(start: date = DATE_START, end: date = DATE_END) -> datetime:
    d = rand_date(start, end)
    return datetime(d.year, d.month, d.day,
                    random.randint(8, 20), random.randint(0, 59))


def batch_insert(cursor, table: str, columns: list, rows: list, batch=500):
    """Inserta filas en lotes para mejor rendimiento."""
    placeholders = ", ".join(["%s"] * len(columns))
    cols = ", ".join(columns)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    for i in range(0, len(rows), batch):
        cursor.executemany(sql, rows[i : i + batch])


# ── Generadores por módulo ────────────────────────────────────────────────────

def seed_sucursales(cursor) -> list[int]:
    ciudades = [
        ("Ciudad de México", "Centro", "CDMX"),
        ("Guadalajara",      "Occidente", "Jalisco"),
        ("Monterrey",        "Norte", "Nuevo León"),
        ("Puebla",           "Centro", "Puebla"),
        ("Mérida",           "Sur", "Yucatán"),
        ("Tijuana",          "Norte", "Baja California"),
    ]
    rows = []
    for nombre, region, ciudad in ciudades:
        rows.append((
            f"Sucursal {ciudad}",
            ciudad,
            region,
            "México",
            fake.street_address(),
            fake.phone_number(),
            1,
            rand_date(date(2015, 1, 1), date(2021, 12, 31)),
        ))
    batch_insert(cursor, "sucursales",
                 ["nombre","ciudad","region","pais","direccion","telefono","activa","fecha_apertura"],
                 rows)
    cursor.execute("SELECT id FROM sucursales ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_departamentos(cursor, sucursal_ids: list[int]) -> list[int]:
    nombres = [
        "Recursos Humanos", "Tecnología de Información", "Finanzas y Contabilidad",
        "Ventas", "Marketing", "Operaciones", "Logística y Almacén",
        "Servicio al Cliente", "Compras y Proveedores", "Legal",
        "Producción", "Calidad", "Proyectos", "Auditoría Interna",
        "Dirección General", "Administración", "Innovación", "Seguridad",
    ]
    rows = []
    for i, nombre in enumerate(nombres):
        rows.append((
            nombre,
            f"Departamento de {nombre}",
            sucursal_ids[i % len(sucursal_ids)],
            None,   # manager se actualiza después
            1,
        ))
    batch_insert(cursor, "departamentos",
                 ["nombre","descripcion","id_sucursal","id_manager","activo"],
                 rows)
    cursor.execute("SELECT id FROM departamentos ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_empleados(cursor, dept_ids: list[int], sucursal_ids: list[int]) -> list[int]:
    cargos_por_nivel = {
        "junior":   ["Analista Jr", "Asistente", "Auxiliar", "Técnico Jr"],
        "mid":      ["Analista", "Especialista", "Técnico", "Coordinador"],
        "senior":   ["Analista Sr", "Especialista Sr", "Supervisor", "Consultor Sr"],
        "lead":     ["Líder de Equipo", "Tech Lead", "Jefe de Área"],
        "gerente":  ["Gerente de Área", "Gerente Regional"],
        "director": ["Director de División", "Director General"],
    }
    niveles = ["junior","mid","mid","senior","senior","lead","gerente","director"]
    pesos   = [15, 30, 0, 25, 0, 15, 10, 5]

    salarios = {
        "junior":   (12_000, 18_000),
        "mid":      (18_000, 30_000),
        "senior":   (30_000, 50_000),
        "lead":     (50_000, 75_000),
        "gerente":  (75_000, 120_000),
        "director": (120_000, 250_000),
    }
    contratos = ["indefinido","indefinido","indefinido","temporal","practicante"]
    rows = []
    for i in range(1, N_EMPLEADOS + 1):
        nivel = random.choices(
            ["junior","mid","senior","lead","gerente","director"],
            weights=[15, 35, 30, 12, 6, 2]
        )[0]
        smin, smax = salarios[nivel]
        cargo = random.choice(cargos_por_nivel[nivel])
        fecha_ingreso = rand_date(date(2018, 1, 1), date(2025, 6, 30))
        activo = 1 if random.random() > 0.08 else 0
        fecha_baja = None
        if not activo:
            fecha_baja = rand_date(fecha_ingreso + timedelta(days=90), DATE_END)

        rows.append((
            f"EMP{i:05d}",
            fake.first_name(),
            fake.last_name(),
            fake.last_name(),
            fake.unique.email(),
            fake.phone_number(),
            fake.date_of_birth(minimum_age=22, maximum_age=60).strftime("%Y-%m-%d"),
            fecha_ingreso,
            fecha_baja,
            cargo,
            nivel,
            round(random.uniform(smin, smax), 2),
            random.choice(contratos),
            random.choice(dept_ids),
            random.choice(sucursal_ids),
            activo,
        ))

    batch_insert(cursor, "empleados",
                 ["numero_empleado","nombre","apellido_paterno","apellido_materno",
                  "email","telefono","fecha_nacimiento","fecha_ingreso","fecha_baja",
                  "cargo","nivel","salario_mensual","tipo_contrato",
                  "id_departamento","id_sucursal","activo"],
                 rows)
    cursor.execute("SELECT id FROM empleados ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_managers(cursor, dept_ids: list[int], empleado_ids: list[int]):
    """Asigna un manager aleatorio a cada departamento."""
    for dept_id in dept_ids:
        manager_id = random.choice(empleado_ids)
        cursor.execute(
            "UPDATE departamentos SET id_manager = %s WHERE id = %s",
            (manager_id, dept_id)
        )


def seed_vendedores(cursor, empleado_ids: list[int]) -> list[int]:
    zonas = ["Norte", "Sur", "Oriente", "Poniente", "Centro", "Bajío", "Sureste"]
    sample = random.sample(empleado_ids, N_VENDEDORES)
    rows = []
    for emp_id in sample:
        rows.append((
            emp_id,
            random.choice(zonas),
            round(random.uniform(200_000, 800_000), 2),
            round(random.uniform(3.0, 10.0), 2),
            1,
        ))
    batch_insert(cursor, "vendedores",
                 ["id_empleado","zona","meta_mensual","comision_pct","activo"],
                 rows)
    cursor.execute("SELECT id FROM vendedores ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_clientes(cursor) -> list[int]:
    cursor.execute("SELECT id FROM segmentos_cliente")
    segmento_ids = [r[0] for r in cursor.fetchall()]

    regiones = ["Norte","Sur","Centro","Oriente","Poniente","Bajío","Sureste","Internacional"]
    rows = []
    for i in range(1, N_CLIENTES + 1):
        rows.append((
            f"CLI{i:05d}",
            fake.company(),
            f"RFC{fake.lexify('???')}{''.join(random.choices('0123456789',k=6))}",
            fake.company_email(),
            fake.phone_number(),
            fake.city(),
            random.choice(regiones),
            "México",
            random.choice(segmento_ids),
            round(random.uniform(50_000, 2_000_000), 2),
            1,
            rand_date(date(2018, 1, 1), date(2024, 12, 31)),
        ))
    batch_insert(cursor, "clientes",
                 ["codigo","razon_social","rfc","email","telefono","ciudad","region",
                  "pais","id_segmento","limite_credito","activo","fecha_registro"],
                 rows)
    cursor.execute("SELECT id FROM clientes ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_categorias(cursor) -> list[int]:
    padres = [
        "Electrónica", "Hogar y Muebles", "Ropa y Accesorios",
        "Alimentos y Bebidas", "Herramientas e Industrial",
        "Salud y Belleza", "Deportes", "Juguetes y Entretenimiento",
        "Oficina y Papelería", "Automotriz",
    ]
    hijos = {
        "Electrónica":           ["Computadoras", "Smartphones", "Audio y Video", "Accesorios Tech"],
        "Hogar y Muebles":       ["Sala", "Recámara", "Cocina", "Decoración"],
        "Ropa y Accesorios":     ["Hombre", "Mujer", "Niños", "Calzado"],
        "Alimentos y Bebidas":   ["Bebidas", "Snacks", "Lácteos", "Congelados"],
        "Herramientas e Industrial": ["Eléctrica", "Manual", "Maquinaria", "Seguridad"],
        "Salud y Belleza":       ["Skincare", "Vitaminas", "Medicamentos OTC", "Higiene"],
        "Deportes":              ["Fitness", "Outdoor", "Acuáticos", "Colectivos"],
        "Juguetes y Entretenimiento": ["Infantil", "Videojuegos", "Juegos de Mesa"],
        "Oficina y Papelería":   ["Material de Oficina", "Impresión", "Mobiliario Oficina"],
        "Automotriz":            ["Refacciones", "Accesorios Auto", "Lubricantes"],
    }

    rows_padres = [(nombre, None, 1) for nombre in padres]
    batch_insert(cursor, "categorias", ["nombre","id_padre","activa"], rows_padres)
    cursor.execute("SELECT id, nombre FROM categorias WHERE id_padre IS NULL")
    padre_map = {nombre: cid for cid, nombre in cursor.fetchall()}

    rows_hijos = []
    for padre_nombre, hijos_lista in hijos.items():
        for hijo in hijos_lista:
            rows_hijos.append((hijo, padre_map[padre_nombre], 1))
    batch_insert(cursor, "categorias", ["nombre","id_padre","activa"], rows_hijos)

    cursor.execute("SELECT id FROM categorias WHERE id_padre IS NOT NULL")
    return [r[0] for r in cursor.fetchall()]


def seed_proveedores(cursor) -> list[int]:
    rows = []
    for i in range(1, N_PROVEEDORES + 1):
        rows.append((
            f"PROV{i:04d}",
            fake.company(),
            f"RFC{fake.lexify('???')}{''.join(random.choices('0123456789',k=6))}",
            fake.company_email(),
            fake.phone_number(),
            fake.city(),
            random.choice(["México","EUA","China","España","Alemania"]),
            random.choice([3, 5, 7, 10, 14, 21, 30]),
            1,
        ))
    batch_insert(cursor, "proveedores",
                 ["codigo","nombre","rfc","email","telefono","ciudad","pais","plazo_entrega","activo"],
                 rows)
    cursor.execute("SELECT id FROM proveedores ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_productos(cursor, categoria_ids: list[int], proveedor_ids: list[int]) -> list[int]:
    nombres_base = [
        "Mesa", "Silla", "Monitor", "Teclado", "Mouse", "Cámara", "Bocina",
        "Lámpara", "Ventilador", "Refrigerador", "Licuadora", "Cafetera",
        "Camiseta", "Pantalón", "Zapatos", "Bolsa", "Reloj", "Lentes",
        "Proteína", "Vitaminas", "Shampoo", "Crema", "Perfume",
        "Martillo", "Taladro", "Sierra", "Cinta", "Guantes",
        "Notebook", "Tablet", "Audífonos", "Cable HDMI", "Hub USB",
        "Pelota", "Mancuerna", "Cinta Correr", "Bicicleta", "Casco",
        "Cuaderno", "Pluma", "Carpeta", "Impresora", "Toner",
        "Aceite Motor", "Filtro Aire", "Batería Auto", "Llanta", "Espejo",
    ]
    rows = []
    skus = set()
    for i in range(1, N_PRODUCTOS + 1):
        sku = f"SKU{i:05d}"
        nombre_base = random.choice(nombres_base)
        modelo = fake.bothify("??-###").upper()
        nombre = f"{nombre_base} {modelo}"
        compra = round(random.uniform(50, 5_000), 2)
        venta  = round(compra * random.uniform(1.2, 2.5), 2)
        stock  = random.randint(0, 400)
        rows.append((
            sku,
            nombre,
            fake.sentence(nb_words=8),
            random.choice(categoria_ids),
            random.choice(proveedor_ids),
            compra,
            venta,
            stock,
            random.randint(5, 20),
            random.randint(200, 600),
            "pieza",
            1,
        ))
    batch_insert(cursor, "productos",
                 ["sku","nombre","descripcion","id_categoria","id_proveedor",
                  "precio_compra","precio_venta","stock_actual","stock_minimo",
                  "stock_maximo","unidad_medida","activo"],
                 rows)
    cursor.execute("SELECT id FROM productos ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def seed_pedidos_y_detalles(
    cursor,
    cliente_ids: list[int],
    vendedor_ids: list[int],
    sucursal_ids: list[int],
    producto_ids: list[int],
) -> list[int]:
    cursor.execute("SELECT id, nombre FROM estados_pedido")
    estado_map = {nombre: eid for eid, nombre in cursor.fetchall()}

    cursor.execute("SELECT id, precio_venta FROM productos WHERE activo=1")
    productos_precios = {pid: precio for pid, precio in cursor.fetchall()}
    prod_list = list(productos_precios.keys())

    print("\n  Insertando pedidos y detalles...")
    pedido_rows = []
    detalle_rows = []
    pedido_ids = []

    BATCH_PEDIDOS = 1_000

    for i in tqdm(range(1, N_PEDIDOS + 1), unit="pedido"):
        fecha_pedido = rand_date()
        estado_nombre = random.choices(
            ["entregado","entregado","enviado","confirmado","pendiente","cancelado","devuelto"],
            weights=[50, 0, 15, 15, 10, 7, 3]
        )[0]
        estado_id = estado_map[estado_nombre]

        fecha_entrega = None
        if estado_nombre in ("entregado", "enviado"):
            fecha_entrega = fecha_pedido + timedelta(days=random.randint(1, 15))

        fecha_cancelacion = None
        if estado_nombre == "cancelado":
            fecha_cancelacion = fecha_pedido + timedelta(days=random.randint(1, 5))

        n_lineas = random.randint(1, 8)
        prods_pedido = random.sample(prod_list, min(n_lineas, len(prod_list)))

        subtotal = Decimal("0")
        descuento = Decimal(str(round(random.uniform(0, 0.15), 4)))
        for prod_id in prods_pedido:
            precio = Decimal(str(productos_precios[prod_id]))
            cantidad = random.randint(1, 20)
            desc_linea = round(random.uniform(0, 0.10), 4)
            sub_linea = precio * cantidad * (1 - Decimal(str(desc_linea)))
            subtotal += sub_linea
            detalle_rows.append((
                i,           # placeholder, se reemplaza con id real
                prod_id,
                cantidad,
                float(precio),
                desc_linea * 100,
                float(sub_linea.quantize(Decimal("0.01"))),
            ))

        descuento_monto = (subtotal * descuento).quantize(Decimal("0.01"))
        impuesto = ((subtotal - descuento_monto) * Decimal("0.16")).quantize(Decimal("0.01"))
        total = (subtotal - descuento_monto + impuesto).quantize(Decimal("0.01"))

        pedido_rows.append((
            f"PED{i:07d}",
            random.choice(cliente_ids),
            random.choice(vendedor_ids),
            random.choice(sucursal_ids),
            estado_id,
            fecha_pedido,
            fecha_entrega,
            fecha_cancelacion,
            float(subtotal.quantize(Decimal("0.01"))),
            float(descuento_monto),
            float(impuesto),
            float(total),
        ))

        # Insertar en lote
        if i % BATCH_PEDIDOS == 0 or i == N_PEDIDOS:
            cols_p = ["folio","id_cliente","id_vendedor","id_sucursal","id_estado",
                      "fecha_pedido","fecha_entrega","fecha_cancelacion",
                      "subtotal","descuento","impuesto","total"]
            batch_insert(cursor, "pedidos", cols_p, pedido_rows)
            pedido_rows = []

    # Recuperar IDs reales de pedidos para los detalles
    cursor.execute("SELECT id FROM pedidos ORDER BY id")
    real_ids = [r[0] for r in cursor.fetchall()]

    # Parchear id_pedido en los detalles (filas tienen i como placeholder)
    # Los detalles se generaron secuencialmente, reconstruimos la posición
    print("  Insertando detalles de pedidos...")
    # Reconstruir detalles con id real usando acumulador
    # detalle_rows contiene (i_placeholder, prod_id, ...)
    # Agrupamos por i_placeholder
    from collections import defaultdict
    detalles_por_pedido = defaultdict(list)
    for row in detalle_rows:
        i_ph = row[0]
        detalles_por_pedido[i_ph].append(row[1:])  # sin el placeholder

    final_detalles = []
    for idx, real_id in enumerate(real_ids, start=1):
        for det in detalles_por_pedido.get(idx, []):
            final_detalles.append((real_id,) + det)

    batch_insert(cursor, "detalle_pedidos",
                 ["id_pedido","id_producto","cantidad","precio_unitario",
                  "descuento_pct","subtotal"],
                 final_detalles, batch=2_000)

    return real_ids


def seed_movimientos_inventario(cursor, producto_ids, sucursal_ids):
    cursor.execute("SELECT id, nombre FROM tipos_movimiento")
    tipo_map = {nombre: tid for tid, nombre in cursor.fetchall()}

    cursor.execute("SELECT id, stock_actual, precio_compra FROM productos")
    prod_info = {pid: (stock, costo) for pid, stock, costo in cursor.fetchall()}

    print("\n  Insertando movimientos de inventario...")
    rows = []
    # ~120k movimientos: ~200 por producto aprox
    for prod_id in tqdm(producto_ids, unit="producto"):
        stock, costo = prod_info[prod_id]
        n_movs = random.randint(80, 250)
        for _ in range(n_movs):
            tipo = random.choices(
                ["entrada_compra","salida_venta","ajuste_positivo","ajuste_negativo","devolucion"],
                weights=[25, 55, 5, 5, 10]
            )[0]
            if tipo in ("entrada_compra", "ajuste_positivo", "devolucion"):
                cantidad = random.randint(10, 100)
            else:
                cantidad = -random.randint(1, 30)

            stock_ant = max(stock, 0)
            stock_nvo = max(stock_ant + cantidad, 0)
            stock = stock_nvo

            rows.append((
                prod_id,
                random.choice(sucursal_ids),
                tipo_map[tipo],
                None,
                cantidad,
                stock_ant,
                stock_nvo,
                float(costo),
                rand_datetime(),
                f"REF-{uuid.uuid4().hex[:8].upper()}",
            ))

            if len(rows) >= 3_000:
                batch_insert(cursor, "movimientos_inventario",
                             ["id_producto","id_sucursal","id_tipo","id_pedido",
                              "cantidad","stock_anterior","stock_nuevo","costo_unitario",
                              "fecha","referencia"],
                             rows, batch=3_000)
                rows = []

    if rows:
        batch_insert(cursor, "movimientos_inventario",
                     ["id_producto","id_sucursal","id_tipo","id_pedido",
                      "cantidad","stock_anterior","stock_nuevo","costo_unitario",
                      "fecha","referencia"],
                     rows, batch=3_000)


def seed_pagos_y_facturas(cursor, pedido_ids):
    cursor.execute("SELECT id, nombre FROM metodos_pago")
    metodo_map = {nombre: mid for mid, nombre in cursor.fetchall()}
    metodo_ids = list(metodo_map.values())

    cursor.execute("""
        SELECT p.id, p.id_cliente, p.total, p.fecha_pedido, ep.nombre
        FROM pedidos p
        JOIN estados_pedido ep ON p.id_estado = ep.id
        WHERE ep.nombre IN ('entregado','enviado','confirmado')
        LIMIT 45000
    """)
    pedidos_elegibles = cursor.fetchall()

    print("\n  Insertando pagos y facturas...")
    pago_rows = []
    factura_rows = []
    folio_counter = 1

    for pid, cliente_id, total, fecha_pedido, estado in tqdm(pedidos_elegibles, unit="pedido"):
        # Pago
        dias_pago = random.randint(0, 30)
        fecha_pago = fecha_pedido + timedelta(days=dias_pago)
        pago_rows.append((
            pid,
            random.choice(metodo_ids),
            float(total),
            fecha_pago,
            f"TRF{uuid.uuid4().hex[:10].upper()}",
            1,
        ))

        # Factura (85% de pedidos entregados/enviados)
        if random.random() < 0.85:
            subtotal = round(float(total) / 1.16, 2)
            iva = round(float(total) - subtotal, 2)
            pagada = 1 if estado == "entregado" else (1 if random.random() > 0.3 else 0)
            factura_rows.append((
                f"UUID-{uuid.uuid4().hex[:20].upper()}",
                pid,
                cliente_id,
                fecha_pedido,
                fecha_pedido + timedelta(days=30),
                subtotal,
                iva,
                float(total),
                pagada,
                0,
            ))
        folio_counter += 1

    batch_insert(cursor, "pagos",
                 ["id_pedido","id_metodo","monto","fecha_pago","referencia","confirmado"],
                 pago_rows, batch=2_000)
    batch_insert(cursor, "facturas",
                 ["folio_fiscal","id_pedido","id_cliente","fecha_emision",
                  "fecha_vencimiento","subtotal","iva","total","pagada","cancelada"],
                 factura_rows, batch=2_000)


# ── Ejecución principal ───────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  ERP DEMO — Generador de Datos")
    print("=" * 60)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SET foreign_key_checks = 0")
    cursor.execute("SET unique_checks = 0")
    cursor.execute("SET sql_mode = ''")

    try:
        print("\n[1/10] Sucursales...")
        sucursal_ids = seed_sucursales(cursor)
        conn.commit()
        print(f"       {len(sucursal_ids)} sucursales creadas.")

        print("\n[2/10] Departamentos...")
        dept_ids = seed_departamentos(cursor, sucursal_ids)
        conn.commit()
        print(f"       {len(dept_ids)} departamentos creados.")

        print("\n[3/10] Empleados...")
        empleado_ids = seed_empleados(cursor, dept_ids, sucursal_ids)
        conn.commit()
        print(f"       {len(empleado_ids)} empleados creados.")

        print("\n[4/10] Asignando managers a departamentos...")
        seed_managers(cursor, dept_ids, empleado_ids)
        conn.commit()

        print("\n[5/10] Vendedores...")
        vendedor_ids = seed_vendedores(cursor, empleado_ids)
        conn.commit()
        print(f"       {len(vendedor_ids)} vendedores creados.")

        print("\n[6/10] Clientes...")
        cliente_ids = seed_clientes(cursor)
        conn.commit()
        print(f"       {len(cliente_ids)} clientes creados.")

        print("\n[7/10] Categorías, proveedores y productos...")
        categoria_ids = seed_categorias(cursor)
        conn.commit()
        proveedor_ids = seed_proveedores(cursor)
        conn.commit()
        producto_ids = seed_productos(cursor, categoria_ids, proveedor_ids)
        conn.commit()
        print(f"       {len(categoria_ids)} categorías, {len(proveedor_ids)} proveedores, {len(producto_ids)} productos.")

        print("\n[8/10] Pedidos y detalles...")
        pedido_ids = seed_pedidos_y_detalles(cursor, cliente_ids, vendedor_ids, sucursal_ids, producto_ids)
        conn.commit()
        print(f"       {len(pedido_ids)} pedidos creados.")

        print("\n[9/10] Movimientos de inventario...")
        seed_movimientos_inventario(cursor, producto_ids, sucursal_ids)
        conn.commit()

        print("\n[10/10] Pagos y facturas...")
        seed_pagos_y_facturas(cursor, pedido_ids)
        conn.commit()

        # Resumen final
        print("\n" + "=" * 60)
        print("  RESUMEN FINAL")
        print("=" * 60)
        tablas = ["sucursales","departamentos","empleados","vendedores",
                  "clientes","categorias","proveedores","productos",
                  "pedidos","detalle_pedidos","movimientos_inventario",
                  "pagos","facturas"]
        for tabla in tablas:
            cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
            n = cursor.fetchone()[0]
            print(f"  {tabla:<30} {n:>10,} registros")
        print("=" * 60)
        print("\n  Datos generados exitosamente.")

    except Exception as e:
        conn.rollback()
        print(f"\n  ERROR: {e}")
        raise
    finally:
        cursor.execute("SET foreign_key_checks = 1")
        cursor.execute("SET unique_checks = 1")
        conn.commit()
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
