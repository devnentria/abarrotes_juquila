-- ============================================================
--  ERP DEMO — Esquema de Base de Datos
--  Archivo: 01_schema.sql
--  Descripción: Crea todas las tablas del sistema ERP demo
-- ============================================================

DROP DATABASE IF EXISTS erp_demo;
CREATE DATABASE erp_demo
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE erp_demo;

-- ============================================================
--  MÓDULO: ESTRUCTURA ORGANIZACIONAL
-- ============================================================

CREATE TABLE sucursales (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL,
    ciudad          VARCHAR(100) NOT NULL,
    region          VARCHAR(100) NOT NULL,
    pais            VARCHAR(100) NOT NULL DEFAULT 'México',
    direccion       VARCHAR(255),
    telefono        VARCHAR(20),
    activa          TINYINT(1) NOT NULL DEFAULT 1,
    fecha_apertura  DATE NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE departamentos (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL,
    descripcion     VARCHAR(255),
    id_sucursal     INT UNSIGNED NOT NULL,
    id_manager      INT UNSIGNED,          -- FK a empleados (se agrega después)
    activo          TINYINT(1) NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_sucursal) REFERENCES sucursales(id)
);

-- ============================================================
--  MÓDULO: RECURSOS HUMANOS
-- ============================================================

CREATE TABLE empleados (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    numero_empleado     VARCHAR(20) NOT NULL UNIQUE,
    nombre              VARCHAR(100) NOT NULL,
    apellido_paterno    VARCHAR(100) NOT NULL,
    apellido_materno    VARCHAR(100),
    email               VARCHAR(150) NOT NULL UNIQUE,
    telefono            VARCHAR(20),
    fecha_nacimiento    DATE,
    fecha_ingreso       DATE NOT NULL,
    fecha_baja          DATE,
    cargo               VARCHAR(100) NOT NULL,
    nivel               ENUM('junior','mid','senior','lead','gerente','director') NOT NULL DEFAULT 'mid',
    salario_mensual     DECIMAL(12,2) NOT NULL,
    tipo_contrato       ENUM('indefinido','temporal','practicante','freelance') NOT NULL DEFAULT 'indefinido',
    id_departamento     INT UNSIGNED NOT NULL,
    id_sucursal         INT UNSIGNED NOT NULL,
    activo              TINYINT(1) NOT NULL DEFAULT 1,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_departamento) REFERENCES departamentos(id),
    FOREIGN KEY (id_sucursal)     REFERENCES sucursales(id),
    INDEX idx_departamento (id_departamento),
    INDEX idx_sucursal    (id_sucursal),
    INDEX idx_activo      (activo),
    INDEX idx_fecha_ingreso (fecha_ingreso)
);

-- Ahora que existe empleados, vinculamos manager
ALTER TABLE departamentos
    ADD CONSTRAINT fk_dept_manager
    FOREIGN KEY (id_manager) REFERENCES empleados(id);

-- ============================================================
--  MÓDULO: CLIENTES Y VENTAS
-- ============================================================

CREATE TABLE segmentos_cliente (
    id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE   -- 'Retail', 'Corporativo', 'Gobierno', 'Mayorista'
);

CREATE TABLE clientes (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    codigo          VARCHAR(20) NOT NULL UNIQUE,
    razon_social    VARCHAR(200) NOT NULL,
    rfc             VARCHAR(20),
    email           VARCHAR(150),
    telefono        VARCHAR(20),
    ciudad          VARCHAR(100),
    region          VARCHAR(100),
    pais            VARCHAR(100) NOT NULL DEFAULT 'México',
    id_segmento     INT UNSIGNED NOT NULL,
    limite_credito  DECIMAL(14,2) NOT NULL DEFAULT 0,
    activo          TINYINT(1) NOT NULL DEFAULT 1,
    fecha_registro  DATE NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_segmento) REFERENCES segmentos_cliente(id),
    INDEX idx_region   (region),
    INDEX idx_segmento (id_segmento)
);

CREATE TABLE vendedores (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_empleado     INT UNSIGNED NOT NULL UNIQUE,
    zona            VARCHAR(100),
    meta_mensual    DECIMAL(14,2) NOT NULL DEFAULT 0,
    comision_pct    DECIMAL(5,2) NOT NULL DEFAULT 5.00,   -- porcentaje de comisión
    activo          TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (id_empleado) REFERENCES empleados(id),
    INDEX idx_zona (zona)
);

