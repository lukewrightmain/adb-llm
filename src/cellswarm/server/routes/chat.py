"""Chat completion proxy — forwards to cellswarm-host's /v1/chat/completions."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

import httpx
from loguru import logger

router = APIRouter(tags=["chat"])

SWARM_HOST_URL = "http://127.0.0.1:8080"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    is_stream = body.get("stream", False)
    messages = body.get("messages", [])

    # Ensure stop tokens for ChatML models (DeepSeek-Coder, Qwen, etc.)
    if "stop" not in body:
        body["stop"] = ["<|im_end|>", "<|im_start|>"]

    # Log the request
    user_msgs = [m for m in messages if m.get("role") == "user"]
    last_user = user_msgs[-1]["content"][:120] if user_msgs else "(no user msg)"
    logger.info(
        "[chat] {} messages, stream={}, last_user={!r}",
        len(messages), is_stream, last_user,
    )

    t0 = time.monotonic()

    try:
        if is_stream:
            # Create client WITHOUT async-with so it survives past the return.
            # The generator closes it when streaming finishes.
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
            req = client.build_request(
                "POST",
                f"{SWARM_HOST_URL}/v1/chat/completions",
                json=body,
            )
            resp = await client.send(req, stream=True)

            if resp.status_code != 200:
                err_body = (await resp.aread()).decode(errors="replace")[:500]
                await resp.aclose()
                await client.aclose()
                logger.error("[chat] backend returned {}: {}", resp.status_code, err_body)
                raise HTTPException(resp.status_code, f"Backend error: {err_body}")

            logger.info("[chat] streaming started ({:.0f}ms connect)", (time.monotonic() - t0) * 1000)

            token_count = 0

            async def generate():
                nonlocal token_count
                try:
                    async for chunk in resp.aiter_bytes():
                        token_count += chunk.count(b'"delta"')
                        yield chunk
                except Exception as e:
                    logger.error("[chat] stream error ({}): {!r}", type(e).__name__, e)
                finally:
                    await resp.aclose()
                    await client.aclose()
                    elapsed = time.monotonic() - t0
                    logger.info(
                        "[chat] stream done: ~{} tokens in {:.1f}s ({:.1f} tok/s)",
                        token_count, elapsed,
                        token_count / elapsed if elapsed > 0 else 0,
                    )

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                resp = await client.post(
                    f"{SWARM_HOST_URL}/v1/chat/completions",
                    json=body,
                )
                elapsed = time.monotonic() - t0
                logger.info("[chat] non-stream response: {} in {:.1f}s", resp.status_code, elapsed)
                if resp.status_code != 200:
                    logger.error("[chat] backend error: {}", resp.text[:500])
                return resp.json()

    except httpx.ConnectError:
        logger.error("[chat] cannot connect to cellswarm-host at {}", SWARM_HOST_URL)
        raise HTTPException(
            503,
            "Inference backend not reachable. Is the ring running and model loaded?",
        )
    except httpx.ReadTimeout:
        logger.error("[chat] read timeout from cellswarm-host after {:.0f}s", time.monotonic() - t0)
        raise HTTPException(504, "Inference backend timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[chat] unexpected error: {}", e)
        raise HTTPException(500, f"Chat proxy error: {e}")
