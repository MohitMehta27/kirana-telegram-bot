# How to import the database

## Option A — Python (recommended on Windows)

```bash
pip install pymysql python-dotenv
python scripts/apply_schema.py
```

This creates `DB_NAME` if missing, then runs `sql/001_schema.sql` + `sql/002_seed.sql`.

## Option B — MySQL Workbench / phpMyAdmin

1. Create database `t_bot` (utf8mb4).
2. Open and execute `sql/001_schema.sql`.
3. Open and execute `sql/002_seed.sql`.

## Option C — mysql CLI

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS t_bot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p t_bot < sql/001_schema.sql
mysql -u root -p t_bot < sql/002_seed.sql
```

## Tables created

| Table | Purpose |
|---|---|
| `products` | SKUs, GST, stock |
| `stock_ledger` | Stock audit trail |
| `customers` | Khata customers |
| `khata_ledger` | Credit audit trail |
| `bills` / `bill_items` | Draft + finalized invoices |
| `preferences` | Durable owner memory |
| `processed_updates` | Telegram idempotency |
| `daily_close` | Day-end snapshot |
| `inbound_documents` | Voice/PDF audit |
| `conversation_messages` | Chat context (cleared on /new) |
