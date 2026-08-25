"""Two rate-limiting algorithms, both backed by Redis so limits are shared
correctly across multiple gateway instances (an in-process counter would
silently under-enforce the limit the moment you run more than one replica).

FixedWindowLimiter is included deliberately as the naive baseline, not
because it's recommended: it has a well-known boundary-burst flaw, which
tests/test_rate_limiter.py proves rather than just asserts. TokenBucketLimiter
is the one you'd actually want in production; it's included second so the
fix is visible right next to the problem it fixes.
"""

import time

from redis.exceptions import WatchError


class FixedWindowLimiter:
    """Naive fixed-window counter: `limit` requests per `window_seconds`,
    reset on a wall-clock boundary (e.g. every :00 second).

    Time complexity: O(1) per check (one INCR, occasionally one EXPIRE).
    Atomicity: INCR and EXPIRE are each atomic in Redis individually, and
    that's sufficient here -- there's no read-modify-write race, because
    INCR itself does the read-and-increment atomically.

    Known flaw (proven in tests, not just described): a client can send
    `limit` requests in the last instant of one window and another `limit`
    requests in the first instant of the next window, getting up to 2x the
    nominal limit through in a short real-time span straddling the
    boundary. This is *why* TokenBucketLimiter exists below.
    """

    def __init__(self, redis_client, limit: int, window_seconds: int, clock=time.time):
        self.redis = redis_client
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock

    def allow(self, client_id: str) -> bool:
        window = int(self._clock() // self.window_seconds)
        key = f"ratelimit:fixed:{client_id}:{window}"
        count = self.redis.incr(key)
        if count == 1:
            # Only the request that created the key needs to set the TTL.
            self.redis.expire(key, self.window_seconds)
        return count <= self.limit


class TokenBucketLimiter:
    """Token bucket: a bucket holds up to `capacity` tokens, refills at
    `refill_rate` tokens/second, and each request consumes one token.
    Smooths bursts instead of allowing a boundary-doubling spike.

    Time complexity: O(1) per check, but requires a read-modify-write
    (current tokens depend on elapsed time since last refill), which a
    single Redis command can't express atomically the way INCR can. This
    uses WATCH/MULTI (optimistic concurrency control): read the bucket
    state, compute the new state locally, then commit only if nothing
    else modified the watched key in between -- retrying on conflict.
    This is a deliberately different concurrency-control technique from
    CineReserve's pessimistic `SELECT ... FOR UPDATE` locking: here,
    conflicts are rare (one bucket per client, low contention per key) and
    optimistic retry is cheaper than always paying for a lock.
    """

    def __init__(self, redis_client, capacity: int, refill_rate: float, max_retries: int = 5, clock=time.time):
        self.redis = redis_client
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.max_retries = max_retries
        self._clock = clock

    def allow(self, client_id: str) -> bool:
        key = f"ratelimit:bucket:{client_id}"
        now = self._clock()

        for _ in range(self.max_retries):
            with self.redis.pipeline() as pipe:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is None:
                    tokens, last_refill = float(self.capacity), now
                else:
                    tokens_str, last_refill_str = raw.decode().split(":")
                    tokens, last_refill = float(tokens_str), float(last_refill_str)

                elapsed = max(0.0, now - last_refill)
                tokens = min(self.capacity, tokens + elapsed * self.refill_rate)

                if tokens < 1.0:
                    allowed = False
                else:
                    allowed = True
                    tokens -= 1.0

                ttl = max(1, int(self.capacity / self.refill_rate) * 2) if self.refill_rate > 0 else 3600
                pipe.multi()
                pipe.set(key, f"{tokens}:{now}", ex=ttl)
                try:
                    pipe.execute()
                    return allowed
                except WatchError:
                    # Another request modified this client's bucket between
                    # our WATCH and our MULTI/EXEC -- retry with fresh state.
                    continue

        # Exhausted retries under heavy contention on the same client's
        # bucket: fail closed (deny) rather than silently skip the check.
        return False
