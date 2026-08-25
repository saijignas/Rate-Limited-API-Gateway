"""The gateway itself: a thin FastAPI layer in front of the (slow) real
backend, adding three things no individual backend call gives you for
free: rate limiting per client, idempotent writes, and cached reads.

Which rate limiter runs is a constructor choice (`create_app`), not a
hardcoded import, specifically so tests can exercise both
FixedWindowLimiter (to prove its boundary flaw) and TokenBucketLimiter
(to prove the fix) against the same app wiring.
"""

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .cache import ResponseCache
from .idempotency import IdempotencyConflict, IdempotencyStore


def create_app(redis_client, rate_limiter, backend_url: str, cache_ttl_seconds: int = 30, http_client=None):
    app = FastAPI(title="Rate-Limited Idempotent API Gateway")
    idempotency_store = IdempotencyStore(redis_client)
    cache = ResponseCache(redis_client, ttl_seconds=cache_ttl_seconds)
    # Tests inject an httpx.Client wired to an ASGITransport pointed directly
    # at the backend's in-process ASGI app, so integration tests exercise the
    # real backend logic (including its process-count counters) without
    # needing a separately running server.
    http_client = http_client or httpx.Client(base_url=backend_url, timeout=10.0)

    def _check_rate_limit(client_id: str) -> None:
        if not rate_limiter.allow(client_id):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    @app.post("/orders")
    def create_order(
        request: Request,
        body: dict,
        x_client_id: str = Header(default="anonymous"),
        idempotency_key: str = Header(default=None, alias="Idempotency-Key"),
    ):
        _check_rate_limit(x_client_id)

        if not idempotency_key:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key header is required for POST /orders",
            )

        status, cached = idempotency_store.begin(idempotency_key)
        if status == "completed":
            return JSONResponse(status_code=cached["status_code"], content=cached["body"])

        try:
            response = http_client.post("/orders", json=body)
        except httpx.HTTPError as exc:
            idempotency_store.release(idempotency_key)
            raise HTTPException(status_code=502, detail=f"Backend unavailable: {exc}") from exc

        idempotency_store.complete(idempotency_key, response.status_code, response.json())
        return JSONResponse(status_code=response.status_code, content=response.json())

    @app.get("/orders/{order_id}")
    def get_order(order_id: int, x_client_id: str = Header(default="anonymous")):
        _check_rate_limit(x_client_id)

        cache_key = f"orders/{order_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return JSONResponse(status_code=200, content={**cached, "_cache": "hit"})

        try:
            response = http_client.get(f"/orders/{order_id}")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Backend unavailable: {exc}") from exc

        body = response.json()
        if response.status_code == 200:
            cache.set(cache_key, body)
        return JSONResponse(status_code=response.status_code, content={**body, "_cache": "miss"})

    @app.exception_handler(IdempotencyConflict)
    def handle_idempotency_conflict(request: Request, exc: IdempotencyConflict):
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "retry_after_seconds": 1},
        )

    return app
