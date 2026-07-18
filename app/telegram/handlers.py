"""Telegram update handlers: text, voice, documents, photos."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import delete
from telegram import Update
from telegram.ext import ContextTypes

from app.agent.runner import process_message
from app.db.base import SessionLocal
from app.db.models import ConversationMessage, InboundDocument, ProcessedUpdate
from app.services import billing_service, doc_extract_service, stt_service

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024


async def handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    update_id = update.update_id
    logger.info("cmd_new chat_id=%s update_id=%s", chat_id, update_id)

    db = SessionLocal()
    try:
        if _already_processed(db, update_id):
            return
        billing_service.cancel_draft(db, chat_id)
        db.execute(delete(ConversationMessage).where(ConversationMessage.chat_id == chat_id))
        db.commit()
        text = (
            "New chat started. Draft bill cleared.\n"
            "Shop preferences (defaults, GSTIN, etc.) are still saved."
        )
        await update.message.reply_text(text)
        _mark_processed(db, update_id, chat_id, text)
    except Exception:
        logger.exception("handle_new_failed chat_id=%s", chat_id)
        await update.message.reply_text("Something went wrong starting a new chat.")
    finally:
        db.close()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    update_id = update.update_id
    user_text = update.message.text.strip()
    logger.info("msg_text chat_id=%s update_id=%s text=%r", chat_id, update_id, user_text[:200])
    await _run_and_reply(update, chat_id, update_id, user_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    update_id = update.update_id
    voice = update.message.voice or update.message.audio
    logger.info("msg_voice chat_id=%s update_id=%s", chat_id, update_id)

    db = SessionLocal()
    try:
        if _already_processed(db, update_id):
            return
    finally:
        db.close()

    await update.message.chat.send_action("typing")
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        suffix = ".ogg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)
        mime = getattr(voice, "mime_type", None) or "audio/ogg"
        transcript = await asyncio.to_thread(stt_service.transcribe, tmp_path, mime)
        os.unlink(tmp_path)
    except Exception:
        logger.exception("voice_download_failed chat_id=%s", chat_id)
        await update.message.reply_text("Couldn't read that voice note. Please type it.")
        return

    if not transcript:
        await update.message.reply_text("I couldn't transcribe that. Please type the order.")
        return

    logger.info("voice_transcript chat_id=%s text=%r", chat_id, transcript[:200])
    prompt = f"Voice note transcript (owner spoke this):\n{transcript}"
    await update.message.reply_text(f"🎙️ Heard: {transcript[:300]}")
    await _run_and_reply(update, chat_id, update_id, prompt, log_content=transcript)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_id = update.effective_chat.id
    update_id = update.update_id
    msg = update.message
    caption = (msg.caption or "").strip()

    doc = msg.document
    photo = msg.photo[-1] if msg.photo else None
    file_obj = doc or photo
    if not file_obj:
        return

    file_name = getattr(doc, "file_name", None) or f"photo_{file_obj.file_id}.jpg"
    mime = getattr(doc, "mime_type", None) or "image/jpeg"
    size = getattr(file_obj, "file_size", 0) or 0
    logger.info("msg_document chat_id=%s name=%s mime=%s size=%s", chat_id, file_name, mime, size)

    if size and size > MAX_FILE_BYTES:
        await msg.reply_text("That file is too big (max 20 MB).")
        return

    db = SessionLocal()
    try:
        if _already_processed(db, update_id):
            return
    finally:
        db.close()

    await msg.chat.send_action("typing")
    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        suffix = Path(file_name).suffix or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)
        source_type, extract = await asyncio.to_thread(
            doc_extract_service.extract_text, tmp_path, mime, file_name
        )
        os.unlink(tmp_path)
    except Exception:
        logger.exception("doc_download_failed chat_id=%s", chat_id)
        await msg.reply_text("Couldn't read that file.")
        return

    doc_kind = doc_extract_service.quick_classify(extract)
    logger.info("doc_extracted chat_id=%s type=%s kind=%s chars=%s", chat_id, source_type, doc_kind, len(extract))

    db = SessionLocal()
    try:
        rec = InboundDocument(
            chat_id=chat_id,
            telegram_file_id=file_obj.file_id,
            file_name=file_name,
            mime_type=mime,
            source_type=source_type if source_type in ("pdf", "docx", "image", "voice", "other") else "other",
            doc_kind=doc_kind,
            raw_extract=extract[:60000],
            status="extracted",
        )
        db.add(rec)
        db.commit()
    except Exception:
        logger.exception("inbound_doc_save_failed")
    finally:
        db.close()

    if not extract:
        await msg.reply_text("I couldn't extract anything from that document.")
        return

    prompt = (
        f"Owner uploaded a document (name: {file_name}, looks like: {doc_kind}).\n"
        f"Owner caption: {caption or '(none)'}\n\n"
        f"Extracted content:\n{extract[:8000]}\n\n"
        "Decide what to do. If it's supplier stock, summarize the items and confirm before receiving."
    )
    await _run_and_reply(update, chat_id, update_id, prompt, log_content=f"[doc:{file_name}] {caption}")


async def _run_and_reply(
    update: Update,
    chat_id: int,
    update_id: int,
    prompt: str,
    log_content: str | None = None,
) -> None:
    # Quick idempotency check on the event loop (fast local query)
    db = SessionLocal()
    try:
        if _already_processed(db, update_id):
            logger.info("skip_duplicate update_id=%s", update_id)
            return
    finally:
        db.close()

    try:
        # Heavy work (Gemini + DB tools + PDF/PPTX) runs off the event loop,
        # so other chats stay responsive.
        reply, files = await asyncio.to_thread(process_message, chat_id, prompt, log_content)
    except Exception:
        logger.exception("run_and_reply_failed chat_id=%s", chat_id)
        if update.message:
            await update.message.reply_text("Sorry — hit an error. Check server logs.")
        return

    if update.message:
        await update.message.reply_text(reply[:4000])
        for path in files:
            await _send_file(update, path)

    db = SessionLocal()
    try:
        _mark_processed(db, update_id, chat_id, reply)
    finally:
        db.close()


async def _send_file(update: Update, path: str) -> None:
    try:
        if not os.path.exists(path):
            logger.warning("file_missing path=%s", path)
            return
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(path))
        logger.info("file_sent path=%s", path)
    except Exception:
        logger.exception("send_file_failed path=%s", path)


def _already_processed(db, update_id: int) -> bool:
    return db.get(ProcessedUpdate, update_id) is not None


def _mark_processed(db, update_id: int, chat_id: int, response_text: str) -> None:
    if db.get(ProcessedUpdate, update_id):
        return
    db.add(
        ProcessedUpdate(
            update_id=update_id,
            chat_id=chat_id,
            response_text=response_text[:65000],
        )
    )
    db.commit()
