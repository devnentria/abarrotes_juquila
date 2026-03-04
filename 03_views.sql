-- ============================================================
--  ERP DEMO — Vistas para el Agente IA
--  Archivo: 03_views.sql
--  Descripción: Vistas de solo lectura que expone el ERP
--               al agente. Nunca accede a tablas directas.
-- ============================================================

USE erp_demo;

-- ============================================================
--  1. VENTAS MENSUALES
--     Resumen de ventas agrupado por mes y año
-- ============================================================
CREATE OR REPLACE VIEW vw_ventas_mensuales AS
SELECT
    YEAR(p.fecha_pedido)                        AS anio,
    MONTH(p.fecha_pedido)                       AS mes,
    DATE_FORMAT(p.fecha_pedido, '%Y-%m')        AS periodo,
    COUNT(p.id)                                 AS total_pedidos,
    SUM(p.total)                                AS ventas_totales,
    SUM(p.descuento)                            AS descuentos_totales,
    SUM(p.impuesto)                             AS impuestos_totales,
    ROUND(AVG(p.total), 2)                      AS ticket_promedio,
    COUNT(DISTINCT p.id_cliente)                AS clientes_unicos,
    COUNT(DISTINCT p.id_vendedor)               AS vendedores_activos
FROM pedidos p
JOIN estados_pedido ep ON p.id_estado = ep.id
WHERE ep.nombre NOT IN ('cancelado', 'devuelto')
GROUP BY YEAR(p.fecha_pedido), MONTH(p.fecha_pedido)
ORDER BY anio DESC, mes DESC;


-- ============================================================
--  2. VENTAS POR REGIÓN Y SUCURSAL
-- ============================================================
CREATE OR REPLACE VIEW vw_ventas_por_region AS
SELECT
    s.region,
    s.ciudad,
    s.nombre                                    AS sucursal,
    YEAR(p.fecha_pedido)                        AS anio,
    MONTH(p.fecha_pedido)                       AS mes,
    COUNT(p.id)                                 AS total_pedidos,
    SUM(p.total)                                AS ventas_totales,
    ROUND(AVG(p.total), 2)                      AS ticket_promedio,
    COUNT(DISTINCT p.id_cliente)                AS clientes_unicos
FROM pedidos p
JOIN sucursales s     ON p.id_sucursal = s.id
JOIN estados_pedido ep ON p.id_estado  = ep.id
WHERE ep.nombre NOT IN ('cancelado', 'devuelto')
GROUP BY s.id, YEAR(p.fecha_pedido), MONTH(p.fecha_pedido)
ORDER BY anio DESC, mes DESC, ventas_totales DESC;


-- ============================================================
--  3. RENDIMIENTO DE VENDEDORES
-- ============================================================
CREATE OR REPLACE VIEW vw_rendimiento_vendedores AS
SELECT
    v.id                                        AS id_vendedor,
    CONCAT(e.nombre, ' ', e.apellido_paterno)   AS vendedor,
    e.cargo,
    v.zona,
    s.nombre                                    AS sucursal,
    v.meta_mensual,
    v.comision_pct,
    COUNT(p.id)                                 AS total_pedidos,
    SUM(p.total)                                AS ventas_totales,
    ROUND(AVG(p.total), 2)                      AS ticket_promedio,
    COUNT(DISTINCT p.id_cliente)                AS clientes_atendidos,
    ROUND(SUM(p.total) * v.comision_pct / 100, 2) AS comision_generada,
    ROUND(
        (SUM(p.total) / GREATEST(TIMESTAMPDIFF(MONTH, MIN(p.fecha_pedido), CURDATE()), 1))
        / NULLIF(v.meta_mensual, 0) * 100, 1
    ) AS pct_meta_mensual
FROM vendedores v
JOIN empleados e       ON v.id_empleado  = e.id
JOIN sucursales s      ON e.id_sucursal  = s.id
LEFT JOIN pedidos p    ON p.id_vendedor  = v.id
LEFT JOIN estados_pedido ep ON p.id_estado = ep.id
    AND ep.nombre NOT IN ('cancelado', 'devuelto')
WHERE v.activo = 1
GROUP BY v.id
ORDER BY ventas_totales DESC;


