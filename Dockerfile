FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs for matplotlib / reportlab charts
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    libpng16-16t64 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Railway injects PORT at runtime; sh -c expands ${PORT} (never pass literal $PORT to uvicorn).
# Using inline shell form avoids CRLF issues from a checked-in .sh script on Windows.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
