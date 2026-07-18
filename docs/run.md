# Run the bot (local)

## 1. One-time setup

```bash
pip install -r requirements.txt
python scripts/apply_schema.py
```

Fill `.env`: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `DB_*`, `TELEGRAM_MODE=polling`.

## 2. Start

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

With `TELEGRAM_MODE=polling` you do **not** need `PUBLIC_BASE_URL`.

## 3. Watch logs

- Terminal: live INFO logs  
- File: `logs/YYYY-MM-DD.log` (daily rotate, 30-day retention, PII redaction)

Look for:
- `startup ... telegram_mode=polling`
- `telegram_bot_ok @YourBot`
- `telegram_polling_started`
- `msg_text` / `tool_call` / `agent_done` when you chat

## 4. Test in Telegram

Message your bot:
- `/start`
- `how much Maggi is left?`
- `50 Maggi came in, cost 12, MRP 14`

## What works now vs later

| Feature | Status |
|---|---|
| Text chat → Gemini → tools | Yes |
| Stock / bill / khata / prefs | Yes |
| Oversell + finalize idempotency | Yes |
| Logging (console + daily files) | Yes |
| PDF invoice / PPTX deck | Not yet |
| Voice / document ingest | Not yet |
| Webhook deploy | Needs `PUBLIC_BASE_URL` later |

## If Telegram times out

Your PC cannot reach `api.telegram.org` (firewall/VPN/ISP). Try VPN, or another network. Health still works: http://127.0.0.1:8000/health
