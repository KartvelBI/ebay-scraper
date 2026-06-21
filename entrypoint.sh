#!/usr/bin/env bash
# Start Tor, wait for its SOCKS proxy, then launch the web app.
# The scraper routes Chromium through Tor via PROXY_URL=socks5://127.0.0.1:9050
set -e

echo "Starting Tor…"
tor --SocksPort 9050 --Log "notice stdout" &
TOR_PID=$!

# Wait (up to ~40s) for Tor's SOCKS port to accept connections.
echo "Waiting for Tor SOCKS port (9050)…"
for i in $(seq 1 40); do
  if (exec 3<>/dev/tcp/127.0.0.1/9050) 2>/dev/null; then
    exec 3>&- 2>/dev/null || true
    echo "Tor SOCKS port is up (after ${i}s)."
    break
  fi
  sleep 1
done

echo "Launching gunicorn…"
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-8080}" \
  --timeout 600 \
  --workers 1 \
  --threads 4
