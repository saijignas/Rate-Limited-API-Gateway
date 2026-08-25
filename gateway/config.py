import os

REDIS_URL = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")
BACKEND_URL = os.environ.get("GATEWAY_BACKEND_URL", "http://localhost:9000")

# Deliberately small defaults so the boundary-burst behavior and the
# token-bucket smoothing are both observable in a few seconds during the
# demo script / tests, without needing to wait out a realistic production
# window (e.g. 100 requests/minute) to see the effect.
RATE_LIMIT = int(os.environ.get("GATEWAY_RATE_LIMIT", "5"))
RATE_WINDOW_SECONDS = int(os.environ.get("GATEWAY_RATE_WINDOW_SECONDS", "1"))
TOKEN_BUCKET_CAPACITY = int(os.environ.get("GATEWAY_BUCKET_CAPACITY", "5"))
TOKEN_BUCKET_REFILL_RATE = float(os.environ.get("GATEWAY_BUCKET_REFILL_RATE", "5.0"))

IDEMPOTENCY_TTL_SECONDS = int(os.environ.get("GATEWAY_IDEMPOTENCY_TTL_SECONDS", str(24 * 60 * 60)))
CACHE_TTL_SECONDS = int(os.environ.get("GATEWAY_CACHE_TTL_SECONDS", "30"))
