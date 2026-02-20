"""Chat completion proxy — forwards to cellswarm-host's /v1/chat/completions."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import httpx

router = APIRouter(tags=["chat"])

SWARM_HOST_URL = "http://127.0.0.1:8080"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    is_stream = body.get("stream", False)

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        if is_stream:
            req = client.build_request(
                "POST",
                f"{SWARM_HOST_URL}/v1/chat/completions",
                json=body,
            )
            resp = await client.send(req, stream=True)

            async def generate():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await resp.aclose()

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            resp = await client.post(
                f"{SWARM_HOST_URL}/v1/chat/completions",
                json=body,
            )
            return resp.json()
