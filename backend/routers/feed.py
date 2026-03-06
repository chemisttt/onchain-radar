import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from db import get_db
import json

log = logging.getLogger("feed_ws")

router = APIRouter()
router_ws = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.debug(f"WS connected, total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.debug(f"WS disconnected, total: {len(self.active)}")

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active[:]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


manager = ConnectionManager()


@router.get("/feed")
async def get_feed(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: str | None = None,
    chain: str | None = None,
):
    db = get_db()
    query = "SELECT * FROM feed_events WHERE 1=1"
    params = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if chain:
        query += " AND chain = ?"
        params.append(chain)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await db.execute_fetchall(query, params)
    events = []
    for row in rows:
        event = dict(row)
        event["details"] = json.loads(event.get("details") or "{}")
        events.append(event)
    return events


@router_ws.websocket("/ws/feed")
async def ws_feed(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        log.warning(f"WS error: {e}")
        manager.disconnect(ws)
