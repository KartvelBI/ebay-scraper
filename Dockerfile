FROM python:3.12-slim

WORKDIR /app

# Install Tor (for IP hiding) + Playwright's Chromium and Python deps
RUN apt-get update && apt-get install -y --no-install-recommends tor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY . .

EXPOSE 8080

# Route the scraper's Chromium through Tor's SOCKS5 proxy.
# (A PROXY_URL set in Railway variables overrides this.)
ENV PROXY_URL=socks5://127.0.0.1:9050

# entrypoint.sh starts Tor, waits for it, then launches gunicorn
# (1 worker so in-memory job state is shared; threads so progress/stop
# requests are served while a scrape runs in its background thread).
CMD ["bash", "/app/entrypoint.sh"]
