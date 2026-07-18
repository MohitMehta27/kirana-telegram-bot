"""App logging: daily file rotate + console + optional PII redaction."""

from __future__ import annotations

import logging
import os
import re as _re
from datetime import date, datetime
from logging.handlers import BaseRotatingHandler

_LOGGING_INITIALIZED = False

_PII_EMAIL_RE = _re.compile(
    r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
_PII_PHONE_RE = _re.compile(r"(?<!\d)(\+?\d[\d\s-]{8,13}\d)(?!\d)")


def _pii_mask_phone(match: _re.Match[str]) -> str:
    digits = _re.sub(r"\D", "", match.group(0))
    if len(digits) < 10:
        return match.group(0)
    return "*" * (len(digits) - 3) + digits[-3:]


class PIIRedactionFilter(logging.Filter):
    """Best-effort masking of emails and phone numbers in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = _PII_EMAIL_RE.sub(r"\1***\2", msg)
        red = _PII_PHONE_RE.sub(_pii_mask_phone, red)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


def _attach_pii_redaction(target_logger: logging.Logger) -> None:
    if os.getenv("LOG_PII_REDACTION", "true").strip().lower() in ("0", "false", "no"):
        return
    pii_filter = PIIRedactionFilter()
    for h in target_logger.handlers:
        h.addFilter(pii_filter)


class DailyRotatingFileHandler(BaseRotatingHandler):
    def __init__(self, log_dir: str = "logs", retention_days: int = 30, encoding: str = "utf-8"):
        self.log_dir = log_dir
        self.retention_days = retention_days
        os.makedirs(log_dir, exist_ok=True)

        self.current_date = date.today()
        log_filename = self._get_log_filename(self.current_date)

        super().__init__(log_filename, mode="a", encoding=encoding, delay=False)
        self._open()

    def _get_log_filename(self, day: date) -> str:
        return os.path.join(self.log_dir, f"{day.strftime('%Y-%m-%d')}.log")

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        return date.today() != self.current_date

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
        self.current_date = date.today()
        self.baseFilename = os.path.abspath(self._get_log_filename(self.current_date))
        self.stream = self._open()
        self.cleanup_old_logs()

    def cleanup_old_logs(self) -> None:
        now = datetime.now()
        for filename in os.listdir(self.log_dir):
            if not filename.endswith(".log"):
                continue
            file_path = os.path.join(self.log_dir, filename)
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if (now - file_time).days > self.retention_days:
                    os.remove(file_path)
                    logging.getLogger(__name__).info("Deleted old log file: %s", file_path)
            except Exception as e:
                logging.getLogger(__name__).error("Error deleting log file %s: %s", file_path, e)


def setup_logging(log_dir: str = "logs", retention_days: int = 30) -> None:
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.handlers.clear()

    handler = DailyRotatingFileHandler(log_dir=log_dir, retention_days=retention_days)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s:%(lineno)d] %(message)s"
    )
    handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)

    logging.captureWarnings(True)
    _attach_pii_redaction(logging.getLogger())

    # Avoid leaking bot tokens in httpx URL logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)

    _LOGGING_INITIALIZED = True