-- ============================================================
--  4. CLIENTES TOP (por facturación total)
-- ============================================================
CREATE OR REPLACE VIEW vw_clientes_top AS
SELECT
    c.id                                        AS id_cliente,
    c.codigo,
    c.razon_social,
    c.ciudad,
    c.region,
    sc.nombre                                   AS segmento,
    COUNT(p.id)                                 AS total_pedidos,
    SUM(p.total)                                AS facturacion_total,
    ROUND(AVG(p.total), 2)                      AS ticket_promedio,
    MAX(p.fecha_pedido)                         AS ultimo_pedido,
    DATEDIFF(CURDATE(), MAX(p.fecha_pedido))    AS dias_desde_ultima_compra,
    c.limite_credito
FROM clientes c
JOIN segmentos_cliente sc  ON c.id_segmento = sc.id
LEFT JOIN pedidos p        ON p.id_cliente  = c.id
LEFT JOIN estados_pedido ep ON p.id_estado  = ep.id
    AND ep.nombre NOT IN ('cancelado', 'devuelto')
WHERE c.activo = 1
GROUP BY c.id
ORDER BY facturacion_total DESC;


-- ============================================================
--  5. EMPLEADOS ACTIVOS
-- ============================================================
CREATE OR REPLACE VIEW vw_empleados_activos AS
SELECT
    e.id,
    e.numero_empleado,
    CONCAT(e.nombre, ' ', e.apellido_paterno, ' ', COALESCE(e.apellido_materno,'')) AS nombre_completo,
    e.cargo,
    e.nivel,
    e.tipo_contrato,
    e.salario_mensual,
    d.nombre                                    AS departamento,
    s.nombre                                    AS sucursal,
    s.ciudad,
    s.region,
    e.fecha_ingreso,
    TIMESTAMPDIFF(YEAR, e.fecha_ingreso, CURDATE())  AS anos_en_empresa,
    TIMESTAMPDIFF(MONTH, e.fecha_ingreso, CURDATE()) AS meses_en_empresa
FROM empleados e
JOIN departamentos d ON e.id_departamento = d.id
JOIN sucursales s    ON e.id_sucursal     = s.id
WHERE e.activo = 1
ORDER BY e.fecha_ingreso;


-- ============================================================
--  6. EMPLEADOS POR ANTIGÜEDAD
-- ============================================================
CREATE OR REPLACE VIEW vw_empleados_antiguedad AS
SELECT
    e.numero_empleado,
    CONCAT(e.nombre, ' ', e.apellido_paterno)   AS nombre,
    e.cargo,
    e.nivel,
    d.nombre                                    AS departamento,
    s.nombre                                    AS sucursal,
    e.fecha_ingreso,
    TIMESTAMPDIFF(YEAR,  e.fecha_ingreso, CURDATE()) AS anos,
    TIMESTAMPDIFF(MONTH, e.fecha_ingreso, CURDATE()) AS meses_total,
    e.salario_mensual,
    e.tipo_contrato
FROM empleados e
JOIN departamentos d ON e.id_departamento = d.id
JOIN sucursales s    ON e.id_sucursal     = s.id
WHERE e.activo = 1
ORDER BY anos DESC, meses_total DESC;


-- ============================================================
--  7. ESTADO DEL INVENTARIO
-- ============================================================
CREATE OR REPLACE VIEW vw_inventario_estado AS
SELECT
    p.id                                        AS id_producto,
    p.sku,
    p.nombre                                    AS producto,
    cat.nombre                                  AS categoria,
    prov.nombre                                 AS proveedor,
    p.stock_actual,
    p.stock_minimo,
    p.stock_maximo,
    p.precio_compra,
    p.precio_venta,
    ROUND(p.precio_venta - p.precio_compra, 2)  AS margen_unitario,
    ROUND((p.precio_venta - p.precio_compra) / NULLIF(p.precio_compra,0) * 100, 1) AS margen_pct,
    ROUND(p.stock_actual * p.precio_compra, 2)  AS valor_inventario,
    CASE
        WHEN p.stock_actual = 0              THEN 'sin_stock'
        WHEN p.stock_actual <= p.stock_minimo THEN 'critico'
        WHEN p.stock_actual >= p.stock_maximo THEN 'sobrestock'
        ELSE 'normal'
    END                                         AS estado_stock
FROM productos p
JOIN categorias cat   ON p.id_categoria = cat.id
JOIN proveedores prov ON p.id_proveedor = prov.id
WHERE p.activo = 1
ORDER BY estado_stock, p.stock_actual;


