"""A small TTL response cache for GET requests, so repeated reads of the
same resource don't all pay the slow backend's latency. Deliberately not
used for POST/PUT -- caching a write's response would be a correctness
bug (a second GET for the same key should return fresh data on the next
TTL cycle, but a second POST is exactly what idempotency.py handles
instead, with different semantics: same *action*, not same cached data).
"""

import json


class ResponseCache:
    def __init__(self, redis_client, ttl_seconds: int = 30):
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    def _key(self, cache_key: str) -> str:
        return f"cache:get:{cache_key}"

    def get(self, cache_key: str):
        raw = self.redis.get(self._key(cache_key))
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, cache_key: str, value: dict) -> None:
        self.redis.set(self._key(cache_key), json.dumps(value), ex=self.ttl_seconds)
