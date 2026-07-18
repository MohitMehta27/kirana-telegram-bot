-- =============================================================================
-- Supermarket Ops Agent — MySQL 8 schema
-- Database: t_bot (or whatever DB_NAME is in .env)
-- Run: mysql -u root -p t_bot < sql/001_schema.sql
-- Or import this file in MySQL Workbench / phpMyAdmin
-- =============================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';
SET FOREIGN_KEY_CHECKS = 0;

-- -----------------------------------------------------------------------------
-- products — SKU master + live stock
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS bill_items;
DROP TABLE IF EXISTS bills;
DROP TABLE IF EXISTS stock_ledger;
DROP TABLE IF EXISTS khata_ledger;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS preferences;
DROP TABLE IF EXISTS processed_updates;
DROP TABLE IF EXISTS daily_close;
DROP TABLE IF EXISTS inbound_documents;
DROP TABLE IF EXISTS conversation_messages;
DROP TABLE IF EXISTS products;

CREATE TABLE products (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  sku_name          VARCHAR(200)    NOT NULL,
  unit              ENUM('kg','g','litre','ml','packet','dozen','piece') NOT NULL,
  is_loose          TINYINT(1)      NOT NULL DEFAULT 0,
  hsn_code          VARCHAR(16)     NOT NULL,
  gst_rate          DECIMAL(5,2)    NOT NULL DEFAULT 0.00 COMMENT '0, 5, 12, 18, etc.',
  cost_price        DECIMAL(12,2)   NOT NULL,
  sell_price        DECIMAL(12,2)   NOT NULL COMMENT 'MRP / selling price',
  quantity_on_hand  DECIMAL(12,3)   NOT NULL DEFAULT 0.000,
  reorder_level     DECIMAL(12,3)   NOT NULL DEFAULT 0.000,
  version           INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT 'optimistic lock',
  is_active         TINYINT(1)      NOT NULL DEFAULT 1,
  created_at        DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at        DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_products_sku_name (sku_name),
  KEY idx_products_reorder (quantity_on_hand, reorder_level),
  KEY idx_products_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- stock_ledger — immutable stock movements
-- -----------------------------------------------------------------------------
CREATE TABLE stock_ledger (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id  BIGINT UNSIGNED NOT NULL,
  change_qty  DECIMAL(12,3)   NOT NULL COMMENT '+receive / -sale',
  reason      ENUM('receive','sale','adjustment') NOT NULL,
  ref_type    VARCHAR(32)     NULL COMMENT 'bill | inbound_doc | manual',
  ref_id      BIGINT UNSIGNED NULL,
  note        VARCHAR(255)    NULL,
  created_at  DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_stock_ledger_product (product_id, created_at),
  KEY idx_stock_ledger_ref (ref_type, ref_id),
  CONSTRAINT fk_stock_ledger_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- customers — khata parties
-- -----------------------------------------------------------------------------
CREATE TABLE customers (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name           VARCHAR(120)    NOT NULL,
  phone          VARCHAR(20)     NULL,
  khata_balance  DECIMAL(12,2)   NOT NULL DEFAULT 0.00 COMMENT '+ means customer owes shop',
  created_at     DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at     DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  -- Names may repeat (two "Ramesh"); phone is the unique identity.
  -- MySQL allows multiple NULL phones, so anonymous rows are still fine.
  UNIQUE KEY uq_customers_phone (phone),
  KEY idx_customers_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- khata_ledger — immutable credit movements
-- -----------------------------------------------------------------------------
CREATE TABLE khata_ledger (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  customer_id  BIGINT UNSIGNED NOT NULL,
  amount       DECIMAL(12,2)   NOT NULL COMMENT '+charge (sale on credit) / -payment (settlement)',
  note         VARCHAR(255)    NULL,
  ref_type     VARCHAR(32)     NULL COMMENT 'bill | manual',
  ref_id       BIGINT UNSIGNED NULL,
  created_at   DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_khata_customer (customer_id, created_at),
  KEY idx_khata_ref (ref_type, ref_id),
  CONSTRAINT fk_khata_customer
    FOREIGN KEY (customer_id) REFERENCES customers(id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- bills — draft / finalized invoices
-- -----------------------------------------------------------------------------
CREATE TABLE bills (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  chat_id          BIGINT          NOT NULL COMMENT 'Telegram chat id of owner',
  status           ENUM('draft','finalized','cancelled') NOT NULL DEFAULT 'draft',
  payment_mode     ENUM('cash','upi','card','khata') NULL,
  payment_ref      VARCHAR(120)    NULL,
  subtotal         DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  cgst_total       DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  sgst_total       DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  grand_total      DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  idempotency_key  VARCHAR(64)     NULL,
  customer_id      BIGINT UNSIGNED NULL COMMENT 'set when sold on khata',
  invoice_number   VARCHAR(32)     NULL COMMENT 'assigned on finalize',
  finalized_at     DATETIME(6)     NULL,
  created_at       DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at       DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_bills_idempotency (idempotency_key),
  UNIQUE KEY uq_bills_invoice_number (invoice_number),
  KEY idx_bills_chat_status (chat_id, status),
  KEY idx_bills_finalized (finalized_at),
  CONSTRAINT fk_bills_customer
    FOREIGN KEY (customer_id) REFERENCES customers(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- bill_items — line items (GST stored per line)
-- -----------------------------------------------------------------------------
CREATE TABLE bill_items (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  bill_id      BIGINT UNSIGNED NOT NULL,
  product_id   BIGINT UNSIGNED NOT NULL,
  qty          DECIMAL(12,3)   NOT NULL,
  unit_price   DECIMAL(12,2)   NOT NULL,
  gst_rate     DECIMAL(5,2)    NOT NULL,
  taxable_amt  DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  cgst_amount  DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  sgst_amount  DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  line_total   DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  created_at   DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_bill_product (bill_id, product_id),
  KEY idx_bill_items_product (product_id),
  CONSTRAINT fk_bill_items_bill
    FOREIGN KEY (bill_id) REFERENCES bills(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_bill_items_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- preferences — durable owner memory (survives /new)
-- -----------------------------------------------------------------------------
CREATE TABLE preferences (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  chat_id     BIGINT          NOT NULL,
  pref_key    VARCHAR(64)     NOT NULL,
  pref_value  TEXT            NOT NULL,
  updated_at  DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  created_at  DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_preferences_chat_key (chat_id, pref_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- processed_updates — Telegram update idempotency
-- -----------------------------------------------------------------------------
CREATE TABLE processed_updates (
  update_id           BIGINT       NOT NULL,
  chat_id             BIGINT       NULL,
  response_text       TEXT         NULL,
  response_file_path  VARCHAR(512) NULL,
  processed_at        DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (update_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- daily_close — end-of-day snapshots (idempotent per date)
-- -----------------------------------------------------------------------------
CREATE TABLE daily_close (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  close_date      DATE            NOT NULL,
  chat_id         BIGINT          NOT NULL,
  total_sales     DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  tax_collected   DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  cash_total      DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  upi_total       DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  card_total      DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  khata_total     DECIMAL(12,2)   NOT NULL DEFAULT 0.00,
  top_items       JSON            NULL,
  created_at      DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_daily_close_date_chat (close_date, chat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- inbound_documents — voice / PDF / photo audit trail
-- -----------------------------------------------------------------------------
CREATE TABLE inbound_documents (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  chat_id          BIGINT          NOT NULL,
  telegram_file_id VARCHAR(255)    NULL,
  file_name        VARCHAR(255)    NULL,
  mime_type        VARCHAR(128)    NULL,
  source_type      ENUM('pdf','docx','image','voice','other') NOT NULL DEFAULT 'other',
  doc_kind         ENUM(
                     'supplier_invoice',
                     'purchase_list',
                     'gst_return',
                     'bank_statement',
                     'unknown'
                   ) NULL,
  raw_extract      MEDIUMTEXT      NULL,
  structured_json  JSON            NULL,
  status           ENUM('received','extracted','applied','rejected') NOT NULL DEFAULT 'received',
  created_at       DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_inbound_chat (chat_id, created_at),
  KEY idx_inbound_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- conversation_messages — short rolling context (NOT preferences)
-- Cleared on /new; preferences are never stored here.
-- -----------------------------------------------------------------------------
CREATE TABLE conversation_messages (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  chat_id     BIGINT          NOT NULL,
  role        ENUM('user','assistant','tool') NOT NULL,
  content     MEDIUMTEXT      NOT NULL,
  created_at  DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY idx_conv_chat_created (chat_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- =============================================================================
-- Done. Next: run sql/002_seed.sql for demo SKUs + Ramesh + shop prefs
-- =============================================================================
