import fakeredis
import pytest

from gateway.rate_limiter import FixedWindowLimiter, TokenBucketLimiter


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


class FakeClock:
    """Deterministic, manually-advanced clock for boundary-condition tests
    that would otherwise require real sleeping (flaky, slow) to exercise."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_fixed_window_allows_up_to_limit(redis_client):
    limiter = FixedWindowLimiter(redis_client, limit=3, window_seconds=60)
    results = [limiter.allow("client-a") for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_fixed_window_resets_on_new_window(redis_client):
    clock = FakeClock(start=0.0)
    limiter = FixedWindowLimiter(redis_client, limit=2, window_seconds=1, clock=clock)
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False  # limit reached in window 0

    clock.advance(1.0)  # now in window 1
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False


def test_fixed_window_boundary_burst_flaw(redis_client):
    """The documented flaw, proven rather than just described: a limit of
    5/window lets 10 requests through in a span smaller than one window,
    because 5 land in the last instant of window N and 5 more land in the
    first instant of window N+1."""
    clock = FakeClock(start=0.999)  # 0.999s -- just before the 1-second boundary
    limiter = FixedWindowLimiter(redis_client, limit=5, window_seconds=1, clock=clock)

    first_burst = [limiter.allow("client-a") for _ in range(5)]
    assert first_burst == [True] * 5

    clock.advance(0.002)  # now at 1.001s -- into the next window
    second_burst = [limiter.allow("client-a") for _ in range(5)]
    assert second_burst == [True] * 5

    # 10 requests allowed within a 2ms real-time span, against a nominal
    # limit of 5/second. This is the flaw -- not a test bug.
    total_allowed = sum(first_burst) + sum(second_burst)
    assert total_allowed == 10


def test_token_bucket_allows_up_to_capacity_then_blocks(redis_client):
    limiter = TokenBucketLimiter(redis_client, capacity=3, refill_rate=0.0)
    results = [limiter.allow("client-a") for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_token_bucket_refills_over_time(redis_client):
    clock = FakeClock(start=0.0)
    limiter = TokenBucketLimiter(redis_client, capacity=2, refill_rate=1.0, clock=clock)  # 1 token/sec
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False  # bucket empty

    clock.advance(1.0)  # one token refilled
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False


def test_token_bucket_does_not_exceed_capacity_even_after_long_idle(redis_client):
    clock = FakeClock(start=0.0)
    limiter = TokenBucketLimiter(redis_client, capacity=2, refill_rate=1.0, clock=clock)
    clock.advance(1000.0)  # a very long idle period
    # Capacity caps accumulation -- must not allow 1000 requests through.
    results = [limiter.allow("client-a") for _ in range(5)]
    assert results == [True, True, False, False, False]


def test_token_bucket_fixes_the_fixed_window_boundary_flaw(redis_client):
    """Same boundary scenario that broke FixedWindowLimiter above, run
    against TokenBucketLimiter instead: still only 5 requests get through
    within that same short span, because token bucket tracks a
    continuously-refilling budget rather than a hard reset-to-full at a
    wall-clock boundary."""
    clock = FakeClock(start=0.999)
    limiter = TokenBucketLimiter(redis_client, capacity=5, refill_rate=5.0, clock=clock)

    first_burst = [limiter.allow("client-a") for _ in range(5)]
    assert first_burst == [True] * 5

    clock.advance(0.002)  # same tiny time advance as the fixed-window test
    second_burst = [limiter.allow("client-a") for _ in range(5)]
    # Only ~0.01 tokens refilled in 2ms at 5 tokens/sec -- none of these
    # should be allowed, unlike the fixed-window case.
    assert second_burst == [False] * 5


def test_token_bucket_independent_per_client(redis_client):
    limiter = TokenBucketLimiter(redis_client, capacity=1, refill_rate=0.0)
    assert limiter.allow("client-a") is True
    assert limiter.allow("client-a") is False
    assert limiter.allow("client-b") is True  # separate bucket, unaffected
