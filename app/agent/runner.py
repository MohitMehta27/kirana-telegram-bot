"""Gemini observe → reason → act tool loop."""

from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.agent.tools.registry import (
    build_tool_declarations,
    dumps_tool_result,
    execute_tool,
)
from app.config import get_settings
from app.services import preference_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the ops agent for an Indian kirana / supermarket.
The owner talks in short shopkeeper English/Hinglish. Reply short and practical. Use ₹.

Rules:
- NEVER invent a product, price, GST rate, or stock number. Always call tools.
- MONEY/TOTALS: never do arithmetic yourself and never guess or re-type totals. When a billing tool returns `summary_text`, show that block to the owner (you may add a short sentence around it). For any single amount, copy the tool's exact `grand_total`/`line_total` value character-for-character. The DB is the source of truth — the total AFTER removing an item is always lower, so never let a removed-item total exceed the previous one.
- If a product name is ambiguous (e.g. "atta"), ask which one — use find_product candidates.
- Business rules are enforced by tools (oversell, below-cost, khata). Relay tool errors clearly.
- Multi-item bills: start_bill → find_product/add_bill_item for each → set_bill_payment → show summary → finalize_bill when owner confirms or clearly wants to cut the bill.
- Default payment mode from preferences if owner doesn't specify.
- CREDIT / KHATA sales ("pay later", "udhaar", "on credit", "khata"): you MUST know WHO is buying on credit. Flow:
  1. Ask the customer's NAME.
  2. Call find_or_create_customer(name=...) to look them up.
     - If it returns one customer -> that's the person (confirm with the phone shown), use its customer_id.
     - If it returns ambiguous=true (several people share that name) -> show the masked phone candidates and ask the owner which one, then call find_or_create_customer(customer_id=...) for the chosen one.
     - If it returns needs_phone=true (no such customer) -> this is a NEW customer: ask for their MOBILE NUMBER, then find_or_create_customer(name=..., phone=...).
  3. set_bill_payment(mode="khata", customer_id=...) → finalize_bill.
  Phone is the unique identity, so two different people can share a name — always disambiguate by phone. NEVER invent a name like "Walk-in Customer"; the tool rejects a khata without a real customer + phone. Anonymous is only OK for cash/upi/card.
- Preferences survive /new; conversation may reset but shop defaults remain.
- Currency INR. GST is CGST+SGST (intra-state).
- For "all stock / full inventory / what do I have / show everything" call list_inventory.
- After you finalize a bill, ALSO call generate_invoice_pdf for that bill_id in the SAME turn so the owner gets the invoice automatically (unless they explicitly said no PDF).
- If the owner says "bill", "invoice", "paid", "give me the bill" right after a sale, generate the invoice PDF for the most recent bill instead of asking.
- For "send bill as PDF" call generate_invoice_pdf (finalize first if still a draft the owner confirmed).
- For "sales analysis deck / this week's report" call generate_analysis_deck.
- For "today's sales" use today_sales_summary; for "close the day" use close_day.
- Auto reports (start/activate/stop/deactivate — all four words mean the same):
  * "start daily report" / "activate daily report" WITHOUT a time -> ASK "At what time (IST) should I send the daily report?" and do nothing else this turn. Once the owner gives a time -> set_preference(daily_report_time, "HH:MM").
  * "send/start daily report at 9pm" (time given inline) -> set_preference(daily_report_time, "21:00") directly, no need to ask.
  * "start weekly report" / "activate weekly report" WITHOUT day+time -> ASK "Which day and time (IST) for the weekly deck?" Once given -> set_preference(weekly_report_day, "monday") AND set_preference(weekly_report_time, "13:00").
  * "stop/deactivate daily report" -> clear_preference(daily_report_time).
  * "stop/deactivate weekly report" -> clear_preference(weekly_report_day) AND clear_preference(weekly_report_time).
  Always convert any time to 24h HH:MM and weekday to lowercase full name. Confirm the schedule back in IST.
