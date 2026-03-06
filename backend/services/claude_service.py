import asyncio
import json
import logging

log = logging.getLogger("claude_service")

SYSTEM_PROMPT = """You are a smart contract security analyst. Analyze the provided token/contract for:
1. Ownership risks (renounced? hidden owner? can reclaim?)
2. Token supply risks (mintable? burn functions?)
3. Trading restrictions (blacklist? transfer limits? max tx?)
4. Proxy/upgrade risks
5. Liquidity risks (locked? single LP holder?)
6. Known exploit patterns (reentrancy, flash loan, oracle manipulation)

Be concise. Use bullet points. Flag critical risks with [CRITICAL], warnings with [WARNING], and info with [INFO].
If source code is available, analyze it. If not, rely on the security check data provided."""


async def analyze_stream(chain: str, address: str, security_context: str, user_prompt: str | None = None):
    """Spawn Claude CLI and yield SSE events as they come."""
    prompt_parts = [SYSTEM_PROMPT]
    if security_context:
        prompt_parts.append(f"\n\nSecurity check results:\n{security_context}")
    prompt_parts.append(f"\n\nToken: {chain}/{address}")
    if user_prompt:
        prompt_parts.append(f"\n\nUser request: {user_prompt}")

    full_prompt = "\n".join(prompt_parts)

    args = [
        "claude",
        "-p", full_prompt,
        "--output-format", "stream-json",
        "--max-turns", "15",
        "--allowedTools", "Read,Glob,Grep",
        "--disallowedTools", "Write,Edit,Bash,NotebookEdit,Task,WebFetch,WebSearch",
    ]

    log.info(f"Spawning Claude for {chain}/{address}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        yield {"type": "error", "content": "Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"}
        return

    buffer = ""
    session_id = None

    try:
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            lines = buffer.split("\n")
            buffer = lines.pop()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)

                    if event.get("type") == "system" and event.get("session_id"):
                        session_id = event["session_id"]

                    if event.get("type") == "assistant" and event.get("message", {}).get("content"):
                        for block in event["message"]["content"]:
                            if block.get("type") == "text" and block.get("text"):
                                yield {"type": "text", "content": block["text"]}

                    if event.get("type") == "result":
                        session_id = event.get("session_id", session_id)

                except json.JSONDecodeError:
                    continue

        # Flush remaining buffer
        if buffer.strip():
            try:
                event = json.loads(buffer.strip())
                if event.get("type") == "assistant" and event.get("message", {}).get("content"):
                    for block in event["message"]["content"]:
                        if block.get("type") == "text" and block.get("text"):
                            yield {"type": "text", "content": block["text"]}
            except json.JSONDecodeError:
                pass

        await proc.wait()
        yield {"type": "done", "session_id": session_id}

    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        yield {"type": "cancelled"}
