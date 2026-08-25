"""End-to-end tests through the actual FastAPI gateway app, hitting the
real backend app in-process (no live server, no sockets -- but the real
backend logic runs, including its process counters, so 'idempotency
prevented a duplicate charge' is demonstrated against real
request/response handling, not mocked).

Note: httpx's ASGITransport only implements the async request path, but
the gateway's own http_client is sync (see main.py's design note on
keeping Redis + HTTP calls synchronous throughout). Starlette's
TestClient is what actually bridges a sync client onto an async ASGI app
under the hood, so it's used here as the backend connection too, not
just for driving the gateway itself.
"""

import concurrent.futures

import fakeredis
import pytest
from fastapi.testclient import TestClient

from backend.slow_backend import app as backend_app, _process_count
from gateway.main import create_app
from gateway.rate_limiter import TokenBucketLimiter


@pytest.fixture
def gateway_client():
    _process_count["orders_created"] = 0
    _process_count["reads"] = 0
    redis_client = fakeredis.FakeRedis()
    limiter = TokenBucketLimiter(redis_client, capacity=100, refill_rate=100.0)  # generous, not under test here
    backend_http_client = TestClient(backend_app, base_url="http://backend")
    app = create_app(redis_client, limiter, backend_url="http://backend", http_client=backend_http_client)
    return TestClient(app)


def test_create_order_requires_idempotency_key(gateway_client):
    response = gateway_client.post("/orders", json={"item": "widget", "amount_cents": 500})
    assert response.status_code == 400


def test_create_order_succeeds_with_idempotency_key(gateway_client):
    response = gateway_client.post(
        "/orders",
        json={"item": "widget", "amount_cents": 500},
        headers={"Idempotency-Key": "order-abc-1"},
    )
    assert response.status_code == 200
    assert response.json()["item"] == "widget"
    assert _process_count["orders_created"] == 1


def test_duplicate_request_same_idempotency_key_hits_backend_only_once(gateway_client):
    headers = {"Idempotency-Key": "order-abc-2"}
    body = {"item": "gadget", "amount_cents": 1200}

    first = gateway_client.post("/orders", json=body, headers=headers)
    second = gateway_client.post("/orders", json=body, headers=headers)

    assert first.json() == second.json()  # same order_id both times
    assert _process_count["orders_created"] == 1  # backend only processed it once


def test_concurrent_duplicate_requests_still_hit_backend_only_once(gateway_client):
    """The real proof: fire genuinely concurrent requests with the same
    idempotency key and confirm the backend only ever processed one of
    them -- not just sequential requests, which a weaker implementation
    could pass by accident."""
    headers = {"Idempotency-Key": "order-concurrent-1"}
    body = {"item": "concurrent-widget", "amount_cents": 999}

    def fire():
        return gateway_client.post("/orders", json=body, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: fire(), range(8)))

    statuses = [r.status_code for r in responses]
    # Exactly one request processes (200); the rest either get the
    # already-completed response (also 200, same body) or a 409 if they
    # raced in while the first was still mid-flight.
    assert all(s in (200, 409) for s in statuses)
    assert _process_count["orders_created"] == 1

    completed_bodies = {r.text for r in responses if r.status_code == 200}
    assert len(completed_bodies) == 1  # every successful response is identical


def test_different_idempotency_keys_both_process(gateway_client):
    r1 = gateway_client.post(
        "/orders", json={"item": "a", "amount_cents": 100}, headers={"Idempotency-Key": "key-a"}
    )
    r2 = gateway_client.post(
        "/orders", json={"item": "b", "amount_cents": 200}, headers={"Idempotency-Key": "key-b"}
    )
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert _process_count["orders_created"] == 2


def test_get_order_is_cached_on_second_call(gateway_client):
    create = gateway_client.post(
        "/orders", json={"item": "cached-item", "amount_cents": 300}, headers={"Idempotency-Key": "key-cache-1"}
    )
    order_id = create.json()["order_id"]

    first_read = gateway_client.get(f"/orders/{order_id}")
    second_read = gateway_client.get(f"/orders/{order_id}")

    assert first_read.json()["_cache"] == "miss"
    assert second_read.json()["_cache"] == "hit"
    assert _process_count["reads"] == 1  # backend only actually read once


def test_rate_limit_returns_429_when_exceeded(gateway_client):
    redis_client = fakeredis.FakeRedis()
    limiter = TokenBucketLimiter(redis_client, capacity=2, refill_rate=0.0)
    backend_http_client = TestClient(backend_app, base_url="http://backend")
    app = create_app(redis_client, limiter, backend_url="http://backend", http_client=backend_http_client)
    client = TestClient(app)

    headers = {"Idempotency-Key": "key-rl-1", "X-Client-Id": "same-client"}
    r1 = client.post("/orders", json={"item": "x", "amount_cents": 1}, headers=headers)
    headers2 = {"Idempotency-Key": "key-rl-2", "X-Client-Id": "same-client"}
    r2 = client.post("/orders", json={"item": "x", "amount_cents": 1}, headers=headers2)
    headers3 = {"Idempotency-Key": "key-rl-3", "X-Client-Id": "same-client"}
    r3 = client.post("/orders", json={"item": "x", "amount_cents": 1}, headers=headers3)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
