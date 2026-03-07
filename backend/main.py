import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from db import init_db, close_db
from routers import feed, tokens, security, watchlist, funding, claude, settings, analyze, derivatives
from services import feed_engine, funding_service, protocol_tracker, derivatives_service
from services import options_service, liquidation_service, orderbook_service, momentum_service
from services import telegram_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    feed_engine.start()
    funding_service.start()
    protocol_tracker.start()
    derivatives_service.start()
    options_service.start()
    liquidation_service.start()
    orderbook_service.start()
    momentum_service.start()
    telegram_service.start()
    yield
    feed_engine.stop()
    funding_service.stop()
    protocol_tracker.stop()
    derivatives_service.stop()
    options_service.stop()
    liquidation_service.stop()
    orderbook_service.stop()
    momentum_service.stop()
    telegram_service.stop()
    await close_db()


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


@app.get("/api/health")
async def health():
    return {"status": "ok"}
