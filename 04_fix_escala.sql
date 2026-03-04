-- ============================================================
--  04_fix_escala.sql
--  Ajusta la escala monetaria a valores realistas para el demo.
--
--  Problema original:
--    - Pedido promedio: 230,000 MXN  →  objetivo: ~11,500 MXN  (÷20)
--    - Meta vendedor:   490,000 MXN  →  objetivo: ~70,000 MXN  (÷7)
--    - Vista pct_meta comparaba total acumulado vs meta mensual → 13,000%
--
--  Ejecutar UNA SOLA VEZ sobre erp_demo.
-- ============================================================

USE erp_demo;

-- 1. Pedidos (subtotal, descuento, impuesto, total)
UPDATE pedidos SET
    subtotal  = ROUND(subtotal  / 20, 2),
    descuento = ROUND(descuento / 20, 2),
    impuesto  = ROUND(impuesto  / 20, 2),
    total     = ROUND(total     / 20, 2);

-- 2. Detalle de pedidos (precio unitario y subtotal de línea)
UPDATE detalle_pedidos SET
    precio_unitario = ROUND(precio_unitario / 20, 2),
    subtotal        = ROUND(subtotal        / 20, 2);

-- 3. Facturas
UPDATE facturas SET
    subtotal = ROUND(subtotal / 20, 2),
    iva      = ROUND(iva      / 20, 2),
    total    = ROUND(total    / 20, 2);

-- 4. Pagos
UPDATE pagos SET
    monto = ROUND(monto / 20, 2);

-- 5. Precios de productos
UPDATE productos SET
    precio_compra = ROUND(precio_compra / 20, 2),
    precio_venta  = ROUND(precio_venta  / 20, 2);

-- 6. Meta mensual de vendedores
UPDATE vendedores SET
    meta_mensual = ROUND(meta_mensual / 7, 2);

-- Verificar resultado
SELECT
    'pedidos'     AS tabla,
    ROUND(MIN(total),0)  AS min_monto,
    ROUND(AVG(total),0)  AS avg_monto,
    ROUND(MAX(total),0)  AS max_monto
FROM pedidos
UNION ALL
SELECT
    'vendedores meta',
    ROUND(MIN(meta_mensual),0),
    ROUND(AVG(meta_mensual),0),
    ROUND(MAX(meta_mensual),0)
FROM vendedores
UNION ALL
SELECT
    'productos precio_venta',
    ROUND(MIN(precio_venta),0),
    ROUND(AVG(precio_venta),0),
    ROUND(MAX(precio_venta),0)
FROM productos;
