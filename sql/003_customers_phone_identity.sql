-- =============================================================================
-- Migration 003 — customers: phone becomes the unique identity
-- Names may now repeat (two different "Ramesh"); the phone number uniquely
-- identifies a credit customer. MySQL allows multiple NULL phones, so
-- anonymous / phone-less rows are still permitted.
--
-- Run once on an existing DB:
--   mysql -u root -p t_bot < sql/003_customers_phone_identity.sql
--
-- If you have real duplicate phone numbers already, deduplicate them first,
-- otherwise the UNIQUE index creation will fail.
-- =============================================================================

-- Drop old unique-on-name and the plain phone index (ignore errors if already gone).
ALTER TABLE customers DROP INDEX uq_customers_name;
ALTER TABLE customers DROP INDEX idx_customers_phone;

-- Phone is now the unique identity; name gets a normal (non-unique) index.
ALTER TABLE customers ADD UNIQUE KEY uq_customers_phone (phone);
ALTER TABLE customers ADD INDEX idx_customers_name (name);
