"""Telegram delivery service — daily digest + real-time alerts.

Follows existing service pattern: _task, _poll_loop, start/stop.
Sends to a forum topic via Telegram Bot API.
Gracefully skips if TELEGRAM_BOT_TOKEN is not configured.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp

from config import settings
from services import market_analyzer

log = logging.getLogger("telegram")

_task: asyncio.Task | None = None

POLL_INTERVAL = 60  # check every 60 seconds
DIGEST_HOUR_UTC = 18  # 18:00 UTC = 21:00 MSK
ALERT_COOLDOWNS = {
    "SIGNAL": 14400,   # 4 hours
    "TRIGGER": 3600,   # 1 hour
}
DEFAULT_COOLDOWN = 7200  # fallback 2 hours
# SETUP tier is too noisy for Telegram — only send SIGNAL and TRIGGER
TELEGRAM_MIN_TIER = "SIGNAL"
TIER_PRIORITY = {"SETUP": 0, "SIGNAL": 1, "TRIGGER": 2}
INITIAL_DELAY = 30  # wait for services to warm up

# State
_last_digest_date: str = ""
_alert_cooldowns: dict[str, float] = {}  # key → timestamp of last fire


async def _send_message(text: str, session: aiohttp.ClientSession) -> bool:
    """Send HTML message to Telegram forum topic."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    thread_id = settings.telegram_thread_id

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        async with session.post(
            url, json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning(f"Telegram send failed ({resp.status}): {body[:200]}")
                return False
            return True
    except Exception as e:
        log.warning(f"Telegram send error: {e}")
        return False


async def _send_long_message(text: str, session: aiohttp.ClientSession):
    """Send message, splitting at paragraph boundaries if >4096 chars."""
    if len(text) <= 4096:
        await _send_message(text, session)
        return

    parts = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > 4096:
            if current:
                parts.append(current.rstrip())
            current = paragraph
        else:
            current = current + "\n\n" + paragraph if current else paragraph
    if current:
        parts.append(current.rstrip())

    for part in parts:
        await _send_message(part, session)
        await asyncio.sleep(0.5)


async def _poll_loop():
    """Main loop: check digest time + alert conditions every 60s."""
    global _last_digest_date

    log.info("Telegram service started")

    # Wait for other services to populate caches
    await asyncio.sleep(INITIAL_DELAY)

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Daily digest check
                now = datetime.now(timezone.utc)
                today = now.strftime("%Y-%m-%d")

                if now.hour == DIGEST_HOUR_UTC and _last_digest_date != today:
                    log.info("Building daily digest...")
                    try:
                        messages = await market_analyzer.build_daily_digest()
                        for msg in messages:
                            await _send_message(msg, session)
                            await asyncio.sleep(0.5)
                        _last_digest_date = today
                        log.info("Daily digest sent")
                    except Exception as e:
                        log.error(f"Digest error: {e}")

                # 2. Alert check
                try:
                    alerts = await market_analyzer.check_alerts()
                    now_ts = time.time()

                    for alert in alerts:
                        key = alert["key"]
                        tier = alert.get("tier", "")

                        # Skip low-quality alerts
                        if TIER_PRIORITY.get(tier, 0) < TIER_PRIORITY.get(TELEGRAM_MIN_TIER, 1):
                            continue

                        cooldown = ALERT_COOLDOWNS.get(tier, DEFAULT_COOLDOWN)
                        if now_ts - _alert_cooldowns.get(key, 0) < cooldown:
                            continue

                        text = f"<b>{alert['title']}</b>\n\n{alert['body']}"
                        sent = await _send_message(text, session)
                        if sent:
                            _alert_cooldowns[key] = now_ts
                            log.info(f"Alert sent: {key}")
                        await asyncio.sleep(0.3)

                except Exception as e:
                    log.error(f"Alert check error: {e}")

        except Exception as e:
            log.error(f"Telegram poll error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


def start():
    global _task
    if not settings.telegram_bot_token:
        log.info("Telegram service skipped (no TELEGRAM_BOT_TOKEN)")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_poll_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