CREATE TABLE estados_pedido (
    id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE
    -- 'pendiente', 'confirmado', 'en_preparacion', 'enviado', 'entregado', 'cancelado', 'devuelto'
);

CREATE TABLE pedidos (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    folio           VARCHAR(20) NOT NULL UNIQUE,
    id_cliente      INT UNSIGNED NOT NULL,
    id_vendedor     INT UNSIGNED NOT NULL,
    id_sucursal     INT UNSIGNED NOT NULL,
    id_estado       INT UNSIGNED NOT NULL,
    fecha_pedido    DATE NOT NULL,
    fecha_entrega   DATE,
    fecha_cancelacion DATE,
    subtotal        DECIMAL(14,2) NOT NULL DEFAULT 0,
    descuento       DECIMAL(14,2) NOT NULL DEFAULT 0,
    impuesto        DECIMAL(14,2) NOT NULL DEFAULT 0,
    total           DECIMAL(14,2) NOT NULL DEFAULT 0,
    notas           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_cliente)  REFERENCES clientes(id),
    FOREIGN KEY (id_vendedor) REFERENCES vendedores(id),
    FOREIGN KEY (id_sucursal) REFERENCES sucursales(id),
    FOREIGN KEY (id_estado)   REFERENCES estados_pedido(id),
    INDEX idx_fecha_pedido (fecha_pedido),
    INDEX idx_cliente      (id_cliente),
    INDEX idx_vendedor     (id_vendedor),
    INDEX idx_estado       (id_estado),
    INDEX idx_sucursal     (id_sucursal)
);

-- ============================================================
--  MÓDULO: INVENTARIO Y PRODUCTOS
-- ============================================================

CREATE TABLE categorias (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL,
    id_padre        INT UNSIGNED,      -- jerarquía: categoría padre
    activa          TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (id_padre) REFERENCES categorias(id)
);

CREATE TABLE proveedores (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    codigo          VARCHAR(20) NOT NULL UNIQUE,
    nombre          VARCHAR(200) NOT NULL,
    rfc             VARCHAR(20),
    email           VARCHAR(150),
    telefono        VARCHAR(20),
    ciudad          VARCHAR(100),
    pais            VARCHAR(100) NOT NULL DEFAULT 'México',
    plazo_entrega   INT UNSIGNED NOT NULL DEFAULT 7,  -- días promedio de entrega
    activo          TINYINT(1) NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE productos (
    id                  INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sku                 VARCHAR(50) NOT NULL UNIQUE,
    nombre              VARCHAR(200) NOT NULL,
    descripcion         TEXT,
    id_categoria        INT UNSIGNED NOT NULL,
    id_proveedor        INT UNSIGNED NOT NULL,
    precio_compra       DECIMAL(12,2) NOT NULL,
    precio_venta        DECIMAL(12,2) NOT NULL,
    stock_actual        INT NOT NULL DEFAULT 0,
    stock_minimo        INT NOT NULL DEFAULT 10,
    stock_maximo        INT NOT NULL DEFAULT 500,
    unidad_medida       VARCHAR(30) NOT NULL DEFAULT 'pieza',
    activo              TINYINT(1) NOT NULL DEFAULT 1,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_categoria) REFERENCES categorias(id),
    FOREIGN KEY (id_proveedor) REFERENCES proveedores(id),
    INDEX idx_categoria (id_categoria),
    INDEX idx_activo    (activo)
);

CREATE TABLE detalle_pedidos (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_pedido       INT UNSIGNED NOT NULL,
    id_producto     INT UNSIGNED NOT NULL,
    cantidad        INT UNSIGNED NOT NULL,
    precio_unitario DECIMAL(12,2) NOT NULL,
    descuento_pct   DECIMAL(5,2) NOT NULL DEFAULT 0,
    subtotal        DECIMAL(14,2) NOT NULL,
    FOREIGN KEY (id_pedido)   REFERENCES pedidos(id),
    FOREIGN KEY (id_producto) REFERENCES productos(id),
    INDEX idx_pedido   (id_pedido),
    INDEX idx_producto (id_producto)
);

