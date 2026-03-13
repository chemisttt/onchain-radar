#!/bin/bash
# Health check — run via cron every 5 min
# Alerts to Telegram if service is down, auto-restarts

HEALTH_URL="http://127.0.0.1:8000/api/health"
STATE_FILE="/tmp/onchain-radar-health-state"

# Load TG config from .env
set -a
source ~/onchain-radar/.env
set +a

response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" 2>/dev/null)

if [ "$response" = "200" ]; then
    # Service is up — clear failure state if it existed
    if [ -f "$STATE_FILE" ]; then
        rm "$STATE_FILE"
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d message_thread_id="${TELEGRAM_THREAD_ID}" \
            -d parse_mode=HTML \
            -d text="<b>✅ OnChain Radar RECOVERED</b>" > /dev/null
    fi
else
    if [ ! -f "$STATE_FILE" ]; then
        # First failure — alert + auto-restart
        touch "$STATE_FILE"
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d message_thread_id="${TELEGRAM_THREAD_ID}" \
            -d parse_mode=HTML \
            -d text="<b>🔴 OnChain Radar DOWN</b>%0AHTTP ${response} — restarting..." > /dev/null
    fi
    pkill -f 'uvicorn main:app' 2>/dev/null || true
    sleep 1
    sudo systemctl restart onchain-radar
fi