-- ============================================================
--  8. PEDIDOS PENDIENTES
-- ============================================================
CREATE OR REPLACE VIEW vw_pedidos_pendientes AS
SELECT
    p.id,
    p.folio,
    c.razon_social                              AS cliente,
    c.region,
    CONCAT(e.nombre, ' ', e.apellido_paterno)   AS vendedor,
    s.nombre                                    AS sucursal,
    ep.nombre                                   AS estado,
    p.fecha_pedido,
    DATEDIFF(CURDATE(), p.fecha_pedido)         AS dias_en_espera,
    p.total,
    COUNT(dp.id)                                AS lineas_pedido,
    SUM(dp.cantidad)                            AS unidades_totales
FROM pedidos p
JOIN clientes c        ON p.id_cliente  = c.id
JOIN vendedores v      ON p.id_vendedor = v.id
JOIN empleados e       ON v.id_empleado = e.id
JOIN sucursales s      ON p.id_sucursal = s.id
JOIN estados_pedido ep ON p.id_estado   = ep.id
JOIN detalle_pedidos dp ON dp.id_pedido = p.id
WHERE ep.nombre IN ('pendiente', 'confirmado', 'en_preparacion', 'enviado')
GROUP BY p.id
ORDER BY dias_en_espera DESC;


-- ============================================================
--  9. PRODUCTOS MÁS VENDIDOS
-- ============================================================
CREATE OR REPLACE VIEW vw_productos_mas_vendidos AS
SELECT
    pr.id                                       AS id_producto,
    pr.sku,
    pr.nombre                                   AS producto,
    cat.nombre                                  AS categoria,
    SUM(dp.cantidad)                            AS unidades_vendidas,
    COUNT(DISTINCT dp.id_pedido)                AS apariciones_en_pedidos,
    SUM(dp.subtotal)                            AS revenue_total,
    ROUND(AVG(dp.precio_unitario), 2)           AS precio_promedio_venta,
    pr.precio_venta                             AS precio_actual,
    pr.stock_actual
FROM detalle_pedidos dp
JOIN pedidos p         ON dp.id_pedido   = p.id
JOIN estados_pedido ep ON p.id_estado    = ep.id
JOIN productos pr      ON dp.id_producto = pr.id
JOIN categorias cat    ON pr.id_categoria = cat.id
WHERE ep.nombre NOT IN ('cancelado', 'devuelto')
GROUP BY pr.id
ORDER BY unidades_vendidas DESC;


-- ============================================================
--  10. PAGOS RECIENTES
-- ============================================================
CREATE OR REPLACE VIEW vw_pagos_recientes AS
SELECT
    pg.id                                       AS id_pago,
    p.folio                                     AS folio_pedido,
    c.razon_social                              AS cliente,
    c.region,
    mp.nombre                                   AS metodo_pago,
    pg.monto,
    pg.fecha_pago,
    DATEDIFF(CURDATE(), pg.fecha_pago)          AS dias_transcurridos,
    pg.referencia,
    pg.confirmado,
    f.folio_fiscal,
    f.pagada                                    AS factura_pagada
FROM pagos pg
JOIN pedidos p       ON pg.id_pedido = p.id
JOIN clientes c      ON p.id_cliente = c.id
JOIN metodos_pago mp ON pg.id_metodo = mp.id
LEFT JOIN facturas f ON f.id_pedido  = p.id
ORDER BY pg.fecha_pago DESC;


-- ============================================================
--  USUARIO DE SOLO LECTURA PARA EL AGENTE
--  Descomentar y ejecutar como root
-- ============================================================
-- DROP USER IF EXISTS 'agente_erp'@'localhost';
-- CREATE USER 'agente_erp'@'localhost' IDENTIFIED BY 'AgenteERP_Demo2026!';
-- GRANT SELECT ON erp_demo.vw_ventas_mensuales       TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_ventas_por_region      TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_rendimiento_vendedores TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_clientes_top           TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_empleados_activos      TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_empleados_antiguedad   TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_inventario_estado      TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_pedidos_pendientes     TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_productos_mas_vendidos TO 'agente_erp'@'localhost';
-- GRANT SELECT ON erp_demo.vw_pagos_recientes        TO 'agente_erp'@'localhost';
-- FLUSH PRIVILEGES;
