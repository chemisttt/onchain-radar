from pydantic import BaseModel
from typing import Any


class FeedEvent(BaseModel):
    id: int | None = None
    event_type: str
    chain: str
    token_address: str | None = None
    pair_address: str | None = None
    token_symbol: str | None = None
    details: dict[str, Any] = {}
    severity: str = "info"
    created_at: str | None = None


class TokenData(BaseModel):
    chain: str
    address: str
    data: dict[str, Any] = {}
    fetched_at: str | None = None


class SecurityData(BaseModel):
    chain: str
    address: str
    goplus: dict[str, Any] = {}
    honeypot: dict[str, Any] = {}
    rugcheck: dict[str, Any] = {}
    fetched_at: str | None = None


class WatchlistItem(BaseModel):
    id: int | None = None
    chain: str
    address: str
    symbol: str | None = None
    name: str | None = None
    notes: str = ""
    added_at: str | None = None


class FundingSnapshot(BaseModel):
    symbol: str
    exchange: str
    rate: float
    next_funding_time: int | None = None
    fetched_at: str | None = None


class ClaudeRequest(BaseModel):
    chain: str
    address: str
    prompt: str | None = None


class WSMessage(BaseModel):
    type: str
    data: dict[str, Any] | None = None
    timestamp: int | None = None
