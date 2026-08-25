"""Production entrypoint: wires the gateway with real Redis and the
token-bucket limiter (the fixed-window limiter is kept in the codebase
specifically as the documented, tested cautionary example -- see
rate_limiter.py and tests/test_rate_limiter.py -- not as a real option
here).

Run with: uvicorn gateway.app:app --port 8080
"""

import redis

from . import config
from .main import create_app
from .rate_limiter import TokenBucketLimiter

redis_client = redis.Redis.from_url(config.REDIS_URL)
limiter = TokenBucketLimiter(
    redis_client,
    capacity=config.TOKEN_BUCKET_CAPACITY,
    refill_rate=config.TOKEN_BUCKET_REFILL_RATE,
)

app = create_app(
    redis_client,
    limiter,
    backend_url=config.BACKEND_URL,
    cache_ttl_seconds=config.CACHE_TTL_SECONDS,
)
