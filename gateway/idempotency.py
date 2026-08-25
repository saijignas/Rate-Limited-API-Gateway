"""Idempotency-key handling, modeled on the pattern Stripe documents
publicly for their API: a client sends an `Idempotency-Key` header on a
non-safe request (POST/PUT); if that exact key was already used, the
client gets back the *original* response instead of the operation running
twice (the concrete failure this prevents: a payment or a booking firing
twice because a client retried after a timeout, not because they actually
wanted to pay/book twice).

The subtlety most naive implementations miss: what happens if a second
request with the same key arrives *while the first one is still running*
(not after it completed)? Just checking "does a cached response exist
yet?" says no, and a naive implementation would let the second request
through to also hit the backend -- exactly the double-processing this
mechanism exists to prevent. The fix is a three-state claim:

    (missing) --SETNX--> PENDING --complete()--> COMPLETED(response)

SETNX (SET-if-Not-eXists) atomically claims the key: only one concurrent
request can win that race. A request that loses the race sees either
PENDING (the original is still running -- tell the caller to retry
shortly, don't process) or COMPLETED (safe to return the cached response).
"""

import json
import time

PENDING = "PENDING"


class IdempotencyConflict(Exception):
    """Raised when a request with this key is still being processed."""


class IdempotencyStore:
    def __init__(self, redis_client, ttl_seconds: int = 24 * 60 * 60):
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    def _key(self, idempotency_key: str) -> str:
        return f"idempotency:{idempotency_key}"

    def begin(self, idempotency_key: str):
        """Attempt to claim this key for processing.

        Returns:
            ("new", None)          -- caller should process the request.
            ("completed", response) -- already done; return this response.
        Raises:
            IdempotencyConflict     -- another request with this key is
                                       currently being processed.
        """
        key = self._key(idempotency_key)
        claimed = self.redis.set(key, PENDING, nx=True, ex=self.ttl_seconds)
        if claimed:
            return "new", None

        raw = self.redis.get(key)
        if raw is None:
            # The PENDING claim expired (crashed worker) between our GET
            # attempts; treat as available and let the caller retry begin().
            return "new", None
        if raw.decode() == PENDING:
            raise IdempotencyConflict(
                f"Request with idempotency key {idempotency_key!r} is already being processed"
            )
        return "completed", json.loads(raw)

    def complete(self, idempotency_key: str, status_code: int, body: dict) -> None:
        key = self._key(idempotency_key)
        payload = json.dumps({"status_code": status_code, "body": body, "completed_at": time.time()})
        self.redis.set(key, payload, ex=self.ttl_seconds)

    def release(self, idempotency_key: str) -> None:
        """Release a PENDING claim without completing it (e.g. the handler
        raised an exception) so the client's retry isn't stuck waiting out
        the full TTL for a request that never actually finished."""
        key = self._key(idempotency_key)
        raw = self.redis.get(key)
        if raw is not None and raw.decode() == PENDING:
            self.redis.delete(key)