- When you receive a document/voice extract, summarize what you understood and, for supplier stock, confirm the parsed items before calling receive_stock/create_product.
- When a tool returns a file_path, the file is delivered automatically — just tell the owner it's attached; do not paste the path.
"""


def _system_with_prefs(prefs: dict[str, str]) -> str:
    pref_lines = "\n".join(f"- {k}: {v}" for k, v in prefs.items()) or "- (none yet)"
    return SYSTEM_PROMPT + "\n\nDurable preferences for this owner:\n" + pref_lines


def run_agent(
    db: Session,
    chat_id: int,
    user_text: str,
    history: list[tuple[str, str]] | None = None,
) -> tuple[str, list[str]]:
    """Synchronous agent loop. Return (reply_text, generated_file_paths).

    `history` is prior (role, content) turns replayed for short-term memory.
    Runs in a worker thread (see process_message) so the event loop stays free.
    """
    settings = get_settings()
    generated_files: list[str] = []
    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY missing")
        return "Gemini API key is not configured. Add GEMINI_API_KEY to .env.", generated_files

    prefs = preference_service.get_preferences(db, chat_id)
    client = genai.Client(api_key=settings.gemini_api_key)

    declarations = build_tool_declarations()
    tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=d["name"],
                    description=d["description"],
                    parameters=d["parameters"],
                )
                for d in declarations
            ]
        )
    ]

    config = types.GenerateContentConfig(
        system_instruction=_system_with_prefs(prefs),
        tools=tools,
        temperature=0.2,
    )

    contents: list[types.Content] = []
    for role, content in history or []:
        if not content:
            continue
        g_role = "model" if role == "assistant" else "user"
        contents.append(
            types.Content(role=g_role, parts=[types.Part.from_text(text=content)])
        )
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
    )

    logger.info(
        "agent_start chat_id=%s history=%s text=%r",
        chat_id,
        len(contents) - 1,
        user_text[:200],
    )

    max_iters = 12
    for step in range(max_iters):
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=config,
        )

        candidate = response.candidates[0] if response.candidates else None
        if not candidate or not candidate.content or not candidate.content.parts:
            logger.warning("agent_empty_response step=%s", step)
            return "I couldn't process that. Try again?", generated_files

        parts = candidate.content.parts
        fn_calls = [p for p in parts if getattr(p, "function_call", None)]

        if not fn_calls:
            text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
            logger.info(
                "agent_done chat_id=%s steps=%s reply_len=%s files=%s",
                chat_id,
                step + 1,
                len(text),
                len(generated_files),
            )
            return text or "Done.", generated_files

        # Append model turn, then tool results
        contents.append(candidate.content)
        result_parts: list[types.Part] = []
        for p in fn_calls:
            fc = p.function_call
            name = fc.name
            args = dict(fc.args or {})
            result = execute_tool(db, chat_id, name, args)
            if isinstance(result, dict) and result.get("file_path"):
                generated_files.append(result["file_path"])
            result_parts.append(
                types.Part.from_function_response(
                    name=name,
                    response={"result": result} if not isinstance(result, dict) else result,
                )
            )
            logger.debug("tool_payload %s", dumps_tool_result(result)[:500])

        contents.append(types.Content(role="user", parts=result_parts))

    logger.error("agent_max_iters chat_id=%s", chat_id)
    return "That took too many steps — send the request again in a shorter form.", generated_files


def process_message(
    chat_id: int, prompt: str, log_content: str | None = None
) -> tuple[str, list[str]]:
    """Blocking end-to-end message handling with its own DB session.

    Safe to run via asyncio.to_thread — does not touch the event loop.
    """
    from app.db.base import SessionLocal
    from app.db.models import ConversationMessage

    settings = get_settings()
    db = SessionLocal()
    try:
        history = _load_history(db, chat_id, settings.history_limit)

        db.add(
            ConversationMessage(
                chat_id=chat_id, role="user", content=(log_content or prompt)[:60000]
            )
        )
        db.commit()

        reply, files = run_agent(db, chat_id, prompt, history=history)

        db.add(ConversationMessage(chat_id=chat_id, role="assistant", content=reply[:60000]))
        db.commit()
        return reply, files
    finally:
        db.close()


def _load_history(db: Session, chat_id: int, limit: int) -> list[tuple[str, str]]:
    """Return the last `limit` (role, content) turns in chronological order."""
    if limit <= 0:
        return []
    from sqlalchemy import select

    from app.db.models import ConversationMessage

    rows = db.execute(
        select(ConversationMessage.role, ConversationMessage.content)
        .where(ConversationMessage.chat_id == chat_id)
        .order_by(ConversationMessage.id.desc())
        .limit(limit)
    ).all()
    return [(r[0], r[1]) for r in reversed(rows)]
