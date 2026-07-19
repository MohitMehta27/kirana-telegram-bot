# KiranaPilot — Conversational Ops Agent for an Indian Kirana Store

A Telegram bot that lets a shopkeeper run the store in plain English/Hinglish (text **or** voice): receive stock, create products, cut GST bills, manage khata (credit), and get PDF invoices, PPTX analysis decks, and scheduled daily/weekly reports.

## The harness I picked — and why

- **Interface: Telegram (`python-telegram-bot`, polling).** The owner already lives in a messaging app; no new UI to learn. Polling means no public HTTPS/webhook setup — it runs anywhere (laptop, Railway) with just a bot token.
- **Server: FastAPI + Uvicorn.** Gives a `/health` endpoint for the platform and a clean async lifespan to own the bot's start/stop. Telegram is started as a **background task** so `/health` answers instantly (this is what made Railway deploys pass).
- **Brain: Google Gemini function-calling.** I switched from Claude to Gemini purely for free API access. The model is *not* hardcoded — it's the orchestrator that decides which tools to call.
- **Data: MySQL 8 + SQLAlchemy 2.0 ORM.** Money and stock need ACID transactions, row locks, and unique constraints — a relational DB is the right tool, not a document store.
- **Docs/media:** `reportlab` (PDF invoices), `python-pptx` + `matplotlib` (analysis decks), Gemini audio for STT, `pdfplumber`/`pypdf`/`python-docx` + Gemini vision for document ingest.
- **Scheduling:** APScheduler via PTB's `job_queue` — one process, no extra infra.

## How the control loop works

1. A Telegram update (text / voice / document / photo) hits a handler. Each `update_id` is checked against a `processed_updates` table for **idempotency** (Telegram re-delivers on timeout).
2. Voice → STT transcript; documents/images → extracted text. The result becomes the user turn.
3. Work runs in a **worker thread** (`asyncio.to_thread`) so the event loop stays free and concurrent chats stay snappy. Each turn gets its own DB session.
4. The agent runs an **observe → reason → act loop** (`runner.py`): the last N messages (configurable short-term memory) + durable preferences + the current IST date are sent to Gemini with the tool schemas. Gemini either calls tools or replies. Tool results are appended and the loop repeats (capped at 12 steps) until it produces a final message.
5. Generated files (PDF/PPTX) are streamed back to the chat; the user turn and reply are persisted for memory.

## Skill / tool design

24 typed tools grouped by domain, each a thin wrapper over a **service** that holds the real logic (tools stay dumb, services are testable):

- **Inventory:** `find_product`, `check_stock`, `list_inventory`, `low_stock_report`, `receive_stock`, `create_product`
- **Billing:** `start_bill`, `add_bill_item`, `remove_bill_item`, `update_bill_item_qty`, `set_bill_payment`, `get_bill_summary`, `finalize_bill`
- **Khata (credit):** `find_or_create_customer`, `get_khata_balance`, `charge_khata`, `settle_khata`
- **Memory/prefs:** `get_preferences`, `set_preference`, `clear_preference`
- **Analytics/artifacts:** `today_sales_summary`, `close_day`, `generate_invoice_pdf`, `generate_analysis_deck`

Business rules (oversell, below-cost, khata-needs-a-customer, idempotent finalize) live **inside the tools**, so the LLM can't violate them even if it "wants" to — the model relays tool errors instead.

## How I solved each hard part

- **Money must never be wrong.** GST is computed per line as CGST+SGST with `Decimal` + `ROUND_HALF_UP`. Bill totals are recomputed from the DB after every change. Two subtle bugs were fixed: (1) LLMs mis-transcribe numbers — so tools return a deterministic `summary_text` the model echoes verbatim; (2) the session is `autoflush=False`, so I `flush()` pending add/deletes before recomputing totals, otherwise totals lagged one step.
- **No double-billing / double-stock.** `processed_updates` dedupes Telegram retries; `finalize_bill` uses an idempotency key + `SELECT ... FOR UPDATE` row locks and re-checks stock before decrementing.
- **Correct dates.** The model doesn't know "today", so it guessed the wrong year for weekly decks. Fixed by injecting the current IST date into the prompt and defaulting relative ranges server-side.
- **Snappy under load.** Agent work is offloaded to threads; the event loop only does I/O.
- **Reliable deploys.** `/health` is decoupled from bot startup; the container uses an inline `sh -c` CMD to expand `$PORT` (avoids CRLF/script issues on Windows→Linux).
- **Memory that survives resets.** Durable shop settings and report schedules live in a `preferences` table (independent of chat history); `/new` clears the draft + short-term memory but keeps preferences.

## Run it

```bash
pip install -r requirements.txt
# configure .env (DB_*, TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, ...)
mysql -u root -p <db> < sql/001_schema.sql
mysql -u root -p <db> < sql/002_seed.sql
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then message the bot: *"50 packets of Maggi came in, cost 12 mrp 14"*, *"bill 2kg sugar and 4 Maggi"*, *"send bill as PDF"*, *"this week's sales analysis"*, *"send daily report at 9pm"*.






Telgram bot handle= @kirana_manager_bot