CREATE TABLE tipos_movimiento (
    id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE
    -- 'entrada_compra', 'salida_venta', 'ajuste_positivo', 'ajuste_negativo', 'devolucion'
);

CREATE TABLE movimientos_inventario (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_producto     INT UNSIGNED NOT NULL,
    id_sucursal     INT UNSIGNED NOT NULL,
    id_tipo         INT UNSIGNED NOT NULL,
    id_pedido       INT UNSIGNED,      -- referencia si viene de venta
    cantidad        INT NOT NULL,      -- positivo = entrada, negativo = salida
    stock_anterior  INT NOT NULL,
    stock_nuevo     INT NOT NULL,
    costo_unitario  DECIMAL(12,2),
    fecha           DATETIME NOT NULL,
    referencia      VARCHAR(100),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_producto) REFERENCES productos(id),
    FOREIGN KEY (id_sucursal) REFERENCES sucursales(id),
    FOREIGN KEY (id_tipo)     REFERENCES tipos_movimiento(id),
    FOREIGN KEY (id_pedido)   REFERENCES pedidos(id),
    INDEX idx_producto  (id_producto),
    INDEX idx_fecha     (fecha),
    INDEX idx_sucursal  (id_sucursal)
);

-- ============================================================
--  MÓDULO: FINANCIERO
-- ============================================================

CREATE TABLE metodos_pago (
    id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE
    -- 'transferencia', 'tarjeta_credito', 'tarjeta_debito', 'efectivo', 'cheque', 'credito_30', 'credito_60'
);

CREATE TABLE pagos (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    id_pedido       INT UNSIGNED NOT NULL,
    id_metodo       INT UNSIGNED NOT NULL,
    monto           DECIMAL(14,2) NOT NULL,
    fecha_pago      DATE NOT NULL,
    referencia      VARCHAR(100),
    confirmado      TINYINT(1) NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_pedido) REFERENCES pedidos(id),
    FOREIGN KEY (id_metodo) REFERENCES metodos_pago(id),
    INDEX idx_pedido     (id_pedido),
    INDEX idx_fecha_pago (fecha_pago)
);

CREATE TABLE facturas (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    folio_fiscal    VARCHAR(50) NOT NULL UNIQUE,
    id_pedido       INT UNSIGNED NOT NULL UNIQUE,
    id_cliente      INT UNSIGNED NOT NULL,
    fecha_emision   DATE NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    subtotal        DECIMAL(14,2) NOT NULL,
    iva             DECIMAL(14,2) NOT NULL,
    total           DECIMAL(14,2) NOT NULL,
    pagada          TINYINT(1) NOT NULL DEFAULT 0,
    cancelada       TINYINT(1) NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (id_pedido)  REFERENCES pedidos(id),
    FOREIGN KEY (id_cliente) REFERENCES clientes(id),
    INDEX idx_cliente       (id_cliente),
    INDEX idx_fecha_emision (fecha_emision),
    INDEX idx_pagada        (pagada)
);

-- ============================================================
--  DATOS CATÁLOGO BASE (estáticos)
-- ============================================================

INSERT INTO estados_pedido (nombre) VALUES
    ('pendiente'), ('confirmado'), ('en_preparacion'),
    ('enviado'), ('entregado'), ('cancelado'), ('devuelto');

INSERT INTO tipos_movimiento (nombre) VALUES
    ('entrada_compra'), ('salida_venta'),
    ('ajuste_positivo'), ('ajuste_negativo'), ('devolucion');

INSERT INTO metodos_pago (nombre) VALUES
    ('transferencia'), ('tarjeta_credito'), ('tarjeta_debito'),
    ('efectivo'), ('cheque'), ('credito_30'), ('credito_60');

INSERT INTO segmentos_cliente (nombre) VALUES
    ('Retail'), ('Corporativo'), ('Gobierno'), ('Mayorista'), ('Startup');

-- ============================================================
--  USUARIO DE SOLO LECTURA PARA EL AGENTE IA
--  (ejecutar como root después de crear las vistas)
-- ============================================================
-- CREATE USER 'agente_erp'@'localhost' IDENTIFIED BY 'AgenteERP_2026!';
-- GRANT SELECT ON erp_demo.vw_* TO 'agente_erp'@'localhost';
-- FLUSH PRIVILEGES;
