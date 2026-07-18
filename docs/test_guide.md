# Test Guide — Supermarket Ops Agent

Bot: **@Kirana_manager_bot** · Harness: **Gemini + custom tool loop** · DB: **MySQL `t_bot`**

This guide covers **every tool and every assignment scenario**. Send the messages in Telegram exactly (or in your words — the agent reasons, it's not keyword-matched). Watch `logs/<date>.log` for `tool_call` / `tool_result` lines.

---

## 0. Start the bot

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Wait for `telegram_polling_started`. Only run ONE instance (one port, one poller).

Reset demo data anytime:
```bash
python scripts/apply_schema.py
```

---

## 1. Tool coverage map

| Skill | Tool | Covered in test |
|---|---|---|
| Inventory | `find_product` | §3, §4 |
| Inventory | `check_stock` | §5 |
| Inventory | `low_stock_report` | §6 |
| Inventory | `receive_stock` | §3 |
| Inventory | `create_product` | §4 |
| Billing | `start_bill` | §7 |
| Billing | `add_bill_item` | §7 |
| Billing | `remove_bill_item` | §8 |
| Billing | `update_bill_item_qty` | §8 |
| Billing | `set_bill_payment` | §7 |
| Billing | `get_bill_summary` | §7 |
| Billing | `finalize_bill` | §7 |
| Khata | `find_or_create_customer` | §10 |
| Khata | `charge_khata` | §10 |
| Khata | `settle_khata` | §10 |
| Khata | `get_khata_balance` | §10 |
| Analytics | `today_sales_summary` | §11 |
| Analytics | `close_day` | §11 |
| Documents | `generate_invoice_pdf` | §12 |
| Documents | `generate_analysis_deck` | §13 |
| Preferences | `set_preference` | §14 |
| Preferences | `get_preferences` | §14 |
| Voice (input) | STT → agent | §15 |
| Docs (input) | extract → classify → agent | §16 |

---

## 2. Sanity check

Send: `hi`
Expect: short shopkeeper-style greeting, no invented data.

---

## 3. Receive stock  (`receive_stock`, `find_product`)

Send:
```
50 packets of Maggi came in, cost ₹12, MRP ₹14
```
Expect: confirms new Maggi qty (was 100 → 150), updates cost/MRP.

Verify:
```
how much Maggi is left?
```

---

## 4. Add a new product  (`create_product`)

Send:
```
new item: Amul Cheese 200g, GST 12%, MRP ₹130, cost ₹110, packet, reorder 5
```
Expect: created with HSN/GST, then can be sold/queried.

Ambiguity test (must ask, not guess):
```
add atta
```
Expect: agent asks **"Aashirvaad 5kg or loose atta?"** (model-generated clarifying question).

---

## 5. Stock query  (`check_stock`)

```
how much sugar is left?
```
Expect: current loose sugar qty + unit (kg).

---

## 6. Low stock / reorder  (`low_stock_report`)

```
what's running out?
```
Expect: list of items at/below reorder level (e.g. Surf Excel if low).

---

## 7. Cut a multi-item bill  (`start_bill`, `add_bill_item`, `set_bill_payment`, `get_bill_summary`, `finalize_bill`)

```
make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI
```
Expect: itemized draft with per-line GST, subtotal, CGST, SGST, grand total.

Then:
```
finalize it
```
Expect: bill finalized, invoice number assigned, stock decremented.

---

## 8. Edit a bill mid-build  (`update_bill_item_qty`, `remove_bill_item`)

Start a fresh bill:
```
start a bill: 1 Amul butter, 4 Maggi, cash
```
Then before finalizing:
```
drop the butter, make it 6 Maggi
```
Expect: butter removed, Maggi qty → 6, totals recomputed. Stock only changes on finalize.

---

## 9. Oversell guard (hard part)

Check a stock level first, then try to exceed it:
```
sell me 500 Amul butter
```
Expect: **refused** — "only N in stock". This refusal comes from `finalize_bill`/`add_bill_item` at the tool layer, not the prompt.

Below-cost guard:
```
sell 1 Maggi at ₹5
```
Expect: agent won't sell below cost (prices come from DB; it uses sell_price, and finalize refuses below-cost).

---

## 10. Khata / credit cycle  (`find_or_create_customer`, `charge_khata`, `get_khata_balance`, `settle_khata`)

```
put ₹500 on Ramesh's credit
```
```
Ramesh's balance?
```
Expect: ₹500.
```
Ramesh paid ₹300
```
```
Ramesh's balance?
```
Expect: ₹200.

Guardrail — settle a khata that doesn't exist:
```
Suresh paid ₹100
```
Expect: refuses / says no khata for Suresh (no negative or phantom ledger).

Khata sale (bill on credit):
```
bill for Ramesh: 2 Maggi on khata
```
Expect: finalize adds to Ramesh's balance in the same transaction.

---

## 11. Daily close / today's sales  (`today_sales_summary`, `close_day`)

```
today's sales?
```
Expect: total, GST collected, cash vs UPI vs card vs khata, top items.

```
close the day
```
Expect: snapshot saved. Run again → returns existing close (idempotent, no double count).

---

## 12. PDF invoice  (`generate_invoice_pdf`)

After finalizing a bill (§7):
```
send me that bill as a PDF
```
Expect: a **PDF document** arrives in chat with shop name/GSTIN, HSN, qty, rate, CGST/SGST columns, rounded grand total, payment mode.

Check: totals on PDF match the chat summary; CGST+SGST+subtotal = grand total.

---

## 13. Analysis deck  (`generate_analysis_deck`)

```
make this week's sales analysis deck
```
Expect: a **.pptx** with title, KPI slide, and **real charts** (sales trend, top items, payment split, stock health) + takeaways. Charts come from actual DB data.

---

## 14. Preferences + memory across `/new`  (`set_preference`, `get_preferences`)

```
always assume UPI unless I say cash
```
```
default atta = Aashirvaad 5kg
```
Then:
```
/new
```
Then (new session):
```
make a bill: 2 Maggi
```
Expect: payment defaults to **UPI** without asking; "atta" resolves to Aashirvaad 5kg — proving preferences survived `/new`.

Also set shop identity (shows on invoice):
```
my shop is BigMantra Kirana, GSTIN 29ABCDE1234F1Z5
```

---

## 15. Voice-note order (input)  (STT → agent)

Send a **voice note** saying something like:
> "fifty Maggi came in, cost twelve rupees, MRP fourteen"

Expect: bot replies `🎙️ Heard: …` then acts (receive stock / bill) just like text.

Hindi/Hinglish also works:
> "do packet Amul butter bill me daal do, UPI"

---

## 16. Document / photo ingest (input)  (extract → classify → agent)

Send a **PDF or photo** of a supplier invoice or a grocery list, with caption:
```
add this stock
```
Expect: bot extracts line items, tells you what it understood, and **asks to confirm** before receiving stock. Confirm with `yes` → stock updated.

Try an unrelated doc (e.g. a random PDF):
Expect: it classifies as unknown and asks what to do — no silent DB writes.

---

## 17. Hard-parts verification (for README)

| Hard part | How to prove it |
|---|---|
| Grounding | Ask for a product you never seeded → it says not found, doesn't invent |
| Oversell | §9 |
| GST correctness | §12 — CGST=SGST=rate/2, sums to total |
| Multi-turn bill | §8 — edits before finalize; stock only drops on finalize |
| Idempotency | Send "finalize" twice fast → one bill, stock decremented once (`bills.idempotency_key`) |
| Concurrency | Two bills on same product in parallel → no negative/corrupt stock (`SELECT … FOR UPDATE`) |
| Guardrails | §9 below-cost, §10 phantom khata |
| Real artifacts | §12 PDF, §13 PPTX |
| Memory | §14 `/new` keeps preferences |

Idempotency quick test:
1. Build + set payment on a bill.
2. Send `finalize` and immediately send `finalize` again.
3. Stock for those items drops once; second finalize returns the same invoice.

---

## 18. Full demo sequence (matches assignment §6 recording)

1. `50 packets of Maggi came in, cost ₹12, MRP ₹14`
2. `make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI`
3. `drop the butter, make it 6 Maggi`
4. `sell me 500 Amul butter` (oversell refusal)
5. `finalize it`
6. `put ₹500 on Ramesh's credit` → `Ramesh's balance?` → `Ramesh paid ₹300`
7. `send me that bill as a PDF`
8. `make this week's sales analysis deck`
9. `always assume UPI` → `/new` → `make a bill: 2 Maggi` (UPI auto)

---

## 19. Where to look when something fails

- Terminal + `logs/<date>.log`: `msg_text`, `tool_call name=…`, `tool_result ok=…`, `agent_done`
- `inbound_documents` table: voice/PDF audit
- `generated/`: produced PDFs and PPTX files
- Health: http://127.0.0.1:8000/health
