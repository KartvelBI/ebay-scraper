FROM python:3.12-slim

WORKDIR /app

# Install dependencies and Playwright's Chromium in one layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY . .

EXPOSE 8080

# 1 worker (in-memory job state must be shared) + threads so progress/stop
# requests are served while a scrape runs in its background thread.
CMD gunicorn app:app --bind "0.0.0.0:${PORT:-8080}" --timeout 600 --workers 1 --threads 4
