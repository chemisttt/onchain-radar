import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import init_db, close_db
from config import settings as app_settings
from routers import feed, tokens, security, watchlist, funding, claude, settings, analyze, derivatives, trading
from services import feed_engine, funding_service, protocol_tracker, derivatives_service
from services import options_service, liquidation_service, orderbook_service, momentum_service
from services import telegram_service, price_service, trading_service, contract_scanner
from services import exploit_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

SERVICES = [
    ("feed_engine", feed_engine),
    ("funding_service", funding_service),
    ("protocol_tracker", protocol_tracker),
    ("derivatives_service", derivatives_service),
    ("price_service", price_service),
    ("options_service", options_service),
    ("liquidation_service", liquidation_service),
    ("orderbook_service", orderbook_service),
    ("momentum_service", momentum_service),
    ("telegram_service", telegram_service),
    ("trading_service", trading_service),
    ("contract_scanner", contract_scanner),
    ("exploit_engine", exploit_engine),
]


async def _send_startup_alert():
    """Send Telegram health notification after all services started."""
    import aiohttp

    token = app_settings.telegram_bot_token
    chat_id = app_settings.telegram_chat_id
    thread_id = app_settings.telegram_thread_id
    if not token or not chat_id:
        return

    lines = ["\U0001f7e2 <b>onchain-radar restarted</b>\n"]
    for name, svc in SERVICES:
        task = getattr(svc, "_task", None)
        if task and not task.done():
            lines.append(f"  \u2705 {name}")
        elif task and task.done():
            lines.append(f"  \u274c {name} (crashed)")
        else:
            lines.append(f"  \u23f8 {name} (disabled)")

    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning(f"Startup alert failed: {resp.status}")
    except Exception as e:
        log.warning(f"Startup alert error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    for name, svc in SERVICES:
        svc.start()
    # Give services a moment to initialize, then send health alert
    asyncio.create_task(_delayed_startup_alert())
    yield
    for name, svc in reversed(SERVICES):
        svc.stop()
    await close_db()


async def _delayed_startup_alert():
    """Wait 5s for services to spin up, then send health notification."""
    await asyncio.sleep(5)
    await _send_startup_alert()


app = FastAPI(title="On-Chain Radar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WS routes at root (no /api prefix — Vite proxies /ws/* directly)
app.include_router(feed.router_ws)
# REST routes under /api
app.include_router(feed.router, prefix="/api")
app.include_router(tokens.router, prefix="/api")
app.include_router(security.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(funding.router, prefix="/api")
app.include_router(claude.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(derivatives.router, prefix="/api")
app.include_router(trading.router, prefix="/api")


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}
