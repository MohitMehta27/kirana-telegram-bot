-- =============================================================================
-- Seed data for demo / review
-- Run AFTER 001_schema.sql
-- mysql -u root -p t_bot < sql/002_seed.sql
-- =============================================================================

SET NAMES utf8mb4;

-- Clear seedable tables (safe for fresh DB)
DELETE FROM bill_items;
DELETE FROM bills;
DELETE FROM stock_ledger;
DELETE FROM khata_ledger;
DELETE FROM daily_close;
DELETE FROM inbound_documents;
DELETE FROM conversation_messages;
DELETE FROM preferences;
DELETE FROM customers;
DELETE FROM products;

-- -----------------------------------------------------------------------------
-- Products (real Indian kirana SKUs)
-- -----------------------------------------------------------------------------
INSERT INTO products
  (sku_name, unit, is_loose, hsn_code, gst_rate, cost_price, sell_price, quantity_on_hand, reorder_level)
VALUES
  ('Aashirvaad Atta 5kg',        'packet', 0, '1101',  5.00, 220.00, 255.00, 40.000, 10.000),
  ('Loose Atta',                 'kg',     1, '1101',  0.00,  38.00,  45.00, 80.000, 20.000),
  ('Tata Salt 1kg',              'packet', 0, '2501',  5.00,  22.00,  28.00, 60.000, 15.000),
  ('Amul Butter 100g',           'packet', 0, '0405', 12.00,  52.00,  62.00, 30.000,  8.000),
  ('Fortune Sunflower Oil 1L',   'litre',  0, '1512',  5.00, 125.00, 145.00, 35.000, 10.000),
  ('Maggi 70g',                  'packet', 0, '1902', 12.00,  12.00,  14.00,100.000, 20.000),
  ('Parle-G Gold 1kg',           'packet', 0, '1905',  5.00,  95.00, 110.00, 25.000,  8.000),
  ('Surf Excel 1kg',             'packet', 0, '3402', 18.00, 140.00, 165.00, 20.000,  5.000),
  ('Loose Sugar',                'kg',     1, '1701',  0.00,  40.00,  46.00, 70.000, 15.000),
  ('Loose Rice',                 'kg',     1, '1006',  0.00,  48.00,  55.00, 90.000, 20.000),
  ('Loose Toor Dal',             'kg',     1, '0713',  0.00, 110.00, 125.00, 40.000, 10.000);

-- Initial stock ledger (receive opening stock)
INSERT INTO stock_ledger (product_id, change_qty, reason, ref_type, note)
SELECT id, quantity_on_hand, 'receive', 'manual', 'Opening stock'
FROM products;

-- Demo khata customer
INSERT INTO customers (name, phone, khata_balance)
VALUES ('Ramesh', '9876543210', 0.00);

-- Default shop preferences for chat_id = 0 (global template).
-- On first message from a real chat, app copies/overrides with real chat_id.
-- You can also set these in Telegram: "my shop name is ... GSTIN is ..."
INSERT INTO preferences (chat_id, pref_key, pref_value) VALUES
  (0, 'shop_name',            'BigMantra Kirana'),
  (0, 'shop_gstin',           '29ABCDE1234F1Z5'),
  (0, 'shop_address',         'MG Road, Bengaluru, Karnataka'),
  (0, 'shop_phone',           '080-12345678'),
  (0, 'shop_state',           'KA'),
  (0, 'default_payment_mode', 'upi'),
  (0, 'default_atta',         'Aashirvaad Atta 5kg');
