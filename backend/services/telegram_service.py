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
from db import get_db
from services import market_analyzer, trading_service

log = logging.getLogger("telegram")

_task: asyncio.Task | None = None

POLL_INTERVAL = 60  # check every 60 seconds
DIGEST_HOUR_UTC = 18  # 18:00 UTC = 21:00 MSK
ALERT_COOLDOWNS = {
    "SIGNAL": 43200,   # 12 hours (match backtest 24h cooldown — persistent state signals)
    "TRIGGER": 21600,  # 6 hours
}
DEFAULT_COOLDOWN = 14400  # fallback 4 hours
# SETUP tier is too noisy for Telegram — only send SIGNAL and TRIGGER
TELEGRAM_MIN_TIER = "SIGNAL"
TIER_PRIORITY = {"SETUP": 0, "INFO": 1, "SIGNAL": 1, "TRIGGER": 2}
INITIAL_DELAY = 30  # wait for services to warm up

# State
_last_digest_date: str = ""
_alert_cooldowns: dict[str, float] = {}  # key → timestamp of last fire
_last_cleanup: float = 0.0


async def _load_cooldowns() -> None:
    """Load cooldowns from DB (survive restarts)."""
    global _alert_cooldowns
    try:
        db = get_db()
        rows = await db.execute_fetchall("SELECT key, fired_at FROM alert_cooldowns")
        now = time.time()
        loaded = 0
        for row in rows:
            if now - row["fired_at"] < 86400:  # skip entries older than 24h
                _alert_cooldowns[row["key"]] = row["fired_at"]
                loaded += 1
        log.info(f"Loaded {loaded} cooldowns from DB")
    except Exception as e:
        log.warning(f"Failed to load cooldowns: {e}")


async def _save_cooldown(key: str, ts: float) -> None:
    """Persist a cooldown entry to DB."""
    try:
        db = get_db()
        await db.execute(
            "INSERT OR REPLACE INTO alert_cooldowns (key, fired_at) VALUES (?, ?)",
            (key, ts),
        )
        await db.commit()
    except Exception as e:
        log.warning(f"Failed to save cooldown: {e}")


async def _cleanup_cooldowns() -> None:
    """Remove cooldown entries older than 24h. Run once per hour."""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 3600:
        return
    _last_cleanup = now
    try:
        db = get_db()
        cutoff = now - 86400
        await db.execute("DELETE FROM alert_cooldowns WHERE fired_at < ?", (cutoff,))
        await db.commit()
        # Also clean in-memory
        expired = [k for k, ts in _alert_cooldowns.items() if ts < cutoff]
        for k in expired:
            del _alert_cooldowns[k]
        if expired:
            log.info(f"Cleaned up {len(expired)} expired cooldowns")
    except Exception as e:
        log.warning(f"Cooldown cleanup error: {e}")


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
            if resp.status == 429:
                body = await resp.json()
                retry = body.get("parameters", {}).get("retry_after", 30)
                log.warning(f"Telegram 429 — backing off {retry}s")
                await asyncio.sleep(retry)
                return False
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


async def _save_trade_decision(alert_key: str, status: str, reason: str):
    """Persist trade decision to alert_tracking row."""
    try:
        db = get_db()
        await db.execute(
            "UPDATE alert_tracking SET trade_status=?, trade_reason=? "
            "WHERE id = (SELECT id FROM alert_tracking WHERE alert_key=? "
            "ORDER BY id DESC LIMIT 1)",
            (status, reason, alert_key))
        await db.commit()
    except Exception as e:
        log.error(f"Failed to save trade decision: {e}")


async def _poll_loop():
    """Main loop: check digest time + alert conditions every 60s."""
    global _last_digest_date

    log.info("Telegram service started")

    # Wait for other services to populate caches
    await asyncio.sleep(INITIAL_DELAY)
    await _load_cooldowns()

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

                # 2. Cleanup expired cooldowns (once per hour)
                await _cleanup_cooldowns()

                # 3. Alert check
                try:
                    alerts = await market_analyzer.check_alerts()
                    now_ts = time.time()

                    # Cache ALL signals for counter-exit detection
                    # (before tier/cooldown filtering so counter-sig sees full stream)
                    for alert in alerts:
                        alert["expected_direction"] = market_analyzer._expected_direction(alert)
                        trading_service.cache_signal(alert)

                    sent_count = 0
                    MAX_PER_CYCLE = 5  # prevent Telegram flood

                    for alert in alerts:
                        if sent_count >= MAX_PER_CYCLE:
                            log.info(f"Cycle cap reached ({MAX_PER_CYCLE}), deferring remaining alerts")
                            break

                        key = alert["key"]
                        tier = alert.get("tier", "")

                        # Skip low-quality alerts
                        if TIER_PRIORITY.get(tier, 0) < TIER_PRIORITY.get(TELEGRAM_MIN_TIER, 1):
                            continue

                        cooldown = alert.get("cooldown_hours", 0) * 3600 if alert.get("cooldown_hours") else ALERT_COOLDOWNS.get(tier, DEFAULT_COOLDOWN)
                        if now_ts - _alert_cooldowns.get(key, 0) < cooldown:
                            continue

                        text = f"<b>{alert['title']}</b>\n\n{alert['body']}"
                        sent = await _send_message(text, session)
                        if sent:
                            _alert_cooldowns[key] = now_ts
                            await _save_cooldown(key, now_ts)
                            await market_analyzer.record_alert(alert)
                            trade_status, trade_reason = await trading_service.on_signal(alert)
                            await _save_trade_decision(key, trade_status, trade_reason)
                            sent_count += 1
                            log.info(f"Alert sent: {key} | trade: {trade_status} ({trade_reason})")
                        await asyncio.sleep(0.5)

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
