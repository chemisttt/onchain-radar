import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from models import ClaudeRequest
from services import claude_service
from db import get_db

router = APIRouter()


@router.post("/claude/analyze")
async def claude_analyze(req: ClaudeRequest, request: Request):
    db = get_db()

    # Get cached security data as context
    security_context = ""
    row = await db.execute_fetchall(
        "SELECT goplus, honeypot, rugcheck FROM security_cache WHERE chain = ? AND address = ?",
        (req.chain, req.address.lower()),
    )
    if row:
        ctx = {
            "goplus": json.loads(row[0]["goplus"]),
            "honeypot": json.loads(row[0]["honeypot"]),
            "rugcheck": json.loads(row[0]["rugcheck"]),
        }
        security_context = json.dumps(ctx, indent=2)

    # Create session record
    await db.execute(
        """INSERT INTO claude_sessions (chain, address, prompt, status)
           VALUES (?, ?, ?, 'running')""",
        (req.chain, req.address, req.prompt or "Full security analysis"),
    )
    await db.commit()

    async def generate():
        full_result = []
        async for event in claude_service.analyze_stream(
            req.chain, req.address, security_context, req.prompt
        ):
            if await request.is_disconnected():
                break
            if event["type"] == "text":
                full_result.append(event["content"])
            yield f"data: {json.dumps(event)}\n\n"

        # Save result
        result_text = "".join(full_result)
        session_id = event.get("session_id") if event else None
        await db.execute(
            """UPDATE claude_sessions SET result = ?, session_id = ?, status = 'done'
               WHERE chain = ? AND address = ? AND status = 'running'""",
            (result_text, session_id, req.chain, req.address),
        )
        await db.commit()

    return StreamingResponse(generate(), media_type="text/event-stream")
